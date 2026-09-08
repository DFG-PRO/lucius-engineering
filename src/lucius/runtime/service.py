from __future__ import annotations

from datetime import datetime
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, BlockerCode, QueueWorkItemState, TaskStatus
from lucius.persistence.orm import (
    AuditEventORM,
    EngineeringPlanORM,
    PersistentWorkflowORM,
    PlanFreezeORM,
    ProjectRepositoryAttachmentORM,
    RepositoryRegistrationORM,
    TaskContractORM,
    TaskORM,
)
from lucius.pilots.queue import NonBlockingQueueService, QueueStateError
from lucius.tasks.service import TaskService
from lucius.runtime.adapters import ExecutionAdapter, RuntimePlanningAdapter
from lucius.runtime.schemas import (
    ExecutionAdapterResult,
    ExecutionRuntimeLoopResult,
    RuntimeExecutionContext,
    RuntimeLoopConfig,
    RuntimeLoopStatus,
    RuntimeTaskExecutionRecord,
)


class ExecutionRuntimeLoopError(RuntimeError):
    pass


class PreMutationReleaseError(ExecutionRuntimeLoopError):
    pass


class ExecutionRuntimeLoopService:
    def __init__(
        self,
        session: Session,
        *,
        planning_adapter: RuntimePlanningAdapter,
        execution_adapter: ExecutionAdapter,
        actor: Actor = Actor.LUCIUS,
    ):
        self.session = session
        self.planning_adapter = planning_adapter
        self.execution_adapter = execution_adapter
        self.actor = actor
        self.audit = AuditService(session)
        self.queue = NonBlockingQueueService(session)

    def run(self, config: RuntimeLoopConfig) -> ExecutionRuntimeLoopResult:
        if config.dispatcher_count != 1:
            raise ExecutionRuntimeLoopError("native runtime loop supports exactly one logical dispatcher")

        started_at = time.monotonic()
        result = ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE)
        provider_ids: list[str] = []
        workflow_ids = config.workflow_ids

        plan_references = self._prepare_workflow_plans(workflow_ids)
        result.plan_references = plan_references

        try:
            while result.selected_tasks < config.max_tasks:
                selection = self.queue.start_global_next(workflow_ids, actor=self.actor)
                if selection.selected_workflow_id is None or selection.selected_item_id is None:
                    result.status = RuntimeLoopStatus.IDLE if result.selected_tasks == 0 else RuntimeLoopStatus.COMPLETED
                    result.stopped_reason = selection.reason
                    break

                context = self._execution_context(
                    workflow_id=selection.selected_workflow_id,
                    item_id=selection.selected_item_id,
                )
                result.selected_tasks += 1
                if selection.started_previous_state == QueueWorkItemState.READY_TO_RESUME.value:
                    result.resumed_tasks += 1
                    result.blocked_task_time_seconds += _blocked_interval_seconds(context.queue_item)

                pre_dispatch_error = self._pre_dispatch_release_error(context)
                if pre_dispatch_error:
                    self._block_pre_dispatch_failure(context, pre_dispatch_error)
                    result.blocked_tasks += 1
                    result.status = RuntimeLoopStatus.FAILED
                    result.stopped_reason = pre_dispatch_error
                    break

                provider_ids.append(self.execution_adapter.provider_id)
                adapter_result = self._execute_adapter_fail_closed(context)
                self._add_observability(result, adapter_result)
                result.task_records.append(
                    RuntimeTaskExecutionRecord(
                        workflow_id=context.workflow_id,
                        item_id=context.item_id,
                        project_id=context.project_id,
                        provider_id=self.execution_adapter.provider_id,
                        outcome=adapter_result.outcome,
                        completed_substeps=adapter_result.completed_substeps,
                        evidence_count=len(adapter_result.evidence),
                        verification_count=len(adapter_result.verification),
                        documentation_count=len(adapter_result.documentation),
                    )
                )

                if adapter_result.outcome.value == "COMPLETED":
                    self.queue.complete_item(
                        context.workflow_id,
                        context.item_id,
                        completed_substeps=adapter_result.completed_substeps,
                        actor=self.actor,
                    )
                    result.completed_tasks += 1
                    self._record_task_execution(context, adapter_result, "COMPLETED")
                    result.status = RuntimeLoopStatus.COMPLETED
                    continue

                self._block_for_non_completion(context, adapter_result)
                result.blocked_tasks += 1
                result.escalations += adapter_result.escalations
                result.status = _status_for_non_completion(adapter_result)
                self._record_task_execution(context, adapter_result, result.status.value)
                if adapter_result.outcome.value in {"ESCALATED", "FAILED"} or config.stop_on_block:
                    result.stopped_reason = adapter_result.blocking_reason or adapter_result.error or adapter_result.outcome.value
                    break
        except QueueStateError as error:
            result.status = RuntimeLoopStatus.FAILED
            result.stopped_reason = str(error)
            self.audit.record(
                event_type="NATIVE_RUNTIME_LOOP_FAILED_CLOSED",
                actor=self.actor.value,
                action="run_native_execution_runtime_loop",
                result="QUEUE_STATE_ERROR",
                metadata={"reason": str(error), "workflow_ids": workflow_ids},
            )
        finally:
            result.provider_ids = list(dict.fromkeys(provider_ids))
            result.wall_clock_duration_seconds = max(0.0, time.monotonic() - started_at)
            self.audit.record(
                event_type="NATIVE_RUNTIME_LOOP_COMPLETED",
                actor=self.actor.value,
                action="run_native_execution_runtime_loop",
                result=result.status.value,
                metadata=result.model_dump(mode="json"),
            )
        return result

    def _prepare_workflow_plans(self, workflow_ids: list[str] | None) -> list:
        rows = _workflow_rows(self.session, workflow_ids)
        references = []
        for row in rows:
            references.append(self.planning_adapter.prepare_workflow_plan(self.session, row.id))
        return references

    def _execution_context(self, *, workflow_id: str, item_id: str) -> RuntimeExecutionContext:
        workflow = self.session.get(PersistentWorkflowORM, workflow_id)
        if workflow is None:
            raise ExecutionRuntimeLoopError(f"Unknown persistent workflow: {workflow_id}")
        item = _queue_item(workflow.task_backlog, item_id)
        return RuntimeExecutionContext(
            workflow_id=workflow.id,
            item_id=item_id,
            logical_task_id=str(item.get("logical_task_id") or item.get("task_id") or item_id),
            project_id=workflow.project_id,
            repository_id=workflow.repository_id,
            workflow_task_id=workflow.task_id,
            title=item.get("title"),
            workflow_objective=workflow.objective,
            worktree_path=workflow.worktree_path,
            authority_tier=workflow.authority_tier,
            plan_id=workflow.plan_id,
            plan_freeze_id=workflow.plan_freeze_id,
            queue_item=dict(item),
        )

    def _pre_dispatch_release_error(self, context: RuntimeExecutionContext) -> str | None:
        workflow = self.session.get(PersistentWorkflowORM, context.workflow_id)
        task = self.session.get(TaskORM, context.workflow_task_id) if context.workflow_task_id else None
        if workflow is None:
            return f"Unknown persistent workflow: {context.workflow_id}"
        if task is None:
            return "Persistent workflow has no canonical task bound for pre-mutation release."
        if task.project_id != context.project_id:
            return "Selected workflow task project does not match execution context project."
        if task.status != TaskStatus.READY.value:
            return f"Task {task.id} is not READY for execution: {task.status}"

        contract = _active_contract(self.session, task.id)
        if contract is None:
            return f"Task {task.id} has no active contract."
        if context.repository_id is None:
            return "Selected workflow has no repository bound for execution."
        if context.repository_id not in set(contract.repository_ids or []):
            return "Selected workflow repository is not authorized by the active task contract."
        registration = self.session.get(RepositoryRegistrationORM, context.repository_id)
        attachment = self.session.scalar(
            select(ProjectRepositoryAttachmentORM).where(
                ProjectRepositoryAttachmentORM.project_id == task.project_id,
                ProjectRepositoryAttachmentORM.repository_id == context.repository_id,
            )
        )
        if registration is None or attachment is None:
            return "Selected workflow repository is not attached to the task project."

        selected_identity_error = _selected_mutation_identity_error(context)
        if selected_identity_error:
            return selected_identity_error

        current_contract_errors = [
            f"{blocker.code.value}: {blocker.message}"
            for blocker in TaskService(self.session).validate_contract(task.id).blockers
        ]
        if current_contract_errors:
            return "Current task contract is not valid for execution: " + "; ".join(current_contract_errors)

        release_error = _readiness_release_evidence_error(self.session, task.id)
        if release_error:
            return release_error

        freeze_error = _plan_freeze_error(self.session, context, contract)
        if freeze_error:
            return freeze_error

        item = _queue_item(workflow.task_backlog, context.item_id)
        if item.get("state") != QueueWorkItemState.RUNNING.value:
            return "Selected queue item is no longer RUNNING immediately before dispatch."
        return None

    def _block_pre_dispatch_failure(self, context: RuntimeExecutionContext, reason: str) -> None:
        self.audit.record(
            event_type="NATIVE_RUNTIME_PRE_MUTATION_RELEASE_BLOCKED",
            actor=self.actor.value,
            project_id=context.project_id,
            repository_id=context.repository_id,
            task_id=context.workflow_task_id,
            action="validate_pre_mutation_release",
            result="BLOCKED",
            metadata={"workflow_id": context.workflow_id, "item_id": context.item_id, "reason": reason},
        )
        self.queue.block_running_item(
            context.workflow_id,
            context.item_id,
            blocking_state=QueueWorkItemState.WAITING_HUMAN,
            blocking_reason=reason,
            blocker_category=BlockerCode.AUTHORITY_INSUFFICIENT.value,
            resume_condition="Run canonical readiness/release validation successfully before retrying dispatch.",
            work_completed=[],
            relevant_artifacts=[],
            actor=self.actor,
        )

    def _block_for_non_completion(
        self,
        context: RuntimeExecutionContext,
        adapter_result: ExecutionAdapterResult,
    ) -> None:
        blocking_state = (
            QueueWorkItemState.WAITING_HUMAN
            if adapter_result.outcome.value in {"ESCALATED", "FAILED"}
            else QueueWorkItemState.WAITING_EXTERNAL
        )
        self.queue.block_running_item(
            context.workflow_id,
            context.item_id,
            blocking_state=blocking_state,
            blocking_reason=adapter_result.blocking_reason or adapter_result.error or adapter_result.outcome.value,
            blocker_category=adapter_result.blocker_category or adapter_result.outcome.value,
            resume_condition=adapter_result.resume_condition or "Runtime adapter must provide safe continuation evidence.",
            work_completed=adapter_result.completed_substeps,
            relevant_artifacts=[str(item) for item in adapter_result.evidence],
            actor=self.actor,
        )

    def _execute_adapter_fail_closed(
        self,
        context: RuntimeExecutionContext,
    ) -> ExecutionAdapterResult:
        started_at = time.monotonic()
        try:
            return self.execution_adapter.execute(context)
        except Exception as error:
            return ExecutionAdapterResult(
                outcome="FAILED",
                active_execution_seconds=max(0.0, time.monotonic() - started_at),
                error=f"{error.__class__.__name__}: {error}",
                blocker_category="EXECUTION_ADAPTER_EXCEPTION",
                blocking_reason="Execution adapter raised before returning a canonical result.",
                resume_condition="Repair the adapter or task implementation and resume from the blocked queue item.",
            )

    def _add_observability(
        self,
        result: ExecutionRuntimeLoopResult,
        adapter_result: ExecutionAdapterResult,
    ) -> None:
        result.active_execution_time_seconds += adapter_result.active_execution_seconds
        result.external_capacity_wait_time_seconds += adapter_result.external_capacity_wait_seconds
        result.human_wait_time_seconds += adapter_result.human_wait_seconds
        result.blocked_task_time_seconds += adapter_result.blocked_task_seconds
        result.retries += adapter_result.retries
        result.escalations += adapter_result.escalations

    def _record_task_execution(
        self,
        context: RuntimeExecutionContext,
        adapter_result: ExecutionAdapterResult,
        result_label: str,
    ) -> None:
        self.audit.record(
            event_type="NATIVE_RUNTIME_TASK_EXECUTION_RECORDED",
            actor=self.actor.value,
            project_id=context.project_id,
            repository_id=context.repository_id,
            task_id=context.workflow_task_id,
            action="execute_runtime_queue_item",
            result=result_label,
            metadata={
                "workflow_id": context.workflow_id,
                "item_id": context.item_id,
                "provider_id": self.execution_adapter.provider_id,
                "completed_substeps": adapter_result.completed_substeps,
                "evidence": adapter_result.evidence,
                "verification": adapter_result.verification,
                "documentation": adapter_result.documentation,
                "active_execution_seconds": adapter_result.active_execution_seconds,
                "external_capacity_wait_seconds": adapter_result.external_capacity_wait_seconds,
                "human_wait_seconds": adapter_result.human_wait_seconds,
                "blocked_task_seconds": adapter_result.blocked_task_seconds,
                "retries": adapter_result.retries,
                "escalations": adapter_result.escalations,
            },
        )


