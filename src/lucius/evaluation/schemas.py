from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from lucius.domain.enums import (
    EvaluationCaseStatus,
    EvaluationHardGate,
    EvaluationHardGateStatus,
    EvaluationMetricName,
    EvaluationReleaseDecision,
    EvaluationRunStatus,
    EvaluationTargetType,
)
from lucius.persistence.orm import utc_now


class EvaluationCase(BaseModel):
    id: str | None = None
    suite_id: str | None = None
    name: str
    description: str
    version: int
    target_type: EvaluationTargetType
    fixture_reference: str
    project_context: dict[str, Any] = Field(default_factory=dict)
    task_definition: dict[str, Any] = Field(default_factory=dict)
    task_contract_definition: dict[str, Any] = Field(default_factory=dict)
    expected_evidence: list[str] = Field(default_factory=list)
    expected_components: list[str] = Field(default_factory=list)
    expected_files: list[str] = Field(default_factory=list)
    expected_acceptance_coverage: list[str] = Field(default_factory=list)
    expected_risk: list[str] = Field(default_factory=list)
    expected_authority: str | None = None
    expected_tests: list[str] = Field(default_factory=list)
    expected_documentation: list[str] = Field(default_factory=list)
    expected_memory_ids: list[str] = Field(default_factory=list)
    authorized_memory_ids: list[str] = Field(default_factory=list)
    known_traps: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    hard_requirements: list[str] = Field(default_factory=list)
    metric_weights: dict[EvaluationMetricName, float] = Field(default_factory=dict)
    hard_gate_rules: list[EvaluationHardGate] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    difficulty: str = "standard"
    rationale: str
    protects_against: str
    ground_truth: str
    flexible: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("version")
    @classmethod
    def positive_version(cls, value: int) -> int:
        if value < 1:
            raise ValueError("case version must be positive")
        return value


class EvaluationSuite(BaseModel):
    id: str | None = None
    name: str
    version: int
    description: str
    case_ids: list[str] = Field(default_factory=list)
    cases: list[EvaluationCase] = Field(default_factory=list)
    scoring_policy: dict[str, Any] = Field(default_factory=dict)
    hard_gate_policy: dict[str, Any] = Field(default_factory=dict)
    baseline_run_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("version")
    @classmethod
    def positive_version(cls, value: int) -> int:
        if value < 1:
            raise ValueError("suite version must be positive")
        return value


class MetricResult(BaseModel):
    name: EvaluationMetricName
    score: float
    passed: bool = True
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("score")
    @classmethod
    def score_range(cls, value: float) -> float:
        if not 0 <= value <= 100:
            raise ValueError("metric score must be between 0 and 100")
        return round(value, 4)


class HardGateResult(BaseModel):
    gate: EvaluationHardGate
    status: EvaluationHardGateStatus
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class EvaluationCaseResult(BaseModel):
    id: str | None = None
    run_id: str | None = None
    case_id: str
    case_version: int
    status: EvaluationCaseStatus
    metric_results: list[MetricResult] = Field(default_factory=list)
    weighted_score: float
    hard_gate_passed: bool
    hard_gate_failures: list[HardGateResult] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    regressions: list[dict[str, Any]] = Field(default_factory=list)
    actual_artifact_reference: str | None = None
    duration_ms: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class EvaluationRun(BaseModel):
    id: str | None = None
    suite_id: str
    suite_version: int
    target_version: str | None = None
    target_commit_sha: str | None = None
    target_dirty: bool = False
    planner_version: str | None = None
    model_provider: str | None = None
    model_profile: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    status: EvaluationRunStatus = EvaluationRunStatus.PENDING
    aggregate_score: float | None = None
    hard_gate_status: EvaluationHardGateStatus = EvaluationHardGateStatus.PASS
    release_decision: EvaluationReleaseDecision | None = None
    baseline_run_id: str | None = None
    environment_metadata: dict[str, Any] = Field(default_factory=dict)
    config_hash: str
    regressions: list[dict[str, Any]] = Field(default_factory=list)
    machine_report: dict[str, Any] = Field(default_factory=dict)
    markdown_report: str | None = None
    created_by: str = "SYSTEM"


class EvaluationReport(BaseModel):
    run: EvaluationRun
    cases: list[EvaluationCaseResult]
    machine: dict[str, Any]
    markdown: str
