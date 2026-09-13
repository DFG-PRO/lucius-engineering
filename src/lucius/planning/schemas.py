from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from lucius.domain.enums import (
    AcceptanceCoverageStatus,
    AffectedFileStatus,
    AuthorityLevel,
    EngineeringPlanStatus,
    Environment,
    PlanRiskLevel,
    PlanningBlockerCode,
    PlanStepSupportStatus,
    ReplanReason,
    TestStrategyKind,
)
from lucius.persistence.orm import utc_now
from lucius.pilots.documentation_contract import canonicalize_documentation_requirement


class PlanningBlocker(BaseModel):
    code: PlanningBlockerCode
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlanningContextBudget(BaseModel):
    max_evidence_items: int = 8
    max_memory_items: int = 6
    max_total_context_bytes: int = 24_000
    max_per_source_bytes: int = 1_200


class PlanningEvidenceItem(BaseModel):
    evidence_id: str
    repository_id: str
    snapshot_id: str
    source_type: str
    path: str
    snippet: str | None = None
    relevance_score: float
    match_reasons: list[str] = Field(default_factory=list)


class PlanningMemoryItem(BaseModel):
    memory_id: str
    scope: str
    statement: str
    validation_status: str
    confidence: float
    requires_revalidation: bool
    source_evidence_ids: list[str] = Field(default_factory=list)


class PlanningContext(BaseModel):
    task_id: str
    run_id: str | None = None
    project_id: str
    task_title: str
    task_objective: str
    task_status: str
    task_authority_level: AuthorityLevel
    task_complexity: str
    task_contract_id: str
    task_contract_version: int
    contract_objective: str
    acceptance_criteria: list[dict[str, Any]]
    constraints: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    environment: Environment
    contract_authority_level: AuthorityLevel
    documentation_required: bool
    documentation_targets: list[str] = Field(default_factory=list)
    snapshot_metadata: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[PlanningEvidenceItem] = Field(default_factory=list)
    memory: list[PlanningMemoryItem] = Field(default_factory=list)
    context_warnings: list[dict[str, Any]] = Field(default_factory=list)
    omitted_counts: dict[str, int] = Field(default_factory=dict)
    total_context_bytes: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class PlanAssumption(BaseModel):
    statement: str
    verified: bool = False
    evidence_ids: list[str] = Field(default_factory=list)


class PlanUnknown(BaseModel):
    statement: str
    blocking: bool = False
    needed_to_resolve: str | None = None


class PlanOpenQuestion(BaseModel):
    question: str
    reason: str
    blocking: bool = False
    required_decision_authority: AuthorityLevel = AuthorityLevel.L1


class AffectedFilePlan(BaseModel):
    path: str
    status: AffectedFileStatus = AffectedFileStatus.UNKNOWN
    evidence_ids: list[str] = Field(default_factory=list)


class PlanStep(BaseModel):
    step_id: str
    sequence: int
    title: str
    description: str
    affected_components: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    required_evidence_ids: list[str] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    expected_result: str
    validation: str
    risk: PlanRiskLevel = PlanRiskLevel.LOW
    authority_level: AuthorityLevel = AuthorityLevel.L1
    support_status: PlanStepSupportStatus = PlanStepSupportStatus.INFERRED

    @field_validator("sequence")
    @classmethod
    def positive_sequence(cls, value: int) -> int:
        if value < 1:
            raise ValueError("step sequence must be positive")
        return value


class AcceptanceCoverage(BaseModel):
    criterion_id: str
    status: AcceptanceCoverageStatus
    step_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    notes: str | None = None


