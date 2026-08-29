from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from lucius.domain.enums import EvidenceStatus, SourceType


class EvidenceReferenceCreate(BaseModel):
    project_id: str
    repository_id: str
    snapshot_id: str
    task_id: str
    task_run_id: str | None = None
    source_type: SourceType
    path: str
    line_start: int | None = None
    line_end: int | None = None
    content_hash: str
    snippet: str | None = None
    claim: str | None = None
    relevance_score: float
    match_reasons: list[str] = Field(default_factory=list)


class EvidenceStatusResult(BaseModel):
    evidence_id: str
    status: EvidenceStatus
    checked_at: datetime

