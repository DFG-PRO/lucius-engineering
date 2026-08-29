from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from lucius.domain.enums import (
    Actor,
    AllowedAction,
    AuthorityLevel,
    BlockerCode,
    ProjectStatus,
    ProjectType,
    TaskPriority,
    TaskRunStatus,
    TaskStatus,
)
from lucius.persistence.orm import (
    AuditEventORM,
    ProjectRepositoryAttachmentORM,
    RepositorySnapshotORM,
    TaskRunORM,
)
from lucius.persistence.repositories import RepositoryRegistrationService, RepositorySnapshotService
from lucius.projects.service import ProjectRegistryService
from lucius.tasks.service import TaskService


def _project_with_repository(session, git_repo: Path, workspace):
    project = ProjectRegistryService(session).register_project(
        "Registry Project",
        organization="DFG",
        project_type=ProjectType.DFG_INTERNAL,
    )
    repository = ProjectRegistryService(session).attach_repository(
        project_id=project.id,
        name="Repo",
        location=git_repo,
        workspace_context=workspace,
        actor=Actor.CODEX,
    )
    return project, repository


def _ready_task(session, project_id: str, repository_id: str | None = None, *, docs_required: bool = False):
    service = TaskService(session)
    task = service.create_task(
        project_id=project_id,
        title="Implement feature",
        objective="Implement a deterministic feature",
        authority_level=AuthorityLevel.L1,
        created_by=Actor.CODEX,
    )
    service.create_or_update_contract(
        task_id=task.id,
        objective="Implement a deterministic feature",
        acceptance_criteria=["Feature is implemented"],
        repository_ids=[repository_id] if repository_id else [],
        allowed_actions=[AllowedAction.READ_REPOSITORY, AllowedAction.WRITE_DOCUMENTATION],
        authority_level=AuthorityLevel.L1,
        documentation_required=docs_required,
        documentation_targets=["docs/feature.md"] if docs_required else [],
        actor=Actor.CODEX,
    )
    result = service.mark_ready(task.id, actor=Actor.CODEX)
    assert result.valid is True
    return task


def test_create_and_duplicate_project_handling(session):
    registry = ProjectRegistryService(session)
    first = registry.register_project("Lucius Core", default_authority_level=AuthorityLevel.L1)
    second = registry.register_project("Lucius Core")
    assert first.id == "LPROJ_000001"
    assert first.slug == "lucius-core"
    assert first.default_authority_level == AuthorityLevel.L1.value
    assert second.id == first.id
    assert registry.get_project(first.id).id == first.id
    assert [project.id for project in registry.list_projects()] == [first.id]


def test_project_lifecycle_pause_reactivate_archive(session):
    registry = ProjectRegistryService(session)
    project = registry.register_project("Lifecycle")
    assert registry.pause_project(project.id).status == ProjectStatus.PAUSED.value
    assert registry.activate_project(project.id).status == ProjectStatus.ACTIVE.value
    assert registry.archive_project(project.id).status == ProjectStatus.ARCHIVED.value


def test_attach_repository_and_prevent_duplicate_attachment(session, git_repo: Path, workspace):
    project, repository = _project_with_repository(session, git_repo, workspace)
    again = ProjectRegistryService(session).attach_repository(project_id=project.id, repository_id=repository.id)
    attachments = session.scalars(select(ProjectRepositoryAttachmentORM)).all()
    assert again.id == repository.id
    assert len(attachments) == 1
    assert ProjectRegistryService(session).list_project_repositories(project.id)[0].id == repository.id


def test_reject_repository_attachment_to_missing_project(session, git_repo: Path, workspace):
    project = ProjectRegistryService(session).register_project("Existing")
    repository = RepositoryRegistrationService(session).register_local_git_repository(
        project_id=project.id,
        name="Repo",
        location=git_repo,
        workspace_context=workspace,
    ).registration
    with pytest.raises(ValueError):
        ProjectRegistryService(session).attach_repository(project_id="LPROJ_999999", repository_id=repository.id)


def test_archived_project_cannot_receive_new_repository(session, git_repo: Path, workspace):
    registry = ProjectRegistryService(session)
    project = registry.register_project("Archived")
    registry.archive_project(project.id)
    with pytest.raises(ValueError):
        registry.attach_repository(project_id=project.id, name="Repo", location=git_repo, workspace_context=workspace)


def test_create_task_starts_draft(session):
    project = ProjectRegistryService(session).register_project("Tasks")
    task = TaskService(session).create_task(
        project_id=project.id,
        title="Write task",
        objective="Create a persisted task",
        priority=TaskPriority.HIGH,
        created_by=Actor.CODEX,
    )
    assert task.id == "LTASK_000001"
    assert task.status == TaskStatus.DRAFT.value
    assert task.created_by == Actor.CODEX.value


