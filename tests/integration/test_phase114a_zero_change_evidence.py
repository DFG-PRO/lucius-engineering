from __future__ import annotations

from lucius.domain.enums import (
    AutonomyRecommendation,
    EngineeringPlanEvaluationMode,
    EngineeringPlanEvaluationResult,
    ImplementationChangeEvidenceState,
    MetricApplicability,
    RepositoryIntegrityResult,
    RepositoryStateClassification,
)
from lucius.persistence.orm import EngineeringPlanEvaluationORM, utc_now
from lucius.pilots.evaluation import EngineeringPlanEvaluationService
from lucius.pilots.freeze import PlanFreezeService
from lucius.pilots.release import ReleaseGateService
from lucius.pilots.rubric import HumanRubricService

from tests.integration.test_phase112_pilot_infrastructure import (
    _benchmark,
    _plan_evaluation,
    _project_task_plan,
    _repository_state,
)


def test_no_migration_expected_and_none_implemented_is_no_change_confirmed(session):
    result = _evaluate(session, _artifact(migration_state=ImplementationChangeEvidenceState.NO_CHANGE_CONFIRMED))

    dimension = _dimension(result, "schema_migration_awareness")

    assert result.result == EngineeringPlanEvaluationResult.PASS
    assert dimension.status == "PASS"
    assert dimension.score == 100.0
    assert f"state={ImplementationChangeEvidenceState.NO_CHANGE_CONFIRMED.value}" in dimension.actual


def test_no_dependency_expected_and_none_added_is_no_change_confirmed(session):
    result = _evaluate(session, _artifact(dependency_state=ImplementationChangeEvidenceState.NO_CHANGE_CONFIRMED))

    dimension = _dimension(result, "dependency_awareness")

    assert result.result == EngineeringPlanEvaluationResult.PASS
    assert dimension.status == "PASS"
    assert dimension.score == 100.0
    assert f"state={ImplementationChangeEvidenceState.NO_CHANGE_CONFIRMED.value}" in dimension.actual


def test_no_migration_expected_but_migration_added_is_unexpected_change(session):
    result = _evaluate(
        session,
        _artifact(
            migration_state=ImplementationChangeEvidenceState.UNEXPECTED_CHANGE_DETECTED,
            migration_files=["alembic/versions/0010_unexpected.py"],
        ),
    )

    dimension = _dimension(result, "schema_migration_awareness")

    assert result.result == EngineeringPlanEvaluationResult.FAIL
    assert dimension.status == "FAIL"
    assert "alembic/versions/0010_unexpected.py" in dimension.unnecessary
    assert any(correction.dimension == "schema_migration_awareness" for correction in result.corrections)


def test_no_dependency_expected_but_dependency_added_is_unexpected_change(session):
    result = _evaluate(
        session,
        _artifact(
            dependency_state=ImplementationChangeEvidenceState.UNEXPECTED_CHANGE_DETECTED,
            dependency_files=["pyproject.toml"],
        ),
    )

    dimension = _dimension(result, "dependency_awareness")

    assert result.result == EngineeringPlanEvaluationResult.FAIL
    assert dimension.status == "FAIL"
    assert "pyproject.toml" in dimension.unnecessary
    assert any(correction.dimension == "dependency_awareness" for correction in result.corrections)


def test_expected_dependency_change_absent_is_failure_state(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    _make_plan_ready(plan)
    plan.dependencies = ["new-provider-sdk"]
    freeze = PlanFreezeService(session).freeze(plan_id=plan.id)

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_artifact(dependency_state=ImplementationChangeEvidenceState.EXPECTED_CHANGE_MISSING),
        evaluation_mode=EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION,
    )

    dimension = _dimension(result, "dependency_awareness")

    assert result.result == EngineeringPlanEvaluationResult.FAIL
    assert dimension.status == "FAIL"
    assert "expected dependency change" in dimension.missing


def test_genuinely_unavailable_zero_change_evidence_remains_not_captured(session):
    result = _evaluate(session, _artifact(include_change_evidence=False))

    assert result.result == EngineeringPlanEvaluationResult.INSUFFICIENT_EVIDENCE
    assert _dimension(result, "schema_migration_awareness").applicability == MetricApplicability.NOT_CAPTURED
    assert _dimension(result, "dependency_awareness").applicability == MetricApplicability.NOT_CAPTURED


def test_zero_change_confirmed_dimensions_contribute_to_aggregate(session):
    result = _evaluate(session, _artifact())

    assert _dimension(result, "schema_migration_awareness").score == 100.0
    assert _dimension(result, "dependency_awareness").score == 100.0
    assert result.aggregate_score is not None
    assert result.aggregate_score >= 90.0


