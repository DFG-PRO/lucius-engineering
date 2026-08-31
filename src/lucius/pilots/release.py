from __future__ import annotations

from sqlalchemy.orm import Session

from lucius.domain.enums import (
    AutonomyRecommendation,
    BenchmarkRegressionStatus,
    EngineeringPlanEvaluationMode,
    EngineeringPlanEvaluationResult,
    HumanRubricCaptureStatus,
    RepositoryIntegrityResult,
    RepositoryStateClassification,
)
from lucius.persistence.orm import (
    BenchmarkResultORM,
    EngineeringPlanEvaluationORM,
    HumanRubricORM,
    PilotEvaluationRecordORM,
    RepositoryStateObservationORM,
)
from lucius.pilots.benchmark import BenchmarkRunnerService
from lucius.pilots.schemas import ReleaseGateResult


class ReleaseGateService:
    def __init__(self, session: Session):
        self.session = session

    def evaluate(
        self,
        *,
        repository_state_id: str | None,
        deterministic_evaluation_id: str | None,
        benchmark_before_id: str | None,
        benchmark_after_id: str | None,
        repository_integrity_result: RepositoryIntegrityResult = RepositoryIntegrityResult.UNCHANGED,
        human_rubric_id: str | None = None,
    ) -> ReleaseGateResult:
        blockers: list[str] = []
        warnings: list[str] = []
        state = self.session.get(RepositoryStateObservationORM, repository_state_id) if repository_state_id else None
        if state is None:
            blockers.append("missing repository state observation")
        elif state.classification != RepositoryStateClassification.CANONICAL_CLEAN.value:
            blockers.append(f"non-canonical repository state: {state.classification}")

        evaluation = self.session.get(EngineeringPlanEvaluationORM, deterministic_evaluation_id) if deterministic_evaluation_id else None
        if evaluation is None:
            blockers.append("missing deterministic plan evaluation")
        else:
            mode = getattr(evaluation, "evaluation_mode", None) or EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION.value
            if evaluation.result in {
                EngineeringPlanEvaluationResult.FAIL.value,
                EngineeringPlanEvaluationResult.INSUFFICIENT_EVIDENCE.value,
            }:
                blockers.append(f"deterministic {mode} evaluation not passing: {evaluation.result}")
            elif any(item.get("severity") == "CRITICAL" for item in evaluation.corrections):
                blockers.append("unresolved critical hallucination/provenance failure")
            leakage = evaluation.implementation_artifact.get("leakage_audit", {})
            leakage_failed = (
                leakage.get("result") == "FAIL"
                or leakage.get("leakage_detected") is True
                or leakage.get("novelty_proven") is False
            )
            if mode == EngineeringPlanEvaluationMode.PLANNING_ONLY.value and leakage_failed:
                blockers.append("planning-only leakage/novelty audit failed")

        before = self.session.get(BenchmarkResultORM, benchmark_before_id) if benchmark_before_id else None
        after = self.session.get(BenchmarkResultORM, benchmark_after_id) if benchmark_after_id else None
        if before is None or after is None:
            blockers.append("missing required benchmark result")
            regression_status = BenchmarkRegressionStatus.NOT_RUN
        else:
            comparison = BenchmarkRunnerService(self.session).compare(before.id, after.id)
            regression_status = comparison.status
            if comparison.status == BenchmarkRegressionStatus.REGRESSION:
                blockers.extend(comparison.reasons or ["benchmark regression detected"])
            elif comparison.status == BenchmarkRegressionStatus.INCONCLUSIVE:
                blockers.extend(comparison.reasons or ["benchmark comparison inconclusive"])

        if repository_integrity_result != RepositoryIntegrityResult.UNCHANGED:
            blockers.append(f"target repository integrity violation: {repository_integrity_result.value}")

        rubric = self.session.get(HumanRubricORM, human_rubric_id) if human_rubric_id else None
        if rubric is None or rubric.status == HumanRubricCaptureStatus.NOT_CAPTURED.value:
            warnings.append("human rubric NOT_CAPTURED; warning for first limited-write pilot, hard blocker before promotion beyond bounded write autonomy")

        recommendation = (
            AutonomyRecommendation.READY_FOR_LIMITED_WRITE_PILOT
            if not blockers
            else AutonomyRecommendation.NOT_READY_FOR_WRITE_AUTONOMY
        )
        return ReleaseGateResult(
            recommendation=recommendation,
            passed=not blockers,
            blockers=blockers,
            warnings=warnings,
            benchmark_regression_status=regression_status,
        )

    def evaluate_record(self, record_id: str) -> ReleaseGateResult:
        record = self.session.get(PilotEvaluationRecordORM, record_id)
        if record is None:
            raise ValueError(f"Unknown pilot evaluation record: {record_id}")
        return self.evaluate(
            repository_state_id=record.repository_state_id,
            deterministic_evaluation_id=record.deterministic_evaluation_id,
            benchmark_before_id=record.benchmark_before_id,
            benchmark_after_id=record.benchmark_after_id,
            repository_integrity_result=RepositoryIntegrityResult(record.repository_integrity_result),
            human_rubric_id=record.human_rubric_id,
        )
