from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from lucius.domain.enums import (
    AutonomyRecommendation,
    BenchmarkRegressionStatus,
    BenchmarkRunStatus,
    EngineeringPlanEvaluationMode,
    EngineeringPlanEvaluationResult,
    HumanRubricCaptureStatus,
    MetricApplicability,
    PilotLearningStatus,
    PlanningEvidenceMode,
    PersistentWorkflowState,
    QueueWorkItemState,
    RepositoryIntegrityResult,
    RepositoryStateClassification,
    ResumeValidationResult,
)
from lucius.persistence.orm import utc_now


class RepositoryStateObservation(BaseModel):
    id: str | None = None
    repository_id: str | None = None
    repository_path: str
    branch: str | None = None
    head_commit: str
    remote: str | None = None
    classification: RepositoryStateClassification
    tracked_modifications: list[str] = Field(default_factory=list)
    staged_modifications: list[str] = Field(default_factory=list)
    untracked_files: list[str] = Field(default_factory=list)
    manifest_hash: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=utc_now)

    @property
    def canonical(self) -> bool:
        return self.classification == RepositoryStateClassification.CANONICAL_CLEAN


class PlanFreeze(BaseModel):
    id: str
    plan_id: str
    task_id: str
    project_id: str
    repository_state_id: str | None = None
    repository_snapshot_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    commit_sha: str | None = None
    planning_mode: PlanningEvidenceMode = PlanningEvidenceMode.CURRENT_STATE_PLANNING
    evaluation_version: str = "1.12.0"
    plan_payload: dict[str, Any]
    frozen_at: datetime
    frozen_by: str


class EvaluationDimension(BaseModel):
    name: str
    status: str
    applicability: MetricApplicability = MetricApplicability.CAPTURED
    score: float | None = None
    expected: list[str] = Field(default_factory=list)
    actual: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    unnecessary: list[str] = Field(default_factory=list)
    notes: str | None = None


class PlanCorrection(BaseModel):
    severity: str
    dimension: str
    message: str

    @field_validator("severity")
    @classmethod
    def valid_severity(cls, value: str) -> str:
        allowed = {"MINOR", "MODERATE", "MAJOR", "CRITICAL"}
        if value not in allowed:
            raise ValueError(f"severity must be one of {sorted(allowed)}")
        return value


class EngineeringPlanEvaluation(BaseModel):
    id: str
    plan_freeze_id: str
    supersedes_evaluation_id: str | None = None
    evaluation_mode: EngineeringPlanEvaluationMode = EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION
    result: EngineeringPlanEvaluationResult
    aggregate_score: float | None = None
    dimensions: list[EvaluationDimension] = Field(default_factory=list)
    corrections: list[PlanCorrection] = Field(default_factory=list)
    implementation_artifact: dict[str, Any] = Field(default_factory=dict)
    evaluator_version: str = "1.12.0"
    evaluated_at: datetime
    evaluated_by: str


HUMAN_RUBRIC_DIMENSIONS = [
    "repository_understanding",
    "architectural_correctness",
    "completeness",
    "usefulness",
    "implementation_realism",
    "risk_awareness",
    "provenance_quality",
    "hallucination_control",
]


class HumanRubric(BaseModel):
    id: str
    plan_freeze_id: str | None = None
    pilot_record_id: str | None = None
    status: HumanRubricCaptureStatus
    evaluator: str | None = None
    scores: dict[str, int | str] = Field(default_factory=dict)
    comments: str | None = None
    captured_at: datetime


class BenchmarkResult(BaseModel):
    id: str
    suite_name: str
    suite_version: int
    benchmark_version: str
    git_head: str | None = None
    target_dirty: bool = False
    status: BenchmarkRunStatus
    total_cases: int
    passed: int
    failed: int
    skipped: int = 0
    duration_ms: int
    deterministic_metrics: dict[str, Any] = Field(default_factory=dict)
    artifact_result_id: str | None = None
    evaluation_run_id: str | None = None
    environment_metadata: dict[str, Any] = Field(default_factory=dict)
    captured_at: datetime


class BenchmarkComparison(BaseModel):
    status: BenchmarkRegressionStatus
    before_id: str | None = None
    after_id: str | None = None
    reasons: list[str] = Field(default_factory=list)


class PilotLearningCandidate(BaseModel):
    id: str
    pilot_record_id: str | None = None
    statement: str
    status: PilotLearningStatus
    evidence_refs: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ReleaseGateResult(BaseModel):
    recommendation: AutonomyRecommendation
    passed: bool
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    benchmark_regression_status: BenchmarkRegressionStatus = BenchmarkRegressionStatus.NOT_RUN


