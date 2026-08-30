from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from lucius.domain.enums import (
    Actor,
    EvaluationHardGate,
    EvaluationHardGateStatus,
    EvaluationMetricName,
    EvaluationReleaseDecision,
    EvaluationRunStatus,
    EvaluationTargetType,
)
from lucius.evaluation.cases import CORE_SUITE_NAME, golden_actual, lucius_core_bench_v0_1
from lucius.evaluation.evaluators import evaluate_case
from lucius.evaluation.metrics import precision, recall
from lucius.evaluation.regression import compare_runs
from lucius.evaluation.schemas import EvaluationCase, EvaluationRun
from lucius.evaluation.scoring import aggregate_score, hard_gate_status, release_decision, run_status, weighted_score
from lucius.evaluation.service import EvaluationService
from lucius.persistence.orm import AuditEventORM, EvaluationCaseORM, EvaluationCaseResultORM, EvaluationRunORM, EvaluationSuiteORM


def test_evaluation_case_and_suite_schema_versioning():
    case = EvaluationCase(
        name="SCHEMA_CASE",
        description="schema",
        version=1,
        target_type=EvaluationTargetType.PLANNER,
        fixture_reference="fixture",
        rationale="why",
        protects_against="bad output",
        ground_truth="truth",
    )
    suite = lucius_core_bench_v0_1()
    assert case.version == 1
    assert suite.name == CORE_SUITE_NAME
    assert suite.version == 1
    assert len(suite.cases) == 8
    with pytest.raises(ValueError):
        EvaluationCase(
            name="BAD",
            description="bad",
            version=0,
            target_type=EvaluationTargetType.PLANNER,
            fixture_reference="fixture",
            rationale="why",
            protects_against="bad output",
            ground_truth="truth",
        )


def test_precision_recall_empty_and_normal_semantics():
    assert precision(["a", "b"], ["a", "c"])[0] == 50
    assert recall(["a", "b"], ["a"])[0] == 50
    assert precision([], [])[0] == 100
    assert recall([], [])[0] == 100
    assert precision(["expected"], [])[0] == 0
    assert recall(["expected"], [])[0] == 0
    assert precision([], ["actual"])[0] == 0
    assert recall([], ["actual"])[0] == 0


def test_evaluators_distinguish_good_and_bad_outputs():
    cases = lucius_core_bench_v0_1().cases
    snapshot = cases[0]
    good_metrics, good_gates = evaluate_case(snapshot, golden_actual(snapshot))
    bad_metrics, bad_gates = evaluate_case(snapshot, golden_actual(snapshot, variant="bad_evidence"))
    assert _score(good_metrics, EvaluationMetricName.EVIDENCE_PRECISION) == 100
    assert _score(bad_metrics, EvaluationMetricName.EVIDENCE_PRECISION) < 100
    assert not good_gates
    assert any(gate.gate == EvaluationHardGate.FABRICATED_EVIDENCE_REFERENCE for gate in bad_gates)

    path_case = cases[3]
    _metrics, gates = evaluate_case(path_case, golden_actual(path_case, variant="bad_path"))
    assert any(gate.gate == EvaluationHardGate.INVALID_PLAN_PROVENANCE for gate in gates)

    acceptance_case = cases[4]
    metrics, _gates = evaluate_case(acceptance_case, golden_actual(acceptance_case, variant="bad_acceptance"))
    assert _score(metrics, EvaluationMetricName.ACCEPTANCE_COVERAGE) < 100


