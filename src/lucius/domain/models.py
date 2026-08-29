from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lucius.domain.enums import (
    Actor,
    AuthorityLevel,
    KnowledgeScope,
    ProjectStatus,
    ProjectType,
    RepositoryAccessMode,
    RepositoryAdapterType,
    SnapshotMode,
    SourceType,
    TaskComplexity,
    TaskPriority,
    TaskRunStatus,
    TaskStatus,
    ValidationStatus,
)


class LuciusModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class Project(LuciusModel):
    id: str
    name: str
    slug: str
    organization: str | None = None
    project_type: ProjectType = ProjectType.DFG_INTERNAL
    status: ProjectStatus = ProjectStatus.ACTIVE
    description: str | None = None
    workspace_scope: str | None = None
    documentation_policy: dict[str, Any] = Field(default_factory=dict)
    default_authority_level: AuthorityLevel = AuthorityLevel.L0
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
    objective: str
    priority: TaskPriority = TaskPriority.NORMAL
    complexity: TaskComplexity = TaskComplexity.T0
    authority_level: AuthorityLevel = AuthorityLevel.L0
    status: TaskStatus = TaskStatus.DRAFT
    created_by: Actor = Actor.SYSTEM
    blocker_code: str | None = None
    blocker_message: str | None = None
    blocked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TaskContract(LuciusModel):
    id: str
    task_id: str
    version: int
    objective: str
    acceptance_criteria: list[dict[str, Any]]
    constraints: list[str] = Field(default_factory=list)
    repository_ids: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    environment: str
    authority_level: AuthorityLevel
    dependencies: list[str] = Field(default_factory=list)
    documentation_required: bool = False
    documentation_targets: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class TaskRun(LuciusModel):
    id: str
    task_id: str
    snapshot_id: str | None = None
    status: TaskRunStatus
    actor: Actor
    model_provider: str | None = None
    model_name: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    confidence: float | None = None
    failure_reason: str | None = None


class EvidenceReference(LuciusModel):
    id: str
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
    captured_at: datetime


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
