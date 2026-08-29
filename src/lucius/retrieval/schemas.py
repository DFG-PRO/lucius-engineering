from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from lucius.domain.enums import SourceType


class RetrievalRequest(BaseModel):
    task_id: str
    task_run_id: str | None = None
    project_id: str
    repository_ids: list[str]
    snapshot_ids: list[str]
    query_terms: list[str]
    requested_source_types: list[SourceType] = Field(default_factory=list)
    max_results: int = 10
    max_total_bytes: int = 24_000
    max_snippet_bytes: int = 600
    include_tests: bool = True
    include_documentation: bool = True
    include_config: bool = True
    created_at: datetime


class SearchMatch(BaseModel):
    repository_id: str
    snapshot_id: str
    path: str
    line_start: int | None = None
    line_end: int | None = None
    matched_term: str
    match_type: str
    snippet: str | None = None
    content_hash: str


class CandidateSource(BaseModel):
    repository_id: str
    snapshot_id: str
    path: str
    source_type: SourceType
    size_bytes: int
    content_hash: str
    matches: list[SearchMatch] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class RankedSource(BaseModel):
    repository_id: str
    snapshot_id: str
    source_type: SourceType
    path: str
    score: float
    matched_terms: list[str]
    match_reasons: list[str]
    line_start: int | None = None
    line_end: int | None = None
    snippet: str | None = None
    content_hash: str
    size_bytes: int


class CoverageItem(BaseModel):
    key: str
    status: str
    evidence_ids: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)


class TechnicalContextPackage(BaseModel):
    id: str
    task_id: str
    task_run_id: str | None = None
    project_id: str
    snapshot_ids: list[str]
    query_terms: list[str]
    evidence_ids: list[str]
    ranked_sources: list[RankedSource]
    total_sources: int
    total_bytes: int
    coverage_summary: dict[str, list[CoverageItem] | dict[str, int]]
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime

    model_config = {"use_enum_values": True}