def test_create_task_contract_and_acceptance_criteria(session):
    project = ProjectRegistryService(session).register_project("Contracts")
    task = TaskService(session).create_task(project_id=project.id, title="Contract", objective="Create contract")
    contract = TaskService(session).create_or_update_contract(
        task_id=task.id,
        objective="Create contract",
        acceptance_criteria=["AC one", {"statement": "AC two", "status": "NOT_EVALUATED"}],
    )
    assert contract.id == "LCONTR_000001"
    assert contract.acceptance_criteria[0]["id"] == "AC-001"
    assert contract.acceptance_criteria[1]["status"] == "NOT_EVALUATED"


def test_blank_objective_rejected(session):
    project = ProjectRegistryService(session).register_project("Blank")
    task = TaskService(session).create_task(project_id=project.id, title="Blank", objective="Initial")
    service = TaskService(session)
    service.create_or_update_contract(task_id=task.id, objective=" ", acceptance_criteria=["AC"])
    result = service.mark_ready(task.id)
    assert result.valid is False
    assert result.blockers[0].code == BlockerCode.INVALID_CONTRACT
    assert task.status == TaskStatus.BLOCKED.value


def test_zero_acceptance_criteria_rejected(session):
    project = ProjectRegistryService(session).register_project("No AC")
    task = TaskService(session).create_task(project_id=project.id, title="No AC", objective="Initial")
    service = TaskService(session)
    service.create_or_update_contract(task_id=task.id, objective="Initial", acceptance_criteria=[])
    result = service.mark_ready(task.id)
    assert result.valid is False
    assert any(blocker.code == BlockerCode.INVALID_CONTRACT for blocker in result.blockers)


def test_repository_not_attached_to_project_rejected(session, git_repo: Path, workspace):
    project_one = ProjectRegistryService(session).register_project("One")
    project_two = ProjectRegistryService(session).register_project("Two")
    repository = ProjectRegistryService(session).attach_repository(
        project_id=project_two.id,
        name="Repo",
        location=git_repo,
        workspace_context=workspace,
    )
    service = TaskService(session)
    task = service.create_task(project_id=project_one.id, title="Bad repo", objective="Bad repo")
    service.create_or_update_contract(
        task_id=task.id,
        objective="Bad repo",
        acceptance_criteria=["AC"],
        repository_ids=[repository.id],
    )
    result = service.validate_contract(task.id)
    assert any(blocker.code == BlockerCode.MISSING_REPOSITORY for blocker in result.blockers)


def test_l0_write_source_and_create_commit_rejected(session):
    project = ProjectRegistryService(session).register_project("Authority")
    service = TaskService(session)
    task = service.create_task(project_id=project.id, title="L0", objective="Read only", authority_level=AuthorityLevel.L0)
    service.create_or_update_contract(
        task_id=task.id,
        objective="Read only",
        acceptance_criteria=["AC"],
        allowed_actions=[AllowedAction.WRITE_SOURCE, AllowedAction.CREATE_COMMIT],
        authority_level=AuthorityLevel.L0,
    )
    result = service.validate_contract(task.id)
    assert [blocker.code for blocker in result.blockers].count(BlockerCode.AUTHORITY_INSUFFICIENT) == 2


def test_contract_authority_exceeding_task_rejected(session):
    project = ProjectRegistryService(session).register_project("Authority Ceiling")
    service = TaskService(session)
    task = service.create_task(project_id=project.id, title="L1", objective="Task", authority_level=AuthorityLevel.L1)
    service.create_or_update_contract(
        task_id=task.id,
        objective="Task",
        acceptance_criteria=["AC"],
        authority_level=AuthorityLevel.L2,
    )
    result = service.validate_contract(task.id)
    assert any(blocker.code == BlockerCode.AUTHORITY_INSUFFICIENT for blocker in result.blockers)


def test_self_dependency_rejected(session):
    project = ProjectRegistryService(session).register_project("Self Dependency")
    service = TaskService(session)
    task = service.create_task(project_id=project.id, title="Self", objective="Self")
    service.create_or_update_contract(
        task_id=task.id,
        objective="Self",
        acceptance_criteria=["AC"],
        dependencies=[task.id],
    )
    result = service.validate_contract(task.id)
    assert any(blocker.code == BlockerCode.DEPENDENCY_BLOCKED for blocker in result.blockers)


def test_incomplete_dependency_blocks_readiness(session):
    project = ProjectRegistryService(session).register_project("Dependency")
    service = TaskService(session)
    dependency = service.create_task(project_id=project.id, title="Dependency", objective="Dependency")
    task = service.create_task(project_id=project.id, title="Main", objective="Main")
    service.create_or_update_contract(
        task_id=task.id,
        objective="Main",
        acceptance_criteria=["AC"],
        dependencies=[dependency.id],
    )
    result = service.mark_ready(task.id)
    assert result.valid is False
    assert any(blocker.code == BlockerCode.DEPENDENCY_BLOCKED for blocker in result.blockers)


