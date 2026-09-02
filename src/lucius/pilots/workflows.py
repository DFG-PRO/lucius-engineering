from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, PersistentWorkflowState, ResumeValidationResult
from lucius.persistence.orm import (
    PersistentWorkflowCheckpointORM,
    PersistentWorkflowORM,
    ResumeValidationORM,
    utc_now,
)
from lucius.persistence.repositories import next_id
from lucius.pilots.schemas import PersistentWorkflow, PersistentWorkflowCheckpoint, ResumeValidation


class PersistentWorkflowService:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def create(
        self,
        *,
        objective: str,
        expected_main_head: str,
        isolated_branch: str,
        worktree_path: str,
        authority_tier: str,
        project_id: str | None = None,
        repository_id: str | None = None,
        repository_snapshot_id: str | None = None,
        task_id: str | None = None,
        task_backlog: list[dict[str, Any]] | None = None,
        dependency_graph: dict[str, list[str]] | None = None,
        active_task_id: str | None = None,
        completed_task_ids: list[str] | None = None,
        pending_task_ids: list[str] | None = None,
        decisions: list[dict[str, Any]] | None = None,
        deviations: list[dict[str, Any]] | None = None,
        repair_counters: dict[str, Any] | None = None,
        pending_human_approvals: list[str] | None = None,
        resume_requirements: list[str] | None = None,
        actor: Actor = Actor.LUCIUS,
    ) -> PersistentWorkflow:
        row = PersistentWorkflowORM(
            id=next_id(self.session, "persistent_workflow"),
            project_id=project_id,
            repository_id=repository_id,
            repository_snapshot_id=repository_snapshot_id,
            task_id=task_id,
            objective=objective,
            expected_main_head=expected_main_head,
            isolated_branch=isolated_branch,
            worktree_path=worktree_path,
            workflow_state=PersistentWorkflowState.OBJECTIVE_ACCEPTED.value,
            authority_tier=authority_tier,
            task_backlog=task_backlog or [],
            dependency_graph=dependency_graph or {},
            active_task_id=active_task_id,
            completed_task_ids=completed_task_ids or [],
            pending_task_ids=pending_task_ids or [],
            decisions=decisions or [],
            deviations=deviations or [],
            repair_counters=repair_counters or {"total": 0, "by_task": {}, "limits": {"per_task": 3, "total": 8}},
            targeted_test_evidence=[],
            full_test_status={},
            checkpoint_history=[],
            pending_human_approvals=pending_human_approvals or [],
            resume_requirements=resume_requirements or [],
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        self.audit.record(
            event_type="PERSISTENT_WORKFLOW_CREATED",
            actor=actor.value,
            project_id=project_id,
            repository_id=repository_id,
            task_id=task_id,
            action="create_persistent_workflow",
            result=row.workflow_state,
            metadata={"workflow_id": row.id, "authority_tier": authority_tier},
        )
        return _workflow_from_row(row)

    def attach_plan(self, workflow_id: str, *, plan_id: str, plan_freeze_id: str, actor: Actor = Actor.LUCIUS) -> PersistentWorkflow:
        row = self._workflow(workflow_id)
        row.plan_id = plan_id
        row.plan_freeze_id = plan_freeze_id
        row.workflow_state = PersistentWorkflowState.PLAN_READY.value
        row.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="PERSISTENT_WORKFLOW_PLAN_ATTACHED",
            actor=actor.value,
            project_id=row.project_id,
            repository_id=row.repository_id,
            task_id=row.task_id,
            action="attach_workflow_plan",
            result=row.workflow_state,
            metadata={"workflow_id": row.id, "plan_id": plan_id, "plan_freeze_id": plan_freeze_id},
        )
        return _workflow_from_row(row)

    def update_progress(
        self,
        workflow_id: str,
        *,
        state: PersistentWorkflowState,
        active_task_id: str | None = None,
        completed_task_ids: list[str] | None = None,
        pending_task_ids: list[str] | None = None,
        decisions: list[dict[str, Any]] | None = None,
        deviations: list[dict[str, Any]] | None = None,
        repair_counters: dict[str, Any] | None = None,
        targeted_test_evidence: list[dict[str, Any]] | None = None,
        full_test_status: dict[str, Any] | None = None,
        pending_human_approvals: list[str] | None = None,
        actor: Actor = Actor.LUCIUS,
    ) -> PersistentWorkflow:
        row = self._workflow(workflow_id)
        row.workflow_state = state.value
        row.active_task_id = active_task_id
        if completed_task_ids is not None:
            row.completed_task_ids = completed_task_ids
        if pending_task_ids is not None:
            row.pending_task_ids = pending_task_ids
        if decisions is not None:
            row.decisions = decisions
        if deviations is not None:
            row.deviations = deviations
        if repair_counters is not None:
            row.repair_counters = repair_counters
        if targeted_test_evidence is not None:
            row.targeted_test_evidence = targeted_test_evidence
        if full_test_status is not None:
            row.full_test_status = full_test_status
        if pending_human_approvals is not None:
            row.pending_human_approvals = pending_human_approvals
        row.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="PERSISTENT_WORKFLOW_PROGRESS_UPDATED",
            actor=actor.value,
            project_id=row.project_id,
            repository_id=row.repository_id,
            task_id=row.task_id,
            action="update_workflow_progress",
            result=row.workflow_state,
            metadata={"workflow_id": row.id, "active_task_id": active_task_id},
        )
        return _workflow_from_row(row)

    def record_checkpoint(
        self,
        workflow_id: str,
        *,
        checkpoint_state: PersistentWorkflowState,
        implementation_head: str,
        expected_main_head: str,
        branch: str,
        worktree_path: str,
        active_task_id: str | None,
        completed_task_ids: list[str],
        pending_task_ids: list[str],
        dependency_graph: dict[str, list[str]],
        decisions: list[dict[str, Any]],
        deviations: list[dict[str, Any]],
        repair_counters: dict[str, Any],
        latest_test_results: list[dict[str, Any]],
        known_warnings: list[str],
        authority_tier: str,
        pending_human_approvals: list[str],
        resume_conditions: list[str],
        recommended_next_action: str,
        actor: Actor = Actor.LUCIUS,
    ) -> PersistentWorkflowCheckpoint:
        workflow = self._workflow(workflow_id)
        checkpoint = PersistentWorkflowCheckpointORM(
            id=next_id(self.session, "workflow_checkpoint"),
            workflow_id=workflow.id,
            checkpoint_state=checkpoint_state.value,
            implementation_head=implementation_head,
            expected_main_head=expected_main_head,
            branch=branch,
            worktree_path=worktree_path,
            active_task_id=active_task_id,
            completed_task_ids=completed_task_ids,
            pending_task_ids=pending_task_ids,
            dependency_graph=dependency_graph,
            decisions=decisions,
            deviations=deviations,
            repair_counters=repair_counters,
            latest_test_results=latest_test_results,
            known_warnings=known_warnings,
            authority_tier=authority_tier,
            pending_human_approvals=pending_human_approvals,
            resume_conditions=resume_conditions,
            recommended_next_action=recommended_next_action,
            created_at=utc_now(),
        )
        self.session.add(checkpoint)
        workflow.workflow_state = checkpoint_state.value
        workflow.active_task_id = active_task_id
        workflow.completed_task_ids = completed_task_ids
        workflow.pending_task_ids = pending_task_ids
        workflow.decisions = decisions
        workflow.deviations = deviations
        workflow.repair_counters = repair_counters
        workflow.targeted_test_evidence = latest_test_results
        workflow.pending_human_approvals = pending_human_approvals
        workflow.checkpoint_history = [*workflow.checkpoint_history, checkpoint.id]
        workflow.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="PERSISTENT_WORKFLOW_CHECKPOINT_RECORDED",
            actor=actor.value,
            project_id=workflow.project_id,
            repository_id=workflow.repository_id,
            task_id=workflow.task_id,
            action="record_workflow_checkpoint",
            result=checkpoint.checkpoint_state,
            metadata={"workflow_id": workflow.id, "checkpoint_id": checkpoint.id},
        )
        return _checkpoint_from_row(checkpoint)

    def validate_resume(
        self,
        workflow_id: str,
        *,
        checkpoint_id: str,
        checks: list[dict[str, Any]],
        next_eligible_task_id: str | None,
        notes: str | None = None,
        actor: Actor = Actor.LUCIUS,
    ) -> ResumeValidation:
        workflow = self._workflow(workflow_id)
        checkpoint = self.session.get(PersistentWorkflowCheckpointORM, checkpoint_id)
        if checkpoint is None or checkpoint.workflow_id != workflow.id:
            raise ValueError(f"Unknown checkpoint for workflow: {checkpoint_id}")
        result = _resume_result(checks)
        row = ResumeValidationORM(
            id=next_id(self.session, "resume_validation"),
            workflow_id=workflow.id,
            checkpoint_id=checkpoint.id,
            result=result.value,
            checks=checks,
            next_eligible_task_id=next_eligible_task_id,
            notes=notes,
            validated_at=utc_now(),
        )
        self.session.add(row)
        workflow.workflow_state = PersistentWorkflowState.RESUME_VALIDATION.value
        workflow.active_task_id = next_eligible_task_id
        workflow.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="PERSISTENT_WORKFLOW_RESUME_VALIDATED",
            actor=actor.value,
            project_id=workflow.project_id,
            repository_id=workflow.repository_id,
            task_id=workflow.task_id,
            action="validate_workflow_resume",
            result=result.value,
            metadata={"workflow_id": workflow.id, "checkpoint_id": checkpoint.id, "resume_validation_id": row.id},
        )
        return _resume_from_row(row)

    def _workflow(self, workflow_id: str) -> PersistentWorkflowORM:
        row = self.session.get(PersistentWorkflowORM, workflow_id)
        if row is None:
            raise ValueError(f"Unknown persistent workflow: {workflow_id}")
        return row


