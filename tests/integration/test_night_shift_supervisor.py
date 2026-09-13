from __future__ import annotations

from pathlib import Path

import pytest

import lucius.integration.controlled_commit as controlled_commit_module
from lucius.domain.enums import (
    Actor,
    AllowedAction,
    AuthorityLevel,
    PersistentWorkflowState,
    ProjectStatus,
    ProjectType,
    RepositoryAccessMode,
    RepositoryAdapterType,
    TaskComplexity,
    TaskPriority,
    TaskStatus,
)
from lucius.integration.controlled_commit import ControlledCommitResult
from lucius.integration.controlled_commit import ControlledCommitService
from lucius.integration.service import CanonicalIntegrationResult
from lucius.integration.service import CanonicalIntegrationService
from lucius.night_shift import NightShiftConfig, NightShiftStatus, NightShiftSupervisor
from lucius.persistence.orm import (
    AuditEventORM,
    ControlledCommitORM,
    EngineeringPlanORM,
    PersistentWorkflowORM,
    PlanFreezeORM,
    ProjectORM,
    ProjectRepositoryAttachmentORM,
    RepositoryRegistrationORM,
    TaskContractORM,
    TaskORM,
    utc_now,
)
from lucius.repositories.git_mutation import GitMutationError
from lucius.runtime.schemas import (
    ExecutionRuntimeLoopResult,
    RuntimeExecutionOutcome,
    RuntimeLoopStatus,
    RuntimeTaskExecutionRecord,
)
from tests.conftest import make_git_repo, run_git


def test_night_shift_happy_path_integrates_commits_and_continues_to_next_cycle(session, tmp_path):
    first = _fixture(session, tmp_path, suffix="A")
    second = _fixture(session, tmp_path, suffix="B")
    runtime = _RuntimeDouble([
        _runtime_result(first.workflow_id, "A", completed=True),
        _runtime_result(second.workflow_id, "B", completed=True, provider_id="provider-b", project_id="PROJ_B"),
        ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE, stopped_reason="NO_ELIGIBLE_MULTI_PROJECT_WORK"),
    ])
    integration = _IntegrationDouble([CanonicalIntegrationResult(status="COMPLETED", workflow_id=first.workflow_id), CanonicalIntegrationResult(status="COMPLETED", workflow_id=second.workflow_id)])
    commit = _CommitDouble([ControlledCommitResult(status="COMPLETED", workflow_id=first.workflow_id, resulting_commit="a" * 40), ControlledCommitResult(status="COMPLETED", workflow_id=second.workflow_id, resulting_commit="b" * 40)])

    result = NightShiftSupervisor(session, runtime_service=runtime, integration_service=integration, commit_service=commit).run(
        NightShiftConfig(max_tasks=5, max_commits=5)
    )

    assert result.status == NightShiftStatus.CLEAN_TERMINATION
    assert result.stop_reason == "IDLE"
    assert result.tasks_completed == 2
    assert result.l1_integrations_succeeded == 2
    assert result.l2_commits_succeeded == 2
    assert result.resulting_commit_shas == ["a" * 40, "b" * 40]
    assert len(runtime.configs) == 3
    assert all(config.max_tasks == 1 and config.dispatcher_count == 1 for config in runtime.configs)
    assert session.query(AuditEventORM).filter(AuditEventORM.event_type == "NIGHT_SHIFT_CYCLE_CHECKPOINT").count() == 3


def test_night_shift_idle_exits_cleanly(session):
    runtime = _RuntimeDouble([ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE, stopped_reason="NO_ELIGIBLE_MULTI_PROJECT_WORK")])

    result = NightShiftSupervisor(session, runtime_service=runtime, integration_service=_IntegrationDouble([]), commit_service=_CommitDouble([])).run(NightShiftConfig())

    assert result.status == NightShiftStatus.CLEAN_TERMINATION
    assert result.stop_reason == "IDLE"
    assert result.tasks_selected == 0


