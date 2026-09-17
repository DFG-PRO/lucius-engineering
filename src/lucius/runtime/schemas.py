from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from lucius.domain.enums import QueueWorkItemState


class RuntimeExecutionOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class RuntimeProviderStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"
    UNAVAILABLE = "UNAVAILABLE"


class RuntimeRetryability(StrEnum):
    RETRYABLE = "RETRYABLE"
    FAILOVERABLE = "FAILOVERABLE"
    RETRYABLE_OR_FAILOVERABLE = "RETRYABLE_OR_FAILOVERABLE"
    NON_RETRYABLE = "NON_RETRYABLE"


class ModelQualificationStatus(StrEnum):
    QUALIFIED = "QUALIFIED"
    QUALIFIED_WITH_CONSTRAINTS = "QUALIFIED_WITH_CONSTRAINTS"
    SUPERVISED_ONLY = "SUPERVISED_ONLY"
    NOT_QUALIFIED = "NOT_QUALIFIED"
    DISABLED = "DISABLED"


class RuntimeExecutionSupervision(StrEnum):
    UNSUPERVISED = "UNSUPERVISED"
    SUPERVISED = "SUPERVISED"
    HUMAN_APPROVED = "HUMAN_APPROVED"


class RuntimeLoopStatus(StrEnum):
    COMPLETED = "COMPLETED"
    IDLE = "IDLE"
    BLOCKED = "BLOCKED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class RuntimeLoopConfig(BaseModel):
    workflow_ids: list[str] | None = None
    max_tasks: int = 1
    dispatcher_count: int = 1
    stop_on_block: bool = False
    stop_on_escalation: bool = True

    @field_validator("max_tasks")
    @classmethod
    def max_tasks_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_tasks must be positive")
        return value

    @field_validator("dispatcher_count")
    @classmethod
    def exactly_one_dispatcher(cls, value: int) -> int:
        if value != 1:
            raise ValueError("native runtime loop supports exactly one logical dispatcher")
        return value


class RuntimePlanReference(BaseModel):
    workflow_id: str
    plan_id: str
    plan_freeze_id: str
    created_by_runtime: bool = False


class RuntimeExecutionContext(BaseModel):
    workflow_id: str
    item_id: str
    logical_task_id: str
    project_id: str | None = None
    repository_id: str | None = None
    workflow_task_id: str | None = None
    title: str | None = None
    workflow_objective: str
    worktree_path: str
    authority_tier: str
    plan_id: str | None = None
    plan_freeze_id: str | None = None
    queue_item: dict[str, Any] = Field(default_factory=dict)


class DispatchCandidate(BaseModel):
    project_id: str
    workflow_id: str
    task_id: str | None = None
    repository_id: str | None = None
    item_id: str
    logical_task_id: str
    priority: str = "NORMAL"
    project_priority: str = "NORMAL"
    state: QueueWorkItemState
    dependency_state: str = "COMPLETE"
    blocker_state: str = "NONE"
    retry_state: str = "READY"
    resume_state: str = "NEW"
    created_order: int = 0
    ready_timestamp: str | None = None
    created_at: str | None = None
    required_capabilities: list[str] = Field(default_factory=list)
    plan_id: str | None = None
    plan_freeze_id: str | None = None
    item_version: int = 0
    scheduling_metadata: dict[str, Any] = Field(default_factory=dict)
    tie_break_fields: dict[str, Any] = Field(default_factory=dict)


class DispatchCandidateEvaluation(BaseModel):
    candidate: DispatchCandidate
    eligible: bool
    reasons: list[str] = Field(default_factory=list)
    scheduling_key: list[str | int] = Field(default_factory=list)


class DispatchSelection(BaseModel):
    cycle_id: str
    selected: DispatchCandidate | None = None
    started: bool = False
    started_previous_state: str | None = None
    started_new_state: str | None = None
    started_previous_version: int | None = None
    started_new_version: int | None = None
    reason: str
    eligible_candidates: list[DispatchCandidateEvaluation] = Field(default_factory=list)
    excluded_candidates: list[DispatchCandidateEvaluation] = Field(default_factory=list)
    blocked_candidates: list[DispatchCandidateEvaluation] = Field(default_factory=list)
    fairness_applied: bool = False
    deterministic_tie_break_reason: str | None = None


