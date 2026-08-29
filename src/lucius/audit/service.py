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
