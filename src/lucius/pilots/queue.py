from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, PersistentWorkflowState, QueueWorkItemState
from lucius.persistence.orm import PersistentWorkflowORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.schemas import (
    GlobalQueueItem,
    GlobalQueueSelection,
    GlobalQueueStatus,
    GlobalWorkflowExclusion,
    QueueBlockCheckpoint,
    QueueSelection,
    QueueStatus,
)


BLOCKED_STATES = {
    QueueWorkItemState.WAITING_HUMAN,
    QueueWorkItemState.WAITING_EXTERNAL,
    QueueWorkItemState.BLOCKED_DEPENDENCY,
    QueueWorkItemState.RETRY_LATER,
}
TERMINAL_STATES = {QueueWorkItemState.COMPLETED, QueueWorkItemState.FAILED}
ELIGIBLE_STATES = {QueueWorkItemState.READY, QueueWorkItemState.READY_TO_RESUME}
PRIORITY_RANK = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}
VALID_PRIORITIES = set(PRIORITY_RANK)
STATE_CLASS_RANK = {
    QueueWorkItemState.READY_TO_RESUME: 0,
    QueueWorkItemState.READY: 1,
}
EXECUTION_ELIGIBLE_WORKFLOW_STATES = {
    PersistentWorkflowState.PLAN_READY,
    PersistentWorkflowState.IMPLEMENTING,
    PersistentWorkflowState.VERIFYING,
    PersistentWorkflowState.CHECKPOINT_REVIEW_REQUIRED,
    PersistentWorkflowState.RESUME_VALIDATION,
    PersistentWorkflowState.BLOCKED,
    PersistentWorkflowState.APPROVED_TO_CONTINUE,
}
GLOBAL_SCHEDULABLE_WORKFLOW_STATES = EXECUTION_ELIGIBLE_WORKFLOW_STATES


class QueueStateError(ValueError):
    """Raised when durable queue state cannot be safely advanced."""


