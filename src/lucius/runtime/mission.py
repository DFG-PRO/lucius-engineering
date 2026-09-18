from __future__ import annotations

from datetime import datetime, timezone
import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, DurableWaitClass, MissionStatus, QueueWorkItemState
from lucius.persistence.json_fields import set_json_field
from lucius.persistence.orm import DurableMissionORM, DurableWaitORM, PersistentWorkflowORM, utc_now
from lucius.runtime.schemas import DurableMissionRecord, DurableWaitRecord

logger = logging.getLogger(__name__)


class DurableMissionSupervisor:
    """Manages durable responsibility for missions, tracking wait states, process restart recovery, and wake rules."""

    def __init__(self, session: Session, actor: Actor = Actor.LUCIUS):
        self.session = session
        self.actor = actor
        self.audit = AuditService(session)

    def _get_all_items(self) -> list[dict[str, Any]]:
        items = []
        workflows = self.session.query(PersistentWorkflowORM).all()
        for wf in workflows:
            for item in wf.task_backlog or []:
                if isinstance(item, dict):
                    items.append(item)
        return items

    def _update_item_state(self, item_id: str, target_state: str) -> dict[str, Any] | None:
        workflows = self.session.query(PersistentWorkflowORM).all()
        for wf in workflows:
            backlog = list(wf.task_backlog or [])
            modified = False
            target_item = None
            for item in backlog:
                if isinstance(item, dict) and (item.get("id") == item_id or item.get("item_id") == item_id or item.get("logical_task_id") == item_id):
                    item["state"] = target_state
                    target_item = item
                    modified = True
            if modified:
                set_json_field(wf, "task_backlog", backlog)
                return target_item
        return None

    def create_mission(
        self,
        canonical_sha: str,
        metadata: dict[str, Any] | None = None,
        mission_id: str | None = None,
        attempt_id: str | None = None,
    ) -> DurableMissionRecord:
        if mission_id is None:
            mission_id = f"mis_{uuid.uuid4().hex[:12]}"
        if attempt_id is None:
            attempt_id = f"att_{uuid.uuid4().hex[:12]}"

        meta = dict(metadata or {})
        meta["current_attempt_id"] = attempt_id
        meta["attempts"] = meta.get("attempts", []) + [{"attempt_id": attempt_id, "started_at": utc_now().isoformat()}]

        mission_orm = DurableMissionORM(
            id=mission_id,
            canonical_sha=canonical_sha,
            status=MissionStatus.ACTIVE.value,
            completed_tasks_count=0,
            waiting_tasks_count=0,
            blocked_tasks_count=0,
            mission_metadata=meta,
        )
        self.session.add(mission_orm)
        self.session.flush()

        self.audit.record(
            event_type="DURABLE_MISSION_CREATED",
            actor=self.actor.value,
            action="create_mission",
            result="SUCCESS",
            metadata={"mission_id": mission_id, "attempt_id": attempt_id, "canonical_sha": canonical_sha},
        )
        return self._orm_to_record(mission_orm, [])

    def record_wait(
        self,
        mission_id: str,
        *,
        wait_class: DurableWaitClass,
        reason: str,
        item_id: str | None = None,
        task_id: str | None = None,
        project_id: str | None = None,
        dependency_or_resource: str | None = None,
        retry_after: datetime | None = None,
        provider_info: dict[str, Any] | None = None,
        attempts: int = 0,
        last_attempt: datetime | None = None,
        next_eligibility_eval: datetime | None = None,
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DurableWaitRecord:
        wait_id = f"wait_{uuid.uuid4().hex[:12]}"
        meta = dict(metadata or {})
        if project_id:
            meta["project_id"] = project_id
        if dependency_or_resource:
            meta["dependency_or_resource"] = dependency_or_resource
        if provider_info:
            meta["provider_info"] = provider_info
        meta["attempts"] = attempts
        if last_attempt:
            meta["last_attempt"] = last_attempt.isoformat()
        if next_eligibility_eval:
            meta["next_eligibility_eval"] = next_eligibility_eval.isoformat()
        if provenance:
            meta["provenance"] = provenance
        meta["entered_at"] = utc_now().isoformat()

        wait_orm = DurableWaitORM(
            id=wait_id,
            mission_id=mission_id,
            item_id=item_id,
            task_id=task_id,
            wait_class=wait_class.value if isinstance(wait_class, DurableWaitClass) else str(wait_class),
            reason=reason,
            retry_after=retry_after,
            cleared_at=None,
            is_cleared=False,
            wait_metadata=meta,
        )
        self.session.add(wait_orm)

        if item_id:
            w_class_str = str(wait_class)
            if w_class_str == DurableWaitClass.RESOURCE_SHORT.value:
                self._update_item_state(item_id, QueueWorkItemState.WAITING_RESOURCE_SHORT.value)
            elif w_class_str == DurableWaitClass.RESOURCE_LONG.value:
                self._update_item_state(item_id, QueueWorkItemState.WAITING_RESOURCE_LONG.value)
            elif w_class_str in (DurableWaitClass.RESOURCE.value, "RESOURCE"):
                self._update_item_state(item_id, QueueWorkItemState.WAITING_RESOURCE.value)
            elif w_class_str == DurableWaitClass.DEPENDENCY.value:
                self._update_item_state(item_id, QueueWorkItemState.WAITING_DEPENDENCY.value)
            elif w_class_str == DurableWaitClass.SCHEDULE.value:
                self._update_item_state(item_id, QueueWorkItemState.WAITING_SCHEDULE.value)
            elif w_class_str == DurableWaitClass.AUTHORITY.value:
                self._update_item_state(item_id, QueueWorkItemState.BLOCKED_AUTHORITY.value)
            elif w_class_str == DurableWaitClass.DECISION.value:
                self._update_item_state(item_id, QueueWorkItemState.BLOCKED_DECISION.value)

        self.session.flush()
        self.reconcile_mission_state(mission_id)

        self.audit.record(
            event_type="DURABLE_WAIT_RECORDED",
            actor=self.actor.value,
            action="record_wait",
            result="SUCCESS",
            metadata={
                "wait_id": wait_id,
                "mission_id": mission_id,
                "item_id": item_id,
                "wait_class": str(wait_class),
                "reason": reason,
            },
        )
        return self._wait_orm_to_record(wait_orm)

    def clear_wait(self, wait_id: str) -> DurableWaitRecord | None:
        wait_orm = self.session.query(DurableWaitORM).filter_by(id=wait_id).first()
        if wait_orm is None:
            return None

        wait_orm.is_cleared = True
        wait_orm.cleared_at = utc_now()

        if wait_orm.item_id and wait_orm.wait_class in (
            DurableWaitClass.RESOURCE.value,
            DurableWaitClass.RESOURCE_SHORT.value,
            DurableWaitClass.RESOURCE_LONG.value,
            DurableWaitClass.DEPENDENCY.value,
            DurableWaitClass.SCHEDULE.value,
        ):
            self._update_item_state(wait_orm.item_id, QueueWorkItemState.READY.value)

        self.session.flush()
        self.reconcile_mission_state(wait_orm.mission_id)

        self.audit.record(
            event_type="DURABLE_WAIT_CLEARED",
            actor=self.actor.value,
            action="clear_wait",
            result="SUCCESS",
            metadata={"wait_id": wait_id, "mission_id": wait_orm.mission_id},
        )
        return self._wait_orm_to_record(wait_orm)

    def get_mission(self, mission_id: str) -> DurableMissionRecord | None:
        mission_orm = self.session.query(DurableMissionORM).filter_by(id=mission_id).first()
        if mission_orm is None:
            return None
        return self.reconcile_mission_state(mission_id)

    def recover_mission(self, mission_id: str, current_canonical_sha: str, attempt_id: str | None = None) -> DurableMissionRecord:
        mission_orm = self.session.query(DurableMissionORM).filter_by(id=mission_id).first()
        if mission_orm is None:
            raise ValueError(f"Durable mission '{mission_id}' not found")

        if mission_orm.canonical_sha != current_canonical_sha:
            raise ValueError(
                f"Canonical SHA mismatch during mission recovery: mission sha={mission_orm.canonical_sha}, current sha={current_canonical_sha}"
            )

        if attempt_id is None:
            attempt_id = f"att_{uuid.uuid4().hex[:12]}"

        meta = dict(mission_orm.mission_metadata or {})
        meta["current_attempt_id"] = attempt_id
        attempts = list(meta.get("attempts", []))
        attempts.append({"attempt_id": attempt_id, "started_at": utc_now().isoformat(), "recovered": True})
        meta["attempts"] = attempts
        set_json_field(mission_orm, "mission_metadata", meta)

        now = utc_now()
        waits_orm = self.session.query(DurableWaitORM).filter_by(mission_id=mission_id, is_cleared=False).all()
        for w in waits_orm:
            if w.retry_after:
                retry_at = w.retry_after if w.retry_after.tzinfo is not None else w.retry_after.replace(tzinfo=timezone.utc)
                if retry_at <= now:
                    w.is_cleared = True
                    w.cleared_at = now
                    if w.item_id and w.wait_class in (
                        DurableWaitClass.RESOURCE.value,
                        DurableWaitClass.RESOURCE_SHORT.value,
                        DurableWaitClass.RESOURCE_LONG.value,
                        DurableWaitClass.DEPENDENCY.value,
                        DurableWaitClass.SCHEDULE.value,
                    ):
                        self._update_item_state(w.item_id, QueueWorkItemState.READY.value)

        self.session.flush()
        rec = self.reconcile_mission_state(mission_id)

        self.audit.record(
            event_type="DURABLE_MISSION_RECOVERED",
            actor=self.actor.value,
            action="recover_mission",
            result="SUCCESS",
            metadata={"mission_id": mission_id, "attempt_id": attempt_id, "canonical_sha": current_canonical_sha},
        )
        return rec

    def record_interruption(
        self,
        mission_id: str,
        attempt_id: str | None = None,
        reason: str = "SIGINT_OR_SIGTERM",
        checkpoint_data: dict[str, Any] | None = None,
    ) -> DurableMissionRecord:
        mission_orm = self.session.query(DurableMissionORM).filter_by(id=mission_id).first()
        if mission_orm is None:
            raise ValueError(f"Durable mission '{mission_id}' not found")

        mission_orm.status = MissionStatus.PAUSED.value
        meta = dict(mission_orm.mission_metadata or {})
        eff_attempt = attempt_id or meta.get("current_attempt_id")
        meta["last_interruption"] = {
            "attempt_id": eff_attempt,
            "timestamp": utc_now().isoformat(),
            "reason": reason,
            "checkpoint": checkpoint_data or {},
        }
        set_json_field(mission_orm, "mission_metadata", meta)
        mission_orm.updated_at = utc_now()
        self.session.flush()

        self.audit.record(
            event_type="DURABLE_MISSION_INTERRUPTED",
            actor=self.actor.value,
            action="record_interruption",
            result="INTERRUPTED",
            metadata={
                "mission_id": mission_id,
                "attempt_id": eff_attempt,
                "reason": reason,
                "checkpoint": checkpoint_data or {},
            },
        )
        return self.reconcile_mission_state(mission_id)

    def reevaluate_durable_waits(self, mission_id: str | None = None) -> list[DurableWaitRecord]:
        now = utc_now()
        query = self.session.query(DurableWaitORM).filter_by(is_cleared=False)
        if mission_id is not None:
            query = query.filter_by(mission_id=mission_id)

        cleared_records: list[DurableWaitRecord] = []
        for w in query.all():
            should_clear = False

            # 1. Expired retry_after timer
            if w.retry_after:
                retry_at = w.retry_after if w.retry_after.tzinfo is not None else w.retry_after.replace(tzinfo=timezone.utc)
                if retry_at <= now:
                    should_clear = True

            # 2. Machine-verifiable dependency condition
            if not should_clear and w.wait_class == DurableWaitClass.DEPENDENCY.value:
                meta = w.wait_metadata or {}
                required_paths = meta.get("required_paths") or meta.get("dependency_file_paths")
                if required_paths and isinstance(required_paths, list):
                    all_exist = True
                    for p in required_paths:
                        if not Path(p).exists():
                            all_exist = False
                            break
                    if all_exist:
                        should_clear = True

            # 3. Provider re-availability flag or metadata check
            if not should_clear:
                meta = w.wait_metadata or {}
                if meta.get("provider_reavailable") is True or meta.get("can_resume") is True:
                    should_clear = True

            if should_clear:
                cleared = self.clear_wait(w.id)
                if cleared:
                    cleared_records.append(cleared)

        return cleared_records

    def reconcile_mission_state(self, mission_id: str) -> DurableMissionRecord:
        mission_orm = self.session.query(DurableMissionORM).filter_by(id=mission_id).first()
        if mission_orm is None:
            raise ValueError(f"Durable mission '{mission_id}' not found")

        waits_orm = self.session.query(DurableWaitORM).filter_by(mission_id=mission_id).all()
        uncleared_waits = [w for w in waits_orm if not w.is_cleared]

        waiting_count = 0
        blocked_count = 0
        for w in uncleared_waits:
            if w.wait_class in (
                DurableWaitClass.RESOURCE.value,
                DurableWaitClass.RESOURCE_SHORT.value,
                DurableWaitClass.RESOURCE_LONG.value,
                DurableWaitClass.DEPENDENCY.value,
                DurableWaitClass.SCHEDULE.value,
            ):
                waiting_count += 1
            elif w.wait_class in (DurableWaitClass.AUTHORITY.value, DurableWaitClass.DECISION.value, "BLOCKED"):
                blocked_count += 1

        items = self._get_all_items()
        completed_count = len([i for i in items if i.get("state") == QueueWorkItemState.COMPLETED.value])

        waiting_items = [
            i for i in items if i.get("state") in (
                QueueWorkItemState.WAITING_RESOURCE.value,
                QueueWorkItemState.WAITING_RESOURCE_SHORT.value,
                QueueWorkItemState.WAITING_RESOURCE_LONG.value,
                QueueWorkItemState.WAITING_DEPENDENCY.value,
                QueueWorkItemState.WAITING_SCHEDULE.value,
                QueueWorkItemState.WAITING_HUMAN.value,
                QueueWorkItemState.WAITING_EXTERNAL.value,
            )
        ]
        blocked_items = [
            i for i in items if i.get("state") in (
                QueueWorkItemState.BLOCKED_DEPENDENCY.value,
                QueueWorkItemState.BLOCKED_AUTHORITY.value,
                QueueWorkItemState.BLOCKED_DECISION.value,
            )
        ]
        ready_items = [i for i in items if i.get("state") in (QueueWorkItemState.READY.value, QueueWorkItemState.READY_TO_RESUME.value)]

        mission_orm.completed_tasks_count = max(completed_count, mission_orm.completed_tasks_count)
        mission_orm.waiting_tasks_count = max(len(waiting_items), waiting_count)
        mission_orm.blocked_tasks_count = max(len(blocked_items), blocked_count)

        if mission_orm.status in (MissionStatus.PAUSED.value, MissionStatus.INTERRUPTED.value) and not ready_items:
            pass
        elif ready_items:
            mission_orm.status = MissionStatus.ACTIVE.value
        elif waiting_items or uncleared_waits:
            # If no ready items, but waiting/uncleared items exist, mission is SLEEPING/WAITING
            def _ensure_utc(dt: datetime | None) -> datetime | None:
                if dt is None:
                    return None
                return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

            now_utc = utc_now()
            has_long_or_schedule = any(
                w.wait_class == DurableWaitClass.RESOURCE_LONG.value
                or (w.retry_after and (_ensure_utc(w.retry_after) - now_utc).total_seconds() > 60)
                for w in uncleared_waits
            )
            mission_orm.status = MissionStatus.SLEEPING.value if has_long_or_schedule else MissionStatus.WAITING.value

            # Record sleeping metadata
            meta = dict(mission_orm.mission_metadata or {})
            retry_dates = [_ensure_utc(w.retry_after) for w in uncleared_waits if w.retry_after]
            earliest_wake = min(retry_dates).isoformat() if retry_dates else None
            meta["sleeping_metadata"] = {
                "why_not_executable": "All active work packages are waiting on resources, dependencies, or schedules.",
                "earliest_wake_at": earliest_wake,
                "unresolved_dependencies": [w.reason for w in uncleared_waits if w.wait_class == DurableWaitClass.DEPENDENCY.value],
                "blocked_authority_decisions": [w.reason for w in uncleared_waits if w.wait_class in (DurableWaitClass.AUTHORITY.value, DurableWaitClass.DECISION.value)],
            }
            set_json_field(mission_orm, "mission_metadata", meta)
        elif items and completed_count == len(items) and not blocked_items:
            mission_orm.status = MissionStatus.COMPLETED.value
        elif blocked_items and not ready_items and not waiting_items:
            mission_orm.status = MissionStatus.FAILED.value
        elif items and completed_count < len(items):
            mission_orm.status = MissionStatus.WAITING.value
        else:
            mission_orm.status = MissionStatus.WAITING.value

        mission_orm.updated_at = utc_now()
        self.session.flush()

        return self._orm_to_record(mission_orm, waits_orm)

    def _orm_to_record(self, mission_orm: DurableMissionORM, waits_orm: list[DurableWaitORM]) -> DurableMissionRecord:
        return DurableMissionRecord(
            mission_id=mission_orm.id,
            canonical_sha=mission_orm.canonical_sha,
            status=MissionStatus(mission_orm.status),
            created_at=mission_orm.created_at,
            updated_at=mission_orm.updated_at,
            completed_tasks_count=mission_orm.completed_tasks_count,
            waiting_tasks_count=mission_orm.waiting_tasks_count,
            blocked_tasks_count=mission_orm.blocked_tasks_count,
            wait_records=[self._wait_orm_to_record(w) for w in waits_orm],
            metadata=dict(mission_orm.mission_metadata or {}),
        )

    def _wait_orm_to_record(self, wait_orm: DurableWaitORM) -> DurableWaitRecord:
        def _tz(dt: datetime | None) -> datetime | None:
            if dt is not None and dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        def _tz_parse(val: Any) -> datetime | None:
            if isinstance(val, datetime):
                return _tz(val)
            if isinstance(val, str):
                try:
                    return _tz(datetime.fromisoformat(val))
                except Exception:
                    return None
            return None

        meta = dict(wait_orm.wait_metadata or {})
        return DurableWaitRecord(
            wait_id=wait_orm.id,
            mission_id=wait_orm.mission_id,
            item_id=wait_orm.item_id,
            task_id=wait_orm.task_id,
            project_id=meta.get("project_id"),
            wait_class=DurableWaitClass(wait_orm.wait_class) if wait_orm.wait_class in DurableWaitClass.__members__ else DurableWaitClass.RESOURCE,
            reason=wait_orm.reason,
            dependency_or_resource=meta.get("dependency_or_resource"),
            entered_at=_tz_parse(meta.get("entered_at")) or _tz(wait_orm.created_at) or utc_now(),
            retry_after=_tz(wait_orm.retry_after),
            provider_info=dict(meta.get("provider_info") or {}),
            attempts=int(meta.get("attempts", 0)),
            last_attempt=_tz_parse(meta.get("last_attempt")),
            next_eligibility_eval=_tz_parse(meta.get("next_eligibility_eval")),
            provenance=dict(meta.get("provenance") or {}),
            cleared_at=_tz(wait_orm.cleared_at),
            is_cleared=wait_orm.is_cleared,
            created_at=_tz(wait_orm.created_at) or utc_now(),
            metadata=meta,
        )
