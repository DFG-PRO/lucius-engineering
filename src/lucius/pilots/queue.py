from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, QueueWorkItemState
from lucius.persistence.orm import PersistentWorkflowORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.schemas import QueueBlockCheckpoint, QueueSelection, QueueStatus


BLOCKED_STATES = {
    QueueWorkItemState.WAITING_HUMAN,
    QueueWorkItemState.WAITING_EXTERNAL,
    QueueWorkItemState.BLOCKED_DEPENDENCY,
    QueueWorkItemState.RETRY_LATER,
}
TERMINAL_STATES = {QueueWorkItemState.COMPLETED, QueueWorkItemState.FAILED}
ELIGIBLE_STATES = {QueueWorkItemState.READY, QueueWorkItemState.READY_TO_RESUME}
PRIORITY_RANK = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}
STATE_CLASS_RANK = {
    QueueWorkItemState.READY_TO_RESUME: 0,
    QueueWorkItemState.READY: 1,
}


class QueueStateError(ValueError):
    """Raised when durable queue state cannot be safely advanced."""


class NonBlockingQueueService:
    """Deterministic scheduler over a workflow's persisted task_backlog."""

    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def inspect(self, workflow_id: str) -> QueueStatus:
        workflow = self._workflow(workflow_id)
        items = self._items(workflow)
        selection = self.select_next(workflow_id)
        return QueueStatus(
            workflow_id=workflow.id,
            running=[deepcopy(item) for item in items if _state(item) == QueueWorkItemState.RUNNING],
            blocked=[deepcopy(item) for item in items if _state(item) in BLOCKED_STATES],
            ready=[deepcopy(item) for item in items if _state(item) == QueueWorkItemState.READY and self._dependencies_complete(item, items)],
            ready_to_resume=[
                deepcopy(item)
                for item in items
                if _state(item) == QueueWorkItemState.READY_TO_RESUME and self._dependencies_complete(item, items)
            ],
            dependency_blocked=[deepcopy(item) for item in items if not self._dependencies_complete(item, items)],
            completed=[deepcopy(item) for item in items if _state(item) == QueueWorkItemState.COMPLETED],
            failed=[deepcopy(item) for item in items if _state(item) == QueueWorkItemState.FAILED],
            next_selection=selection,
        )

    def select_next(self, workflow_id: str) -> QueueSelection:
        workflow = self._workflow(workflow_id)
        items = self._items(workflow)
        self._validate_unique_items(items)
        running = [item for item in items if _state(item) == QueueWorkItemState.RUNNING]
        if running:
            return QueueSelection(
                reason="RUNNING_ITEM_ACTIVE_NO_PREEMPTION",
                running_item_ids=[_item_id(item) for item in running],
            )
        eligible = [item for item in items if self._is_eligible(item, items)]
        blocked = [item for item in items if _state(item) in BLOCKED_STATES or not self._dependencies_complete(item, items)]
        if not eligible:
            return QueueSelection(
                reason="NO_ELIGIBLE_WORK",
                blocked_item_ids=[_item_id(item) for item in blocked],
            )
        selected = sorted(eligible, key=_selection_key)[0]
        return QueueSelection(
            selected_item_id=_item_id(selected),
            reason=_selection_reason(selected),
            eligible_item_ids=[_item_id(item) for item in sorted(eligible, key=_selection_key)],
            blocked_item_ids=[_item_id(item) for item in blocked],
        )

    def start_next(self, workflow_id: str, *, actor: Actor = Actor.LUCIUS) -> QueueSelection:
        workflow = self._workflow(workflow_id)
        selection = self.select_next(workflow.id)
        if selection.selected_item_id is None:
            return selection
        items = self._items(workflow)
        item = self._item(items, selection.selected_item_id)
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
        item_ids = [_item_id(item) for item in items]
        if len(item_ids) != len(set(item_ids)):
            raise QueueStateError("Duplicate queue work item id detected.")
        running_logical = {
            item.get("logical_task_id", _item_id(item))
            for item in items
            if _state(item) == QueueWorkItemState.RUNNING
        }
        if len(running_logical) < len([item for item in items if _state(item) == QueueWorkItemState.RUNNING]):
            raise QueueStateError("Duplicate RUNNING logical task detected.")

    def _set_state(self, item: dict[str, Any], state: QueueWorkItemState) -> None:
        item["state"] = state.value
        item["version"] = int(item.get("version", 0)) + 1
        item["updated_at"] = _now_iso()


def _item_id(item: dict[str, Any]) -> str:
    return str(item.get("item_id") or item.get("task_id"))


def _state(item: dict[str, Any]) -> QueueWorkItemState:
    return QueueWorkItemState(str(item.get("state", QueueWorkItemState.READY.value)))


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merge_substeps(existing: list[str], additional: list[str]) -> list[str]:
    merged = list(existing)
    for substep in additional:
        if substep not in merged:
            merged.append(substep)
    return merged
