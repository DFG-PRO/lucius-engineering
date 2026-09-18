from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from lucius.audit.schemas import AuditEventCreate
from lucius.domain.enums import AuthorityLevel
from lucius.persistence.orm import AuditEventORM, utc_now
from lucius.persistence.repositories import next_id


class AuditService:
    def __init__(self, session: Session):
        self.session = session

    def record(
        self,
        *,
        event_type: str,
        action: str,
        result: str,
        actor: str = "system",
        project_id: str | None = None,
        repository_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        authority_level: AuthorityLevel = AuthorityLevel.L0,
        metadata: dict[str, Any] | None = None,
    ) -> AuditEventORM:
        event = AuditEventCreate(
            event_type=event_type,
            actor=actor,
            project_id=project_id,
            repository_id=repository_id,
            task_id=task_id,
            run_id=run_id,
            authority_level=authority_level,
            action=action,
            result=result,
            metadata=metadata or {},
        )
        row = AuditEventORM(
            id=next_id(self.session, "audit"),
            event_type=event.event_type,
            actor=event.actor,
            project_id=event.project_id,
            repository_id=event.repository_id,
            task_id=event.task_id,
            run_id=event.run_id,
            authority_level=event.authority_level.value,
            action=event.action,
            result=event.result,
            timestamp=utc_now(),
            event_metadata=event.metadata,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_audit_observability(self, scheduler_cycles: int = 1) -> dict[str, Any]:
        """Calculates storage metrics, payload sizes, and oversized payload detection over recorded audit events."""
        import json
        events = self.session.query(AuditEventORM).all()
        if not events:
            return {
                "event_count": 0,
                "total_audit_bytes": 0,
                "average_metadata_payload_size": 0.0,
                "maximum_metadata_payload_size": 0,
                "audit_bytes_per_cycle": 0.0,
                "largest_event_types": {},
                "oversized_events": [],
            }

        total_bytes = 0
        total_meta_bytes = 0
        max_meta_bytes = 0
        event_type_bytes: dict[str, int] = {}
        oversized_events: list[dict[str, Any]] = []

        for ev in events:
            meta_str = json.dumps(ev.event_metadata or {})
            meta_len = len(meta_str.encode("utf-8"))
            row_bytes = meta_len + len(ev.event_type or "") + len(ev.action or "") + len(ev.result or "") + 50

            total_bytes += row_bytes
            total_meta_bytes += meta_len
            if meta_len > max_meta_bytes:
                max_meta_bytes = meta_len

            event_type_bytes[ev.event_type] = event_type_bytes.get(ev.event_type, 0) + row_bytes

            if meta_len > 10240:  # 10 KB threshold
                oversized_events.append({
                    "id": ev.id,
                    "event_type": ev.event_type,
                    "metadata_bytes": meta_len,
                    "action": ev.action,
                })

        event_count = len(events)
        avg_meta_size = round(total_meta_bytes / event_count, 2) if event_count > 0 else 0.0
        cycles = max(1, scheduler_cycles)
        bytes_per_cycle = round(total_bytes / cycles, 2)

        sorted_types = dict(sorted(event_type_bytes.items(), key=lambda item: item[1], reverse=True)[:5])

        return {
            "event_count": event_count,
            "total_audit_bytes": total_bytes,
            "total_metadata_bytes": total_meta_bytes,
            "average_metadata_payload_size": avg_meta_size,
            "maximum_metadata_payload_size": max_meta_bytes,
            "audit_bytes_per_cycle": bytes_per_cycle,
            "largest_event_types": sorted_types,
            "oversized_events": oversized_events,
        }