def test_night_shift_max_tasks_bound(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    result = NightShiftSupervisor(
        session,
        runtime_service=_RuntimeDouble([_runtime_result(fixture.workflow_id, "ITEM")]),
        integration_service=_IntegrationDouble([CanonicalIntegrationResult(status="COMPLETED", workflow_id=fixture.workflow_id)]),
        commit_service=_CommitDouble([ControlledCommitResult(status="COMPLETED", workflow_id=fixture.workflow_id, resulting_commit="c" * 40)]),
    ).run(NightShiftConfig(max_tasks=1, max_commits=5))

    assert result.stop_reason == "MAX_TASKS_REACHED"
    assert result.max_limit_reached_reason == "MAX_TASKS_REACHED"


def test_night_shift_max_commits_bound(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    result = NightShiftSupervisor(
        session,
        runtime_service=_RuntimeDouble([_runtime_result(fixture.workflow_id, "ITEM"), _runtime_result(fixture.workflow_id, "ITEM2")]),
        integration_service=_IntegrationDouble([CanonicalIntegrationResult(status="COMPLETED", workflow_id=fixture.workflow_id)]),
        commit_service=_CommitDouble([ControlledCommitResult(status="COMPLETED", workflow_id=fixture.workflow_id, resulting_commit="d" * 40)]),
    ).run(NightShiftConfig(max_tasks=5, max_commits=1))

    assert result.stop_reason == "MAX_COMMITS_REACHED"
    assert len(result.cycles) == 1


def test_night_shift_max_wall_clock_bound_uses_clock(session):
    clock = _Clock([0.0, 11.0, 11.0])
    runtime = _RuntimeDouble([])

    result = NightShiftSupervisor(session, runtime_service=runtime, integration_service=_IntegrationDouble([]), commit_service=_CommitDouble([]), clock=clock).run(
        NightShiftConfig(max_wall_clock_seconds=10)
    )

    assert result.stop_reason == "MAX_WALL_CLOCK_REACHED"
    assert runtime.configs == []


def test_night_shift_task_local_failure_continues_to_independent_work(session, tmp_path):
    failed = _empirical_fixture(session, tmp_path, suffix="FAILED", expected="VALUE = 'FAILED'\n", apply_candidate_mutation=False)
    fixture = _fixture(session, tmp_path)
    runtime = _RuntimeDouble([
        _runtime_result(failed.workflow_id, "FAIL", completed=False, project_id=failed.project_id),
        _runtime_result(fixture.workflow_id, "OK", completed=True),
        ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE, stopped_reason="NO_ELIGIBLE_MULTI_PROJECT_WORK"),
    ])

    result = NightShiftSupervisor(
        session,
        runtime_service=runtime,
        integration_service=_IntegrationDouble([CanonicalIntegrationResult(status="COMPLETED", workflow_id=fixture.workflow_id)]),
        commit_service=_CommitDouble([ControlledCommitResult(status="COMPLETED", workflow_id=fixture.workflow_id, resulting_commit="e" * 40)]),
    ).run(NightShiftConfig(max_tasks=5, max_commits=5))

    assert result.status == NightShiftStatus.CLEAN_TERMINATION
    assert result.tasks_blocked == 1
    assert result.contained_task_local_failures == 1
    assert result.l1_integrations_attempted == 1
    assert run_git(failed.canonical, "rev-parse", "HEAD") == failed.baseline
    assert run_git(failed.canonical, "status", "--short") == ""


def test_night_shift_contained_failure_preserves_protected_untracked_state(session, tmp_path):
    failed = _empirical_fixture(session, tmp_path, suffix="FAILED", expected="VALUE = 'FAILED'\n", apply_candidate_mutation=False)
    protected = failed.canonical / "analysis" / "human.txt"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("human-owned\n", encoding="utf-8")
    protected_before = protected.read_bytes()
    fixture = _fixture(session, tmp_path, suffix="OK")
    runtime = _RuntimeDouble([
        _runtime_result(failed.workflow_id, "FAIL", completed=False, project_id=failed.project_id),
        _runtime_result(fixture.workflow_id, "OK", completed=True),
        ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE, stopped_reason="NO_ELIGIBLE_MULTI_PROJECT_WORK"),
    ])

    result = NightShiftSupervisor(
        session,
        runtime_service=runtime,
        integration_service=_IntegrationDouble([CanonicalIntegrationResult(status="COMPLETED", workflow_id=fixture.workflow_id)]),
        commit_service=_CommitDouble([ControlledCommitResult(status="COMPLETED", workflow_id=fixture.workflow_id, resulting_commit="1" * 40)]),
    ).run(NightShiftConfig(max_tasks=5, max_commits=5))

    assert result.status == NightShiftStatus.CLEAN_TERMINATION
    assert result.contained_task_local_failures == 1
    assert protected.read_bytes() == protected_before
    assert run_git(failed.canonical, "rev-parse", "HEAD") == failed.baseline
    assert run_git(failed.canonical, "status", "--short") == "?? analysis/"


def test_night_shift_uncontained_canonical_mutation_hard_stops(session, tmp_path):
    failed = _empirical_fixture(session, tmp_path, suffix="FAILED", expected="VALUE = 'FAILED'\n", apply_candidate_mutation=False)
    fixture = _fixture(session, tmp_path, suffix="OK")

    def mutate_canonical_before_failure():
        (failed.canonical / "surprise.txt").write_text("unsafe\n", encoding="utf-8")

    runtime = _RuntimeDouble(
        [
            _runtime_result(failed.workflow_id, "FAIL", completed=False, project_id=failed.project_id),
            _runtime_result(fixture.workflow_id, "OK", completed=True),
        ],
        side_effects=[mutate_canonical_before_failure, None],
    )

    result = NightShiftSupervisor(
        session,
        runtime_service=runtime,
        integration_service=_IntegrationDouble([]),
        commit_service=_CommitDouble([]),
    ).run(NightShiftConfig(max_tasks=5, max_commits=5))

    assert result.status == NightShiftStatus.HARD_STOP
    assert result.stop_reason == "FAILED_TASK_LEFT_UNAUTHORIZED_CANONICAL_CHANGES"
    assert result.contained_task_local_failures == 0
    assert result.hard_stop_failures == 1
    assert len(runtime.configs) == 1
    assert run_git(failed.canonical, "rev-parse", "HEAD") == failed.baseline
    assert run_git(failed.canonical, "status", "--short") == "?? surprise.txt"