def test_verified_zero_change_does_not_produce_insufficient_evidence(session):
    result = _evaluate(session, _artifact())

    assert result.result == EngineeringPlanEvaluationResult.PASS
    assert all(item.applicability != MetricApplicability.NOT_CAPTURED for item in result.dimensions)


def test_plan_vs_implementation_passes_with_changed_and_confirmed_unchanged_evidence(session):
    result = _evaluate(session, _artifact(files=["src/pilots/evaluation.py"]))

    assert result.result == EngineeringPlanEvaluationResult.PASS
    assert _dimension(result, "file_path_prediction").status == "PASS"
    assert _dimension(result, "schema_migration_awareness").status == "PASS"
    assert _dimension(result, "dependency_awareness").status == "PASS"


def test_autonomy_gate_can_process_corrected_limited_write_evidence(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _post_write_evaluation_row(session, EngineeringPlanEvaluationResult.PASS)
    before = _benchmark(session, score=100.0, failed=0)
    after = _benchmark(session, score=100.0, failed=0)
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
    assert result.recommendation == AutonomyRecommendation.READY_FOR_ANOTHER_LIMITED_WRITE_PILOT


def test_unrestricted_autonomy_remains_impossible_for_corrected_write_pilot(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _post_write_evaluation_row(session, EngineeringPlanEvaluationResult.PASS)
    before = _benchmark(session, score=100.0, failed=0)

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=before.id,
    )

    assert result.recommendation != AutonomyRecommendation.READY_FOR_BOUNDED_WRITE_AUTONOMY
    assert result.recommendation != AutonomyRecommendation.READY_FOR_BOUNDED_ENGINEERING_PILOT


def _evaluate(session, artifact: dict):
    _project, _task, _contract, plan = _project_task_plan(session)
    _make_plan_ready(plan)
    freeze = PlanFreezeService(session).freeze(plan_id=plan.id)
    return EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=artifact,
        evaluation_mode=EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION,
    )


def _make_plan_ready(plan) -> None:
    plan.summary = "Implement service and pilot files; no migration required and no dependency change required."
    plan.evidence_ids = ["LEVID_000001"]
    plan.unknowns = ["Output shape may be adjusted to local CLI conventions."]
    plan.open_questions = ["Should public IDs also be accepted?"]
    plan.assumptions = [{"statement": "No migration required.", "verified": False, "evidence_ids": ["LEVID_000001"]}]
    plan.dependencies = []
    plan.affected_components = ["service", "pilot"]
    plan.affected_files = [
        {"path": "src/pilots/evaluation.py", "status": "EXISTING_VERIFIED", "evidence_ids": ["LEVID_000001"]},
    ]
    plan.steps = [
        {
            "step_id": "STEP-1",
            "title": "service pilot",
            "description": "Update service and pilot behavior.",
            "affected_files": ["src/pilots/evaluation.py"],
        }
    ]


def _artifact(
    *,
    files: list[str] | None = None,
    migration_state: ImplementationChangeEvidenceState = ImplementationChangeEvidenceState.NO_CHANGE_CONFIRMED,
    dependency_state: ImplementationChangeEvidenceState = ImplementationChangeEvidenceState.NO_CHANGE_CONFIRMED,
    migration_files: list[str] | None = None,
    dependency_files: list[str] | None = None,
    include_change_evidence: bool = True,
) -> dict:
    artifact = {
        "architecture": ["service", "pilot"],
        "components": ["service", "pilot"],
        "files": files or ["src/pilots/evaluation.py"],
        "known_paths": ["src/pilots/evaluation.py", "docs/evaluation/CANONICAL_PILOT_METHODOLOGY.md"],
        "tests": ["tests/integration/test_phase114a_zero_change_evidence.py"],
        "documentation": ["docs/evaluation/CANONICAL_PILOT_METHODOLOGY.md"],
        "risk_level": "LOW",
        "required_authority_level": "L1",
        "material_work": ["service"],
    }
    if include_change_evidence:
        artifact["change_evidence"] = {
            "migrations": {
                "state": migration_state.value,
                "changed_files": migration_files or [],
                "verified_paths": ["alembic/versions"],
            },
            "dependencies": {
                "state": dependency_state.value,
                "changed_files": dependency_files or [],
                "verified_paths": ["pyproject.toml"],
            },
        }
    return artifact


def _dimension(result, name: str):
    return next(item for item in result.dimensions if item.name == name)


def _post_write_evaluation_row(session, result: EngineeringPlanEvaluationResult) -> EngineeringPlanEvaluationORM:
    row = _plan_evaluation(session, result)
    row.evaluation_mode = EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION.value
    row.implementation_artifact = {"pilot_stage": "LIMITED_WRITE_PILOT"}
    row.evaluator_version = "1.14A-test"
    row.evaluated_at = utc_now()
    session.flush()
    return row