def _resume_result(checks: list[dict[str, Any]]) -> ResumeValidationResult:
    failed = [check for check in checks if check.get("status") != "PASS"]
    if not failed:
        return ResumeValidationResult.SAFE_TO_RESUME
    if any(check.get("severity") == "BLOCKING" for check in failed):
        return ResumeValidationResult.BLOCKED
    return ResumeValidationResult.CHECKPOINT_REVIEW_REQUIRED


def _workflow_from_row(row: PersistentWorkflowORM) -> PersistentWorkflow:
    return PersistentWorkflow(
        id=row.id,
        project_id=row.project_id,
        repository_id=row.repository_id,
        repository_snapshot_id=row.repository_snapshot_id,
        task_id=row.task_id,
        plan_id=row.plan_id,
        plan_freeze_id=row.plan_freeze_id,
        objective=row.objective,
        expected_main_head=row.expected_main_head,
        isolated_branch=row.isolated_branch,
        worktree_path=row.worktree_path,
        workflow_state=PersistentWorkflowState(row.workflow_state),
        authority_tier=row.authority_tier,
        task_backlog=row.task_backlog,
        dependency_graph=row.dependency_graph,
        active_task_id=row.active_task_id,
        completed_task_ids=row.completed_task_ids,
        pending_task_ids=row.pending_task_ids,
        decisions=row.decisions,
        deviations=row.deviations,
        repair_counters=row.repair_counters,
        targeted_test_evidence=row.targeted_test_evidence,
        full_test_status=row.full_test_status,
        checkpoint_history=row.checkpoint_history,
        pending_human_approvals=row.pending_human_approvals,
        resume_requirements=row.resume_requirements,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _checkpoint_from_row(row: PersistentWorkflowCheckpointORM) -> PersistentWorkflowCheckpoint:
    return PersistentWorkflowCheckpoint(
        id=row.id,
        workflow_id=row.workflow_id,
        checkpoint_state=PersistentWorkflowState(row.checkpoint_state),
        implementation_head=row.implementation_head,
        expected_main_head=row.expected_main_head,
        branch=row.branch,
        worktree_path=row.worktree_path,
        active_task_id=row.active_task_id,
        completed_task_ids=row.completed_task_ids,
        pending_task_ids=row.pending_task_ids,
        dependency_graph=row.dependency_graph,
        decisions=row.decisions,
        deviations=row.deviations,
        repair_counters=row.repair_counters,
        latest_test_results=row.latest_test_results,
        known_warnings=row.known_warnings,
        authority_tier=row.authority_tier,
        pending_human_approvals=row.pending_human_approvals,
        resume_conditions=row.resume_conditions,
        recommended_next_action=row.recommended_next_action,
        created_at=row.created_at,
    )


def _resume_from_row(row: ResumeValidationORM) -> ResumeValidation:
    return ResumeValidation(
        id=row.id,
        workflow_id=row.workflow_id,
        checkpoint_id=row.checkpoint_id,
        result=ResumeValidationResult(row.result),
        checks=row.checks,
        next_eligible_task_id=row.next_eligible_task_id,
        notes=row.notes,
        validated_at=row.validated_at,
    )