def test_night_shift_runtime_escalation_hard_stops(session):
    result = NightShiftSupervisor(
        session,
        runtime_service=_RuntimeDouble([ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.ESCALATED, selected_tasks=1, escalations=1, task_records=[_record("LWORK_ESC", "ESC", RuntimeExecutionOutcome.ESCALATED)])]),
        integration_service=_IntegrationDouble([]),
        commit_service=_CommitDouble([]),
    ).run(NightShiftConfig())

    assert result.status == NightShiftStatus.HARD_STOP
    assert result.stop_reason == "RUNTIME_ESCALATION"


def test_night_shift_l1_integration_failure_prevents_commit(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    commit = _CommitDouble([])

    result = NightShiftSupervisor(
        session,
        runtime_service=_RuntimeDouble([_runtime_result(fixture.workflow_id, "ITEM")]),
        integration_service=_IntegrationDouble([CanonicalIntegrationResult(status="FAILED", workflow_id=fixture.workflow_id, reason="CANONICAL_HEAD_DRIFT")]),
        commit_service=commit,
    ).run(NightShiftConfig())

    assert result.status == NightShiftStatus.HARD_STOP
    assert result.stop_reason == "L1_INTEGRATION_FAILED"
    assert commit.requests == []


def test_night_shift_l1_rollback_uncertainty_hard_stops(session, tmp_path):
    fixture = _fixture(session, tmp_path)

    result = NightShiftSupervisor(
        session,
        runtime_service=_RuntimeDouble([_runtime_result(fixture.workflow_id, "ITEM")]),
        integration_service=_IntegrationDouble([CanonicalIntegrationResult(status="ROLLBACK_FAILED", workflow_id=fixture.workflow_id)]),
        commit_service=_CommitDouble([]),
    ).run(NightShiftConfig())

    assert result.status == NightShiftStatus.HARD_STOP
    assert result.stop_reason == "L1_ROLLBACK_UNCERTAIN"


def test_night_shift_controlled_commit_failure_hard_stops(session, tmp_path):
    fixture = _fixture(session, tmp_path)

    result = NightShiftSupervisor(
        session,
        runtime_service=_RuntimeDouble([_runtime_result(fixture.workflow_id, "ITEM")]),
        integration_service=_IntegrationDouble([CanonicalIntegrationResult(status="COMPLETED", workflow_id=fixture.workflow_id)]),
        commit_service=_CommitDouble([ControlledCommitResult(status="FAILED", workflow_id=fixture.workflow_id, reason="COMMIT_FAILED")]),
    ).run(NightShiftConfig())

    assert result.status == NightShiftStatus.HARD_STOP
    assert result.stop_reason == "CONTROLLED_COMMIT_FAILED"


def test_night_shift_manual_reconciliation_hard_stops(session, tmp_path):
    fixture = _fixture(session, tmp_path)

    result = NightShiftSupervisor(
        session,
        runtime_service=_RuntimeDouble([_runtime_result(fixture.workflow_id, "ITEM")]),
        integration_service=_IntegrationDouble([CanonicalIntegrationResult(status="COMPLETED", workflow_id=fixture.workflow_id)]),
        commit_service=_CommitDouble([ControlledCommitResult(status="POST_COMMIT_VERIFICATION_FAILED", workflow_id=fixture.workflow_id, manual_reconciliation_required=True)]),
    ).run(NightShiftConfig())

    assert result.status == NightShiftStatus.HARD_STOP
    assert result.stop_reason == "MANUAL_RECONCILIATION_REQUIRED"
    assert result.manual_reconciliation_required is True


def test_night_shift_already_committed_is_handled_idempotently(session, tmp_path):
    fixture = _fixture(session, tmp_path)

    result = NightShiftSupervisor(
        session,
        runtime_service=_RuntimeDouble([_runtime_result(fixture.workflow_id, "ITEM"), ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE)]),
        integration_service=_IntegrationDouble([CanonicalIntegrationResult(status="COMPLETED", workflow_id=fixture.workflow_id)]),
        commit_service=_CommitDouble([ControlledCommitResult(status="ALREADY_COMMITTED", workflow_id=fixture.workflow_id, resulting_commit="f" * 40)]),
    ).run(NightShiftConfig(max_tasks=5, max_commits=5))

    assert result.status == NightShiftStatus.CLEAN_TERMINATION
    assert result.l2_commits_already_committed == 1
    assert result.resulting_commit_shas == ["f" * 40]


