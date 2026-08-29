from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from lucius.domain.enums import RepositoryAccessMode, RepositoryAdapterType, SnapshotMode
from lucius.persistence.orm import AuditEventORM, RepositorySnapshotORM
from lucius.persistence.repositories import (
    ProjectService,
    RepositoryRegistrationService,
    RepositorySnapshotService,
)
from lucius.repositories.schemas import WorkspaceContext


def test_repository_registration(session, git_repo: Path, workspace: WorkspaceContext):
    project = ProjectService(session).register_project("Project")
    result = RepositoryRegistrationService(session).register_local_git_repository(
        project_id=project.id,
        name="Repo",
        location=git_repo,
        workspace_context=workspace,
    )
    assert result.created is True
    assert result.registration.id == "LREPO_000001"
    assert result.registration.adapter_type == RepositoryAdapterType.LOCAL_GIT.value
    assert result.registration.access_mode == RepositoryAccessMode.READ_ONLY.value
    assert result.registration.location == str(git_repo.resolve())


def test_duplicate_repository_registration_is_deterministic(session, git_repo: Path, workspace: WorkspaceContext):
    project = ProjectService(session).register_project("Project")
    service = RepositoryRegistrationService(session)
    first = service.register_local_git_repository(
        project_id=project.id,
        name="Repo",
        location=git_repo,
        workspace_context=workspace,
    )
    second = service.register_local_git_repository(
        project_id=project.id,
        name="Repo Again",
        location=git_repo / ".",
        workspace_context=workspace,
    )
    assert first.created is True
    assert second.created is False
    assert second.registration.id == first.registration.id


def test_snapshot_creation_and_audit_events(session, git_repo: Path, workspace: WorkspaceContext):
    project = ProjectService(session).register_project("Project")
    registration = RepositoryRegistrationService(session).register_local_git_repository(
        project_id=project.id,
        name="Repo",
        location=git_repo,
        workspace_context=workspace,
    ).registration
    result = RepositorySnapshotService(session).inspect_repository(
        repository_id=registration.id,
        workspace_context=workspace,
        mode=SnapshotMode.STANDARD,
    )
    assert result.snapshot_id == "LSNAP_000001"
    assert result.reused_existing_snapshot is False
    assert result.manifest_summary.file_count >= 3
    assert result.manifest_summary.document_count >= 1
    assert result.manifest_summary.test_count >= 1
    event_types = [row.event_type for row in session.scalars(select(AuditEventORM)).all()]
    assert "REPOSITORY_REGISTERED" in event_types
    assert "REPOSITORY_INSPECTED" in event_types
    assert "SNAPSHOT_CREATED" in event_types


def test_unchanged_snapshot_reuse(session, git_repo: Path, workspace: WorkspaceContext):
    project = ProjectService(session).register_project("Project")
    registration = RepositoryRegistrationService(session).register_local_git_repository(
        project_id=project.id,
        name="Repo",
        location=git_repo,
        workspace_context=workspace,
    ).registration
    service = RepositorySnapshotService(session)
    first = service.inspect_repository(repository_id=registration.id, workspace_context=workspace)
    second = service.inspect_repository(repository_id=registration.id, workspace_context=workspace)
    snapshots = session.scalars(select(RepositorySnapshotORM)).all()
    assert second.snapshot_id == first.snapshot_id
    assert second.reused_existing_snapshot is True
    assert len(snapshots) == 1
    assert session.scalar(select(AuditEventORM).where(AuditEventORM.event_type == "SNAPSHOT_REUSED")) is not None


def test_changed_repo_creates_new_snapshot(session, git_repo: Path, workspace: WorkspaceContext):
    project = ProjectService(session).register_project("Project")
    registration = RepositoryRegistrationService(session).register_local_git_repository(
        project_id=project.id,
        name="Repo",
        location=git_repo,
        workspace_context=workspace,
    ).registration
    service = RepositorySnapshotService(session)
    first = service.inspect_repository(repository_id=registration.id, workspace_context=workspace)
    (git_repo / "README.md").write_text("# Changed\n", encoding="utf-8")
    second = service.inspect_repository(repository_id=registration.id, workspace_context=workspace)
    assert second.snapshot_id != first.snapshot_id
    assert second.reused_existing_snapshot is False
    assert second.git_state.is_dirty is True
    assert any(warning["code"] == "DIRTY_REPOSITORY" for warning in second.warnings)

