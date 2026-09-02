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
        evaluation_mode = EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION.value
        implementation_artifact = {}
        if evaluation is None:
            blockers.append("missing deterministic plan evaluation")
        else:
            evaluation_mode = getattr(evaluation, "evaluation_mode", None) or EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION.value
            implementation_artifact = evaluation.implementation_artifact or {}
            if evaluation.result in {
                EngineeringPlanEvaluationResult.FAIL.value,
                EngineeringPlanEvaluationResult.INSUFFICIENT_EVIDENCE.value,
            }:
                blockers.append(f"deterministic {evaluation_mode} evaluation not passing: {evaluation.result}")
            elif any(item.get("severity") == "CRITICAL" for item in evaluation.corrections):
                blockers.append("unresolved critical hallucination/provenance failure")
            leakage = implementation_artifact.get("leakage_audit", {})
            leakage_failed = (
                leakage.get("result") == "FAIL"
                or leakage.get("leakage_detected") is True
                or leakage.get("novelty_proven") is False
            )
            if evaluation_mode == EngineeringPlanEvaluationMode.PLANNING_ONLY.value and leakage_failed:
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
        human_rubric_captured = rubric is not None and rubric.status == HumanRubricCaptureStatus.CAPTURED.value

        is_plan_vs_implementation = (
            evaluation_mode == EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION.value
        )
        pilot_stage = implementation_artifact.get("pilot_stage")
        if not human_rubric_captured:
            if pilot_stage == "BOUNDED_MULTI_TASK_ENGINEERING_PILOT":
                warnings.append(
                    "human rubric NOT_CAPTURED; hard blocker before supervised engineering workflow promotion"
                )
            elif pilot_stage == "SUPERVISED_ENGINEERING_WORKFLOW":
                warnings.append(
                    "human rubric NOT_CAPTURED; hard blocker before persistent supervised engineering promotion"
                )
            elif pilot_stage == "PERSISTENT_SUPERVISED_WORKFLOW":
                warnings.append(
                    "human rubric NOT_CAPTURED; expected for persistent pause/resume pilot, hard blocker before broader autonomy"
                )
            elif pilot_stage == "NON_BLOCKING_PROJECT_QUEUE_PILOT":
                warnings.append(
                    "human rubric NOT_CAPTURED; warning for non-blocking queue pilot, hard blocker before broader autonomy"
                )
            elif pilot_stage == "CROSS_PROJECT_NON_BLOCKING_QUEUE_PILOT":
                warnings.append(
                    "human rubric NOT_CAPTURED; warning for cross-project queue pilot, hard blocker before broader autonomy"
                )
            else:
                warnings.append("human rubric NOT_CAPTURED; warning for first limited-write pilot, hard blocker before promotion beyond bounded write autonomy")

        if blockers:
            recommendation = (
                AutonomyRecommendation.NOT_READY_FOR_NON_BLOCKING_PROJECT_QUEUE
                if is_plan_vs_implementation
                and pilot_stage in {"NON_BLOCKING_PROJECT_QUEUE_PILOT", "CROSS_PROJECT_NON_BLOCKING_QUEUE_PILOT"}
                else
                AutonomyRecommendation.NOT_READY_FOR_PERSISTENT_SUPERVISED_ENGINEERING
                if is_plan_vs_implementation and pilot_stage == "PERSISTENT_SUPERVISED_WORKFLOW"
                else
                AutonomyRecommendation.NOT_READY_FOR_SUPERVISED_ENGINEERING_WORKFLOW
                if is_plan_vs_implementation and pilot_stage == "SUPERVISED_ENGINEERING_WORKFLOW"
                else
                AutonomyRecommendation.NOT_READY_FOR_BOUNDED_MULTI_TASK_ENGINEERING
                if is_plan_vs_implementation and pilot_stage == "BOUNDED_MULTI_TASK_ENGINEERING_PILOT"
                else
                AutonomyRecommendation.NOT_READY_FOR_BOUNDED_ENGINEERING
                if is_plan_vs_implementation and pilot_stage == "BOUNDED_ENGINEERING_PILOT"
                else AutonomyRecommendation.NOT_READY_FOR_WRITE_AUTONOMY
            )
        elif is_plan_vs_implementation and pilot_stage == "SUPERVISED_ENGINEERING_WORKFLOW":
            if human_rubric_captured and self._rubric_scores_meet_multi_task_threshold(rubric):
                recommendation = AutonomyRecommendation.READY_FOR_PERSISTENT_SUPERVISED_ENGINEERING
            else:
                recommendation = AutonomyRecommendation.READY_FOR_ANOTHER_SUPERVISED_ENGINEERING_WORKFLOW
                if human_rubric_captured:
                    warnings.append(
                        "persistent supervised engineering promotion requires 5/5 captured human scores across all canonical rubric dimensions"
                    )
        elif is_plan_vs_implementation and pilot_stage == "PERSISTENT_SUPERVISED_WORKFLOW":
            pause_resume = implementation_artifact.get("pause_resume_evaluation", {})
            if (
                pause_resume.get("result") == "PASS"
                and pause_resume.get("resume_decision") == "SAFE_TO_RESUME"
                and implementation_artifact.get("final_workflow_state") == "COMPLETED_PENDING_INTEGRATION"
            ):
                recommendation = AutonomyRecommendation.READY_FOR_NON_BLOCKING_PROJECT_QUEUE_PILOT
            else:
                recommendation = AutonomyRecommendation.READY_FOR_ANOTHER_PERSISTENT_SUPERVISED_WORKFLOW
        elif is_plan_vs_implementation and pilot_stage == "NON_BLOCKING_PROJECT_QUEUE_PILOT":
            required_checks = [
                implementation_artifact.get("queue_evaluation", {}).get("result") == "PASS",
                implementation_artifact.get("fresh_context_reconstruction", {}).get("result") == "PASS",
                implementation_artifact.get("duplicate_work_protection", {}).get("result") == "PASS",
                implementation_artifact.get("project_isolation", {}).get("result") == "PASS",
                implementation_artifact.get("safe_interruption", {}).get("result") == "PASS",
                implementation_artifact.get("multi_project_non_blocking_tested") is True,
            ]
            if all(required_checks):
                recommendation = AutonomyRecommendation.READY_FOR_MULTI_PROJECT_NON_BLOCKING_QUEUE
            else:
                recommendation = AutonomyRecommendation.READY_FOR_ANOTHER_NON_BLOCKING_PROJECT_QUEUE_PILOT
        elif is_plan_vs_implementation and pilot_stage == "CROSS_PROJECT_NON_BLOCKING_QUEUE_PILOT":
            required_checks = [
                implementation_artifact.get("cross_project_evaluation", {}).get("result") == "PASS",
                implementation_artifact.get("global_scheduler_evaluation", {}).get("result") == "PASS",
                implementation_artifact.get("fresh_context_reconstruction", {}).get("result") == "PASS",
                implementation_artifact.get("duplicate_work_protection", {}).get("result") == "PASS",
                implementation_artifact.get("project_isolation", {}).get("result") == "PASS",
                implementation_artifact.get("safe_interruption", {}).get("result") == "PASS",
                implementation_artifact.get("multi_project_non_blocking_tested") is True,
            ]
            if all(required_checks):
                recommendation = AutonomyRecommendation.READY_FOR_MULTI_PROJECT_NON_BLOCKING_QUEUE
            else:
                recommendation = AutonomyRecommendation.READY_FOR_ANOTHER_CROSS_PROJECT_QUEUE_PILOT
        elif is_plan_vs_implementation and pilot_stage == "BOUNDED_MULTI_TASK_ENGINEERING_PILOT":
            if human_rubric_captured and self._rubric_scores_meet_multi_task_threshold(rubric):
                recommendation = AutonomyRecommendation.READY_FOR_SUPERVISED_ENGINEERING_WORKFLOW
            else:
                recommendation = AutonomyRecommendation.READY_FOR_ANOTHER_BOUNDED_MULTI_TASK_ENGINEERING_PILOT
                if human_rubric_captured:
                    warnings.append(
                        "supervised engineering workflow promotion requires 5/5 captured human scores across all canonical rubric dimensions"
                    )
        elif is_plan_vs_implementation and pilot_stage == "BOUNDED_ENGINEERING_PILOT":
            if human_rubric_captured and self._rubric_scores_meet_multi_task_threshold(rubric):
                recommendation = AutonomyRecommendation.READY_FOR_BOUNDED_MULTI_TASK_ENGINEERING
            else:
                recommendation = AutonomyRecommendation.READY_FOR_ANOTHER_BOUNDED_ENGINEERING_PILOT
                if human_rubric_captured:
                    warnings.append(
                        "bounded multi-task promotion requires 5/5 captured human scores across all canonical rubric dimensions"
                    )
        elif is_plan_vs_implementation and pilot_stage == "LIMITED_WRITE_PILOT":
            if human_rubric_captured and self._has_prior_successful_limited_write_pilot(
                current_evaluation_id=deterministic_evaluation_id,
            ):
                recommendation = AutonomyRecommendation.READY_FOR_BOUNDED_ENGINEERING_PILOT
            else:
                recommendation = AutonomyRecommendation.READY_FOR_ANOTHER_LIMITED_WRITE_PILOT
        else:
            recommendation = AutonomyRecommendation.READY_FOR_LIMITED_WRITE_PILOT
        return ReleaseGateResult(
            recommendation=recommendation,
            passed=not blockers,
            blockers=blockers,
            warnings=warnings,
            benchmark_regression_status=regression_status,
        )

    def _has_prior_successful_limited_write_pilot(
        self,
        *,
        current_evaluation_id: str | None,
    ) -> bool:
        records = self.session.query(PilotEvaluationRecordORM).all()
        for record in records:
            if record.deterministic_evaluation_id == current_evaluation_id:
                continue
            if record.repository_integrity_result != RepositoryIntegrityResult.UNCHANGED.value:
                continue
            gate_result = record.gate_result or {}
            if gate_result.get("passed") is not True:
                continue
            if gate_result.get("benchmark_regression_status") != BenchmarkRegressionStatus.NO_REGRESSION.value:
                continue
            evaluation = (
                self.session.get(EngineeringPlanEvaluationORM, record.deterministic_evaluation_id)
                if record.deterministic_evaluation_id
                else None
            )
            if evaluation is None:
                continue
            if evaluation.evaluation_mode != EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION.value:
                continue
            if evaluation.result in {
                EngineeringPlanEvaluationResult.FAIL.value,
                EngineeringPlanEvaluationResult.INSUFFICIENT_EVIDENCE.value,
            }:
                continue
            if (evaluation.implementation_artifact or {}).get("pilot_stage") != "LIMITED_WRITE_PILOT":
                continue
            return True
        return False

    @staticmethod
    def _rubric_scores_meet_multi_task_threshold(rubric: HumanRubricORM | None) -> bool:
        if rubric is None:
            return False
        scores = rubric.scores or {}
        if not scores:
            return False
        return all(isinstance(score, int) and score == 5 for score in scores.values())

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