class RuntimeProviderModel(BaseModel):
    model_id: str
    model_version: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    context_limit_tokens: int | None = None
    cost_class: str | None = None
    latency_class: str | None = None
    capability_profile: "ModelCapabilityProfile | None" = None


class ModelCapabilityProfile(BaseModel):
    provider_id: str
    model_id: str
    execution_tier: str
    is_local: bool = False
    supported_task_classes: list[str] = Field(default_factory=list)
    supported_capabilities: list[str] = Field(default_factory=list)
    supports_mutation: bool = False
    evidence_sensitive_suitable: bool = False
    schema_constrained_required: bool = False
    deterministic_verification_required: bool = True
    supervision_required: bool = True
    unattended_eligible: bool = False
    unattended_mutation_eligible: bool = False
    max_task_complexity: str = "T0"
    default_timeout_seconds: int | None = None
    max_timeout_seconds: int | None = None
    status: ModelQualificationStatus = ModelQualificationStatus.NOT_QUALIFIED
    policy_notes: list[str] = Field(default_factory=list)


class RuntimeProviderRegistration(BaseModel):
    provider_id: str
    provider_version: str = "unknown"
    status: RuntimeProviderStatus = RuntimeProviderStatus.ACTIVE
    capabilities: list[str] = Field(default_factory=list)
    supported_task_classes: list[str] = Field(default_factory=list)
    supports_code_modification: bool = False
    supported_workspace_kinds: list[str] = Field(default_factory=list)
    supported_isolation_modes: list[str] = Field(default_factory=list)
    supported_tools: list[str] = Field(default_factory=list)
    max_context_tokens: int | None = None
    models: list[RuntimeProviderModel] = Field(default_factory=list)
    available: bool = True
    retry_eligible: bool = True
    failover_eligible: bool = True
    cost_class: str | None = None
    latency_class: str | None = None
    policy_labels: list[str] = Field(default_factory=list)
    allowed_project_ids: list[str] = Field(default_factory=list)
    allowed_repository_ids: list[str] = Field(default_factory=list)
    reliability_score: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("reliability_score")
    @classmethod
    def reliability_score_range(cls, value: int) -> int:
        if not 0 <= value <= 100:
            raise ValueError("reliability_score must be between 0 and 100")
        return value


class RuntimeWorkerShape(BaseModel):
    valid: bool
    reasons: list[str] = Field(default_factory=list)
    context_file_count: int = 0
    context_bytes: int = 0
    max_context_files: int | None = None
    max_context_bytes: int | None = None
    requested_evidence_refs: int | None = None
    max_evidence_refs: int | None = None


class RuntimePreflightProvider(BaseModel):
    provider_id: str
    model_id: str | None = None
    eligible: bool
    reasons: list[str] = Field(default_factory=list)


class RuntimePreflightResult(BaseModel):
    workflow_id: str
    eligible_providers: list[RuntimePreflightProvider] = Field(default_factory=list)
    rejected_providers: list[RuntimePreflightProvider] = Field(default_factory=list)
    worker_shape: RuntimeWorkerShape
    launchable: bool


class RuntimeExecutionRequest(BaseModel):
    execution_id: str
    task_id: str
    workflow_id: str
    item_id: str
    logical_task_id: str
    plan_id: str
    plan_freeze_id: str
    project_id: str | None = None
    repository_id: str | None = None
    isolated_workspace: str
    task_intent: str
    allowed_mutation_scope: str
    allowed_mutation_paths: list[str] = Field(default_factory=list)
    deterministic_acceptance_checks: list[dict[str, Any]] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    task_type: str | None = None
    task_complexity: str = "T1"
    task_risk: str = "LOW"
    unattended: bool = False
    read_only: bool = False
    execution_supervision: RuntimeExecutionSupervision = RuntimeExecutionSupervision.UNSUPERVISED
    tool_requirements: list[str] = Field(default_factory=list)
    isolation_mode: str = "ISOLATED_WORKTREE"
    context_limits: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int | None = None
    budget_policy: dict[str, Any] = Field(default_factory=dict)
    release_evidence_refs: list[str] = Field(default_factory=list)
    routing_decision_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def _normalize_required_capabilities(cls, value: Any) -> list[str]:
        if not isinstance(value, (list, tuple, set)):
            return []
        return [
            "code_modification" if str(item) == "code_mutation" else str(item)
            for item in value
        ]