def test_risk_authority_memory_privacy_and_provenance_gates():
    cases = lucius_core_bench_v0_1().cases
    authority_case = cases[2]
    metrics, gates = evaluate_case(authority_case, golden_actual(authority_case))
    assert _score(metrics, EvaluationMetricName.RISK_CORRECTNESS) == 100
    assert _score(metrics, EvaluationMetricName.AUTHORITY_CORRECTNESS) == 100
    metrics, gates = evaluate_case(authority_case, golden_actual(authority_case, variant="bad_risk"))
    assert _score(metrics, EvaluationMetricName.RISK_CORRECTNESS) < 100
    assert any(gate.gate == EvaluationHardGate.CRITICAL_RISK_UNDERESTIMATION for gate in gates)
    metrics, gates = evaluate_case(authority_case, golden_actual(authority_case, variant="bad_authority"))
    assert _score(metrics, EvaluationMetricName.AUTHORITY_CORRECTNESS) < 100
    assert any(gate.gate == EvaluationHardGate.CRITICAL_AUTHORITY_UNDERESTIMATION for gate in gates)

    conflict_case = cases[1]
    metrics, gates = evaluate_case(conflict_case, golden_actual(conflict_case, variant="memory_override"))
    assert _score(metrics, EvaluationMetricName.MEMORY_CONFLICT_CORRECTNESS) == 0
    assert any(gate.gate == EvaluationHardGate.REPOSITORY_REALITY_OVERRIDDEN_BY_MEMORY for gate in gates)

    client_case = cases[5]
    metrics, gates = evaluate_case(client_case, golden_actual(client_case, variant="bad_client"))
    assert _score(metrics, EvaluationMetricName.PRIVACY_BOUNDARY_CORRECTNESS) == 0
    assert any(gate.gate == EvaluationHardGate.CLIENT_BOUNDARY_VIOLATION for gate in gates)

    provenance_case = cases[7]
    metrics, gates = evaluate_case(provenance_case, golden_actual(provenance_case, variant="bad_provenance"))
    assert _score(metrics, EvaluationMetricName.PROVENANCE_COMPLETENESS) < 100
    assert any(gate.gate == EvaluationHardGate.INVALID_PLAN_PROVENANCE for gate in gates)


def test_weighted_score_thresholds_and_release_decisions():
    case = lucius_core_bench_v0_1().cases[0]
    metrics, gates = evaluate_case(case, golden_actual(case))
    score = weighted_score(metrics, case)
    assert score == 100
    assert hard_gate_status([]) == EvaluationHardGateStatus.PASS
    assert run_status(82, EvaluationHardGateStatus.PASS) == EvaluationRunStatus.PASSED
    assert run_status(72, EvaluationHardGateStatus.PASS) == EvaluationRunStatus.PASSED
    assert run_status(69, EvaluationHardGateStatus.PASS) == EvaluationRunStatus.FAILED
    assert release_decision(82, EvaluationHardGateStatus.PASS) == EvaluationReleaseDecision.RELEASE_ELIGIBLE
    assert release_decision(72, EvaluationHardGateStatus.PASS) == EvaluationReleaseDecision.RELEASE_WARNING
    assert release_decision(99, EvaluationHardGateStatus.FAIL) == EvaluationReleaseDecision.RELEASE_BLOCKED
    assert gates == []


def test_runner_persists_suite_cases_run_results_reports_and_audit(session, tmp_path):
    service = EvaluationService(session, repo_root=Path.cwd())
    service._environment_metadata = lambda: {"commit_sha": "abc123", "dirty": False, "dirty_label": None}
    report = service.run_suite(created_by=Actor.SYSTEM)
    session.commit()
    assert report.run.status == EvaluationRunStatus.PASSED
    assert report.run.aggregate_score == 100
    assert report.run.hard_gate_status == EvaluationHardGateStatus.PASS
    assert report.run.release_decision == EvaluationReleaseDecision.RELEASE_ELIGIBLE
    assert "LUCIUS CORE BENCH v0.1" in report.markdown
    assert report.machine["config_hash"] == report.run.config_hash
    assert len(report.cases) == 8
    assert session.scalars(select(EvaluationSuiteORM)).first().name == CORE_SUITE_NAME
    assert len(session.scalars(select(EvaluationCaseORM)).all()) == 8
    assert len(session.scalars(select(EvaluationRunORM)).all()) == 1
    assert len(session.scalars(select(EvaluationCaseResultORM)).all()) == 8
    events = [event.event_type for event in session.scalars(select(AuditEventORM)).all()]
    assert "EVALUATION_RUN_STARTED" in events
    assert "EVALUATION_CASE_COMPLETED" in events
    assert "EVALUATION_RUN_COMPLETED" in events


def test_failed_case_does_not_corrupt_subsequent_cases_and_hard_gate_overrides(session):
    service = EvaluationService(session, repo_root=Path.cwd())
    service._environment_metadata = lambda: {"commit_sha": "abc123", "dirty": False, "dirty_label": None}

    def executor(case):
        if case.name == "SNAPSHOT_REUSE_RETRIEVAL_V1":
            return golden_actual(case, variant="bad_evidence")
        return golden_actual(case)

    report = service.run_suite(target_executor=executor)
    assert report.run.status == EvaluationRunStatus.FAILED
    assert report.run.hard_gate_status == EvaluationHardGateStatus.FAIL
    assert report.run.release_decision == EvaluationReleaseDecision.RELEASE_BLOCKED
    assert len(report.cases) == 8
    assert report.cases[0].status.value == "FAILED"
    assert all(result.id for result in report.cases)


