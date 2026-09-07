from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from lucius.domain.enums import (
    Actor,
    AutonomyRecommendation,
    BenchmarkRegressionStatus,
    BenchmarkRunStatus,
    EngineeringPlanEvaluationResult,
    EngineeringPlanStatus,
    HumanRubricCaptureStatus,
    PilotLearningStatus,
    PlanningEvidenceMode,
    RepositoryIntegrityResult,
    RepositoryStateClassification,
)
from lucius.persistence.orm import (
    BenchmarkResultORM,
    EngineeringPlanEvaluationORM,
    EngineeringPlanORM,
    HumanRubricORM,
    PilotEvaluationRecordORM,
    ProjectRepositoryAttachmentORM,
    ProjectORM,
    RepositoryStateObservationORM,
    TaskContractORM,
    TaskORM,
    utc_now,
)
from lucius.persistence.repositories import RepositoryRegistrationService, RepositorySnapshotService, next_id
from lucius.pilots.benchmark import BenchmarkRunnerService
from lucius.pilots.evaluation import EngineeringPlanEvaluationService
from lucius.pilots.freeze import PlanFreezeService
from lucius.pilots.historical import HistoricalPlanningContextService
from lucius.pilots.learning import PilotLearningService
from lucius.pilots.records import PilotRecordService
from lucius.pilots.release import ReleaseGateService
from lucius.pilots.repository_state import RepositoryStateError, RepositoryStateService
from lucius.pilots.rubric import HumanRubricService
from lucius.repositories.errors import LuciusRepositoryError
from lucius.repositories.local_git import LocalGitRepositoryAdapter
from lucius.repositories.schemas import WorkspaceContext

from tests.conftest import run_git