def test_valid_contract_transitions_task_to_ready(session, git_repo: Path, workspace):
    project, repository = _project_with_repository(session, git_repo, workspace)
    task = _ready_task(session, project.id, repository.id)
    assert task.status == TaskStatus.READY.value


def test_start_run_transitions_task_and_binds_valid_snapshot(session, git_repo: Path, workspace):
    project, repository = _project_with_repository(session, git_repo, workspace)
    snapshot = RepositorySnapshotService(session).inspect_repository(repository_id=repository.id, workspace_context=workspace)
    task = _ready_task(session, project.id, repository.id)
    run = TaskService(session).start_run(task.id, snapshot_id=snapshot.snapshot_id, actor=Actor.CODEX)
    assert task.status == TaskStatus.RUNNING.value
    assert run.status == TaskRunStatus.RUNNING.value
    assert run.snapshot_id == snapshot.snapshot_id


def test_unrelated_snapshot_rejected(session, git_repo: Path, tmp_path: Path, workspace):
    project_one = ProjectRegistryService(session).register_project("Project One")
    task = _ready_task(session, project_one.id)

    other_repo = git_repo
    other_project = ProjectRegistryService(session).register_project("Project Two")
    other_workspace = workspace
    other_registration = ProjectRegistryService(session).attach_repository(
        project_id=other_project.id,
        name="Other Repo",
        location=other_repo,
        workspace_context=other_workspace,
    )
    snapshot = RepositorySnapshotService(session).inspect_repository(
        repository_id=other_registration.id,
        workspace_context=other_workspace,
    )
    with pytest.raises(ValueError):
        TaskService(session).start_run(task.id, snapshot_id=snapshot.snapshot_id)


def test_block_task_preserves_structured_blocker(session):
    project = ProjectRegistryService(session).register_project("Block")
    task = _ready_task(session, project.id)
    blocked = TaskService(session).block_task(
        task.id,
        blocker_code=BlockerCode.DECISION_REQUIRED,
        blocker_message="Need a human decision",
        actor=Actor.HUMAN,
    )
    assert blocked.status == TaskStatus.BLOCKED.value
    assert blocked.blocker_code == BlockerCode.DECISION_REQUIRED.value
    assert blocked.blocked_at is not None


def test_fail_task_preserves_failure_history(session):
    project = ProjectRegistryService(session).register_project("Fail")
    task = _ready_task(session, project.id)
    service = TaskService(session)
    run = service.start_run(task.id, actor=Actor.CODEX)
    failed = service.fail_task(task.id, run_id=run.id, failure_reason="Tests failed", actor=Actor.CODEX)
    assert failed.status == TaskStatus.FAILED.value
    assert session.get(TaskRunORM, run.id).status == TaskRunStatus.FAILED.value


def test_implementation_completion_transition(session):
    project = ProjectRegistryService(session).register_project("Implementation")
    task = _ready_task(session, project.id)
    service = TaskService(session)
    run = service.start_run(task.id, actor=Actor.CODEX)
    complete = service.mark_implementation_complete(task.id, run_id=run.id, output_summary="Tests passed", actor=Actor.CODEX)
    assert complete.status == TaskStatus.IMPLEMENTATION_COMPLETE.value
    assert session.get(TaskRunORM, run.id).status == TaskRunStatus.SUCCEEDED.value


def test_documentation_required_creates_gate_and_cannot_complete_while_pending(session):
    project = ProjectRegistryService(session).register_project("Docs Gate")
    task = _ready_task(session, project.id, docs_required=True)
    service = TaskService(session)
    run = service.start_run(task.id, actor=Actor.CODEX)
    service.mark_implementation_complete(task.id, run_id=run.id, actor=Actor.CODEX)
    direct = service.complete_task(task.id, actor=Actor.CODEX)
    assert direct.valid is False
    assert direct.blockers[0].code == BlockerCode.DOCUMENTATION_REQUIRED
    service.mark_documentation_pending(task.id, actor=Actor.CODEX)
    pending = service.complete_task(task.id, actor=Actor.CODEX)
    assert pending.valid is False
    assert pending.blockers[0].code == BlockerCode.DOCUMENTATION_REQUIRED


