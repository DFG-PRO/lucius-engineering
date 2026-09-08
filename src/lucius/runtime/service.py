from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, QueueWorkItemState
from lucius.persistence.orm import PersistentWorkflowORM
from lucius.pilots.queue import NonBlockingQueueService, QueueStateError
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

                provider_ids.append(self.execution_adapter.provider_id)
                adapter_result = self.execution_adapter.execute(context)
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
                if adapter_result.outcome.value == "ESCALATED" or config.stop_on_block:
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


def _status_for_non_completion(adapter_result: ExecutionAdapterResult) -> RuntimeLoopStatus:
    if adapter_result.outcome.value == "ESCALATED":
        return RuntimeLoopStatus.ESCALATED
    if adapter_result.outcome.value == "FAILED":
        return RuntimeLoopStatus.FAILED
    return RuntimeLoopStatus.BLOCKED
