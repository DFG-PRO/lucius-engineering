from __future__ import annotations

from lucius.domain.enums import (
    AutonomyRecommendation,
    BenchmarkRegressionStatus,
    EngineeringPlanEvaluationMode,
    EngineeringPlanEvaluationResult,
    HumanRubricCaptureStatus,
    MetricApplicability,
    RepositoryIntegrityResult,
    RepositoryStateClassification,
)
from lucius.persistence.orm import EngineeringPlanEvaluationORM, EvidenceReferenceORM, HumanRubricORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.evaluation import EngineeringPlanEvaluationService
from lucius.pilots.freeze import PlanFreezeService
from lucius.pilots.release import ReleaseGateService
from lucius.pilots.rubric import HumanRubricService

from tests.integration.test_phase112_pilot_infrastructure import (
    _benchmark,
    _implementation_artifact,
    _plan_evaluation,
    _project_task_plan,
    _repository_state,
)


def test_planning_only_evaluation_does_not_require_implementation_artifact(session):
    freeze = _planning_freeze(session)

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_planning_artifact(),
        evaluation_mode=EngineeringPlanEvaluationMode.PLANNING_ONLY,
        evaluator_version="1.13A-test",
    )

    assert result.evaluation_mode == EngineeringPlanEvaluationMode.PLANNING_ONLY
    assert result.result == EngineeringPlanEvaluationResult.PASS
    assert all(
        item.applicability != MetricApplicability.NOT_CAPTURED
        for item in result.dimensions
    )


def test_plan_vs_implementation_still_marks_missing_implementation_evidence_not_captured(session):
    freeze = _planning_freeze(session)

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        evaluation_mode=EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION,
        evaluator_version="1.13A-test",
    )

    assert result.result == EngineeringPlanEvaluationResult.INSUFFICIENT_EVIDENCE
    assert any(
        item.applicability == MetricApplicability.NOT_CAPTURED
        and item.name == "architecture_alignment"
        for item in result.dimensions
    )
    assert any(item.severity == "MODERATE" for item in result.corrections)


def test_planning_only_marks_implementation_relative_dimensions_not_applicable(session):
    freeze = _planning_freeze(session)

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_planning_artifact(),
        evaluation_mode=EngineeringPlanEvaluationMode.PLANNING_ONLY,
    )

    not_applicable = {
        item.name: item
        for item in result.dimensions
        if item.applicability == MetricApplicability.NOT_APPLICABLE
    }

    assert "file_path_prediction" in not_applicable
    assert "missed_material_implementation_work" in not_applicable
    assert all(item.score is None for item in not_applicable.values())
    assert all(
        correction.dimension not in not_applicable
        for correction in result.corrections
    )


def test_planning_only_missing_planning_evidence_remains_insufficient(session):
    freeze = _planning_freeze(session)

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact={},
        evaluation_mode=EngineeringPlanEvaluationMode.PLANNING_ONLY,
    )

    assert result.result == EngineeringPlanEvaluationResult.INSUFFICIENT_EVIDENCE
    assert any(
        item.applicability == MetricApplicability.NOT_CAPTURED
        and item.name == "planned_path_validity"
        for item in result.dimensions
    )
    assert any(correction.dimension == "novelty_leakage_status" for correction in result.corrections)


def test_planning_only_does_not_treat_unnecessary_work_as_a_correction(session):
    freeze = _planning_freeze(session)

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_planning_artifact(),
        evaluation_mode=EngineeringPlanEvaluationMode.PLANNING_ONLY,
    )

    dimension = next(item for item in result.dimensions if item.name == "unnecessary_work")
    assert dimension.applicability == MetricApplicability.NOT_APPLICABLE
    assert all(item.dimension != "unnecessary_work" for item in result.corrections)


def test_planning_only_hallucinated_planned_paths_are_critical(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    _make_planning_ready(plan)
    plan.affected_files.append({"path": "ghost.py", "status": "LIKELY_EXISTING", "evidence_ids": []})
    plan.steps[0]["affected_files"].append("ghost.py")
    freeze = PlanFreezeService(session).freeze(plan_id=plan.id)

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_planning_artifact(),
        evaluation_mode=EngineeringPlanEvaluationMode.PLANNING_ONLY,
    )

    assert result.result == EngineeringPlanEvaluationResult.FAIL
    assert any(item.dimension == "hallucinated_files_paths" and item.severity == "CRITICAL" for item in result.corrections)