class NonBlockingQueueService:
    """Deterministic scheduler over a workflow's persisted task_backlog."""

    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def inspect(self, workflow_id: str) -> QueueStatus:
        workflow = self._workflow(workflow_id)
        parsed = self._execution_items(workflow)
        executable_items = [item for item in parsed if item.schedulable]
        selection = self.select_next(workflow_id)
        return QueueStatus(
            workflow_id=workflow.id,
            running=[
                deepcopy(item.item)
                for item in executable_items
                if item.state == QueueWorkItemState.RUNNING
            ],
            blocked=[deepcopy(item.item) for item in executable_items if item.state in BLOCKED_STATES],
            ready=[
                deepcopy(item.item)
                for item in executable_items
                if item.state == QueueWorkItemState.READY and self._global_dependencies_complete(item, executable_items)
            ],
            ready_to_resume=[
                deepcopy(item.item)
                for item in executable_items
                if item.state == QueueWorkItemState.READY_TO_RESUME
                and self._global_dependencies_complete(item, executable_items)
            ],
            dependency_blocked=[
                deepcopy(item.item)
                for item in executable_items
                if not self._global_dependencies_complete(item, executable_items)
            ],
            completed=[deepcopy(item.item) for item in executable_items if item.state == QueueWorkItemState.COMPLETED],
            failed=[deepcopy(item.item) for item in executable_items if item.state == QueueWorkItemState.FAILED],
            excluded=[_excluded_payload(item) for item in parsed if not item.schedulable],
            next_selection=selection,
        )

    def select_next(self, workflow_id: str) -> QueueSelection:
        workflow = self._workflow(workflow_id)
        parsed = self._execution_items(workflow)
        stateful_items = [item for item in parsed if item.queue_state_present]
        executable_items = [item for item in parsed if item.schedulable]
        self._validate_scoped_running_identities(stateful_items)
        running = [item for item in stateful_items if item.state == QueueWorkItemState.RUNNING]
        if running:
            return QueueSelection(
                reason="RUNNING_ITEM_ACTIVE_NO_PREEMPTION",
                running_item_ids=[item.item_id for item in running],
            )
        eligible = [item for item in executable_items if self._global_is_eligible(item, executable_items)]
        blocked = [
            item
            for item in executable_items
            if item.state in BLOCKED_STATES or not self._global_dependencies_complete(item, executable_items)
        ]
        if not eligible:
            return QueueSelection(
                reason="NO_ELIGIBLE_WORK",
                blocked_item_ids=[item.item_id for item in blocked],
            )
        selected = sorted(eligible, key=_scoped_selection_key)[0]
        return QueueSelection(
            selected_item_id=selected.item_id,
            reason=_scoped_selection_reason(selected),
            eligible_item_ids=[item.item_id for item in sorted(eligible, key=_scoped_selection_key)],
            blocked_item_ids=[item.item_id for item in blocked],
        )

    def start_next(self, workflow_id: str, *, actor: Actor = Actor.LUCIUS) -> QueueSelection:
        workflow = self._workflow(workflow_id)
        lifecycle_reason = workflow_execution_exclusion_reason(workflow)
        if lifecycle_reason is not None:
            raise QueueStateError(f"Workflow is not executable: {lifecycle_reason}")
        selection = self.select_next(workflow.id)
        if selection.selected_item_id is None:
            excluded = [item for item in self._execution_items(workflow) if not item.schedulable]
            if excluded:
                raise QueueStateError(f"Queue work item is not executable: {excluded[0].exclusion_reason}")
            return selection
        items = self._items(workflow)
        item = self._item(items, selection.selected_item_id)
        selected = next(
            (candidate for candidate in self._execution_items(workflow) if candidate.item_id == selection.selected_item_id),
            None,
        )
        if selected is None:
            raise QueueStateError("Selected queue work item no longer exists.")
        if not selected.schedulable:
            raise QueueStateError(f"Selected queue work item is not schedulable: {selected.exclusion_reason}")
        if selected.state not in ELIGIBLE_STATES:
            raise QueueStateError(f"Selected queue work item is not executable: {selected.state_label}")
        self._set_state(item, QueueWorkItemState.RUNNING)
        item["started_at"] = _now_iso()
        item["run_generation"] = int(item.get("run_generation", 0)) + 1
        workflow.active_task_id = _item_id(item)
        workflow.task_backlog = items
        workflow.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="QUEUE_WORK_ITEM_STARTED",
            actor=actor.value,
            project_id=workflow.project_id,
            repository_id=workflow.repository_id,
            task_id=workflow.task_id,
            action="start_queue_work_item",
            result=_item_id(item),
            metadata={"workflow_id": workflow.id, "selection_reason": selection.reason},
        )
        return selection

    def block_running_item(
        self,
        workflow_id: str,
        item_id: str,
        *,
        blocking_state: QueueWorkItemState,
        blocking_reason: str,
        blocker_category: str,
        resume_condition: str,
        work_completed: list[str] | None = None,
        implementation_head: str | None = None,
        pending_decision_or_dependency: str | None = None,
        known_risks: list[str] | None = None,
        relevant_artifacts: list[str] | None = None,
        repair_counters: dict[str, Any] | None = None,
        approval_requirements: list[str] | None = None,
        next_safe_action: str = "Select another eligible work item.",
        stale_state_validation_requirements: list[str] | None = None,
        actor: Actor = Actor.LUCIUS,
    ) -> QueueBlockCheckpoint:
        if blocking_state not in BLOCKED_STATES:
            raise QueueStateError(f"Blocking state required, got {blocking_state.value}")
        workflow = self._workflow(workflow_id)
        items = self._items(workflow)
        item = self._item(items, item_id)
        prior_state = _state(item)
        if prior_state != QueueWorkItemState.RUNNING:
            raise QueueStateError(f"Only RUNNING work can be blocked, got {prior_state.value}")
        checkpoint = QueueBlockCheckpoint(
            id=next_id(self.session, "queue_checkpoint"),
            workflow_id=workflow.id,
            item_id=item_id,
            prior_state=prior_state,
            blocking_state=blocking_state,
            blocking_reason=blocking_reason,
            blocker_category=blocker_category,
            work_completed=work_completed or list(item.get("completed_substeps", [])),
            implementation_head=implementation_head,
            pending_decision_or_dependency=pending_decision_or_dependency,
            resume_condition=resume_condition,
            known_risks=known_risks or [],
            relevant_artifacts=relevant_artifacts or [],
            repair_counters=repair_counters or workflow.repair_counters or {},
            approval_requirements=approval_requirements or [],
            next_safe_action=next_safe_action,
            stale_state_validation_requirements=stale_state_validation_requirements or [
                "current_block_checkpoint_id matches",
                "item_state matches blocking_state",
                "item_version matches checkpoint item_version",
            ],
            created_at=utc_now(),
        )
        item.setdefault("block_checkpoints", []).append(
            checkpoint.model_dump(mode="json") | {"item_version": int(item.get("version", 0))}
        )
        item["current_block_checkpoint_id"] = checkpoint.id
        item["blocked_at"] = _now_iso()
        item["blocking_reason"] = blocking_reason
        item["blocker_category"] = blocker_category
        item["resume_condition"] = resume_condition
        item["work_completed"] = checkpoint.work_completed
        item["completed_substeps"] = _merge_substeps(
            list(item.get("completed_substeps", [])),
            checkpoint.work_completed,
        )
        self._set_state(item, blocking_state)
        workflow.active_task_id = None
        workflow.task_backlog = items
        workflow.checkpoint_history = [*workflow.checkpoint_history, checkpoint.id]
        workflow.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="QUEUE_WORK_ITEM_BLOCKED",
            actor=actor.value,
            project_id=workflow.project_id,
            repository_id=workflow.repository_id,
            task_id=workflow.task_id,
            action="block_queue_work_item",
            result=blocking_state.value,
            metadata={"workflow_id": workflow.id, "item_id": item_id, "queue_checkpoint_id": checkpoint.id},
        )
        return checkpoint

    def resolve_blocker(
        self,
        workflow_id: str,
        item_id: str,
        *,
        checkpoint_id: str,
        expected_item_version: int,
        resolution_event: str,
        actor: Actor = Actor.LUCIUS,
    ) -> dict[str, Any]:
        workflow = self._workflow(workflow_id)
        items = self._items(workflow)
        item = self._item(items, item_id)
        if item.get("current_block_checkpoint_id") != checkpoint_id:
            raise QueueStateError("Stale checkpoint cannot resolve newer queue state.")
        if int(item.get("version", 0)) != expected_item_version:
            raise QueueStateError("Stale item version cannot resolve queue state.")
        if _state(item) not in BLOCKED_STATES:
            raise QueueStateError(f"Blocked item required, got {_state(item).value}")
        item["resolution_event"] = resolution_event
        item["resolved_at"] = _now_iso()
        self._set_state(item, QueueWorkItemState.READY_TO_RESUME)
        workflow.task_backlog = items
        workflow.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="QUEUE_WORK_ITEM_READY_TO_RESUME",
            actor=actor.value,
            project_id=workflow.project_id,
            repository_id=workflow.repository_id,
            task_id=workflow.task_id,
            action="resolve_queue_blocker",
            result=QueueWorkItemState.READY_TO_RESUME.value,
            metadata={"workflow_id": workflow.id, "item_id": item_id, "queue_checkpoint_id": checkpoint_id},
        )
        return deepcopy(item)

    def complete_item(
        self,
        workflow_id: str,
        item_id: str,
        *,
        completed_substeps: list[str] | None = None,
        actor: Actor = Actor.LUCIUS,
    ) -> dict[str, Any]:
        workflow = self._workflow(workflow_id)
        items = self._items(workflow)
        item = self._item(items, item_id)
        if _state(item) not in {QueueWorkItemState.RUNNING, QueueWorkItemState.READY_TO_RESUME}:
            raise QueueStateError(f"Cannot complete item from state {_state(item).value}")
        existing = list(item.get("completed_substeps", []))
        for substep in completed_substeps or []:
            if substep not in existing:
                existing.append(substep)
        item["completed_substeps"] = existing
        item["completed_at"] = _now_iso()
        self._set_state(item, QueueWorkItemState.COMPLETED)
        workflow.active_task_id = None if workflow.active_task_id == item_id else workflow.active_task_id
        workflow.completed_task_ids = sorted({_item_id(done) for done in items if _state(done) == QueueWorkItemState.COMPLETED})
        workflow.pending_task_ids = sorted(
            {
                _item_id(pending)
                for pending in items
                if _state(pending) not in TERMINAL_STATES
            }
        )
        workflow.task_backlog = items
        workflow.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="QUEUE_WORK_ITEM_COMPLETED",
            actor=actor.value,
            project_id=workflow.project_id,
            repository_id=workflow.repository_id,
            task_id=workflow.task_id,
            action="complete_queue_work_item",
            result=item_id,
            metadata={"workflow_id": workflow.id, "completed_substeps": existing},
        )
        return deepcopy(item)

    def validate_fresh_reconstruction(self, workflow_id: str) -> QueueStatus:
        return self.inspect(workflow_id)

    def inspect_global(self, workflow_ids: list[str] | None = None) -> GlobalQueueStatus:
        workflows = self._global_workflows(workflow_ids)
        schedulable_workflows, lifecycle_excluded = self._partition_global_workflows(workflows)
        workflow_ids = [workflow.id for workflow in schedulable_workflows]
        items = self._global_items(schedulable_workflows)
        legacy_unschedulable = [item for item in items if not item.queue_state_present]
        executable_items = [item for item in items if item.schedulable]
        selection = self.select_global_next([workflow.id for workflow in workflows])
        return GlobalQueueStatus(
            observed_workflow_ids=[workflow.id for workflow in workflows],
            workflow_ids=workflow_ids,
            active_project_ids=sorted({item.project_id for item in executable_items if item.project_id}),
            running=[item for item in executable_items if item.state == QueueWorkItemState.RUNNING],
            blocked=[item for item in executable_items if item.state in BLOCKED_STATES],
            ready=[
                item
                for item in executable_items
                if item.state == QueueWorkItemState.READY and self._global_dependencies_complete(item, executable_items)
            ],
            ready_to_resume=[
                item
                for item in executable_items
                if item.state == QueueWorkItemState.READY_TO_RESUME and self._global_dependencies_complete(item, executable_items)
            ],
            dependency_blocked=[
                item for item in executable_items if not self._global_dependencies_complete(item, executable_items)
            ],
            completed=[item for item in executable_items if item.state == QueueWorkItemState.COMPLETED],
            failed=[item for item in executable_items if item.state == QueueWorkItemState.FAILED],
            legacy_unschedulable=legacy_unschedulable,
            lifecycle_excluded=lifecycle_excluded,
            next_selection=selection,
        )

    def select_global_next(self, workflow_ids: list[str] | None = None) -> GlobalQueueSelection:
        workflows = self._global_workflows(workflow_ids)
        schedulable_workflows, lifecycle_excluded = self._partition_global_workflows(workflows)
        items = self._global_items(schedulable_workflows)
        legacy_unschedulable = [item for item in items if not item.queue_state_present]
        stateful_items = [item for item in items if item.queue_state_present]
        executable_items = [item for item in items if item.schedulable]
        excluded_items = [
            item
            for exclusion in lifecycle_excluded
            for item in exclusion.items
        ] + [item for item in items if not item.schedulable]
        self._validate_global_running_identities(stateful_items)
        running = [item for item in stateful_items if item.state == QueueWorkItemState.RUNNING]
        if running:
            return GlobalQueueSelection(
                reason="GLOBAL_RUNNING_ITEM_ACTIVE_NO_PREEMPTION",
                running_items=running,
                excluded_items=excluded_items,
            )
        eligible = [item for item in executable_items if self._global_is_eligible(item, executable_items)]
        blocked = [
            item
            for item in executable_items
            if item.state in BLOCKED_STATES or not self._global_dependencies_complete(item, executable_items)
        ]
        if not eligible:
            return GlobalQueueSelection(
                reason="NO_GLOBAL_ELIGIBLE_WORK",
                blocked_items=blocked,
                excluded_items=excluded_items,
            )
        ordered = sorted(eligible, key=_global_selection_key)
        selected = ordered[0]
        return GlobalQueueSelection(
            selected_project_id=selected.project_id,
            selected_workflow_id=selected.workflow_id,
            selected_item_id=selected.item_id,
            selected_item_version=selected.version,
            reason=_global_selection_reason(selected),
            eligible_items=ordered,
            blocked_items=blocked,
            excluded_items=excluded_items,
        )

    def start_global_next(
        self,
        workflow_ids: list[str] | None = None,
        *,
        actor: Actor = Actor.LUCIUS,
    ) -> GlobalQueueSelection:
        selection = self.select_global_next(workflow_ids)
        return self.start_selected_global_item(selection, workflow_ids=workflow_ids, actor=actor)

    def start_selected_global_item(
        self,
        selection: GlobalQueueSelection,
        *,
        workflow_ids: list[str] | None = None,
        actor: Actor = Actor.LUCIUS,
    ) -> GlobalQueueSelection:
        if selection.selected_workflow_id is None:
            return selection
        return self._start_global_selection(selection, workflow_ids=workflow_ids, actor=actor)

    def validate_fresh_global_reconstruction(self, workflow_ids: list[str] | None = None) -> GlobalQueueStatus:
        return self.inspect_global(workflow_ids)

    def _workflow(self, workflow_id: str) -> PersistentWorkflowORM:
        row = self.session.get(PersistentWorkflowORM, workflow_id)
        if row is None:
            raise QueueStateError(f"Unknown persistent workflow: {workflow_id}")
        return row

    def _items(self, workflow: PersistentWorkflowORM) -> list[dict[str, Any]]:
        return [dict(item) for item in workflow.task_backlog]

    def _item(self, items: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
        for item in items:
            if _item_id(item) == item_id:
                return item
        raise QueueStateError(f"Unknown queue work item: {item_id}")

    def _dependencies_complete(self, item: dict[str, Any], items: list[dict[str, Any]]) -> bool:
        completed = {_item_id(candidate) for candidate in items if _state(candidate) == QueueWorkItemState.COMPLETED}
        return all(dependency in completed for dependency in item.get("dependencies", []))

    def _is_eligible(self, item: dict[str, Any], items: list[dict[str, Any]]) -> bool:
        return _state(item) in ELIGIBLE_STATES and self._dependencies_complete(item, items)

    def _validate_unique_items(self, items: list[dict[str, Any]]) -> None:
        _validate_unique_item_ids(items)
        running_logical = {
            item.get("logical_task_id", _item_id(item))
            for item in items
            if _state(item) == QueueWorkItemState.RUNNING
        }
        if len(running_logical) < len([item for item in items if _state(item) == QueueWorkItemState.RUNNING]):
            raise QueueStateError("Duplicate RUNNING logical task detected.")

    def _execution_items(self, workflow: PersistentWorkflowORM) -> list[GlobalQueueItem]:
        return self._global_items([workflow])

    def _validate_scoped_running_identities(self, items: list[GlobalQueueItem]) -> None:
        running = [item for item in items if item.state == QueueWorkItemState.RUNNING]
        logical_ids = [item.logical_task_id for item in running]
        if len(logical_ids) != len(set(logical_ids)):
            raise QueueStateError("Duplicate RUNNING logical task detected.")

    def _set_state(self, item: dict[str, Any], state: QueueWorkItemState) -> None:
        item["state"] = state.value
        item["version"] = int(item.get("version", 0)) + 1
        item["updated_at"] = _now_iso()

    def _global_workflows(self, workflow_ids: list[str] | None) -> list[PersistentWorkflowORM]:
        if workflow_ids:
            workflows = [self._workflow(workflow_id) for workflow_id in workflow_ids]
        else:
            workflows = list(self.session.query(PersistentWorkflowORM).order_by(PersistentWorkflowORM.id).all())
        return [workflow for workflow in workflows if workflow.task_backlog]

    def _partition_global_workflows(
        self, workflows: list[PersistentWorkflowORM]
    ) -> tuple[list[PersistentWorkflowORM], list[GlobalWorkflowExclusion]]:
        schedulable: list[PersistentWorkflowORM] = []
        excluded: list[GlobalWorkflowExclusion] = []
        for workflow in workflows:
            reason = workflow_execution_exclusion_reason(workflow)
            if reason is None:
                schedulable.append(workflow)
                continue
            excluded.append(
                GlobalWorkflowExclusion(
                    project_id=workflow.project_id,
                    workflow_id=workflow.id,
                    workflow_state=PersistentWorkflowState(workflow.workflow_state),
                    reason=reason,
                    item_count=len(workflow.task_backlog),
                    items=self._global_items([workflow], exclusion_reason=reason),
                )
            )
        return schedulable, excluded

    def _global_items(
        self, workflows: list[PersistentWorkflowORM], *, exclusion_reason: str | None = None
    ) -> list[GlobalQueueItem]:
        global_items: list[GlobalQueueItem] = []
        for workflow in workflows:
            items = self._items(workflow)
            _validate_unique_item_ids(items)
            for item in items:
                item_id = _item_id(item)
                state_result = _parse_global_state(item)
                priority_result = _parse_global_priority(item)
                item_exclusion_reason = (
                    exclusion_reason
                    or state_result["exclusion_reason"]
                    or priority_result["exclusion_reason"]
                )
                global_items.append(
                    GlobalQueueItem(
                        project_id=workflow.project_id,
                        workflow_id=workflow.id,
                        repository_id=workflow.repository_id,
                        workflow_task_id=workflow.task_id,
                        item_id=item_id,
                        logical_task_id=str(item.get("logical_task_id") or item.get("task_id") or item_id),
                        title=item.get("title"),
                        state=state_result["state"],
                        state_label=state_result["state_label"],
                        raw_state=state_result["raw_state"],
                        priority=priority_result["priority"],
                        raw_priority=priority_result["raw_priority"],
                        created_order=int(item.get("created_order", 0)),
                        version=int(item.get("version", 0)),
                        dependencies=[str(dependency) for dependency in item.get("dependencies", [])],
                        current_block_checkpoint_id=item.get("current_block_checkpoint_id"),
                        queue_state_present=state_result["state"] is not None,
                        priority_valid=priority_result["exclusion_reason"] is None,
                        schedulable=state_result["state"] is not None and item_exclusion_reason is None,
                        exclusion_reason=item_exclusion_reason,
                        item=deepcopy(item),
                    )
                )
        return global_items

    def _global_dependencies_complete(self, item: GlobalQueueItem, items: list[GlobalQueueItem]) -> bool:
        completed = {
            candidate.item_id
            for candidate in items
            if candidate.workflow_id == item.workflow_id and candidate.state == QueueWorkItemState.COMPLETED
        }
        return all(dependency in completed for dependency in item.dependencies)

    def _global_is_eligible(self, item: GlobalQueueItem, items: list[GlobalQueueItem]) -> bool:
        return item.state in ELIGIBLE_STATES and self._global_dependencies_complete(item, items)

    def _validate_global_running_identities(self, items: list[GlobalQueueItem]) -> None:
        running = [item for item in items if item.state == QueueWorkItemState.RUNNING]
        scoped_identities = [(item.project_id, item.logical_task_id) for item in running]
        if len(scoped_identities) != len(set(scoped_identities)):
            raise QueueStateError("Duplicate global RUNNING logical work item detected.")

    def _start_global_selection(
        self,
        selection: GlobalQueueSelection,
        *,
        workflow_ids: list[str] | None,
        actor: Actor,
    ) -> GlobalQueueSelection:
        workflow = self._workflow(selection.selected_workflow_id or "")
        if workflow_ids and workflow.id not in set(workflow_ids):
            raise QueueStateError("Global selection workflow is outside requested scope.")
        if workflow_execution_exclusion_reason(workflow) is not None:
            raise QueueStateError("Stale global selection: workflow is no longer schedulable.")
        current_selection = self.select_global_next(workflow_ids)
        if (
            current_selection.selected_project_id != selection.selected_project_id
            or current_selection.selected_workflow_id != selection.selected_workflow_id
            or current_selection.selected_item_id != selection.selected_item_id
            or current_selection.selected_item_version != selection.selected_item_version
        ):
            raise QueueStateError("Stale global selection: caller must re-run global selection.")
        items = self._items(workflow)
        parsed = self._global_items([workflow])
        self._validate_global_running_identities([item for item in parsed if item.queue_state_present])
        running = [item for item in parsed if item.state == QueueWorkItemState.RUNNING]
        if running:
            raise QueueStateError("Stale global selection: running item active, no preemption allowed.")
        item = self._item(items, selection.selected_item_id or "")
        selected = next((candidate for candidate in parsed if candidate.item_id == selection.selected_item_id), None)
        if selected is None:
            raise QueueStateError("Stale global selection: selected item no longer exists.")
        if not selected.schedulable:
            raise QueueStateError(f"Selected global item is not schedulable: {selected.exclusion_reason}")
        if selected.state not in ELIGIBLE_STATES:
            raise QueueStateError(f"Selected global item is no longer eligible: {selected.state_label}")
        if not self._global_dependencies_complete(selected, parsed):
            raise QueueStateError("Selected global item dependencies are no longer complete.")
        if _global_selection_key(selected) != _global_selection_key(current_selection.eligible_items[0]):
            raise QueueStateError("Stale global selection: ordering changed, caller must re-run global selection.")

        previous_state = selected.state.value
        previous_version = int(item.get("version", 0))
        self._set_state(item, QueueWorkItemState.RUNNING)
        item["started_at"] = _now_iso()
        item["run_generation"] = int(item.get("run_generation", 0)) + 1
        workflow.active_task_id = _item_id(item)
        workflow.task_backlog = items
        workflow.updated_at = utc_now()
        self.session.flush()
        new_version = int(item.get("version", 0))
        if (
            selection.selected_project_id != workflow.project_id
            or selection.selected_workflow_id != workflow.id
            or selection.selected_item_id != _item_id(item)
        ):
            raise QueueStateError("Global selection identity did not match mutation identity.")
        self.audit.record(
            event_type="GLOBAL_QUEUE_WORK_ITEM_STARTED",
            actor=actor.value,
            project_id=workflow.project_id,
            repository_id=workflow.repository_id,
            task_id=workflow.task_id,
            action="start_global_queue_work_item",
            result=_item_id(item),
            metadata={
                "workflow_id": workflow.id,
                "selection_reason": selection.reason,
                "selected_item_version": selection.selected_item_version,
                "started_previous_version": previous_version,
                "started_new_version": new_version,
                "mutation_identity_matches_selection": True,
            },
        )
        return selection.model_copy(
            update={
                "started_project_id": workflow.project_id,
                "started_workflow_id": workflow.id,
                "started_item_id": _item_id(item),
                "started_previous_state": previous_state,
                "started_new_state": QueueWorkItemState.RUNNING.value,
                "started_previous_version": previous_version,
                "started_new_version": new_version,
                "mutation_identity_matches_selection": True,
            }
        )


def _item_id(item: dict[str, Any]) -> str:
    return str(item.get("item_id") or item.get("task_id"))


def _validate_unique_item_ids(items: list[dict[str, Any]]) -> None:
    item_ids = [_item_id(item) for item in items]
    if len(item_ids) != len(set(item_ids)):
        raise QueueStateError("Duplicate queue work item id detected.")


def _state(item: dict[str, Any]) -> QueueWorkItemState:
    return QueueWorkItemState(str(item.get("state", QueueWorkItemState.READY.value)))


def _explicit_state(item: dict[str, Any]) -> QueueWorkItemState | None:
    if "state" not in item or item.get("state") is None:
        return None
    return QueueWorkItemState(str(item["state"]))


def _execution_state(item: dict[str, Any]) -> QueueWorkItemState:
    if "state" not in item:
        raise QueueStateError("Queue work item is not executable: missing queue state.")
    if item.get("state") is None:
        raise QueueStateError("Queue work item is not executable: null queue state.")
    try:
        return QueueWorkItemState(str(item["state"]))
    except ValueError as error:
        raise QueueStateError(f"Queue work item is not executable: unknown queue state {item['state']!r}.") from error


def _execution_priority(item: dict[str, Any]) -> str:
    if "priority" not in item or item.get("priority") is None:
        return "NORMAL"
    raw_priority = str(item["priority"])
    if raw_priority not in VALID_PRIORITIES:
        raise QueueStateError(f"Queue work item is not executable: unknown priority {raw_priority!r}.")
    return raw_priority


def _parse_global_state(item: dict[str, Any]) -> dict[str, Any]:
    if "state" not in item:
        return {
            "state": None,
            "state_label": "LEGACY_UNSCHEDULABLE",
            "raw_state": None,
            "exclusion_reason": "LEGACY_UNSCHEDULABLE:MISSING_STATE",
        }
    if item.get("state") is None:
        return {
            "state": None,
            "state_label": "MALFORMED_UNSCHEDULABLE",
            "raw_state": None,
            "exclusion_reason": "MALFORMED_UNSCHEDULABLE:NULL_STATE",
        }
    raw_state = str(item["state"])
    try:
        state = QueueWorkItemState(raw_state)
    except ValueError:
        return {
            "state": None,
            "state_label": "MALFORMED_UNSCHEDULABLE",
            "raw_state": raw_state,
            "exclusion_reason": f"MALFORMED_UNSCHEDULABLE:UNKNOWN_STATE:{raw_state}",
        }
    return {
        "state": state,
        "state_label": state.value,
        "raw_state": raw_state,
        "exclusion_reason": None,
    }


def _parse_global_priority(item: dict[str, Any]) -> dict[str, Any]:
    if "priority" not in item or item.get("priority") is None:
        return {
            "priority": "NORMAL",
            "raw_priority": None,
            "exclusion_reason": None,
        }
    raw_priority = str(item["priority"])
    if raw_priority not in VALID_PRIORITIES:
        return {
            "priority": raw_priority,
            "raw_priority": raw_priority,
            "exclusion_reason": f"MALFORMED_UNSCHEDULABLE:UNKNOWN_PRIORITY:{raw_priority}",
        }
    return {
        "priority": raw_priority,
        "raw_priority": raw_priority,
        "exclusion_reason": None,
    }


def workflow_is_execution_eligible(workflow: PersistentWorkflowORM) -> bool:
    return workflow_execution_exclusion_reason(workflow) is None


def workflow_execution_exclusion_reason(workflow: PersistentWorkflowORM) -> str | None:
    state = PersistentWorkflowState(workflow.workflow_state)
    if state in EXECUTION_ELIGIBLE_WORKFLOW_STATES:
        return None
    return f"WORKFLOW_LIFECYCLE_INELIGIBLE:{state.value}"


def _global_workflow_exclusion_reason(workflow: PersistentWorkflowORM) -> str | None:
    return workflow_execution_exclusion_reason(workflow)


def _selection_key(item: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        PRIORITY_RANK.get(str(item.get("priority", "NORMAL")).upper(), PRIORITY_RANK["NORMAL"]),
        STATE_CLASS_RANK.get(_state(item), 9),
        int(item.get("created_order", 0)),
        _item_id(item),
    )


def _selection_reason(item: dict[str, Any]) -> str:
    return (
        f"priority={str(item.get('priority', 'NORMAL')).upper()} "
        f"state={_state(item).value} order={int(item.get('created_order', 0))}"
    )


def _scoped_selection_key(item: GlobalQueueItem) -> tuple[int, int, int, str]:
    return (
        PRIORITY_RANK.get(item.priority.upper(), PRIORITY_RANK["NORMAL"]),
        STATE_CLASS_RANK.get(item.state, 9),
        item.created_order,
        item.item_id,
    )


def _scoped_selection_reason(item: GlobalQueueItem) -> str:
    return f"priority={item.priority.upper()} state={item.state.value} order={item.created_order}"


def _global_selection_key(item: GlobalQueueItem) -> tuple[int, int, int, str, str, str]:
    return (
        PRIORITY_RANK.get(item.priority.upper(), PRIORITY_RANK["NORMAL"]),
        STATE_CLASS_RANK.get(item.state, 9),
        item.created_order,
        item.project_id or "",
        item.workflow_id,
        item.item_id,
    )


def _global_selection_reason(item: GlobalQueueItem) -> str:
    return (
        f"project={item.project_id} workflow={item.workflow_id} "
        f"priority={item.priority.upper()} state={item.state.value} order={item.created_order}"
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _excluded_payload(item: GlobalQueueItem) -> dict[str, Any]:
    payload = deepcopy(item.item)
    payload["state_label"] = item.state_label
    payload["raw_state"] = item.raw_state
    payload["raw_priority"] = item.raw_priority
    payload["exclusion_reason"] = item.exclusion_reason
    payload["queue_state_present"] = item.queue_state_present
    payload["priority_valid"] = item.priority_valid
    return payload


def _merge_substeps(existing: list[str], additional: list[str]) -> list[str]:
    merged = list(existing)
    for substep in additional:
        if substep not in merged:
            merged.append(substep)
    return merged