class DeterministicAcceptanceCheck(BaseModel):
    type: str
    path: str
    expected_text: str | None = None

    @field_validator("type")
    @classmethod
    def supported_type(cls, value: str) -> str:
        if value not in {"exact_file_content", "file_exists", "file_contains", "file_not_contains"}:
            raise ValueError("Unsupported deterministic acceptance check type.")
        return value

    @field_validator("path")
    @classmethod
    def nonempty_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Deterministic acceptance check path must be non-empty.")
        return normalized

    @model_validator(mode="after")
    def type_specific_arguments(self):
        if self.type == "file_exists":
            if self.expected_text is not None:
                raise ValueError("file_exists checks must not provide expected_text.")
            return self
        if not isinstance(self.expected_text, str):
            raise ValueError(f"{self.type} checks require expected_text.")
        return self


class TestRecommendation(BaseModel):
    kind: TestStrategyKind
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class DocumentationRequirement(BaseModel):
    target: str
    reason: str
    trigger: str
    target_type: str | None = None
    exact_path_required: bool | None = None
    acceptable_paths: list[str] = Field(default_factory=list)
    acceptable_categories: list[str] = Field(default_factory=list)
    proposed_path: str | None = None
    canonical_target: str | None = None

    @model_validator(mode="before")
    @classmethod
    def canonicalize_input(cls, value):
        if isinstance(value, dict):
            return canonicalize_documentation_requirement(value)
        return value


class PlanRisk(BaseModel):
    risk: str
    level: PlanRiskLevel
    mitigation: str | None = None


class ModelEngineeringPlanOutput(BaseModel):
    summary: str
    objective: str
    risk_level: PlanRiskLevel = PlanRiskLevel.LOW
    required_authority_level: AuthorityLevel = AuthorityLevel.L1
    assumptions: list[PlanAssumption] = Field(default_factory=list)
    unknowns: list[PlanUnknown] = Field(default_factory=list)
    open_questions: list[PlanOpenQuestion] = Field(default_factory=list)
    affected_components: list[str] = Field(default_factory=list)
    affected_files: list[AffectedFilePlan] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    acceptance_coverage: list[AcceptanceCoverage] = Field(default_factory=list)
    deterministic_acceptance_checks: list[DeterministicAcceptanceCheck] = Field(default_factory=list)
    test_strategy: list[TestRecommendation] = Field(default_factory=list)
    documentation_requirements: list[DocumentationRequirement] = Field(default_factory=list)
    rollback_considerations: list[str] = Field(default_factory=list)
    orchestration_contract_required: bool = False
    orchestration_contract: dict[str, Any] = Field(default_factory=dict)
    adversarial_probes: list[dict[str, Any]] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    risks: list[PlanRisk] = Field(default_factory=list)
    estimated_scope: str | None = None
    confidence: float | None = None

    @field_validator("confidence")
    @classmethod
    def confidence_range(cls, value: float | None) -> float | None:
        if value is not None and not 0 <= value <= 1:
            raise ValueError("confidence must be between 0 and 1")
        return value


class EngineeringPlan(ModelEngineeringPlanOutput):
    id: str
    task_id: str
    run_id: str | None = None
    project_id: str
    task_contract_id: str
    task_contract_version: int
    version: int
    status: EngineeringPlanStatus
    repository_snapshot_ids: list[str]
    evidence_ids: list[str]
    memory_ids: list[str]
    model_execution_ids: list[str]
    validation_warnings: list[dict[str, Any]] = Field(default_factory=list)
    blockers: list[PlanningBlocker] = Field(default_factory=list)
    planner_version: str
    supersedes_plan_id: str | None = None
    superseded_by_plan_id: str | None = None
    supersession_reason: str | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime


class PlanningResult(BaseModel):
    plan: EngineeringPlan | None = None
    context: PlanningContext | None = None
    blockers: list[PlanningBlocker] = Field(default_factory=list)
    model_response_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.plan is not None and not any(blocker.code != PlanningBlockerCode.AUTHORITY_ESCALATION_REQUIRED for blocker in self.blockers)


class ReplanRequest(BaseModel):
    previous_plan_id: str
    reason: ReplanReason
    notes: str | None = None