def test_planning_only_unsupported_claims_are_critical(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    _make_planning_ready(plan)
    plan.assumptions = [{"statement": "Unsupported implementation fact.", "verified": True, "evidence_ids": []}]
    freeze = PlanFreezeService(session).freeze(plan_id=plan.id)

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_planning_artifact(),
        evaluation_mode=EngineeringPlanEvaluationMode.PLANNING_ONLY,
    )

    assert result.result == EngineeringPlanEvaluationResult.FAIL
    assert any(item.dimension == "unsupported_claims" and item.severity == "CRITICAL" for item in result.corrections)


def test_planning_only_evidence_backed_verified_assumptions_are_supported(session):
    project, task, _contract, plan = _project_task_plan(session)
    _make_planning_ready(plan)
    evidence = _semantic_evidence(session, project_id=project.id, task_id=task.id, claim="Supported repository fact.")
    plan.assumptions = [{"statement": "Supported repository fact.", "verified": True, "evidence_ids": [evidence.id]}]
    freeze = PlanFreezeService(session).freeze(plan_id=plan.id)

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_planning_artifact(),
        evaluation_mode=EngineeringPlanEvaluationMode.PLANNING_ONLY,
    )

    unsupported = next(item for item in result.dimensions if item.name == "unsupported_claims")

    assert result.result != EngineeringPlanEvaluationResult.FAIL
    assert unsupported.status == "PASS"


def test_planning_only_provenance_quality_deficiency_is_major_warning_not_fabricated_pass(session):
    freeze = _planning_freeze(session)

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_planning_artifact(provenance_quality="DEFICIENT"),
        evaluation_mode=EngineeringPlanEvaluationMode.PLANNING_ONLY,
    )

    assert result.result == EngineeringPlanEvaluationResult.PASS_WITH_WARNINGS
    assert any(item.dimension == "provenance_quality" and item.severity == "MAJOR" for item in result.corrections)


def test_superseded_evaluation_link_persists_without_mutating_original(session):
    freeze = _planning_freeze(session)
    original = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_implementation_artifact(files=["src/pilots/evaluation.py"]),
        evaluator_version="1.12-test",
    )

    replacement = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_planning_artifact(),
        evaluation_mode=EngineeringPlanEvaluationMode.PLANNING_ONLY,
        supersedes_evaluation_id=original.id,
        evaluator_version="1.13A-test",
    )

    original_row = session.get(EngineeringPlanEvaluationORM, original.id)
    replacement_row = session.get(EngineeringPlanEvaluationORM, replacement.id)
    assert original_row.evaluation_mode == EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION.value
    assert replacement_row.supersedes_evaluation_id == original.id
    assert replacement_row.evaluation_mode == EngineeringPlanEvaluationMode.PLANNING_ONLY.value


def test_release_gate_allows_canonical_planning_only_warning_for_missing_human_rubric(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _planning_evaluation_row(session, EngineeringPlanEvaluationResult.PASS)
    before = _benchmark(session, score=98.0, failed=0)
    after = _benchmark(session, score=98.0, failed=0)
    rubric = HumanRubricService(session).record_not_captured()

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=after.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
        human_rubric_id=rubric.id,
    )

    assert result.passed is True
    assert result.recommendation == AutonomyRecommendation.READY_FOR_LIMITED_WRITE_PILOT
    assert any("human rubric NOT_CAPTURED" in item for item in result.warnings)


def test_release_gate_blocks_non_canonical_planning_only_state(session):
    state = _repository_state(session, RepositoryStateClassification.NON_CANONICAL_DIRTY)
    evaluation = _planning_evaluation_row(session, EngineeringPlanEvaluationResult.PASS)
    before = _benchmark(session, score=98.0, failed=0)

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=before.id,
    )

    assert result.recommendation == AutonomyRecommendation.NOT_READY_FOR_WRITE_AUTONOMY
    assert any("non-canonical repository state" in item for item in result.blockers)


def test_release_gate_blocks_planning_only_benchmark_regression(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _planning_evaluation_row(session, EngineeringPlanEvaluationResult.PASS)
    before = _benchmark(session, score=98.0, failed=0)
    after = _benchmark(session, score=70.0, failed=1)

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=after.id,
    )

    assert result.benchmark_regression_status == BenchmarkRegressionStatus.REGRESSION
    assert result.recommendation == AutonomyRecommendation.NOT_READY_FOR_WRITE_AUTONOMY


def test_release_gate_blocks_planning_only_leakage_failure(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _planning_evaluation_row(
        session,
        EngineeringPlanEvaluationResult.PASS,
        implementation_artifact={"leakage_audit": {"result": "FAIL", "novelty_proven": False}},
    )
    before = _benchmark(session, score=98.0, failed=0)

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=before.id,
    )

    assert result.recommendation == AutonomyRecommendation.NOT_READY_FOR_WRITE_AUTONOMY
    assert "planning-only leakage/novelty audit failed" in result.blockers


