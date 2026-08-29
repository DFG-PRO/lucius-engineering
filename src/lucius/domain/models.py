from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lucius.domain.enums import (
    AuthorityLevel,
    KnowledgeScope,
    ProjectStatus,
    RepositoryAccessMode,
    RepositoryAdapterType,
    SnapshotMode,
    TaskComplexity,
    ValidationStatus,
)


class LuciusModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class Project(LuciusModel):
    id: str
    name: str
    status: ProjectStatus = ProjectStatus.ACTIVE
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class RepositoryRegistration(LuciusModel):
    id: str
    project_id: str
    name: str
    adapter_type: RepositoryAdapterType
    location: str
    canonical_remote: str | None = None
    default_branch: str | None = None
    access_mode: RepositoryAccessMode
    status: str
    created_at: datetime
    updated_at: datetime


class RepositorySnapshot(LuciusModel):
    id: str
    repository_id: str
    mode: SnapshotMode
    captured_at: datetime
    branch: str | None = None
    commit_sha: str
    is_dirty: bool
    dirty_summary: dict[str, Any] = Field(default_factory=dict)
    manifest_hash: str
    file_count: int
    document_count: int
    test_count: int
    technology_profile: dict[str, Any] = Field(default_factory=dict)
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class Task(LuciusModel):
    id: str
    project_id: str
    title: str
    complexity: TaskComplexity = TaskComplexity.T0


class TaskRun(LuciusModel):
    id: str
    task_id: str
    status: str


class EvidenceReference(LuciusModel):
    id: str
    project_id: str
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class EngineeringPlan(LuciusModel):
    id: str
    project_id: str
    title: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryEntry(LuciusModel):
    id: str
    scope: KnowledgeScope
    content_ref: str
    validation_status: ValidationStatus


class LearningCandidate(LuciusModel):
    id: str
    source_ref: str
    validation_status: ValidationStatus = ValidationStatus.CANDIDATE


class EvaluationRun(LuciusModel):
    id: str
    name: str
    result: str


class AuditEvent(LuciusModel):
    id: str
    event_type: str
    actor: str
    project_id: str | None = None
    repository_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    authority_level: AuthorityLevel = AuthorityLevel.L0
    action: str
    result: str
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