def test_night_shift_without_create_commit_authority_does_not_bypass_l2(session, tmp_path):
    fixture = _fixture(session, tmp_path, create_commit_authorized=False)
    commit = _CommitDouble([])

    result = NightShiftSupervisor(
        session,
        runtime_service=_RuntimeDouble([_runtime_result(fixture.workflow_id, "ITEM")]),
        integration_service=_IntegrationDouble([CanonicalIntegrationResult(status="COMPLETED", workflow_id=fixture.workflow_id)]),
        commit_service=commit,
    ).run(NightShiftConfig())

    assert result.status == NightShiftStatus.HARD_STOP
    assert result.stop_reason == "CREATE_COMMIT_NOT_AUTHORIZED"
    assert commit.requests == []


def test_night_shift_canonical_baseline_drift_fails_closed(session, tmp_path):
    fixture = _fixture(session, tmp_path)

    result = NightShiftSupervisor(
        session,
        runtime_service=_RuntimeDouble([_runtime_result(fixture.workflow_id, "ITEM")]),
        integration_service=_IntegrationDouble([CanonicalIntegrationResult(status="FAILED", workflow_id=fixture.workflow_id, reason="CANONICAL_HEAD_DRIFT")]),
        commit_service=_CommitDouble([]),
    ).run(NightShiftConfig())

    assert result.status == NightShiftStatus.HARD_STOP
    assert result.cycles[-1].integration_reason == "CANONICAL_HEAD_DRIFT"


def test_night_shift_enforces_exact_single_dispatcher_configuration():
    with pytest.raises(ValueError, match="one logical dispatcher"):
        NightShiftConfig(dispatcher_count=2)


def test_night_shift_invokes_runtime_with_max_tasks_one(session):
    runtime = _RuntimeDouble([ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE)])

    NightShiftSupervisor(session, runtime_service=runtime, integration_service=_IntegrationDouble([]), commit_service=_CommitDouble([])).run(
        NightShiftConfig(max_tasks=3)
    )

    assert runtime.configs[0].max_tasks == 1
    assert runtime.configs[0].dispatcher_count == 1


def test_night_shift_records_no_remote_mutation(session):
    runtime = _RuntimeDouble([ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE)])

    result = NightShiftSupervisor(session, runtime_service=runtime, integration_service=_IntegrationDouble([]), commit_service=_CommitDouble([])).run(NightShiftConfig())

    assert result.unauthorized_remote_actions == []


def test_night_shift_empirical_two_cycle_real_l1_l2_happy_path(session, tmp_path):
    first = _empirical_fixture(session, tmp_path, suffix="A", expected="VALUE = 'A'\n", with_remote=True)
    second = _empirical_fixture(session, tmp_path, suffix="B", expected="VALUE = 'B'\n", with_remote=True)
    runtime = _RuntimeDouble(
        [
            _runtime_result(first.workflow_id, "ITEM_A", provider_id="provider-a", project_id=first.project_id),
            _runtime_result(second.workflow_id, "ITEM_B", provider_id="provider-b", project_id=second.project_id),
            ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE, stopped_reason="NO_ELIGIBLE_MULTI_PROJECT_WORK"),
        ]
    )

    result = NightShiftSupervisor(
        session,
        runtime_service=runtime,
        integration_service=CanonicalIntegrationService(session),
        commit_service=ControlledCommitService(session),
    ).run(NightShiftConfig(max_tasks=5, max_commits=5))

    assert result.status == NightShiftStatus.CLEAN_TERMINATION
    assert result.stop_reason == "IDLE"
    assert result.tasks_completed == 2
    assert result.l1_integrations_attempted == 2
    assert result.l1_integrations_succeeded == 2
    assert result.l2_commits_attempted == 2
    assert result.l2_commits_succeeded == 2
    assert len(result.resulting_commit_shas) == 2
    assert len(set(result.resulting_commit_shas)) == 2
    assert len(runtime.configs) == 3
    assert all(config.max_tasks == 1 and config.dispatcher_count == 1 for config in runtime.configs)

    for fixture, commit_sha in zip((first, second), result.resulting_commit_shas, strict=True):
        assert run_git(fixture.canonical, "rev-list", "--count", "HEAD") == str(fixture.baseline_rev_count + 1)
        assert run_git(fixture.canonical, "rev-parse", "HEAD") == commit_sha
        assert run_git(fixture.canonical, "rev-parse", "HEAD^") == fixture.baseline
        assert _commit_paths(fixture.canonical, commit_sha) == ["src/demo.py"]
        assert run_git(fixture.canonical, "status", "--short") == ""
        assert (fixture.canonical / "src" / "demo.py").read_text(encoding="utf-8") == fixture.expected
        assert session.get(PersistentWorkflowORM, fixture.workflow_id).workflow_state == PersistentWorkflowState.CLOSED.value
        record = session.query(ControlledCommitORM).filter_by(workflow_id=fixture.workflow_id).one()
        assert record.resulting_commit_sha == commit_sha
        assert record.actual_changed_paths == ["src/demo.py"]
        assert record.baseline_commit_sha == fixture.baseline
        assert run_git(fixture.remote, "rev-parse", "refs/heads/main") == fixture.remote_head