def _workflow_rows(session: Session, workflow_ids: list[str] | None) -> list[PersistentWorkflowORM]:
    if workflow_ids:
        rows = [session.get(PersistentWorkflowORM, workflow_id) for workflow_id in workflow_ids]
        missing = [workflow_id for workflow_id, row in zip(workflow_ids, rows, strict=True) if row is None]
        if missing:
            raise ExecutionRuntimeLoopError(f"Unknown persistent workflows: {missing}")
        return [row for row in rows if row is not None]
    return [
        row
        for row in session.query(PersistentWorkflowORM).order_by(PersistentWorkflowORM.id).all()
        if row.task_backlog
    ]


def _queue_item(items: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
    for item in items:
        if str(item.get("item_id") or item.get("task_id")) == item_id:
            return item
    raise ExecutionRuntimeLoopError(f"Unknown queue item: {item_id}")


def _active_contract(session: Session, task_id: str) -> TaskContractORM | None:
    return session.scalar(
        select(TaskContractORM)
        .where(TaskContractORM.task_id == task_id)
        .order_by(TaskContractORM.version.desc())
        .limit(1)
    )


def _selected_mutation_identity_error(context: RuntimeExecutionContext) -> str | None:
    item = context.queue_item or {}
    item_targets = [
        item.get("mutation_item_id"),
        item.get("mutates_item_id"),
        item.get("execution_item_id"),
        item.get("selected_item_id"),
    ]
    for target in item_targets:
        if target is not None and str(target) != context.item_id:
            return "Queue item mutation identity does not match the selected item."
    task_targets = [item.get("mutation_task_id"), item.get("mutates_task_id")]
    allowed_task_ids = {context.logical_task_id, context.workflow_task_id, context.item_id} - {None}
    for target in task_targets:
        if target is not None and str(target) not in {str(value) for value in allowed_task_ids}:
            return "Queue item mutation task identity does not match the selected task."
    return None


def _readiness_release_evidence_error(session: Session, task_id: str) -> str | None:
    latest_ready = _latest_task_audit(session, task_id, {"TASK_READY"})
    if latest_ready is None:
        return f"Task {task_id} has no successful canonical readiness transition."
    latest_failed_ready = _latest_task_audit(session, task_id, {"POLICY_BLOCK", "TASK_BLOCKED"}, action="mark_ready")
    if latest_failed_ready and latest_failed_ready.timestamp >= latest_ready.timestamp:
        return f"Task {task_id} readiness failure was not superseded by a later TASK_READY event."
    latest_contract_change = _latest_task_audit(session, task_id, {"TASK_CONTRACT_CREATED", "TASK_CONTRACT_UPDATED"})
    if latest_contract_change and latest_contract_change.timestamp > latest_ready.timestamp:
        return f"Task {task_id} readiness is stale after a contract change."
    return None


def _latest_task_audit(
    session: Session,
    task_id: str,
    event_types: set[str],
    *,
    action: str | None = None,
) -> AuditEventORM | None:
    query = select(AuditEventORM).where(AuditEventORM.task_id == task_id, AuditEventORM.event_type.in_(event_types))
    if action is not None:
        query = query.where(AuditEventORM.action == action)
    return session.scalar(query.order_by(AuditEventORM.timestamp.desc(), AuditEventORM.id.desc()).limit(1))


def _plan_freeze_error(
    session: Session,
    context: RuntimeExecutionContext,
    contract: TaskContractORM,
) -> str | None:
    if not context.plan_id or not context.plan_freeze_id:
        return "Selected workflow has no frozen EngineeringPlan."
    plan = session.get(EngineeringPlanORM, context.plan_id)
    freeze = session.get(PlanFreezeORM, context.plan_freeze_id)
    if plan is None or freeze is None:
        return "Selected workflow references an unknown EngineeringPlan or PlanFreeze."
    if freeze.plan_id != plan.id:
        return "Selected workflow PlanFreeze does not match the EngineeringPlan."
    if plan.task_id != context.workflow_task_id or freeze.task_id != context.workflow_task_id:
        return "Selected workflow frozen plan does not match the exact selected task."
    if plan.project_id != context.project_id or freeze.project_id != context.project_id:
        return "Selected workflow frozen plan does not match the execution project."
    if plan.task_contract_id != contract.id or plan.task_contract_version != contract.version:
        return "Selected workflow frozen plan is stale relative to the active task contract."
    return None


def _blocked_interval_seconds(item: dict[str, Any]) -> float:
    blocked_at = _parse_datetime(item.get("blocked_at"))
    resolved_at = _parse_datetime(item.get("resolved_at"))
    if blocked_at is None or resolved_at is None or resolved_at < blocked_at:
        return 0.0
    return (resolved_at - blocked_at).total_seconds()


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _status_for_non_completion(adapter_result: ExecutionAdapterResult) -> RuntimeLoopStatus:
    if adapter_result.outcome.value == "ESCALATED":
        return RuntimeLoopStatus.ESCALATED
    if adapter_result.outcome.value == "FAILED":
        return RuntimeLoopStatus.FAILED
    return RuntimeLoopStatus.BLOCKED
