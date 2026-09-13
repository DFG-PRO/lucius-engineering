from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, AllowedAction, AuthorityLevel, PersistentWorkflowState
from lucius.integration.controlled_commit import ControlledCommitRequest, ControlledCommitResult, ControlledCommitService
from lucius.integration.service import CanonicalIntegrationRequest, CanonicalIntegrationResult, CanonicalIntegrationService
from lucius.persistence.orm import (
    EngineeringPlanORM,
    PersistentWorkflowORM,
    PlanFreezeORM,
    RepositoryRegistrationORM,
    TaskContractORM,
    TaskORM,
)
from lucius.runtime.schemas import RuntimeExecutionOutcome, RuntimeLoopConfig, RuntimeLoopStatus


class NightShiftStatus(StrEnum):
    CLEAN_TERMINATION = "CLEAN_TERMINATION"
    HARD_STOP = "HARD_STOP"


@dataclass(frozen=True)
class NightShiftConfig:
    workflow_ids: list[str] | None = None
    max_tasks: int = 1
    max_commits: int = 1
    max_wall_clock_seconds: float = 3600.0
    stop_on_escalation: bool = True
    stop_on_manual_reconciliation: bool = True
    dispatcher_count: int = 1
    allowed_task_complexities: tuple[str, ...] = ("T0", "T1")
    allowed_risk_levels: tuple[str, ...] = ("LOW",)

    def __post_init__(self) -> None:
        if self.dispatcher_count != 1:
            raise ValueError("Night Shift v0 supports exactly one logical dispatcher")
        if self.max_tasks < 1:
            raise ValueError("max_tasks must be positive")
        if self.max_commits < 0:
            raise ValueError("max_commits must be non-negative")
        if self.max_wall_clock_seconds <= 0:
            raise ValueError("max_wall_clock_seconds must be positive")


@dataclass
class NightShiftCycleRecord:
    cycle_number: int
    runtime_status: str | None = None
    runtime_stopped_reason: str | None = None
    workflow_id: str | None = None
    item_id: str | None = None
    project_id: str | None = None
    provider_id: str | None = None
    task_outcome: str | None = None
    integration_status: str | None = None
    integration_reason: str | None = None
    commit_status: str | None = None
    commit_reason: str | None = None
    resulting_commit_sha: str | None = None
    manual_reconciliation_required: bool = False
    hard_stop_reason: str | None = None


@dataclass
class NightShiftResult:
    status: NightShiftStatus = NightShiftStatus.CLEAN_TERMINATION
    stop_reason: str | None = None
    cycles_attempted: int = 0
    tasks_selected: int = 0
    tasks_completed: int = 0
    tasks_blocked: int = 0
    tasks_escalated: int = 0
    workflows_processed: list[str] = field(default_factory=list)
    provider_ids: list[str] = field(default_factory=list)
    l1_integrations_attempted: int = 0
    l1_integrations_succeeded: int = 0
    l1_integrations_failed: int = 0
    l2_commits_attempted: int = 0
    l2_commits_succeeded: int = 0
    l2_commits_already_committed: int = 0
    l2_commits_failed: int = 0
    resulting_commit_shas: list[str] = field(default_factory=list)
    manual_reconciliation_required: bool = False
    project_switches: int = 0
    wall_clock_duration_seconds: float = 0.0
    max_limit_reached_reason: str | None = None
    unauthorized_remote_actions: list[str] = field(default_factory=list)
    integrity_violation: str | None = None
    cycles: list[NightShiftCycleRecord] = field(default_factory=list)


class _RuntimeService(Protocol):
    def run(self, config: RuntimeLoopConfig): ...


class _IntegrationService(Protocol):
    def integrate(self, request: CanonicalIntegrationRequest) -> CanonicalIntegrationResult: ...


class _CommitService(Protocol):
    def commit(self, request: ControlledCommitRequest) -> ControlledCommitResult: ...