def test_documentation_evidence_permits_complete(session):
    project = ProjectRegistryService(session).register_project("Docs Complete")
    task = _ready_task(session, project.id, docs_required=True)
    service = TaskService(session)
    run = service.start_run(task.id, actor=Actor.CODEX)
    service.mark_implementation_complete(task.id, run_id=run.id, actor=Actor.CODEX)
    service.mark_documentation_pending(task.id, actor=Actor.CODEX)
    service.record_documentation_completion(
        task.id,
        targets_completed=["docs/feature.md"],
        evidence_references=["LEVID_000001"],
        completed_by=Actor.CODEX,
    )
    result = service.complete_task(task.id, actor=Actor.CODEX)
    assert result.valid is True
    assert task.status == TaskStatus.COMPLETE.value


def test_documentation_not_required_can_complete_through_valid_lifecycle(session):
    project = ProjectRegistryService(session).register_project("No Docs")
    task = _ready_task(session, project.id, docs_required=False)
    service = TaskService(session)
    run = service.start_run(task.id, actor=Actor.LUCIUS)
    service.mark_implementation_complete(task.id, run_id=run.id, actor=Actor.LUCIUS)
    result = service.complete_task(task.id, actor=Actor.LUCIUS)
    assert result.valid is True
    assert task.status == TaskStatus.COMPLETE.value


def test_audit_events_emitted_for_lifecycle_changes(session):
    project = ProjectRegistryService(session).register_project("Audit")
    task = _ready_task(session, project.id)
    service = TaskService(session)
    run = service.start_run(task.id, actor=Actor.CODEX)
    service.mark_implementation_complete(task.id, run_id=run.id, actor=Actor.CODEX)
    service.complete_task(task.id, actor=Actor.CODEX)
    event_types = {event.event_type for event in session.scalars(select(AuditEventORM)).all()}
    assert {
        "TASK_CREATED",
        "TASK_CONTRACT_CREATED",
        "TASK_READY",
        "TASK_RUN_STARTED",
        "TASK_IMPLEMENTATION_COMPLETE",
        "TASK_COMPLETE",
    }.issubset(event_types)


def test_multiple_task_runs_preserve_history(session):
    project = ProjectRegistryService(session).register_project("Runs")
    task = _ready_task(session, project.id)
    service = TaskService(session)
    first = service.start_run(task.id, actor=Actor.CODEX)
    service.fail_task(task.id, run_id=first.id, failure_reason="First failed", actor=Actor.CODEX)
    service.mark_ready(task.id, actor=Actor.CODEX)
    second = service.start_run(task.id, actor=Actor.CODEX)
    runs = session.scalars(select(TaskRunORM).where(TaskRunORM.task_id == task.id).order_by(TaskRunORM.id)).all()
    assert [run.id for run in runs] == [first.id, second.id]
    assert runs[0].status == TaskRunStatus.FAILED.value
    assert runs[1].status == TaskRunStatus.RUNNING.value


def test_actor_provenance_distinguishes_codex_from_lucius(session):
    project = ProjectRegistryService(session).register_project("Provenance")
    service = TaskService(session)
    task = service.create_task(
        project_id=project.id,
        title="Implement Repository Core",
        objective="Represent historical Phase 1.4B work",
        authority_level=AuthorityLevel.L1,
        created_by=Actor.CODEX,
    )
    service.create_or_update_contract(
        task_id=task.id,
        objective="Represent historical Phase 1.4B work",
        acceptance_criteria=[
            {"id": "AC-001", "statement": "Implementation complete", "status": "PASS"},
            {"id": "AC-002", "statement": "Tests passed", "status": "PASS"},
            {"id": "AC-003", "statement": "Documentation completed", "status": "PASS"},
        ],
        allowed_actions=[AllowedAction.READ_REPOSITORY],
        authority_level=AuthorityLevel.L1,
        actor=Actor.CODEX,
    )
    service.mark_ready(task.id, actor=Actor.CODEX)
    run = service.start_run(task.id, actor=Actor.CODEX)
    assert task.created_by == Actor.CODEX.value
    assert run.actor == Actor.CODEX.value
    assert run.actor != Actor.LUCIUS.value


def test_invalid_status_transitions_rejected(session):
    project = ProjectRegistryService(session).register_project("Invalid")
    task = TaskService(session).create_task(project_id=project.id, title="Invalid", objective="Invalid")
    with pytest.raises(ValueError):
        TaskService(session).start_run(task.id)
    with pytest.raises(ValueError):
        TaskService(session).complete_task(task.id)


def test_alembic_upgrade_from_prior_revision_and_clean_head(tmp_path: Path):
    for sidecar in Path("alembic").glob("**/._*"):
        sidecar.unlink()

    first_db = tmp_path / "from_0001.sqlite3"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{first_db}")
    command.upgrade(cfg, "0001_repository_core")
    command.upgrade(cfg, "head")

    clean_db = tmp_path / "clean_head.sqlite3"
    clean_cfg = Config("alembic.ini")
    clean_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{clean_db}")
    command.upgrade(clean_cfg, "head")

    assert first_db.exists()
    assert clean_db.exists()
