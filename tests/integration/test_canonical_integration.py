from __future__ import annotations

from pathlib import Path

from lucius.domain.enums import (
    Actor,
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
from lucius.integration.service import CanonicalIntegrationRequest, CanonicalIntegrationService
from lucius.repositories.git_mutation import capture_utf8_file_contents
from lucius.persistence.orm import (
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
from lucius.runtime.deterministic_acceptance import DeterministicAcceptanceError
from tests.conftest import make_git_repo, run_git


def test_successful_controlled_canonical_integration_closes_workflow_without_commit(session, tmp_path):
    fixture = _fixture(session, tmp_path, path="src/lucius/demo.py", expected="VALUE = 1\n")

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "COMPLETED"
    assert session.get(PersistentWorkflowORM, fixture.workflow_id).workflow_state == PersistentWorkflowState.CLOSED.value
    assert run_git(fixture.canonical, "rev-parse", "HEAD") == fixture.baseline
    assert run_git(fixture.canonical, "status", "--short") == "?? src/"
    assert (fixture.canonical / "src" / "lucius" / "demo.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_integration_accepts_non_exact_deterministic_checks(session, tmp_path):
    fixture = _fixture(
        session,
        tmp_path,
        path="src/lucius/demo.py",
        expected="VALUE = 1\n# safe marker\n",
        acceptance_checks=[
            {"type": "file_exists", "path": "src/lucius/demo.py"},
            {"type": "file_contains", "path": "src/lucius/demo.py", "expected_text": "safe marker"},
            {"type": "file_not_contains", "path": "src/lucius/demo.py", "expected_text": "forbidden marker"},
        ],
    )

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "COMPLETED"
    assert result.actual_changed_paths == ["src/lucius/demo.py"]
    assert (fixture.canonical / "src" / "lucius" / "demo.py").read_text(encoding="utf-8") == "VALUE = 1\n# safe marker\n"


def test_integration_rejects_wrong_workflow_state(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    session.get(PersistentWorkflowORM, fixture.workflow_id).workflow_state = PersistentWorkflowState.PLAN_READY.value
    session.flush()

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "WORKFLOW_NOT_COMPLETED_PENDING_INTEGRATION"
    assert run_git(fixture.canonical, "status", "--short") == ""


def test_integration_allows_and_preserves_preexisting_untracked_state(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    protected = fixture.canonical / "dirty.txt"
    protected.write_text("human-owned\n", encoding="utf-8")

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "COMPLETED"
    assert protected.read_text(encoding="utf-8") == "human-owned\n"
    assert "dirty.txt" in result.protected_untracked_hashes
    assert "dirty.txt" not in result.actual_changed_paths


def test_integration_rejects_preexisting_tracked_change(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    (fixture.canonical / "README.md").write_text("# Human edit\n", encoding="utf-8")

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "PREEXISTING_TRACKED_CHANGES"
    assert session.get(PersistentWorkflowORM, fixture.workflow_id).workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value


def test_integration_rejects_preexisting_staged_change(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    (fixture.canonical / "staged.txt").write_text("human staged\n", encoding="utf-8")
    run_git(fixture.canonical, "add", "staged.txt")

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "PREEXISTING_STAGED_CHANGES"
    assert session.get(PersistentWorkflowORM, fixture.workflow_id).workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value


def test_integration_rejects_canonical_head_drift(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    (fixture.canonical / "README.md").write_text("# Drift\n", encoding="utf-8")
    run_git(fixture.canonical, "add", "README.md")
    run_git(fixture.canonical, "commit", "-m", "drift")

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "CANONICAL_HEAD_DRIFT"


def test_integration_rejects_candidate_head_baseline_mismatch_before_mutation(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    (fixture.candidate / "README.md").write_text("# Candidate drift\n", encoding="utf-8")
    run_git(fixture.candidate, "add", "README.md")
    run_git(fixture.candidate, "commit", "-m", "candidate drift")

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "CANDIDATE_HEAD_BASELINE_MISMATCH"
    assert run_git(fixture.canonical, "rev-parse", "HEAD") == fixture.baseline
    assert run_git(fixture.canonical, "status", "--short") == ""
    assert session.get(PersistentWorkflowORM, fixture.workflow_id).workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value


def test_integration_rejects_unauthorized_candidate_path(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    (fixture.candidate / "other.txt").write_text("unexpected\n", encoding="utf-8")

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "CANDIDATE_HAS_UNAUTHORIZED_CHANGES"
    assert run_git(fixture.canonical, "status", "--short") == ""


def test_integration_rejects_candidate_content_that_fails_frozen_acceptance_before_mutation(session, tmp_path):
    fixture = _fixture(session, tmp_path, path="src/lucius/demo.py", expected="VALUE = 1\n")
    (fixture.candidate / "src" / "lucius" / "demo.py").write_text("VALUE = 2\n", encoding="utf-8")

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "FAILED"
    assert "Exact file content acceptance failed" in result.reason
    assert run_git(fixture.canonical, "status", "--short") == ""
    assert session.get(PersistentWorkflowORM, fixture.workflow_id).workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value


def test_integration_rejects_malformed_traversal_frozen_path(session, tmp_path):
    fixture = _fixture(session, tmp_path, affected_path="../escape.txt", check_path="../escape.txt")

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "INVALID_FROZEN_AFFECTED_FILES"


def test_successful_integration_writes_captured_candidate_payload_without_second_read(session, tmp_path, monkeypatch):
    fixture = _fixture(session, tmp_path, path="src/lucius/demo.py", expected="VALUE = 1\n")

    def capture_then_tamper(repo, paths, *, authorized_paths):
        captured = capture_utf8_file_contents(repo, paths, authorized_paths=authorized_paths)
        (fixture.candidate / "src" / "lucius" / "demo.py").write_text("VALUE = 999\n", encoding="utf-8")
        return captured

    monkeypatch.setattr("lucius.integration.service.capture_utf8_file_contents", capture_then_tamper)

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "COMPLETED"
    assert (fixture.canonical / "src" / "lucius" / "demo.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (fixture.candidate / "src" / "lucius" / "demo.py").read_text(encoding="utf-8") == "VALUE = 999\n"
    assert run_git(fixture.canonical, "rev-parse", "HEAD") == fixture.baseline


def test_integration_rejects_captured_payload_mismatch_before_canonical_write(session, tmp_path, monkeypatch):
    fixture = _fixture(session, tmp_path, path="src/lucius/demo.py", expected="VALUE = 1\n")

    def mismatched_capture(repo, paths, *, authorized_paths):
        return [{"path": "src/lucius/demo.py", "content": "VALUE = 999\n"}]

    monkeypatch.setattr("lucius.integration.service.capture_utf8_file_contents", mismatched_capture)

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "CANDIDATE_CAPTURED_CONTENT_MISMATCH"
    assert run_git(fixture.canonical, "status", "--short") == ""
    assert session.get(PersistentWorkflowORM, fixture.workflow_id).workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value


def test_integration_acceptance_failure_after_new_file_write_rolls_back_untracked(session, tmp_path, monkeypatch):
    fixture = _fixture(session, tmp_path, path="src/new_file.py", expected="CREATED = True\n")
    _force_canonical_acceptance_failure(monkeypatch)

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "FAILED"
    assert result.rollback_attempted is True
    assert result.rollback_succeeded is True
    assert not (fixture.canonical / "src" / "new_file.py").exists()
    assert run_git(fixture.canonical, "status", "--short") == ""
    assert session.get(PersistentWorkflowORM, fixture.workflow_id).workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value



def test_integration_rollback_preserves_preexisting_protected_untracked_state(session, tmp_path, monkeypatch):
    fixture = _fixture(session, tmp_path, path="src/new_file.py", expected="CREATED = True\n")

    protected = fixture.canonical / "analysis" / "human.txt"
    protected.parent.mkdir(parents=True, exist_ok=True)
    protected.write_text("human-owned\n", encoding="utf-8")
    protected_before = protected.read_bytes()

    _force_canonical_acceptance_failure(monkeypatch)

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "FAILED"
    assert result.rollback_attempted is True
    assert result.rollback_succeeded is True
    assert not (fixture.canonical / "src" / "new_file.py").exists()
    assert protected.exists()
    assert protected.read_bytes() == protected_before
    assert run_git(fixture.canonical, "status", "--short") == "?? analysis/"
    assert session.get(PersistentWorkflowORM, fixture.workflow_id).workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value

def test_integration_acceptance_failure_after_tracked_file_write_rolls_back_modified(session, tmp_path, monkeypatch):
    fixture = _fixture(session, tmp_path, path="README.md", expected="# Integrated\n")
    _force_canonical_acceptance_failure(monkeypatch)

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "FAILED"
    assert result.rollback_succeeded is True
    assert (fixture.canonical / "README.md").read_text(encoding="utf-8") == "# Test Repo\n"
    assert run_git(fixture.canonical, "status", "--short") == ""
    assert session.get(PersistentWorkflowORM, fixture.workflow_id).workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value


def test_integration_l0_insufficient_and_l1_succeeds(session, tmp_path):
    fixture = _fixture(session, tmp_path)

    l0 = CanonicalIntegrationService(session).integrate(
        _request(fixture, authority_level=AuthorityLevel.L0)
    )
    l1 = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert l0.status == "FAILED"
    assert l0.reason == "L1_AUTHORITY_REQUIRED"
    assert l1.status == "COMPLETED"


def test_integration_requires_canonical_access_mode(session, tmp_path):
    fixture = _fixture(session, tmp_path)
    session.get(RepositoryRegistrationORM, fixture.repository_id).access_mode = RepositoryAccessMode.READ_ONLY.value
    session.flush()

    result = CanonicalIntegrationService(session).integrate(_request(fixture))

    assert result.status == "FAILED"
    assert result.reason == "REPOSITORY_ACCESS_MODE_NOT_CANONICAL_INTEGRATION"


class _Fixture:
    def __init__(self, canonical: Path, candidate: Path, baseline: str, workflow_id: str, repository_id: str):
        self.canonical = canonical
        self.candidate = candidate
        self.baseline = baseline
        self.workflow_id = workflow_id
        self.repository_id = repository_id


def _fixture(
    session,
    tmp_path,
    *,
    path: str = "src/lucius/demo.py",
    expected: str = "VALUE = 1\n",
    affected_path: str | None = None,
    check_path: str | None = None,
    acceptance_checks: list[dict[str, str]] | None = None,
) -> _Fixture:
    canonical = make_git_repo(tmp_path / "canonical")
    baseline = run_git(canonical, "rev-parse", "HEAD")
    candidate = tmp_path / "candidate"
    run_git(tmp_path, "clone", str(canonical), str(candidate))
    target = candidate / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected, encoding="utf-8")

    project = ProjectORM(
        id="LPROJ_INT",
        name="Integration Project",
        slug="integration-project",
        project_type=ProjectType.DFG_INTERNAL.value,
        status=ProjectStatus.ACTIVE.value,
        documentation_policy={},
        default_authority_level=AuthorityLevel.L1.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    repository = RepositoryRegistrationORM(
        id="LREPO_INT",
        project_id=project.id,
        name="Integration Repository",
        adapter_type=RepositoryAdapterType.LOCAL_GIT.value,
        location=str(canonical.resolve()),
        default_branch="main",
        access_mode=RepositoryAccessMode.CANONICAL_INTEGRATION.value,
        status="ACTIVE",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    task = TaskORM(
        id="LTASK_INT",
        project_id=project.id,
        title="Integrate candidate",
        objective="Integrate a verified candidate into canonical working tree.",
        priority=TaskPriority.NORMAL.value,
        complexity=TaskComplexity.T1.value,
        authority_level=AuthorityLevel.L1.value,
        status=TaskStatus.COMPLETE.value,
        created_by=Actor.LUCIUS.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    contract = TaskContractORM(
        id="LCONTR_INT",
        task_id=task.id,
        version=1,
        objective=task.objective,
        acceptance_criteria=[{"description": "Exact content", "status": "PENDING"}],
        constraints=["NO_COMMIT", "NO_PUSH", "NO_DEPLOY"],
        repository_ids=[repository.id],
        allowed_actions=["WRITE_SOURCE"],
        environment="DEVELOPMENT",
        authority_level=AuthorityLevel.L1.value,
        dependencies=[],
        documentation_required=False,
        documentation_targets=[],
        stop_conditions=[],
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    checks = acceptance_checks or [
        {"type": "exact_file_content", "path": check_path or path, "expected_text": expected}
    ]
    plan = EngineeringPlanORM(
        id="LPLAN_INT",
        task_id=task.id,
        run_id=None,
        project_id=project.id,
        task_contract_id=contract.id,
        task_contract_version=contract.version,
        version=1,
        status="FROZEN",
        summary="Integration plan",
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
        affected_files=[{"path": affected_path or path}],
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
        id="LFREEZE_INT",
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
            "affected_files": plan.affected_files,
            "deterministic_acceptance_checks": plan.deterministic_acceptance_checks,
        },
        frozen_at=utc_now(),
        frozen_by=Actor.LUCIUS.value,
    )
    workflow = PersistentWorkflowORM(
        id="LWORK_INT",
        project_id=project.id,
        repository_id=repository.id,
        repository_snapshot_id=None,
        task_id=task.id,
        plan_id=plan.id,
        plan_freeze_id=freeze.id,
        objective=task.objective,
        expected_main_head=baseline,
        isolated_branch="integration-candidate",
        worktree_path=str(candidate.resolve()),
        workflow_state=PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value,
        authority_tier=AuthorityLevel.L1.value,
        task_backlog=[{"item_id": "ITEM_INT", "state": "COMPLETED"}],
        dependency_graph={},
        active_task_id=None,
        completed_task_ids=["ITEM_INT"],
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
    session.add_all([project, repository, ProjectRepositoryAttachmentORM(project_id=project.id, repository_id=repository.id, attached_by=Actor.LUCIUS.value), task, contract, plan, freeze, workflow])
    session.flush()
    return _Fixture(canonical, candidate, baseline, workflow.id, repository.id)


def _request(fixture: _Fixture, *, authority_level: AuthorityLevel = AuthorityLevel.L1) -> CanonicalIntegrationRequest:
    return CanonicalIntegrationRequest(
        workflow_id=fixture.workflow_id,
        canonical_repository_path=fixture.canonical,
        candidate_workspace_path=fixture.candidate,
        expected_baseline_commit=fixture.baseline,
        authority_level=authority_level,
    )


def _force_canonical_acceptance_failure(monkeypatch) -> None:
    calls = {"count": 0}

    def fail_on_canonical(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise DeterministicAcceptanceError("FORCED_FAILURE", "forced deterministic failure")
        from lucius.runtime.deterministic_acceptance import verify_deterministic_acceptance

        return verify_deterministic_acceptance(*args, **kwargs)

    monkeypatch.setattr("lucius.integration.service.verify_deterministic_acceptance", fail_on_canonical)