class NightShiftSupervisor:
    """Thin single-worker supervisor over runtime, L1 integration, and L2 commit services."""

    def __init__(
        self,
        session: Session,
        *,
        runtime_service: _RuntimeService,
        integration_service: _IntegrationService | None = None,
        commit_service: _CommitService | None = None,
        actor: Actor = Actor.LUCIUS,
        clock=time.monotonic,
    ):
        self.session = session
        self.runtime_service = runtime_service
        self.integration_service = integration_service or CanonicalIntegrationService(session)
        self.commit_service = commit_service or ControlledCommitService(session)
        self.actor = actor
        self.audit = AuditService(session)
        self.clock = clock

    def run(self, config: NightShiftConfig) -> NightShiftResult:
        started = self.clock()
        result = NightShiftResult()
        self._audit("NIGHT_SHIFT_STARTED", "STARTED", {"config": _config_payload(config)})

        while True:
            elapsed = max(0.0, self.clock() - started)
            if result.tasks_selected >= config.max_tasks:
                return self._finish_clean(result, started, "MAX_TASKS_REACHED")
            if result.l2_commits_succeeded >= config.max_commits:
                return self._finish_clean(result, started, "MAX_COMMITS_REACHED")
            if elapsed >= config.max_wall_clock_seconds:
                return self._finish_clean(result, started, "MAX_WALL_CLOCK_REACHED")

            cycle = NightShiftCycleRecord(cycle_number=result.cycles_attempted + 1)
            result.cycles.append(cycle)
            result.cycles_attempted += 1
            self._audit("NIGHT_SHIFT_CYCLE_STARTED", "STARTED", {"cycle_number": cycle.cycle_number})

            runtime_result = self.runtime_service.run(
                RuntimeLoopConfig(
                    workflow_ids=config.workflow_ids,
                    max_tasks=1,
                    dispatcher_count=1,
                    stop_on_block=False,
                    stop_on_escalation=config.stop_on_escalation,
                )
            )
            cycle.runtime_status = runtime_result.status.value
            cycle.runtime_stopped_reason = runtime_result.stopped_reason
            result.tasks_selected += runtime_result.selected_tasks
            result.tasks_completed += runtime_result.completed_tasks
            result.tasks_blocked += runtime_result.blocked_tasks
            result.tasks_escalated += runtime_result.escalations
            result.project_switches += runtime_result.project_switches
            _extend_unique(result.provider_ids, runtime_result.provider_ids)

            if runtime_result.status == RuntimeLoopStatus.IDLE and runtime_result.selected_tasks == 0:
                self._audit_cycle(cycle, "IDLE")
                return self._finish_clean(result, started, "IDLE")

            if runtime_result.escalations or runtime_result.status == RuntimeLoopStatus.ESCALATED:
                cycle.hard_stop_reason = "RUNTIME_ESCALATION"
                return self._hard_stop(result, started, "RUNTIME_ESCALATION", cycle)

            if not runtime_result.task_records:
                cycle.hard_stop_reason = runtime_result.stopped_reason or "RUNTIME_RESULT_WITHOUT_TASK_RECORD"
                return self._hard_stop(result, started, cycle.hard_stop_reason, cycle)

            record = runtime_result.task_records[-1]
            cycle.workflow_id = record.workflow_id
            cycle.item_id = record.item_id
            cycle.project_id = record.project_id
            cycle.provider_id = record.provider_id
            cycle.task_outcome = record.outcome.value
            _extend_unique(result.workflows_processed, [record.workflow_id])

            if record.outcome != RuntimeExecutionOutcome.COMPLETED:
                self._audit_cycle(cycle, "TASK_LOCAL_NON_COMPLETION")
                continue

            context = self._promotion_context(record.workflow_id, config)
            if isinstance(context, str):
                cycle.hard_stop_reason = context
                return self._hard_stop(result, started, context, cycle)

            integration = self.integration_service.integrate(
                CanonicalIntegrationRequest(
                    workflow_id=record.workflow_id,
                    canonical_repository_path=context.canonical_repository_path,
                    candidate_workspace_path=context.candidate_workspace_path,
                    expected_baseline_commit=context.expected_baseline_commit,
                    actor=self.actor,
                )
            )
            result.l1_integrations_attempted += 1
            cycle.integration_status = integration.status
            cycle.integration_reason = integration.reason
            if integration.status != "COMPLETED":
                result.l1_integrations_failed += 1
                reason = _integration_stop_reason(integration)
                cycle.hard_stop_reason = reason
                return self._hard_stop(result, started, reason, cycle)
            result.l1_integrations_succeeded += 1

            if not context.create_commit_authorized:
                reason = "CREATE_COMMIT_NOT_AUTHORIZED"
                cycle.hard_stop_reason = reason
                return self._hard_stop(result, started, reason, cycle)

            commit = self.commit_service.commit(
                ControlledCommitRequest(
                    workflow_id=record.workflow_id,
                    canonical_repository_path=context.canonical_repository_path,
                    expected_baseline_commit=context.expected_baseline_commit,
                    authority_level=AuthorityLevel.L2,
                    actor=self.actor,
                    protected_untracked_hashes=dict(
                        integration.protected_untracked_hashes
                    ),
                )
            )
            result.l2_commits_attempted += 1
            cycle.commit_status = commit.status
            cycle.commit_reason = commit.reason
            cycle.resulting_commit_sha = commit.resulting_commit
            cycle.manual_reconciliation_required = commit.manual_reconciliation_required
            if commit.status == "COMPLETED":
                result.l2_commits_succeeded += 1
                if commit.resulting_commit:
                    _extend_unique(result.resulting_commit_shas, [commit.resulting_commit])
                self._audit_cycle(cycle, "COMPLETED")
                continue
            if commit.status == "ALREADY_COMMITTED":
                result.l2_commits_already_committed += 1
                if commit.resulting_commit:
                    _extend_unique(result.resulting_commit_shas, [commit.resulting_commit])
                self._audit_cycle(cycle, "ALREADY_COMMITTED")
                continue

            result.l2_commits_failed += 1
            if commit.manual_reconciliation_required and config.stop_on_manual_reconciliation:
                result.manual_reconciliation_required = True
                reason = "MANUAL_RECONCILIATION_REQUIRED"
            else:
                reason = "CONTROLLED_COMMIT_FAILED"
            cycle.hard_stop_reason = reason
            return self._hard_stop(result, started, reason, cycle)

    def _promotion_context(self, workflow_id: str, config: NightShiftConfig) -> "_PromotionContext | str":
        workflow = self.session.get(PersistentWorkflowORM, workflow_id)
        if workflow is None:
            return "WORKFLOW_NOT_FOUND"
        if workflow.workflow_state != PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value:
            return "WORKFLOW_NOT_COMPLETED_PENDING_INTEGRATION"
        task = self.session.get(TaskORM, workflow.task_id) if workflow.task_id else None
        plan = self.session.get(EngineeringPlanORM, workflow.plan_id) if workflow.plan_id else None
        freeze = self.session.get(PlanFreezeORM, workflow.plan_freeze_id) if workflow.plan_freeze_id else None
        repository = self.session.get(RepositoryRegistrationORM, workflow.repository_id) if workflow.repository_id else None
        if task is None or plan is None or freeze is None or repository is None:
            return "PROMOTION_STATE_INCOMPLETE"
        if task.project_id != workflow.project_id or plan.project_id != workflow.project_id or freeze.project_id != workflow.project_id:
            return "PROMOTION_PROJECT_RELATIONSHIP_INVALID"
        if freeze.task_id != task.id or freeze.plan_id != plan.id or plan.task_id != task.id:
            return "PROMOTION_PLAN_FREEZE_RELATIONSHIP_INVALID"
        if not freeze.commit_sha:
            return "FROZEN_BASELINE_COMMIT_REQUIRED"
        if task.complexity not in set(config.allowed_task_complexities):
            return "TASK_COMPLEXITY_OUTSIDE_NIGHT_SHIFT_BOUNDS"
        if str(plan.risk_level).upper() not in set(config.allowed_risk_levels):
            return "PLAN_RISK_OUTSIDE_NIGHT_SHIFT_BOUNDS"
        if not workflow.worktree_path:
            return "CANDIDATE_WORKTREE_REQUIRED"
        return _PromotionContext(
            canonical_repository_path=Path(repository.location).resolve(),
            candidate_workspace_path=Path(workflow.worktree_path).resolve(),
            expected_baseline_commit=freeze.commit_sha,
            create_commit_authorized=_create_commit_authorized(self.session, task, plan),
        )

    def _finish_clean(self, result: NightShiftResult, started: float, reason: str) -> NightShiftResult:
        result.status = NightShiftStatus.CLEAN_TERMINATION
        result.stop_reason = reason
        result.max_limit_reached_reason = reason if reason.startswith("MAX_") else None
        result.wall_clock_duration_seconds = max(0.0, self.clock() - started)
        self._audit("NIGHT_SHIFT_COMPLETED", "CLEAN_TERMINATION", _result_payload(result))
        return result

    def _hard_stop(
        self,
        result: NightShiftResult,
        started: float,
        reason: str,
        cycle: NightShiftCycleRecord,
    ) -> NightShiftResult:
        result.status = NightShiftStatus.HARD_STOP
        result.stop_reason = reason
        result.integrity_violation = reason
        result.wall_clock_duration_seconds = max(0.0, self.clock() - started)
        self._audit_cycle(cycle, "HARD_STOP")
        self._audit("NIGHT_SHIFT_HARD_STOP", "HARD_STOP", {"reason": reason, **_result_payload(result)})
        return result

    def _audit_cycle(self, cycle: NightShiftCycleRecord, result: str) -> None:
        self._audit("NIGHT_SHIFT_CYCLE_CHECKPOINT", result, asdict(cycle))

    def _audit(self, event_type: str, result: str, metadata: dict[str, Any]) -> None:
        self.audit.record(
            event_type=event_type,
            actor=self.actor.value,
            authority_level=AuthorityLevel.L2,
            action="run_night_shift_supervisor",
            result=result,
            metadata=metadata,
        )


