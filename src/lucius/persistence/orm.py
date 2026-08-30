from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class IdCounterORM(Base):
    __tablename__ = "id_counters"

    entity: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ProjectORM(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    organization: Mapped[str | None] = mapped_column(String(255))
    project_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    workspace_scope: Mapped[str | None] = mapped_column(Text)
    documentation_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    default_authority_level: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class RepositoryRegistrationORM(Base):
    __tablename__ = "repository_registrations"
    __table_args__ = (
        UniqueConstraint("project_id", "location", name="uq_repository_project_location"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(32), nullable=False)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_remote: Mapped[str | None] = mapped_column(Text)
    default_branch: Mapped[str | None] = mapped_column(String(255))
    access_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class RepositorySnapshotORM(Base):
    __tablename__ = "repository_snapshots"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repository_registrations.id"), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    branch: Mapped[str | None] = mapped_column(String(255))
    commit_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    is_dirty: Mapped[bool] = mapped_column(Boolean, nullable=False)
    dirty_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False)
    test_count: Mapped[int] = mapped_column(Integer, nullable=False)
    technology_profile: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    documentation_map: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    test_map: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    configuration_map: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)


class ProjectRepositoryAttachmentORM(Base):
    __tablename__ = "project_repository_attachments"
    __table_args__ = (
        UniqueConstraint("project_id", "repository_id", name="uq_project_repository_attachment"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repository_registrations.id"), nullable=False, index=True)
    attached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    attached_by: Mapped[str] = mapped_column(String(255), nullable=False)


class TaskORM(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(32), nullable=False)
    complexity: Mapped[str] = mapped_column(String(8), nullable=False)
    authority_level: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    blocker_code: Mapped[str | None] = mapped_column(String(64))
    blocker_message: Mapped[str | None] = mapped_column(Text)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class TaskContractORM(Base):
    __tablename__ = "task_contracts"
    __table_args__ = (
        UniqueConstraint("task_id", "version", name="uq_task_contract_version"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    constraints: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    repository_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    allowed_actions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    allowed_tools: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    authority_level: Mapped[str] = mapped_column(String(8), nullable=False)
    dependencies: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    documentation_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    documentation_targets: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    stop_conditions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class TaskRunORM(Base):
    __tablename__ = "task_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("repository_snapshots.id"), index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    model_provider: Mapped[str | None] = mapped_column(String(255))
    model_name: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    failure_reason: Mapped[str | None] = mapped_column(Text)


class DocumentationCompletionORM(Base):
    __tablename__ = "documentation_completions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, unique=True, index=True)
    targets_completed: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence_references: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_by: Mapped[str] = mapped_column(String(64), nullable=False)


class EvidenceReferenceORM(Base):
    __tablename__ = "evidence_references"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repository_registrations.id"), nullable=False, index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("repository_snapshots.id"), nullable=False, index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    task_run_id: Mapped[str | None] = mapped_column(ForeignKey("task_runs.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snippet: Mapped[str | None] = mapped_column(Text)
    claim: Mapped[str | None] = mapped_column(Text)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False)
    match_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class MemoryEntryORM(Base):
    __tablename__ = "memory_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True)
    organization_id: Mapped[str | None] = mapped_column(String(255), index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(255), index=True)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(64))
    source_reference: Mapped[str | None] = mapped_column(Text)
    source_project_id: Mapped[str | None] = mapped_column(String(32), index=True)
    source_task_id: Mapped[str | None] = mapped_column(String(32), index=True)
    source_run_id: Mapped[str | None] = mapped_column(String(32), index=True)
    source_evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    context_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    technology_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_id: Mapped[str | None] = mapped_column(String(32), index=True)
    superseded_by_id: Mapped[str | None] = mapped_column(String(32), index=True)
    supersession_reason: Mapped[str | None] = mapped_column(Text)
    requires_revalidation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revalidation_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LearningCandidateORM(Base):
    __tablename__ = "learning_candidates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("task_runs.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    candidate_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_memory_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_document_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    proposed_scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sanitization_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_notes: Mapped[str | None] = mapped_column(Text)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)


class ModelProviderORM(Base):
    __tablename__ = "model_providers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    supports_local: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_timeout: Mapped[int | None] = mapped_column(Integer)
    secret_reference_name: Mapped[str | None] = mapped_column(String(255))
    provider_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class ModelProfileORM(Base):
    __tablename__ = "model_profiles"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_name", name="uq_model_profile_provider_model"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("model_providers.id"), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    privacy_class: Mapped[str] = mapped_column(String(32), nullable=False)
    cost_class: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_class: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supports_structured_output: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_tool_use: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_large_context: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_code: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_reasoning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_context_tokens: Mapped[int | None] = mapped_column(Integer)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    input_cost_per_million: Mapped[float | None] = mapped_column(Float)
    output_cost_per_million: Mapped[float | None] = mapped_column(Float)
    profile_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class ModelExecutionORM(Base):
    __tablename__ = "model_executions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(32), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), index=True)
    provider_id: Mapped[str | None] = mapped_column(String(32), index=True)
    provider: Mapped[str | None] = mapped_column(String(255))
    model_profile_id: Mapped[str | None] = mapped_column(String(32), index=True)
    model: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    privacy_class: Mapped[str] = mapped_column(String(32), nullable=False)
    routing_decision: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost: Mapped[float | None] = mapped_column(Float)
    cost_source: Mapped[str] = mapped_column(String(32), nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class EngineeringPlanORM(Base):
    __tablename__ = "engineering_plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    task_contract_id: Mapped[str] = mapped_column(String(32), nullable=False)
    task_contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    required_authority_level: Mapped[str] = mapped_column(String(8), nullable=False)
    repository_snapshot_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    memory_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    model_execution_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    assumptions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    unknowns: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    open_questions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    affected_components: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    affected_files: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    acceptance_coverage: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    test_strategy: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    documentation_requirements: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    rollback_considerations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    dependencies: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    risks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    estimated_scope: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    validation_warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    blockers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    planner_version: Mapped[str] = mapped_column(String(32), nullable=False)
    supersedes_plan_id: Mapped[str | None] = mapped_column(String(32), index=True)
    superseded_by_plan_id: Mapped[str | None] = mapped_column(String(32), index=True)
    supersession_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class EvaluationSuiteORM(Base):
    __tablename__ = "evaluation_suites"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_evaluation_suite_name_version"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    case_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    scoring_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    hard_gate_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    baseline_run_id: Mapped[str | None] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class EvaluationCaseORM(Base):
    __tablename__ = "evaluation_cases"
    __table_args__ = (
        UniqueConstraint("suite_id", "name", "version", name="uq_evaluation_case_suite_name_version"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("evaluation_suites.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    difficulty: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class EvaluationRunORM(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    suite_id: Mapped[str] = mapped_column(ForeignKey("evaluation_suites.id"), nullable=False, index=True)
    suite_version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_version: Mapped[str | None] = mapped_column(String(64))
    target_commit_sha: Mapped[str | None] = mapped_column(String(64))
    target_dirty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    planner_version: Mapped[str | None] = mapped_column(String(32))
    model_provider: Mapped[str | None] = mapped_column(String(255))
    model_profile: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    aggregate_score: Mapped[float | None] = mapped_column(Float)
    hard_gate_status: Mapped[str] = mapped_column(String(32), nullable=False)
    release_decision: Mapped[str | None] = mapped_column(String(64))
    baseline_run_id: Mapped[str | None] = mapped_column(String(32), index=True)
    environment_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    regressions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    machine_report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    markdown_report: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)


class EvaluationCaseResultORM(Base):
    __tablename__ = "evaluation_case_results"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"), nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("evaluation_cases.id"), nullable=False, index=True)
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    metric_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    weighted_score: Mapped[float] = mapped_column(Float, nullable=False)
    hard_gate_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    hard_gate_failures: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    observations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    regressions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    actual_artifact_reference: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)


class AuditEventORM(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), index=True)
    repository_id: Mapped[str | None] = mapped_column(ForeignKey("repository_registrations.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32))
    run_id: Mapped[str | None] = mapped_column(String(32))
    authority_level: Mapped[str] = mapped_column(String(8), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