def test_repository_state_gate_classifies_clean_dirty_and_refuses_strict_dirty(session, git_repo: Path, workspace):
    service = RepositoryStateService(session)

    clean = service.inspect(repository_path=git_repo, workspace_context=workspace)

    assert clean.classification == RepositoryStateClassification.CANONICAL_CLEAN
    assert clean.canonical is True
    assert clean.head_commit == run_git(git_repo, "rev-parse", "HEAD")
    assert clean.manifest_hash is not None
    assert session.get(RepositoryStateObservationORM, clean.id) is not None

    (git_repo / "README.md").write_text("# Test Repo\n\nchanged\n", encoding="utf-8")
    dirty = service.inspect(repository_path=git_repo, workspace_context=workspace)

    assert dirty.classification == RepositoryStateClassification.NON_CANONICAL_DIRTY
    assert "README.md" in dirty.tracked_modifications + dirty.staged_modifications
    with pytest.raises(RepositoryStateError):
        service.inspect(repository_path=git_repo, workspace_context=workspace, strict_canonical=True)

    (git_repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")
    untracked = service.inspect(repository_path=git_repo, workspace_context=workspace)

    assert untracked.classification == RepositoryStateClassification.NON_CANONICAL_UNTRACKED_STATE
    assert "scratch.txt" in untracked.untracked_files


def test_repository_head_and_worktree_mutation_detection(session, git_repo: Path, workspace):
    service = RepositoryStateService(session)
    before = service.inspect(repository_path=git_repo, workspace_context=workspace)

    (git_repo / "README.md").write_text("# Test Repo\n\nexternal mutation\n", encoding="utf-8")
    dirty_after = service.inspect(repository_path=git_repo, workspace_context=workspace)

    assert service.compare_integrity(before, dirty_after) == RepositoryStateClassification.NON_CANONICAL_EXTERNAL_MUTATION

    run_git(git_repo, "add", "README.md")
    run_git(git_repo, "commit", "-m", "external change")
    head_after = service.inspect(repository_path=git_repo, workspace_context=workspace)

    assert service.compare_integrity(before, head_after) == RepositoryStateClassification.NON_CANONICAL_HEAD_CHANGED


def test_historical_planning_uses_selected_commit_tag_and_snapshot_without_future_files(session, git_repo: Path, workspace):
    before_commit = run_git(git_repo, "rev-parse", "HEAD")
    run_git(git_repo, "tag", "phase112-before")
    before_readme = LocalGitRepositoryAdapter(
        git_repo,
        workspace.model_copy(
            update={
                "repository_ref": before_commit,
                "planning_mode": PlanningEvidenceMode.HISTORICAL_STATE_PLANNING.value,
            }
        ),
    ).read_file("README.md")

    (git_repo / "README.md").write_text("# Test Repo\n\nfuture content\n", encoding="utf-8")
    (git_repo / "future.py").write_text("LOOKAHEAD = True\n", encoding="utf-8")
    run_git(git_repo, "add", "README.md", "future.py")
    run_git(git_repo, "commit", "-m", "future implementation")

    commit_context = workspace.model_copy(
        update={"repository_ref": before_commit, "planning_mode": PlanningEvidenceMode.HISTORICAL_STATE_PLANNING.value}
    )
    commit_adapter = LocalGitRepositoryAdapter(git_repo, commit_context)
    tag_adapter = LocalGitRepositoryAdapter(
        git_repo,
        workspace.model_copy(
            update={
                "repository_ref": "phase112-before",
                "planning_mode": PlanningEvidenceMode.HISTORICAL_STATE_PLANNING.value,
            }
        ),
    )

    assert commit_adapter.read_file("README.md") == before_readme
    assert "future.py" not in commit_adapter.list_files()
    assert "future.py" not in tag_adapter.list_files()
    with pytest.raises(LuciusRepositoryError):
        commit_adapter.read_file("future.py")

    project = _project(session)
    registration = RepositoryRegistrationService(session).register_local_git_repository(
        project_id=project.id,
        name="historical repo",
        location=git_repo,
        workspace_context=workspace,
    ).registration
    _attach(session, project.id, registration.id)
    snapshot = RepositorySnapshotService(session).inspect_repository(
        repository_id=registration.id,
        workspace_context=commit_context,
    )
    from_snapshot = HistoricalPlanningContextService(session).historical_context_for_snapshot(
        repository_id=registration.id,
        snapshot_id=snapshot.snapshot_id,
        base_context=workspace,
    )

    assert from_snapshot.repository_ref == before_commit
    assert from_snapshot.planning_mode == PlanningEvidenceMode.HISTORICAL_STATE_PLANNING.value
    assert "future.py" not in LocalGitRepositoryAdapter(git_repo, from_snapshot).list_files()


def test_plan_freeze_preserves_original_plan_payload(session):
    project, task, _contract, plan = _project_task_plan(session, summary="original plan")

    freeze = PlanFreezeService(session).freeze(
        plan_id=plan.id,
        repository_state_id=None,
        planning_mode=PlanningEvidenceMode.HISTORICAL_STATE_PLANNING,
    )
    plan.summary = "mutated after freeze"
    session.flush()
    again = PlanFreezeService(session).freeze(plan_id=plan.id)

    assert freeze.id == again.id
    assert freeze.task_id == task.id
    assert freeze.project_id == project.id
    assert freeze.plan_payload["summary"] == "original plan"
    assert again.plan_payload["summary"] == "original plan"


def test_deterministic_plan_evaluation_persists_metrics_and_hallucinated_path_correction(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    freeze = PlanFreezeService(session).freeze(plan_id=plan.id)

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_implementation_artifact(files=["src/pilots/evaluation.py"]),
    )

    persisted = session.get(EngineeringPlanEvaluationORM, result.id)
    hallucinated = next(item for item in result.dimensions if item.name == "hallucinated_files_paths")

    assert persisted is not None
    assert result.result == EngineeringPlanEvaluationResult.FAIL
    assert hallucinated.status == "FAIL"
    assert "ghost.py" in hallucinated.missing
    assert any(item.severity == "CRITICAL" and item.dimension == "hallucinated_files_paths" for item in result.corrections)


def test_missing_human_assessment_is_not_captured_and_explicit_rubric_persists(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    freeze = PlanFreezeService(session).freeze(plan_id=plan.id)
    service = HumanRubricService(session)

    missing = service.record_not_captured(plan_freeze_id=freeze.id)
    captured = service.capture(
        plan_freeze_id=freeze.id,
        evaluator="phase112-human",
        scores={
            "repository_understanding": 5,
            "architectural_correctness": 4,
            "completeness": 4,
            "usefulness": 5,
            "implementation_realism": 4,
            "risk_awareness": 5,
            "provenance_quality": 5,
            "hallucination_control": 4,
        },
        comments="Useful and evidence-bound.",
    )

    assert missing.status == HumanRubricCaptureStatus.NOT_CAPTURED
    assert all(value == "NOT_CAPTURED" for value in missing.scores.values())
    assert captured.status == HumanRubricCaptureStatus.CAPTURED
    assert captured.scores["usefulness"] == 5
    assert session.get(HumanRubricORM, captured.id).comments == "Useful and evidence-bound."


def test_core_benchmark_runner_persists_formal_result(session):
    result = BenchmarkRunnerService(session, repo_root=Path.cwd()).run_core_benchmark()

    persisted = session.get(BenchmarkResultORM, result.id)

    assert persisted is not None
    assert result.benchmark_version == "LUCIUS_CORE_BENCH_V0_1"
    assert result.total_cases > 0
    assert result.passed + result.failed + result.skipped == result.total_cases
    assert result.artifact_result_id == result.evaluation_run_id
    assert "aggregate_score" in result.deterministic_metrics


def test_benchmark_comparison_regression_and_pilot_record_association(session):
    clean_state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _plan_evaluation(session, EngineeringPlanEvaluationResult.PASS)
    before = _benchmark(session, score=95.0, failed=0)
    after = _benchmark(session, score=95.0, failed=0)

    record = PilotRecordService(session).create(
        repository_state_id=clean_state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=after.id,
    )
    regression = _benchmark(session, score=80.0, failed=1)
    comparison = BenchmarkRunnerService(session).compare(before.id, regression.id)

    assert record.benchmark_before_id == before.id
    assert record.benchmark_after_id == after.id
    assert session.get(PilotEvaluationRecordORM, record.id) is not None
    assert comparison.status == BenchmarkRegressionStatus.REGRESSION
    assert "Failed case count increased." in comparison.reasons


def test_learning_candidate_persistence_and_status_transition(session):
    service = PilotLearningService(session)

    candidate = service.identify(statement="Canonical clean-repository rerun required.", evidence_refs=["LPILOT_TEST"])
    accepted = service.transition(candidate.id, status=PilotLearningStatus.ACCEPTED, evidence_refs=["LFREEZE_TEST"])

    assert candidate.id.startswith("LPLEARN_")
    assert accepted.status == PilotLearningStatus.ACCEPTED
    assert accepted.evidence_refs == ["LPILOT_TEST", "LFREEZE_TEST"]


def test_release_gate_rejects_non_canonical_missing_benchmark_and_regression(session):
    evaluation = _plan_evaluation(session, EngineeringPlanEvaluationResult.PASS)
    dirty_state = _repository_state(session, RepositoryStateClassification.NON_CANONICAL_DIRTY)
    clean_state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    before = _benchmark(session, score=90.0, failed=0)
    after = _benchmark(session, score=70.0, failed=1)
    gate = ReleaseGateService(session)

    non_canonical = gate.evaluate(
        repository_state_id=dirty_state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=before.id,
    )
    missing_benchmark = gate.evaluate(
        repository_state_id=clean_state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=None,
        benchmark_after_id=None,
    )
    regression = gate.evaluate(
        repository_state_id=clean_state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=after.id,
    )

    assert non_canonical.recommendation == AutonomyRecommendation.NOT_READY_FOR_WRITE_AUTONOMY
    assert any("non-canonical repository state" in item for item in non_canonical.blockers)
    assert missing_benchmark.benchmark_regression_status == BenchmarkRegressionStatus.NOT_RUN
    assert any("missing required benchmark result" in item for item in missing_benchmark.blockers)
    assert regression.benchmark_regression_status == BenchmarkRegressionStatus.REGRESSION
    assert regression.recommendation == AutonomyRecommendation.NOT_READY_FOR_WRITE_AUTONOMY


def test_release_gate_valid_evidence_reaches_limited_pilot_only(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _plan_evaluation(session, EngineeringPlanEvaluationResult.PASS)
    before = _benchmark(session, score=98.0, failed=0)
    after = _benchmark(session, score=98.0, failed=0)
    rubric = HumanRubricService(session).capture(
        evaluator="phase112-human",
        scores={
            "repository_understanding": 5,
            "architectural_correctness": 5,
            "completeness": 5,
            "usefulness": 5,
            "implementation_realism": 5,
            "risk_awareness": 5,
            "provenance_quality": 5,
            "hallucination_control": 5,
        },
    )

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
    assert result.recommendation != AutonomyRecommendation.READY_FOR_BOUNDED_WRITE_AUTONOMY


def test_alembic_0007_to_head_and_clean_db_to_head(tmp_path: Path):
    for sidecar in Path("alembic").glob("**/._*"):
        sidecar.unlink()

    def config_for(path: Path) -> Config:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
        return cfg

    upgrade_db = tmp_path / "from_0007.sqlite"
    command.upgrade(config_for(upgrade_db), "0007_evaluation_harness")
    command.upgrade(config_for(upgrade_db), "head")

    clean_db = tmp_path / "clean.sqlite"
    command.upgrade(config_for(clean_db), "head")


def _project(session) -> ProjectORM:
    row = ProjectORM(
        id=next_id(session, "project"),
        name=f"Phase 1.12 {next_id(session, 'audit')}",
        slug=f"phase-112-{next_id(session, 'audit')}",
        organization=None,
        project_type="DFG_INTERNAL",
        status="ACTIVE",
        description=None,
        workspace_scope=None,
        documentation_policy={},
        default_authority_level="L0",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row


def _attach(session, project_id: str, repository_id: str) -> None:
    session.add(ProjectRepositoryAttachmentORM(project_id=project_id, repository_id=repository_id, attached_by=Actor.SYSTEM.value))
    session.flush()


def _project_task_plan(
    session,
    *,
    summary: str = "Implement service, migration, tests, and docs.",
) -> tuple[ProjectORM, TaskORM, TaskContractORM, EngineeringPlanORM]:
    project = _project(session)
    task = TaskORM(
        id=next_id(session, "task"),
        project_id=project.id,
        title="Evaluate pilot infrastructure",
        objective="Implement deterministic pilot evaluation infrastructure.",
        priority="HIGH",
        complexity="M",
        authority_level="L1",
        status="READY",
        created_by=Actor.SYSTEM.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    contract = TaskContractORM(
        id=next_id(session, "task_contract"),
        task_id=task.id,
        version=1,
        objective=task.objective,
        acceptance_criteria=[{"id": "AC-1", "description": "Evaluation persists."}],
        constraints=[],
        repository_ids=[],
        allowed_actions=[],
        allowed_tools=[],
        environment="LOCAL",
        authority_level="L1",
        dependencies=[],
        documentation_required=True,
        documentation_targets=["docs/evaluation/CANONICAL_PILOT_METHODOLOGY.md"],
        stop_conditions=[],
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    plan = EngineeringPlanORM(
        id=next_id(session, "engineering_plan"),
        task_id=task.id,
        run_id=None,
        project_id=project.id,
        task_contract_id=contract.id,
        task_contract_version=contract.version,
        version=1,
        status=EngineeringPlanStatus.PROPOSED.value,
        summary=summary,
        objective=task.objective,
        risk_level="LOW",
        required_authority_level="L1",
        repository_snapshot_ids=[],
        evidence_ids=[],
        memory_ids=[],
        model_execution_ids=[],
        assumptions=[{"statement": "No unsupported current-state claim.", "verified": False, "evidence_ids": []}],
        unknowns=[],
        open_questions=[],
        affected_components=["service", "pilot"],
        affected_files=[
            {"path": "src/pilots/evaluation.py", "status": "EXISTING_VERIFIED", "evidence_ids": []},
            {"path": "ghost.py", "status": "LIKELY_EXISTING", "evidence_ids": []},
        ],
        steps=[
            {
                "step_id": "STEP-1",
                "title": "service migration test docs",
                "description": "Update service migration pytest regression docs and sqlalchemy dependency handling.",
                "affected_files": ["src/pilots/evaluation.py", "ghost.py"],
            }
        ],
        acceptance_coverage=[],
        test_strategy=[{"kind": "INTEGRATION", "description": "Run pytest regression tests."}],
        documentation_requirements=[{"target": "docs/evaluation/CANONICAL_PILOT_METHODOLOGY.md"}],
        rollback_considerations=[],
        dependencies=["sqlalchemy"],
        risks=[],
        estimated_scope="medium",
        confidence=0.8,
        validation_warnings=[],
        blockers=[],
        planner_version="1.12-test",
        created_by=Actor.SYSTEM.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add_all([task, contract, plan])
    session.flush()
    return project, task, contract, plan


def _implementation_artifact(*, files: list[str]) -> dict:
    return {
        "architecture": ["service"],
        "components": ["service", "pilot"],
        "files": files,
        "known_paths": ["src/pilots/evaluation.py", "docs/evaluation/CANONICAL_PILOT_METHODOLOGY.md"],
        "migrations": ["alembic/versions/0008_pilot_evaluation_infrastructure.py"],
        "tests": ["tests/integration/test_phase112_pilot_infrastructure.py"],
        "documentation": ["docs/evaluation/CANONICAL_PILOT_METHODOLOGY.md"],
        "dependencies": ["sqlalchemy"],
        "risk_level": "LOW",
        "required_authority_level": "L1",
        "material_work": ["service"],
    }


def _repository_state(session, classification: RepositoryStateClassification) -> RepositoryStateObservationORM:
    row = RepositoryStateObservationORM(
        id=next_id(session, "repository_state"),
        repository_id=None,
        repository_path="/tmp/phase112",
        branch="main",
        head_commit="a" * 40,
        remote=None,
        classification=classification.value,
        tracked_modifications=[],
        staged_modifications=[],
        untracked_files=[],
        manifest_hash="b" * 64,
        details={},
        observed_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row


def _plan_evaluation(session, result: EngineeringPlanEvaluationResult) -> EngineeringPlanEvaluationORM:
    _project, _task, _contract, plan = _project_task_plan(session)
    freeze = PlanFreezeService(session).freeze(plan_id=plan.id)
    row = EngineeringPlanEvaluationORM(
        id=next_id(session, "plan_evaluation"),
        plan_freeze_id=freeze.id,
        result=result.value,
        aggregate_score=95.0 if result == EngineeringPlanEvaluationResult.PASS else 50.0,
        dimensions=[],
        corrections=[],
        implementation_artifact={},
        evaluator_version="1.12-test",
        evaluated_at=utc_now(),
        evaluated_by=Actor.SYSTEM.value,
    )
    session.add(row)
    session.flush()
    return row


def _benchmark(session, *, score: float, failed: int) -> BenchmarkResultORM:
    total = 3
    row = BenchmarkResultORM(
        id=next_id(session, "benchmark"),
        suite_name="LUCIUS_CORE_BENCH_V0_1",
        suite_version=1,
        benchmark_version="LUCIUS_CORE_BENCH_V0_1",
        git_head="c" * 40,
        target_dirty=False,
        status=BenchmarkRunStatus.PASSED.value if failed == 0 else BenchmarkRunStatus.FAILED.value,
        total_cases=total,
        passed=total - failed,
        failed=failed,
        skipped=0,
        duration_ms=10,
        deterministic_metrics={"aggregate_score": score, "hard_gate_status": "PASS" if failed == 0 else "FAIL"},
        artifact_result_id=None,
        evaluation_run_id=None,
        environment_metadata={"test": True},
        captured_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row
