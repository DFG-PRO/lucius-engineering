from __future__ import annotations

from pydantic import BaseModel, Field

from lucius.domain.enums import MemoryType, ValidationStatus


class MemoryMatch(BaseModel):
    memory_id: str
    score: float
    statement: str
    memory_type: MemoryType
    validation_status: ValidationStatus
    match_reasons: list[str] = Field(default_factory=list)


class MemoryRetrievalResult(BaseModel):
    matches: list[MemoryMatch]
    total: int


class FailureExperience(BaseModel):
    what_failed: str
    context: str
    error_summary: str
    attempted_approaches: list[str] = Field(default_factory=list)
    root_cause: str | None = None
    final_fix: str | None = None
    test_result: str | None = None
    regression_result: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)