class PilotEvaluationRecord(BaseModel):
    id: str
    target_repository_id: str | None = None
    repository_snapshot_id: str | None = None
    repository_state_id: str | None = None
    task_id: str | None = None
    plan_id: str | None = None
    plan_freeze_id: str | None = None
    deterministic_evaluation_id: str | None = None
    human_rubric_id: str | None = None
    benchmark_before_id: str | None = None
    benchmark_after_id: str | None = None
    learning_candidate_ids: list[str] = Field(default_factory=list)
    corrections: list[dict[str, Any]] = Field(default_factory=list)
    repository_integrity_result: RepositoryIntegrityResult = RepositoryIntegrityResult.NOT_CHECKED
    canonical_status: RepositoryStateClassification
    autonomy_recommendation: AutonomyRecommendation
    gate_result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    created_by: str


class PersistentWorkflow(BaseModel):
    id: str
    project_id: str | None = None
    repository_id: str | None = None
    repository_snapshot_id: str | None = None
    task_id: str | None = None
    plan_id: str | None = None
    plan_freeze_id: str | None = None
    objective: str
    expected_main_head: str
    isolated_branch: str
    worktree_path: str
    workflow_state: PersistentWorkflowState
    authority_tier: str
    task_backlog: list[dict[str, Any]] = Field(default_factory=list)
    dependency_graph: dict[str, list[str]] = Field(default_factory=dict)
    active_task_id: str | None = None
    completed_task_ids: list[str] = Field(default_factory=list)
    pending_task_ids: list[str] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    deviations: list[dict[str, Any]] = Field(default_factory=list)
    repair_counters: dict[str, Any] = Field(default_factory=dict)
    targeted_test_evidence: list[dict[str, Any]] = Field(default_factory=list)
    full_test_status: dict[str, Any] = Field(default_factory=dict)
    checkpoint_history: list[str] = Field(default_factory=list)
    pending_human_approvals: list[str] = Field(default_factory=list)
    resume_requirements: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PersistentWorkflowCheckpoint(BaseModel):
    id: str
    workflow_id: str
    checkpoint_state: PersistentWorkflowState
    implementation_head: str
    expected_main_head: str
    branch: str
    worktree_path: str
    active_task_id: str | None = None
    completed_task_ids: list[str] = Field(default_factory=list)
    pending_task_ids: list[str] = Field(default_factory=list)
    dependency_graph: dict[str, list[str]] = Field(default_factory=dict)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    deviations: list[dict[str, Any]] = Field(default_factory=list)
    repair_counters: dict[str, Any] = Field(default_factory=dict)
    latest_test_results: list[dict[str, Any]] = Field(default_factory=list)
    known_warnings: list[str] = Field(default_factory=list)
    authority_tier: str
    pending_human_approvals: list[str] = Field(default_factory=list)
    resume_conditions: list[str] = Field(default_factory=list)
    recommended_next_action: str
    created_at: datetime


class ResumeValidation(BaseModel):
    id: str
    workflow_id: str
    checkpoint_id: str
    result: ResumeValidationResult
    checks: list[dict[str, Any]] = Field(default_factory=list)
    next_eligible_task_id: str | None = None
    notes: str | None = None
    validated_at: datetime


class QueueSelection(BaseModel):
    selected_item_id: str | None = None
    reason: str
    eligible_item_ids: list[str] = Field(default_factory=list)
    blocked_item_ids: list[str] = Field(default_factory=list)
    running_item_ids: list[str] = Field(default_factory=list)


class QueueBlockCheckpoint(BaseModel):
    id: str
    workflow_id: str
    item_id: str
    prior_state: QueueWorkItemState
    blocking_state: QueueWorkItemState
    blocking_reason: str
    blocker_category: str
    work_completed: list[str] = Field(default_factory=list)
    implementation_head: str | None = None
    pending_decision_or_dependency: str | None = None
    resume_condition: str
    known_risks: list[str] = Field(default_factory=list)
    relevant_artifacts: list[str] = Field(default_factory=list)
    repair_counters: dict[str, Any] = Field(default_factory=dict)
    approval_requirements: list[str] = Field(default_factory=list)
    next_safe_action: str
    stale_state_validation_requirements: list[str] = Field(default_factory=list)
    created_at: datetime


class QueueStatus(BaseModel):
    workflow_id: str
    running: list[dict[str, Any]] = Field(default_factory=list)
    blocked: list[dict[str, Any]] = Field(default_factory=list)
    ready: list[dict[str, Any]] = Field(default_factory=list)
    ready_to_resume: list[dict[str, Any]] = Field(default_factory=list)
    dependency_blocked: list[dict[str, Any]] = Field(default_factory=list)
    completed: list[dict[str, Any]] = Field(default_factory=list)
    failed: list[dict[str, Any]] = Field(default_factory=list)
    next_selection: QueueSelection
    generated_at: datetime = Field(default_factory=utc_now)
