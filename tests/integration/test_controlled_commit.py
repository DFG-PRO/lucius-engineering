from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

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
import lucius.integration.controlled_commit as controlled_commit_module
from lucius.integration.controlled_commit import ControlledCommitRequest, ControlledCommitService
from lucius.persistence.orm import (
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
from lucius.repositories.git_mutation import GitMutationError, changed_paths, staged_paths
from tests.conftest import make_git_repo, run_git


def test_successful_controlled_local_commit_creates_one_verified_record(session, tmp_path):
    fixture = _fixture(session, tmp_path)

    before_count = _rev_count(fixture.repo)
    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "COMPLETED"
    assert _rev_count(fixture.repo) == before_count + 1
    assert run_git(fixture.repo, "rev-parse", "HEAD") == result.resulting_commit
    assert run_git(fixture.repo, "rev-parse", "HEAD^") == fixture.baseline
    assert _commit_paths(fixture.repo, result.resulting_commit) == ["src/lucius/demo.py"]
    assert run_git(fixture.repo, "status", "--short") == ""
    record = session.get(ControlledCommitORM, result.record_id)
    assert record is not None
    assert record.workflow_id == fixture.workflow_id
    assert record.project_id == "LPROJ_COMMIT"
    assert record.repository_id == fixture.repository_id
    assert record.task_id == "LTASK_COMMIT"
    assert record.plan_freeze_id == "LFREEZE_COMMIT"
    assert record.baseline_commit_sha == fixture.baseline
    assert record.parent_commit_sha == fixture.baseline
    assert record.resulting_commit_sha == result.resulting_commit
    assert record.actual_changed_paths == ["src/lucius/demo.py"]
    assert record.authority_level == AuthorityLevel.L2.value
    assert record.status == "COMPLETED"
    assert record.result == "SUCCESS"


def test_controlled_commit_accepts_non_exact_deterministic_checks(session, tmp_path):
    fixture = _fixture(
        session,
        tmp_path,
        expected="VALUE = 1\n# safe marker\n",
        acceptance_checks=[
            {"type": "file_exists", "path": "src/lucius/demo.py"},
            {"type": "file_contains", "path": "src/lucius/demo.py", "expected_text": "safe marker"},
            {"type": "file_not_contains", "path": "src/lucius/demo.py", "expected_text": "forbidden marker"},
        ],
    )

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "COMPLETED"
    assert result.committed_paths == ["src/lucius/demo.py"]
    record = session.get(ControlledCommitORM, result.record_id)
    assert [item["type"] for item in record.deterministic_acceptance_evidence] == [
        "deterministic_acceptance_file_exists",
        "deterministic_acceptance_file_contains",
        "deterministic_acceptance_file_not_contains",
    ]


def test_controlled_commit_canonical_command_failure_prevents_l2(session, tmp_path):
    fixture = _fixture(
        session,
        tmp_path,
        with_pytest_command_runtime=True,
        acceptance_checks=[
            {"type": "exact_file_content", "path": "src/lucius/demo.py", "expected_text": "VALUE = 1\n"},
            {
                "type": "command_succeeds",
                "argv": [".venv/bin/python", "-m", "pytest", "tests/missing_profitability.py", "-q"],
                "timeout_seconds": 10,
            },
        ],
    )

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "FAILED"
    assert "command_succeeds failed with exit code" in result.reason
    assert run_git(fixture.repo, "rev-parse", "HEAD") == fixture.baseline
    assert run_git(fixture.repo, "status", "--short") == "?? src/"



def test_controlled_commit_preserves_protected_untracked_state_and_commits_only_authorized_path(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    protected = fixture.repo / "analysis" / "human.txt"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("human-owned\n", encoding="utf-8")
    protected_hash = __import__("hashlib").sha256(protected.read_bytes()).hexdigest()

    request = _request(fixture)
    request.protected_untracked_hashes["analysis/human.txt"] = protected_hash

    result = ControlledCommitService(session).commit(request)

    assert result.status == "COMPLETED"
    assert protected.read_text(encoding="utf-8") == "human-owned\n"
    assert _commit_paths(fixture.repo, result.resulting_commit) == ["src/lucius/demo.py"]
    assert run_git(fixture.repo, "status", "--short") == "?? analysis/"


def test_controlled_commit_rejects_modified_protected_untracked_state(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    protected = fixture.repo / "analysis" / "human.txt"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("original\n", encoding="utf-8")
    protected_hash = __import__("hashlib").sha256(protected.read_bytes()).hexdigest()

    request = _request(fixture)
    request.protected_untracked_hashes["analysis/human.txt"] = protected_hash
    protected.write_text("modified\n", encoding="utf-8")

    result = ControlledCommitService(session).commit(request)

    assert result.status == "FAILED"
    assert "Protected untracked paths changed" in result.reason
    assert run_git(fixture.repo, "rev-parse", "HEAD") == fixture.baseline


def test_controlled_commit_rejects_missing_protected_untracked_state(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    protected = fixture.repo / "analysis" / "human.txt"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("original\n", encoding="utf-8")
    protected_hash = __import__("hashlib").sha256(protected.read_bytes()).hexdigest()

    request = _request(fixture)
    request.protected_untracked_hashes["analysis/human.txt"] = protected_hash
    protected.unlink()

    result = ControlledCommitService(session).commit(request)

    assert result.status == "FAILED"
    assert "Protected untracked paths disappeared" in result.reason
    assert run_git(fixture.repo, "rev-parse", "HEAD") == fixture.baseline


def test_controlled_commit_rejects_new_untracked_state_not_in_protected_snapshot(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    protected = fixture.repo / "analysis" / "human.txt"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("original\n", encoding="utf-8")
    protected_hash = __import__("hashlib").sha256(protected.read_bytes()).hexdigest()

    request = _request(fixture)
    request.protected_untracked_hashes["analysis/human.txt"] = protected_hash

    unexpected = fixture.repo / "output" / "late.txt"
    unexpected.parent.mkdir(parents=True, exist_ok=True)
    unexpected.write_text("late arrival\n", encoding="utf-8")

    result = ControlledCommitService(session).commit(request)

    assert result.status == "FAILED"
    assert result.reason == "CANONICAL_HAS_UNAUTHORIZED_CHANGES"
    assert run_git(fixture.repo, "rev-parse", "HEAD") == fixture.baseline


def test_controlled_commit_rejects_protected_path_overlapping_authorized_mutation(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    target = fixture.repo / "src" / "lucius" / "demo.py"
    target_hash = __import__("hashlib").sha256(target.read_bytes()).hexdigest()

    request = _request(fixture)
    request.protected_untracked_hashes["src/lucius/demo.py"] = target_hash

    result = ControlledCommitService(session).commit(request)

    assert result.status == "FAILED"
    assert result.reason == "PROTECTED_UNTRACKED_PATH_AUTHORIZED_FOR_MUTATION"
    assert run_git(fixture.repo, "rev-parse", "HEAD") == fixture.baseline

def test_controlled_commit_rejects_l1_request(session, tmp_path):
    fixture = _fixture(session, tmp_path)

    result = ControlledCommitService(session).commit(_request(fixture, authority_level=AuthorityLevel.L1))

    assert result.status == "FAILED"
    assert result.reason == "L2_AUTHORITY_REQUIRED"
    assert run_git(fixture.repo, "rev-parse", "HEAD") == fixture.baseline
    assert run_git(fixture.repo, "status", "--short") == "?? src/"


def test_controlled_commit_requires_create_commit_contract_action(session, tmp_path):
    fixture = _fixture(session, tmp_path, allowed_actions=[AllowedAction.WRITE_SOURCE.value])

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "CREATE_COMMIT_NOT_AUTHORIZED"
    assert run_git(fixture.repo, "rev-parse", "HEAD") == fixture.baseline


def test_controlled_commit_requires_closed_workflow(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    session.get(PersistentWorkflowORM, fixture.workflow_id).workflow_state = PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value
    session.flush()

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "WORKFLOW_NOT_CLOSED"


def test_controlled_commit_rejects_head_drift_before_staging(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    (fixture.repo / "README.md").write_text("# Drift\n", encoding="utf-8")
    run_git(fixture.repo, "add", "README.md")
    run_git(fixture.repo, "commit", "-m", "drift")

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "CANONICAL_HEAD_DRIFT"


def test_controlled_commit_rejects_clean_canonical_tree(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    (fixture.repo / "src" / "lucius" / "demo.py").unlink()

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "CANONICAL_HAS_NO_CHANGES"


def test_controlled_commit_rejects_unauthorized_dirty_path(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    (fixture.repo / "other.txt").write_text("unexpected\n", encoding="utf-8")

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "CANONICAL_HAS_UNAUTHORIZED_CHANGES"
    assert run_git(fixture.repo, "rev-parse", "HEAD") == fixture.baseline


def test_controlled_commit_rejects_preexisting_staged_path(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    (fixture.repo / "other.txt").write_text("unexpected\n", encoding="utf-8")
    run_git(fixture.repo, "add", "other.txt")

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "PREEXISTING_STAGED_CHANGES"
    assert staged_paths(fixture.repo) == ["other.txt"]


def test_controlled_commit_rejects_deterministic_acceptance_failure_before_commit(session, tmp_path):
    fixture = _fixture(session, tmp_path, expected="VALUE = 1\n")
    (fixture.repo / "src" / "lucius" / "demo.py").write_text("VALUE = 2\n", encoding="utf-8")

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "FAILED"
    assert "Exact file content acceptance failed" in result.reason
    assert run_git(fixture.repo, "rev-parse", "HEAD") == fixture.baseline


def test_controlled_commit_stages_only_actual_manifest_paths(session, tmp_path):
    fixture = _fixture(
        session,
        tmp_path,
        extra_affected_files=[{"path": "src/lucius/unchanged.py"}],
        extra_acceptance_checks=[
            {"type": "exact_file_content", "path": "src/lucius/unchanged.py", "expected_text": "UNCHANGED = True\n"}
        ],
    )
    extra = fixture.repo / "src" / "lucius" / "unchanged.py"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("UNCHANGED = True\n", encoding="utf-8")
    run_git(fixture.repo, "add", "src/lucius/unchanged.py")
    run_git(fixture.repo, "commit", "-m", "add unchanged baseline")
    fixture.baseline = run_git(fixture.repo, "rev-parse", "HEAD")
    session.get(PlanFreezeORM, "LFREEZE_COMMIT").commit_sha = fixture.baseline
    session.get(PersistentWorkflowORM, fixture.workflow_id).expected_main_head = fixture.baseline
    (fixture.repo / "src" / "lucius" / "demo.py").write_text("VALUE = 1\n", encoding="utf-8")
    session.flush()

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "COMPLETED"
    assert result.committed_paths == ["src/lucius/demo.py"]
    assert _commit_paths(fixture.repo, result.resulting_commit) == ["src/lucius/demo.py"]


def test_controlled_commit_precommit_failure_unstages_only_service_paths_and_preserves_worktree(session, tmp_path, monkeypatch):
    fixture = _fixture(session, tmp_path)

    def fail_staged_hash(repo, path):
        raise GitMutationError("FORCED_STAGED_HASH_FAILURE", "forced staged hash failure")

    monkeypatch.setattr("lucius.integration.controlled_commit.staged_file_sha256", fail_staged_hash)

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "FAILED"
    assert result.unstaged_paths == ["src/lucius/demo.py"]
    assert staged_paths(fixture.repo) == []
    assert changed_paths(fixture.repo) == ["src/lucius/demo.py"]
    assert (fixture.repo / "src" / "lucius" / "demo.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert run_git(fixture.repo, "rev-parse", "HEAD") == fixture.baseline


def test_controlled_commit_can_commit_new_untracked_authorized_file(session, tmp_path):
    fixture = _fixture(session, tmp_path, path="src/lucius/new_file.py", expected="CREATED = True\n")

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "COMPLETED"
    assert result.committed_paths == ["src/lucius/new_file.py"]
    assert run_git(fixture.repo, "status", "--short") == ""


def test_controlled_commit_rejects_malformed_frozen_path(session, tmp_path):
    fixture = _fixture(session, tmp_path, affected_path="../escape.txt", check_path="../escape.txt")

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "INVALID_FROZEN_AFFECTED_FILES"


def test_controlled_commit_duplicate_invocation_does_not_create_second_commit(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    service = ControlledCommitService(session)
    first = service.commit(_request(fixture))
    rev_count = _rev_count(fixture.repo)

    second = service.commit(_request(fixture))

    assert first.status == "COMPLETED"
    assert second.status == "ALREADY_COMMITTED"
    assert second.resulting_commit == first.resulting_commit
    assert _rev_count(fixture.repo) == rev_count


def test_controlled_commit_missing_persistence_with_prior_local_commit_requires_reconciliation(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    run_git(fixture.repo, "add", "src/lucius/demo.py")
    run_git(fixture.repo, "commit", "-m", "lucius: complete LTASK_COMMIT")

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "CONTROLLED_COMMIT_RECONCILIATION_REQUIRED"


def test_controlled_commit_post_commit_verification_failure_preserves_commit_and_records_no_success(session, tmp_path, monkeypatch):
    fixture = _fixture(session, tmp_path)

    def fail_commit_path_inspection(repo, commit_sha):
        raise GitMutationError("FORCED_POST_COMMIT_FAILURE", "forced post-commit failure")

    monkeypatch.setattr("lucius.integration.controlled_commit.commit_changed_paths", fail_commit_path_inspection)

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "POST_COMMIT_VERIFICATION_FAILED"
    assert result.manual_reconciliation_required is True
    assert run_git(fixture.repo, "rev-parse", "HEAD") == result.resulting_commit
    assert run_git(fixture.repo, "rev-parse", "HEAD^") == fixture.baseline
    assert session.query(ControlledCommitORM).count() == 0


def test_controlled_commit_detects_dirty_path_drift_before_staging(session, tmp_path, monkeypatch):
    fixture = _fixture(session, tmp_path)
    real_changed_paths = controlled_commit_module.changed_paths
    calls = {"count": 0}

    def drifting_changed_paths(repo):
        calls["count"] += 1
        if calls["count"] == 1:
            return real_changed_paths(repo)
        (fixture.repo / "surprise.txt").write_text("late\n", encoding="utf-8")
        return real_changed_paths(repo)

    monkeypatch.setattr("lucius.integration.controlled_commit.changed_paths", drifting_changed_paths)

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "CANONICAL_CHANGED_PATHS_DRIFT"
    assert staged_paths(fixture.repo) == []
    assert run_git(fixture.repo, "rev-parse", "HEAD") == fixture.baseline


def test_controlled_commit_does_not_push_or_mutate_remote(session, tmp_path):
    fixture = _fixture(session, tmp_path, with_remote=True)
    remote_head = run_git(fixture.remote, "rev-parse", "refs/heads/main")

    result = ControlledCommitService(session).commit(_request(fixture))

    assert result.status == "COMPLETED"
    assert run_git(fixture.remote, "rev-parse", "refs/heads/main") == remote_head
    assert run_git(fixture.repo, "rev-parse", "HEAD") != remote_head


def test_alembic_0012_to_head_and_clean_head_include_controlled_commits(tmp_path: Path):
    for sidecar in Path("alembic").glob("**/._*"):
        sidecar.unlink()

    def config_for(path: Path) -> Config:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
        return cfg

    upgrade_db = tmp_path / "from_0012.sqlite"
    command.upgrade(config_for(upgrade_db), "0012_deterministic_acceptance_checks")
    command.upgrade(config_for(upgrade_db), "head")

    clean_db = tmp_path / "clean.sqlite"
    command.upgrade(config_for(clean_db), "head")

    for db in (upgrade_db, clean_db):
        inspector = inspect(create_engine(f"sqlite:///{db}"))
        assert "controlled_commits" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("controlled_commits")}
        assert {
            "workflow_id",
            "plan_freeze_id",
            "baseline_commit_sha",
            "parent_commit_sha",
            "resulting_commit_sha",
            "manifest_hash",
            "deterministic_acceptance_evidence",
        } <= columns


class _Fixture:
    def __init__(self, repo: Path, baseline: str, workflow_id: str, repository_id: str, remote: Path | None = None):
        self.repo = repo
        self.baseline = baseline
        self.workflow_id = workflow_id
        self.repository_id = repository_id
        self.remote = remote


def _fixture(
    session,
    tmp_path,
    *,
    path: str = "src/lucius/demo.py",
    expected: str = "VALUE = 1\n",
    affected_path: str | None = None,
    check_path: str | None = None,
    allowed_actions: list[str] | None = None,
    extra_affected_files: list[dict[str, str]] | None = None,
    extra_acceptance_checks: list[dict[str, str]] | None = None,
    acceptance_checks: list[dict[str, str]] | None = None,
    with_remote: bool = False,
    with_pytest_command_runtime: bool = False,
) -> _Fixture:
    repo = make_git_repo(tmp_path / "canonical")
    if with_pytest_command_runtime:
        _add_tracked_python_runtime(repo)
    remote = None
    if with_remote:
        remote = tmp_path / "remote.git"
        run_git(tmp_path, "init", "--bare", str(remote))
        run_git(repo, "remote", "add", "origin", str(remote))
        run_git(repo, "push", "-u", "origin", "main")
    baseline = run_git(repo, "rev-parse", "HEAD")
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected, encoding="utf-8")

    project = ProjectORM(
        id="LPROJ_COMMIT",
        name="Commit Project",
        slug="commit-project",
        project_type=ProjectType.DFG_INTERNAL.value,
        status=ProjectStatus.ACTIVE.value,
        documentation_policy={},
        default_authority_level=AuthorityLevel.L2.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    repository = RepositoryRegistrationORM(
        id="LREPO_COMMIT",
        project_id=project.id,
        name="Commit Repository",
        adapter_type=RepositoryAdapterType.LOCAL_GIT.value,
        location=str(repo.resolve()),
        default_branch="main",
        access_mode=RepositoryAccessMode.CANONICAL_INTEGRATION.value,
        status="ACTIVE",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    task = TaskORM(
        id="LTASK_COMMIT",
        project_id=project.id,
        title="Commit verified integration",
        objective="Create a controlled local commit for verified integration output.",
        priority=TaskPriority.NORMAL.value,
        complexity=TaskComplexity.T1.value,
        authority_level=AuthorityLevel.L2.value,
        status=TaskStatus.COMPLETE.value,
        created_by=Actor.LUCIUS.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    contract = TaskContractORM(
        id="LCONTR_COMMIT",
        task_id=task.id,
        version=1,
        objective=task.objective,
        acceptance_criteria=[{"description": "Commit exact content", "status": "PENDING"}],
        constraints=["NO_PUSH", "NO_DEPLOY", "NO_MERGE"],
        repository_ids=[repository.id],
        allowed_actions=allowed_actions or [AllowedAction.WRITE_SOURCE.value, AllowedAction.CREATE_COMMIT.value],
        environment="DEVELOPMENT",
        authority_level=AuthorityLevel.L2.value,
        dependencies=[],
        documentation_required=False,
        documentation_targets=[],
        stop_conditions=[],
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    affected_files = [{"path": affected_path or path}, *(extra_affected_files or [])]
    checks = acceptance_checks or [
        {"type": "exact_file_content", "path": check_path or path, "expected_text": expected},
        *(extra_acceptance_checks or []),
    ]
    plan = EngineeringPlanORM(
        id="LPLAN_COMMIT",
        task_id=task.id,
        run_id=None,
        project_id=project.id,
        task_contract_id=contract.id,
        task_contract_version=contract.version,
        version=1,
        status="FROZEN",
        summary="Commit plan",
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
        deterministic_acceptance_checks=checks,
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
        id="LFREEZE_COMMIT",
        plan_id=plan.id,
        task_id=task.id,
        project_id=project.id,
        repository_state_id=None,
        repository_snapshot_ids=[],
        evidence_ids=[],
        commit_sha=baseline,
        planning_mode="CURRENT_STATE_PLANNING",
        evaluation_version="test",
        plan_payload={
            "id": plan.id,
            "task_id": task.id,
            "project_id": project.id,
            "task_contract_id": contract.id,
            "task_contract_version": contract.version,
            "required_authority_level": plan.required_authority_level,
            "affected_files": affected_files,
            "deterministic_acceptance_checks": checks,
        },
        frozen_at=utc_now(),
        frozen_by=Actor.LUCIUS.value,
    )
    workflow = PersistentWorkflowORM(
        id="LWORK_COMMIT",
        project_id=project.id,
        repository_id=repository.id,
        repository_snapshot_id=None,
        task_id=task.id,
        plan_id=plan.id,
        plan_freeze_id=freeze.id,
        objective=task.objective,
        expected_main_head=baseline,
        isolated_branch="integration-candidate",
        worktree_path=str(repo.resolve()),
        workflow_state=PersistentWorkflowState.CLOSED.value,
        authority_tier=AuthorityLevel.L1.value,
        task_backlog=[{"item_id": "ITEM_COMMIT", "state": "COMPLETED"}],
        dependency_graph={},
        active_task_id=None,
        completed_task_ids=["ITEM_COMMIT"],
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
            ProjectRepositoryAttachmentORM(project_id=project.id, repository_id=repository.id, attached_by=Actor.LUCIUS.value),
            task,
            contract,
            plan,
            freeze,
            workflow,
        ]
    )
    session.flush()
    return _Fixture(repo, baseline, workflow.id, repository.id, remote)


def _add_tracked_python_runtime(repo: Path) -> None:
    os.symlink(Path.cwd() / ".venv", repo / ".venv")
    run_git(repo, "add", ".venv")
    run_git(repo, "commit", "-m", "add pytest runtime")


def _request(fixture: _Fixture, *, authority_level: AuthorityLevel = AuthorityLevel.L2) -> ControlledCommitRequest:
    return ControlledCommitRequest(
        workflow_id=fixture.workflow_id,
        canonical_repository_path=fixture.repo,
        expected_baseline_commit=fixture.baseline,
        authority_level=authority_level,
    )


def _commit_paths(repo: Path, commit_sha: str) -> list[str]:
    output = run_git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit_sha)
    return sorted(line for line in output.splitlines() if line)


def _rev_count(repo: Path) -> int:
    return int(run_git(repo, "rev-list", "--count", "HEAD"))
