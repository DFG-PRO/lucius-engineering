from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from lucius.domain.enums import BlockerCode


class StructuredBlocker(BaseModel):
    code: BlockerCode
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContractValidationResult(BaseModel):
    valid: bool
    blockers: list[StructuredBlocker] = Field(default_factory=list)


class DocumentationCompletion(BaseModel):
    task_id: str
    targets_completed: list[str]
    evidence_references: list[str]
    completed_at: datetime
    completed_by: str