def test_release_gate_blocks_insufficient_planning_only_evaluation(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _planning_evaluation_row(session, EngineeringPlanEvaluationResult.INSUFFICIENT_EVIDENCE)
    before = _benchmark(session, score=98.0, failed=0)

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=before.id,
    )

    assert result.recommendation == AutonomyRecommendation.NOT_READY_FOR_WRITE_AUTONOMY
    assert any("deterministic PLANNING_ONLY evaluation not passing" in item for item in result.blockers)


def test_no_evaluation_path_grants_bounded_write_autonomy(session):
    result = ReleaseGateService(session).evaluate(
        repository_state_id=None,
        deterministic_evaluation_id=None,
        benchmark_before_id=None,
        benchmark_after_id=None,
    )

    assert result.recommendation == AutonomyRecommendation.NOT_READY_FOR_WRITE_AUTONOMY
    assert result.recommendation != AutonomyRecommendation.READY_FOR_BOUNDED_WRITE_AUTONOMY


def test_human_rubric_not_captured_artifact_keeps_scores_explicit(session):
    rubric = HumanRubricService(session).record_not_captured()
    row = session.get(HumanRubricORM, rubric.id)

    assert row.status == HumanRubricCaptureStatus.NOT_CAPTURED.value
    assert all(value == "NOT_CAPTURED" for value in row.scores.values())


def _planning_freeze(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    _make_planning_ready(plan)
    return PlanFreezeService(session).freeze(plan_id=plan.id)


def _make_planning_ready(plan) -> None:
    plan.evidence_ids = ["LEVID_000001", "LEVID_000002"]
    plan.repository_snapshot_ids = ["LSNAP_000001"]
    plan.affected_files = [
        {"path": "src/pilots/evaluation.py", "status": "EXISTING_VERIFIED", "evidence_ids": ["LEVID_000001"]},
        {"path": "docs/evaluation/CANONICAL_PILOT_METHODOLOGY.md", "status": "EXISTING_VERIFIED", "evidence_ids": ["LEVID_000002"]},
    ]
    plan.steps[0]["affected_files"] = [
        "src/pilots/evaluation.py",
        "docs/evaluation/CANONICAL_PILOT_METHODOLOGY.md",
    ]
    plan.unknowns = ["Human usefulness score remains external."]
    plan.open_questions = ["Whether the operator wants docs in the first limited write pilot."]
    plan.assumptions = [{"statement": "No unsupported current-state claim.", "verified": False, "evidence_ids": ["LEVID_000001"]}]


def _planning_artifact(*, provenance_quality: str = "SUPPORTED") -> dict:
    return {
        "architecture": ["service", "pilot"],
        "components": ["service", "pilot"],
        "known_paths": [
            "src/pilots/evaluation.py",
            "docs/evaluation/CANONICAL_PILOT_METHODOLOGY.md",
        ],
        "schema_migration_required": False,
        "risk_level": "LOW",
        "required_authority_level": "L1",
        "provenance_quality": provenance_quality,
        "novelty_proven": True,
        "leakage_detected": False,
        "leakage_audit": {"result": "PASS", "novelty_proven": True, "leakage_detected": False},
    }


def _semantic_evidence(session, *, project_id: str, task_id: str, claim: str) -> EvidenceReferenceORM:
    row = EvidenceReferenceORM(
        id=next_id(session, "evidence"),
        project_id=project_id,
        repository_id="LREPO_TEST",
        snapshot_id="LSNAP_TEST",
        task_id=task_id,
        task_run_id=None,
        source_type="TEST",
        path="tests/integration/test_phase113a_planning_evaluation_gate.py",
        line_start=None,
        line_end=None,
        content_hash="a" * 64,
        snippet=claim,
        claim=claim,
        relevance_score=1.0,
        match_reasons=["semantic test evidence"],
        captured_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row


def _planning_evaluation_row(
    session,
    result: EngineeringPlanEvaluationResult,
    *,
    implementation_artifact: dict | None = None,
) -> EngineeringPlanEvaluationORM:
    row = _plan_evaluation(session, result)
    row.evaluation_mode = EngineeringPlanEvaluationMode.PLANNING_ONLY.value
    row.implementation_artifact = implementation_artifact or _planning_artifact()
    row.evaluator_version = "1.13A-test"
    row.evaluated_at = utc_now()
    session.flush()
    return row