def test_night_shift_empirical_hard_stop_after_second_cycle_prevents_third_runtime_result(
    session,
    tmp_path,
    monkeypatch,
):
    first = _empirical_fixture(session, tmp_path, suffix="A", expected="VALUE = 'A'\n", with_remote=True)
    second = _empirical_fixture(session, tmp_path, suffix="B", expected="VALUE = 'B'\n", with_remote=True)
    third = _empirical_fixture(session, tmp_path, suffix="C", expected="VALUE = 'C'\n", with_remote=True)
    runtime = _RuntimeDouble(
        [
            _runtime_result(first.workflow_id, "ITEM_A", provider_id="provider-a", project_id=first.project_id),
            _runtime_result(second.workflow_id, "ITEM_B", provider_id="provider-b", project_id=second.project_id),
            _runtime_result(third.workflow_id, "ITEM_C", provider_id="provider-c", project_id=third.project_id),
        ]
    )
    real_commit_changed_paths = controlled_commit_module.commit_changed_paths

    def fail_second_post_commit(repo, commit_sha):
        if Path(repo).resolve() == second.canonical.resolve():
            raise GitMutationError("FORCED_POST_COMMIT_FAILURE", "forced post-commit failure")
        return real_commit_changed_paths(repo, commit_sha)

    monkeypatch.setattr("lucius.integration.controlled_commit.commit_changed_paths", fail_second_post_commit)

    result = NightShiftSupervisor(
        session,
        runtime_service=runtime,
        integration_service=CanonicalIntegrationService(session),
        commit_service=ControlledCommitService(session),
    ).run(NightShiftConfig(max_tasks=5, max_commits=5))

    assert result.status == NightShiftStatus.HARD_STOP
    assert result.stop_reason == "MANUAL_RECONCILIATION_REQUIRED"
    assert result.manual_reconciliation_required is True
    assert result.tasks_completed == 2
    assert result.l1_integrations_attempted == 2
    assert result.l1_integrations_succeeded == 2
    assert result.l2_commits_attempted == 2
    assert result.l2_commits_succeeded == 1
    assert result.l2_commits_failed == 1
    assert len(runtime.configs) == 2

    first_commit = result.resulting_commit_shas[0]
    assert run_git(first.canonical, "rev-parse", "HEAD") == first_commit
    assert run_git(first.canonical, "rev-parse", "HEAD^") == first.baseline
    assert run_git(first.canonical, "status", "--short") == ""
    assert session.query(ControlledCommitORM).filter_by(workflow_id=first.workflow_id).one().resulting_commit_sha == first_commit

    second_commit = result.cycles[-1].resulting_commit_sha
    assert second_commit is not None
    assert run_git(second.canonical, "rev-parse", "HEAD") == second_commit
    assert run_git(second.canonical, "rev-parse", "HEAD^") == second.baseline
    assert run_git(second.canonical, "status", "--short") == ""
    assert session.query(ControlledCommitORM).filter_by(workflow_id=second.workflow_id).count() == 0
    assert result.cycles[-1].manual_reconciliation_required is True

    assert run_git(third.canonical, "rev-parse", "HEAD") == third.baseline
    assert run_git(third.canonical, "status", "--short") == ""
    assert session.query(ControlledCommitORM).filter_by(workflow_id=third.workflow_id).count() == 0
    assert session.get(PersistentWorkflowORM, third.workflow_id).workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value

    for fixture in (first, second, third):
        assert run_git(fixture.remote, "rev-parse", "refs/heads/main") == fixture.remote_head


class _RuntimeDouble:
    def __init__(self, results, *, side_effects=None):
        self.results = list(results)
        self.side_effects = list(side_effects or [])
        self.configs = []

    def run(self, config):
        self.configs.append(config)
        if not self.results:
            raise AssertionError("Runtime double exhausted")
        if self.side_effects:
            side_effect = self.side_effects.pop(0)
            if side_effect is not None:
                side_effect()
        return self.results.pop(0)


class _IntegrationDouble:
    def __init__(self, results):
        self.results = list(results)
        self.requests = []

    def integrate(self, request):
        self.requests.append(request)
        if not self.results:
            raise AssertionError("Integration double exhausted")
        return self.results.pop(0)


class _CommitDouble:
    def __init__(self, results):
        self.results = list(results)
        self.requests = []

    def commit(self, request):
        self.requests.append(request)
        if not self.results:
            raise AssertionError("Commit double exhausted")
        return self.results.pop(0)


class _Clock:
    def __init__(self, values):
        self.values = list(values)

    def __call__(self):
        if not self.values:
            return 0.0
        return self.values.pop(0)