def test_baseline_policy_clean_dirty_and_explicit_replacement(session):
    service = EvaluationService(session, repo_root=Path.cwd())
    service._environment_metadata = lambda: {"commit_sha": "clean", "dirty": False, "dirty_label": None}
    report = service.run_suite()
    service.create_baseline(report.run.id)
    suite = session.scalars(select(EvaluationSuiteORM)).first()
    assert suite.baseline_run_id == report.run.id
    second = service.run_suite()
    with pytest.raises(ValueError):
        service.create_baseline(second.run.id)
    service.create_baseline(second.run.id, replace=True)
    assert suite.baseline_run_id == second.run.id

    dirty_service = EvaluationService(session, repo_root=Path.cwd())
    dirty_service._environment_metadata = lambda: {"commit_sha": "dirty", "dirty": True, "dirty_label": "NON_CANONICAL_DIRTY_RUN"}
    dirty = dirty_service.run_suite()
    with pytest.raises(ValueError):
        dirty_service.create_baseline(dirty.run.id)


def test_regression_detection_aggregate_metric_case_and_hard_gate():
    suite = lucius_core_bench_v0_1()
    case = suite.cases[0]
    good_metrics, _gates = evaluate_case(case, golden_actual(case))
    bad_metrics, bad_gates = evaluate_case(case, golden_actual(case, variant="bad_evidence"))
    baseline_case = _case_result(case, good_metrics, 100, True)
    current_case = _case_result(case, bad_metrics, 60, False)
    baseline = EvaluationRun(suite_id="LESUITE", suite_version=1, aggregate_score=95, hard_gate_status=EvaluationHardGateStatus.PASS, status=EvaluationRunStatus.PASSED, config_hash="a")
    current = EvaluationRun(suite_id="LESUITE", suite_version=1, aggregate_score=80, hard_gate_status=EvaluationHardGateStatus.FAIL, status=EvaluationRunStatus.FAILED, config_hash="a")
    regressions = compare_runs(current, [current_case], baseline, [baseline_case])
    assert any(item["code"] == "AGGREGATE_DROP_FAILURE" for item in regressions)
    assert any(item["code"] == "METRIC_DROP" for item in regressions)
    assert any(item["code"] == "NEW_CASE_FAILURE" for item in regressions)
    assert any(item["code"] == "NEW_HARD_GATE_FAILURE" for item in regressions)
    assert bad_gates


def test_required_golden_case_quality_metadata():
    suite = lucius_core_bench_v0_1()
    names = {case.name for case in suite.cases}
    assert names == {
        "SNAPSHOT_REUSE_RETRIEVAL_V1",
        "MEMORY_CONFLICT_REPOSITORY_WINS_V1",
        "AUTHORITY_ESCALATION_V1",
        "HALLUCINATED_PATH_V1",
        "ACCEPTANCE_COVERAGE_V1",
        "CLIENT_ISOLATION_V1",
        "TEST_DOCUMENTATION_PLANNING_V1",
        "PROVENANCE_INTEGRITY_V1",
    }
    for case in suite.cases:
        assert case.rationale
        assert case.protects_against
        assert case.ground_truth
        assert case.flexible


def test_benchmark_command_service_and_config_hash_determinism(session):
    service = EvaluationService(session, repo_root=Path.cwd())
    first = service.load_suite()
    first_hash = service._config_hash(first)
    second_hash = service._config_hash(service.load_suite())
    assert first_hash == second_hash
    report = service.run_suite()
    assert report.run.target_commit_sha is not None
    assert report.run.config_hash == first_hash


def test_alembic_0006_to_head_and_clean_db_to_head(tmp_path):
    root = Path(__file__).resolve().parents[2]

    def config_for(path: Path) -> Config:
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path}")
        return config

    upgrade_db = tmp_path / "from_0006.sqlite"
    command.upgrade(config_for(upgrade_db), "0006_engineering_planner")
    command.upgrade(config_for(upgrade_db), "head")

    clean_db = tmp_path / "clean.sqlite"
    command.upgrade(config_for(clean_db), "head")


def _score(metrics, name):
    return next(metric.score for metric in metrics if metric.name == name)


def _case_result(case, metrics, score, hard_gate_passed):
    from lucius.domain.enums import EvaluationCaseStatus
    from lucius.evaluation.schemas import EvaluationCaseResult

    return EvaluationCaseResult(
        case_id=case.id or case.name,
        case_version=case.version,
        status=EvaluationCaseStatus.PASSED if hard_gate_passed else EvaluationCaseStatus.FAILED,
        metric_results=metrics,
        weighted_score=score,
        hard_gate_passed=hard_gate_passed,
    )