@dataclass(frozen=True)
class _PromotionContext:
    canonical_repository_path: Path
    candidate_workspace_path: Path
    expected_baseline_commit: str
    create_commit_authorized: bool


def _create_commit_authorized(session: Session, task: TaskORM, plan: EngineeringPlanORM) -> bool:
    contract = session.get(TaskContractORM, plan.task_contract_id)
    if contract is None or contract.task_id != task.id or contract.version != plan.task_contract_version:
        return False
    if AllowedAction.CREATE_COMMIT.value not in {str(action) for action in contract.allowed_actions}:
        return False
    return _authority_rank(task.authority_level) >= 2 and _authority_rank(contract.authority_level) >= 2


def _authority_rank(value: str) -> int:
    return {AuthorityLevel.L0.value: 0, AuthorityLevel.L1.value: 1, AuthorityLevel.L2.value: 2, AuthorityLevel.L3.value: 3}.get(value, -1)


def _integration_stop_reason(result: CanonicalIntegrationResult) -> str:
    if result.status == "ROLLBACK_FAILED":
        return "L1_ROLLBACK_UNCERTAIN"
    if result.rollback_attempted and not result.rollback_succeeded:
        return "L1_ROLLBACK_UNCERTAIN"
    return "L1_INTEGRATION_FAILED"


def _config_payload(config: NightShiftConfig) -> dict[str, Any]:
    return {
        **asdict(config),
        "single_worker": True,
        "single_dispatcher": config.dispatcher_count == 1,
        "no_unattended_codex": True,
        "no_push_deploy_or_remote_mutation": True,
    }


def _result_payload(result: NightShiftResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["status"] = result.status.value
    return payload


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)
