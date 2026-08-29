from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from lucius.domain.enums import AuthorityLevel


class AuditEventCreate(BaseModel):
    event_type: str
    actor: str = "system"
    project_id: str | None = None
    repository_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    authority_level: AuthorityLevel = AuthorityLevel.L0
    action: str
    result: str
    metadata: dict[str, Any] = Field(default_factory=dict)