class _Fixture:
    def __init__(self, workflow_id: str):
        self.workflow_id = workflow_id


class _EmpiricalFixture:
    def __init__(
        self,
        *,
        project_id: str,
        workflow_id: str,
        repository_id: str,
        canonical: Path,
        candidate: Path,
        baseline: str,
        baseline_rev_count: int,
        expected: str,
        remote: Path,
        remote_head: str,
    ):
        self.project_id = project_id
        self.workflow_id = workflow_id
        self.repository_id = repository_id
        self.canonical = canonical
        self.candidate = candidate
        self.baseline = baseline
        self.baseline_rev_count = baseline_rev_count
        self.expected = expected
        self.remote = remote
        self.remote_head = remote_head


def _fixture(session, tmp_path: Path, *, suffix: str = "", create_commit_authorized: bool = True) -> _Fixture:
    suffix = suffix or "MAIN"
    project_id = f"LPROJ_NS_{suffix}"
    repo_id = f"LREPO_NS_{suffix}"
    task_id = f"LTASK_NS_{suffix}"
    contract_id = f"LCONTR_NS_{suffix}"
    plan_id = f"LPLAN_NS_{suffix}"
    freeze_id = f"LFREEZE_NS_{suffix}"
    workflow_id = f"LWORK_NS_{suffix}"
    canonical = tmp_path / f"canonical-{suffix}"
    candidate = tmp_path / f"candidate-{suffix}"
    canonical.mkdir()
    candidate.mkdir()
    project = ProjectORM(
        id=project_id,
        name=f"Night Shift {suffix}",
        slug=f"night-shift-{suffix.lower()}",
        project_type=ProjectType.DFG_INTERNAL.value,
        status=ProjectStatus.ACTIVE.value,
        documentation_policy={},
        default_authority_level=AuthorityLevel.L2.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    repository = RepositoryRegistrationORM(
        id=repo_id,
        project_id=project_id,
        name=f"Night Repo {suffix}",
        adapter_type=RepositoryAdapterType.LOCAL_GIT.value,
        location=str(canonical.resolve()),
        default_branch="main",
        access_mode=RepositoryAccessMode.CANONICAL_INTEGRATION.value,
        status="ACTIVE",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    task = TaskORM(
        id=task_id,
        project_id=project_id,
        title="Night Shift task",
        objective="Run bounded local mutation.",
        priority=TaskPriority.NORMAL.value,
        complexity=TaskComplexity.T1.value,
        authority_level=AuthorityLevel.L2.value if create_commit_authorized else AuthorityLevel.L1.value,
        status=TaskStatus.COMPLETE.value,
        created_by=Actor.LUCIUS.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    contract = TaskContractORM(
        id=contract_id,
        task_id=task_id,
        version=1,
        objective=task.objective,
        acceptance_criteria=[],
        constraints=[],
        repository_ids=[repo_id],
        allowed_actions=[AllowedAction.WRITE_SOURCE.value, *( [AllowedAction.CREATE_COMMIT.value] if create_commit_authorized else [] )],
        environment="DEVELOPMENT",
        authority_level=AuthorityLevel.L2.value if create_commit_authorized else AuthorityLevel.L1.value,
        dependencies=[],
        documentation_required=False,
        documentation_targets=[],
        stop_conditions=[],
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    plan = EngineeringPlanORM(
        id=plan_id,
        task_id=task_id,
        run_id=None,
        project_id=project_id,
        task_contract_id=contract_id,
        task_contract_version=1,
        version=1,
        status="FROZEN",
        summary="Night plan",
        objective=task.objective,
        risk_level="LOW",
        required_authority_level=AuthorityLevel.L1.value,
        repository_snapshot_ids=[],
        evidence_ids=[],
        memory_ids=[],
        model_execution_ids=[],
        assumptions=[],
        unknowns=[],
        open_questions=[],
        affected_components=[],
        affected_files=[{"path": "src/demo.py"}],
        steps=[],
        acceptance_coverage=[],
        deterministic_acceptance_checks=[{"type": "exact_file_content", "path": "src/demo.py", "expected_text": "VALUE = 1\n"}],
        test_strategy=[],
        documentation_requirements=[],
        rollback_considerations=[],
        orchestration_contract_required=False,
        orchestration_contract={},
        adversarial_probes=[],
        dependencies=[],
        risks=[],
        estimated_scope="small",
        confidence=1.0,
        validation_warnings=[],
        blockers=[],
        planner_version="test",
        created_by=Actor.LUCIUS.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    freeze = PlanFreezeORM(
        id=freeze_id,
        plan_id=plan_id,
        task_id=task_id,
        project_id=project_id,
        repository_state_id=None,
        repository_snapshot_ids=[],
        evidence_ids=[],
        commit_sha="1" * 40,
        planning_mode="CURRENT_STATE_PLANNING",
        evaluation_version="test",
        plan_payload={"affected_files": plan.affected_files, "deterministic_acceptance_checks": plan.deterministic_acceptance_checks},
        frozen_at=utc_now(),
        frozen_by=Actor.LUCIUS.value,
    )
    workflow = PersistentWorkflowORM(
        id=workflow_id,
        project_id=project_id,
        repository_id=repo_id,
        repository_snapshot_id=None,
        task_id=task_id,
        plan_id=plan_id,
        plan_freeze_id=freeze_id,
        objective=task.objective,
        expected_main_head="1" * 40,
        isolated_branch="night-shift",
        worktree_path=str(candidate.resolve()),
        workflow_state=PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value,
        authority_tier=AuthorityLevel.L1.value,
        task_backlog=[{"item_id": "ITEM", "state": "COMPLETED"}],
        dependency_graph={},
        active_task_id=None,
        completed_task_ids=["ITEM"],
        pending_task_ids=[],
        decisions=[],
        deviations=[],
        repair_counters={},
        targeted_test_evidence=[],
        full_test_status={},
        checkpoint_history=[],
        pending_human_approvals=[],
        resume_requirements=[],
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add_all([
        project,
        repository,
        ProjectRepositoryAttachmentORM(project_id=project_id, repository_id=repo_id, attached_by=Actor.LUCIUS.value),
        task,
        contract,
        plan,
        freeze,
        workflow,
    ])
    session.flush()
    return _Fixture(workflow_id)


def _empirical_fixture(
    session,
    tmp_path: Path,
    *,
    suffix: str,
    expected: str,
    with_remote: bool = True,
    apply_candidate_mutation: bool = True,
) -> _EmpiricalFixture:
    project_id = f"LPROJ_NS_EMP_{suffix}"
    repo_id = f"LREPO_NS_EMP_{suffix}"
    task_id = f"LTASK_NS_EMP_{suffix}"
    contract_id = f"LCONTR_NS_EMP_{suffix}"
    plan_id = f"LPLAN_NS_EMP_{suffix}"
    freeze_id = f"LFREEZE_NS_EMP_{suffix}"
    workflow_id = f"LWORK_NS_EMP_{suffix}"
    path = "src/demo.py"

    canonical = make_git_repo(tmp_path / f"empirical-canonical-{suffix}")
    remote = tmp_path / f"empirical-remote-{suffix}.git"
    if with_remote:
        run_git(tmp_path, "init", "--bare", str(remote))
        run_git(canonical, "remote", "add", "origin", str(remote))
        run_git(canonical, "push", "-u", "origin", "main")
    baseline = run_git(canonical, "rev-parse", "HEAD")
    baseline_rev_count = int(run_git(canonical, "rev-list", "--count", "HEAD"))
    remote_head = run_git(remote, "rev-parse", "refs/heads/main") if with_remote else baseline

    candidate = tmp_path / f"empirical-candidate-{suffix}"
    run_git(tmp_path, "clone", str(canonical), str(candidate))
    if apply_candidate_mutation:
        target = candidate / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(expected, encoding="utf-8")

    project = ProjectORM(
        id=project_id,
        name=f"Night Shift Empirical {suffix}",
        slug=f"night-shift-empirical-{suffix.lower()}",
        project_type=ProjectType.DFG_INTERNAL.value,
        status=ProjectStatus.ACTIVE.value,
        documentation_policy={},
        default_authority_level=AuthorityLevel.L2.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    repository = RepositoryRegistrationORM(
        id=repo_id,
        project_id=project_id,
        name=f"Night Empirical Repo {suffix}",
        adapter_type=RepositoryAdapterType.LOCAL_GIT.value,
        location=str(canonical.resolve()),
        default_branch="main",
        access_mode=RepositoryAccessMode.CANONICAL_INTEGRATION.value,
        status="ACTIVE",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    task = TaskORM(
        id=task_id,
        project_id=project_id,
        title=f"Night empirical task {suffix}",
        objective="Promote a bounded deterministic candidate through Night Shift.",
        priority=TaskPriority.NORMAL.value,
        complexity=TaskComplexity.T1.value,
        authority_level=AuthorityLevel.L2.value,
        status=TaskStatus.COMPLETE.value,
        created_by=Actor.LUCIUS.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    contract = TaskContractORM(
        id=contract_id,
        task_id=task_id,
        version=1,
        objective=task.objective,
        acceptance_criteria=[{"description": "Exact content", "status": "PENDING"}],
        constraints=["NO_PUSH", "NO_DEPLOY", "NO_MERGE"],
        repository_ids=[repo_id],
        allowed_actions=[AllowedAction.WRITE_SOURCE.value, AllowedAction.CREATE_COMMIT.value],
        environment="DEVELOPMENT",
        authority_level=AuthorityLevel.L2.value,
        dependencies=[],
        documentation_required=False,
        documentation_targets=[],
        stop_conditions=[],
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    affected_files = [{"path": path}]
    acceptance_checks = [{"type": "exact_file_content", "path": path, "expected_text": expected}]
    plan = EngineeringPlanORM(
        id=plan_id,
        task_id=task_id,
        run_id=None,
        project_id=project_id,
        task_contract_id=contract_id,
        task_contract_version=1,
        version=1,
        status="FROZEN",
        summary="Night empirical plan",
        objective=task.objective,
        risk_level="LOW",
        required_authority_level=AuthorityLevel.L1.value,
        repository_snapshot_ids=[],
        evidence_ids=[],
        memory_ids=[],
        model_execution_ids=[],
        assumptions=[],
        unknowns=[],
        open_questions=[],
        affected_components=[],
        affected_files=affected_files,
        steps=[],
        acceptance_coverage=[],
        deterministic_acceptance_checks=acceptance_checks,
        test_strategy=[],
        documentation_requirements=[],
        rollback_considerations=[],
        orchestration_contract_required=False,
        orchestration_contract={},
        adversarial_probes=[],
        dependencies=[],
        risks=[],
        estimated_scope="small",
        confidence=1.0,
        validation_warnings=[],
        blockers=[],
        planner_version="test",
        created_by=Actor.LUCIUS.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    freeze = PlanFreezeORM(
        id=freeze_id,
        plan_id=plan_id,
        task_id=task_id,
        project_id=project_id,
        repository_state_id=None,
        repository_snapshot_ids=[],
        evidence_ids=[],
        commit_sha=baseline,
        planning_mode="CURRENT_STATE_PLANNING",
        evaluation_version="test",
        plan_payload={
            "id": plan_id,
            "task_id": task_id,
            "project_id": project_id,
            "task_contract_id": contract_id,
            "task_contract_version": 1,
            "required_authority_level": plan.required_authority_level,
            "affected_files": affected_files,
            "deterministic_acceptance_checks": acceptance_checks,
        },
        frozen_at=utc_now(),
        frozen_by=Actor.LUCIUS.value,
    )
    workflow = PersistentWorkflowORM(
        id=workflow_id,
        project_id=project_id,
        repository_id=repo_id,
        repository_snapshot_id=None,
        task_id=task_id,
        plan_id=plan_id,
        plan_freeze_id=freeze_id,
        objective=task.objective,
        expected_main_head=baseline,
        isolated_branch=f"night-empirical-{suffix.lower()}",
        worktree_path=str(candidate.resolve()),
        workflow_state=PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value,
        authority_tier=AuthorityLevel.L1.value,
        task_backlog=[{"item_id": f"ITEM_{suffix}", "state": "COMPLETED"}],
        dependency_graph={},
        active_task_id=None,
        completed_task_ids=[f"ITEM_{suffix}"],
        pending_task_ids=[],
        decisions=[],
        deviations=[],
        repair_counters={},
        targeted_test_evidence=[],
        full_test_status={},
        checkpoint_history=[],
        pending_human_approvals=[],
        resume_requirements=[],
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add_all(
        [
            project,
            repository,
            ProjectRepositoryAttachmentORM(project_id=project_id, repository_id=repo_id, attached_by=Actor.LUCIUS.value),
            task,
            contract,
            plan,
            freeze,
            workflow,
        ]
    )
    session.flush()
    return _EmpiricalFixture(
        project_id=project_id,
        workflow_id=workflow_id,
        repository_id=repo_id,
        canonical=canonical,
        candidate=candidate,
        baseline=baseline,
        baseline_rev_count=baseline_rev_count,
        expected=expected,
        remote=remote,
        remote_head=remote_head,
    )


def _runtime_result(workflow_id: str, item_id: str, *, completed: bool = True, provider_id: str = "provider-a", project_id: str = "PROJ") -> ExecutionRuntimeLoopResult:
    outcome = RuntimeExecutionOutcome.COMPLETED if completed else RuntimeExecutionOutcome.FAILED
    return ExecutionRuntimeLoopResult(
        status=RuntimeLoopStatus.COMPLETED if completed else RuntimeLoopStatus.FAILED,
        selected_tasks=1,
        completed_tasks=1 if completed else 0,
        blocked_tasks=0 if completed else 1,
        provider_ids=[provider_id],
        project_ids=[project_id],
        task_records=[_record(workflow_id, item_id, outcome, provider_id=provider_id, project_id=project_id)],
    )


def _record(workflow_id: str, item_id: str, outcome: RuntimeExecutionOutcome, *, provider_id: str = "provider-a", project_id: str = "PROJ") -> RuntimeTaskExecutionRecord:
    return RuntimeTaskExecutionRecord(
        workflow_id=workflow_id,
        item_id=item_id,
        project_id=project_id,
        provider_id=provider_id,
        outcome=outcome,
    )


def _commit_paths(repo: Path, commit_sha: str) -> list[str]:
    output = run_git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit_sha)
    return sorted(line for line in output.splitlines() if line)