class ExecutionAdapterResult(BaseModel):
    outcome: RuntimeExecutionOutcome
    completed_substeps: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    verification: list[dict[str, Any]] = Field(default_factory=list)
    documentation: list[dict[str, Any]] = Field(default_factory=list)
    active_execution_seconds: float = 0.0
    external_capacity_wait_seconds: float = 0.0
    human_wait_seconds: float = 0.0
    blocked_task_seconds: float = 0.0
    latency_ms: int | None = None
    retries: int = 0
    escalations: int = 0
    blocking_reason: str | None = None
    blocker_category: str | None = None
    resume_condition: str | None = None
    error: str | None = None
    execution_id: str | None = None
    provider_id: str | None = None
    provider_version: str | None = None
    model_id: str | None = None
    model_version: str | None = None
    routing_decision_id: str | None = None
    provider_native_status: str | None = None
    retryability: RuntimeRetryability = RuntimeRetryability.NON_RETRYABLE
    failure_class: str | None = None
    provider_error_metadata: dict[str, Any] = Field(default_factory=dict)
    fallback_used: bool = False
    failover_from_provider_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    cost_currency: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    verification_handoff_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "active_execution_seconds",
        "external_capacity_wait_seconds",
        "human_wait_seconds",
        "blocked_task_seconds",
    )
    @classmethod
    def duration_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("runtime durations must be non-negative")
        return value


class RuntimeExecutionResult(BaseModel):
    execution_id: str
    provider_id: str
    provider_version: str = "unknown"
    model_id: str | None = None
    model_version: str | None = None
    routing_decision_id: str | None = None
    status: RuntimeExecutionOutcome
    provider_native_status: str | None = None
    output_artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    mutation_summary: str | None = None
    completed_substeps: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    verification: list[dict[str, Any]] = Field(default_factory=list)
    documentation: list[dict[str, Any]] = Field(default_factory=list)
    active_execution_seconds: float = 0.0
    external_capacity_wait_seconds: float = 0.0
    human_wait_seconds: float = 0.0
    blocked_task_seconds: float = 0.0
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    cost_currency: str | None = None
    retryability: RuntimeRetryability = RuntimeRetryability.NON_RETRYABLE
    failure_class: str | None = None
    provider_error_metadata: dict[str, Any] = Field(default_factory=dict)
    fallback_used: bool = False
    failover_from_provider_id: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    verification_handoff_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "active_execution_seconds",
        "external_capacity_wait_seconds",
        "human_wait_seconds",
        "blocked_task_seconds",
    )
    @classmethod
    def runtime_result_duration_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("runtime durations must be non-negative")
        return value


class RuntimeProviderCandidateEvaluation(BaseModel):
    provider_id: str
    eligible: bool
    reasons: list[str] = Field(default_factory=list)
    selected_model_id: str | None = None


class RuntimeRoutingDecision(BaseModel):
    routing_decision_id: str
    execution_id: str
    selected_provider_id: str | None = None
    selected_model_id: str | None = None
    fallback_provider_ids: list[str] = Field(default_factory=list)
    candidates: list[RuntimeProviderCandidateEvaluation] = Field(default_factory=list)
    policy_reasons: list[str] = Field(default_factory=list)
    no_eligible_reason: str | None = None


class RuntimeTaskExecutionRecord(BaseModel):
    workflow_id: str
    item_id: str
    project_id: str | None = None
    provider_id: str
    outcome: RuntimeExecutionOutcome
    completed_substeps: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    verification_count: int = 0
    documentation_count: int = 0


class ExecutionRuntimeLoopResult(BaseModel):
    status: RuntimeLoopStatus
    selected_tasks: int = 0
    completed_tasks: int = 0
    blocked_tasks: int = 0
    resumed_tasks: int = 0
    retries: int = 0
    escalations: int = 0
    provider_ids: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    project_switches: int = 0
    scheduler_decisions: list[dict[str, Any]] = Field(default_factory=list)
    plan_references: list[RuntimePlanReference] = Field(default_factory=list)
    task_records: list[RuntimeTaskExecutionRecord] = Field(default_factory=list)
    wall_clock_duration_seconds: float = 0.0
    active_execution_time_seconds: float = 0.0
    external_capacity_wait_time_seconds: float = 0.0
    human_wait_time_seconds: float = 0.0
    blocked_task_time_seconds: float = 0.0
    idle_eligible_work_time_seconds: float = 0.0
    single_dispatcher_enforced: bool = True
    stopped_reason: str | None = None
