from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.sqltypes import String, Text

from lucius.domain.enums import (
    AuthorityLevel,
    ProjectType,
    ProjectStatus,
    RepositoryAccessMode,
    RepositoryAdapterType,
    SnapshotMode,
)
from lucius.domain.ids import ENTITY_PREFIXES, format_public_id
from lucius.persistence.orm import (
    Base,
    IdCounterORM,
    ProjectORM,
    RepositoryRegistrationORM,
    RepositorySnapshotORM,
    utc_now,
)
from lucius.repositories.local_git import LocalGitRepositoryAdapter
from lucius.repositories.schemas import SnapshotResult, WorkspaceContext


def next_id(session: Session, entity: str) -> str:
    if entity not in ENTITY_PREFIXES:
        raise ValueError(f"Unknown Lucius entity type: {entity}")
    row = session.get(IdCounterORM, entity)
    persisted_next = _persisted_next_number(session, entity)
    if row is None:
        row = IdCounterORM(entity=entity, next_number=persisted_next)
        session.add(row)
        session.flush()
    number = max(row.next_number, persisted_next)
    row.next_number = number + 1
    session.flush()
    return format_public_id(entity, number)


def _persisted_next_number(session: Session, entity: str) -> int:
    prefix = ENTITY_PREFIXES[entity]
    highest = 0
    for table in Base.metadata.sorted_tables:
        column = table.c.get("id")
        if column is None or not isinstance(column.type, (String, Text)):
            continue
        ids = session.execute(select(column).where(column.like(f"{prefix}_%"))).scalars()
        for public_id in ids:
            highest = max(highest, _public_id_number(str(public_id), prefix))
    return highest + 1


def _public_id_number(public_id: str, prefix: str) -> int:
    expected_prefix = f"{prefix}_"
    if not public_id.startswith(expected_prefix):
        return 0
    suffix = public_id[len(expected_prefix) :]
    return int(suffix) if suffix.isdigit() else 0


class ProjectService:
    def __init__(self, session: Session):
        self.session = session

    def register_project(self, name: str, *, description: str | None = None, actor: str = "system") -> ProjectORM:
        from lucius.projects.service import slugify_project

        slug = slugify_project(name)
        existing = self.session.scalar(select(ProjectORM).where(ProjectORM.slug == slug))
        if existing:
            return existing
        project = ProjectORM(
            id=next_id(self.session, "project"),
            name=name,
            slug=slug,
            organization=None,
            project_type=ProjectType.DFG_INTERNAL.value,
            description=description,
            workspace_scope=None,
            documentation_policy={},
            default_authority_level=AuthorityLevel.L0.value,
            status=ProjectStatus.ACTIVE.value,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(project)
        self.session.flush()
        from lucius.audit.service import AuditService

        AuditService(self.session).record(
            event_type="PROJECT_REGISTERED",
            actor=actor,
            project_id=project.id,
            action="register_project",
            result="SUCCESS",
            metadata={"name": name},
        )
        return project


@dataclass(frozen=True)
class RepositoryRegistrationResult:
    registration: RepositoryRegistrationORM
    created: bool


class RepositoryRegistrationService:
    def __init__(self, session: Session):
        self.session = session

    def register_local_git_repository(
        self,
        *,
        project_id: str,
        name: str,
        location: str | Path,
        workspace_context: WorkspaceContext,
        actor: str = "system",
    ) -> RepositoryRegistrationResult:
        context = workspace_context.model_copy(update={"repository_id": None, "access_mode": RepositoryAccessMode.READ_ONLY})
        adapter = LocalGitRepositoryAdapter(location, context)
        identity = adapter.get_identity()
        canonical_location = str(Path(identity.root).resolve())
        existing = self.session.scalar(
            select(RepositoryRegistrationORM).where(
                RepositoryRegistrationORM.project_id == project_id,
                RepositoryRegistrationORM.location == canonical_location,
            )
        )
        if existing:
            return RepositoryRegistrationResult(existing, created=False)
        registration = RepositoryRegistrationORM(
            id=next_id(self.session, "repository"),
            project_id=project_id,
            name=name,
            adapter_type=RepositoryAdapterType.LOCAL_GIT.value,
            location=canonical_location,
            canonical_remote=identity.canonical_remote,
            default_branch=identity.default_branch,
            access_mode=RepositoryAccessMode.READ_ONLY.value,
            status="ACTIVE",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(registration)
        self.session.flush()
        from lucius.audit.service import AuditService

        AuditService(self.session).record(
            event_type="REPOSITORY_REGISTERED",
            actor=actor,
            project_id=project_id,
            repository_id=registration.id,
            action="register_repository",
            result="SUCCESS",
            metadata={
                "name": name,
                "adapter_type": registration.adapter_type,
                "location": canonical_location,
                "access_mode": registration.access_mode,
            },
        )
        return RepositoryRegistrationResult(registration, created=True)


class RepositorySnapshotService:
    def __init__(self, session: Session):
        self.session = session

    def inspect_repository(
        self,
        *,
        repository_id: str,
        workspace_context: WorkspaceContext,
        mode: SnapshotMode = SnapshotMode.STANDARD,
        actor: str = "system",
    ) -> SnapshotResult:
        registration = self.session.get(RepositoryRegistrationORM, repository_id)
        if registration is None:
            raise ValueError(f"Unknown repository registration: {repository_id}")
        context = workspace_context.model_copy(
            update={"repository_id": repository_id, "access_mode": RepositoryAccessMode.READ_ONLY}
        )
        adapter = LocalGitRepositoryAdapter(registration.location, context)
        result = adapter.build_snapshot(mode)
        result.repository_id = repository_id
        dirty_summary = _dirty_summary(result.git_state.model_dump(mode="json"))
        latest = self.session.scalar(
            select(RepositorySnapshotORM)
            .where(RepositorySnapshotORM.repository_id == repository_id, RepositorySnapshotORM.mode == result.mode.value)
            .order_by(RepositorySnapshotORM.captured_at.desc(), RepositorySnapshotORM.id.desc())
            .limit(1)
        )
        from lucius.audit.service import AuditService

        audit = AuditService(self.session)
        audit.record(
            event_type="REPOSITORY_INSPECTED",
            actor=actor,
            project_id=registration.project_id,
            repository_id=repository_id,
            action="inspect_repository",
            result="SUCCESS",
            metadata={"mode": result.mode.value},
        )
        if latest and _same_material_snapshot(latest, result, dirty_summary):
            result.snapshot_id = latest.id
            result.status = "REUSED"
            result.reused_existing_snapshot = True
            audit.record(
                event_type="SNAPSHOT_REUSED",
                actor=actor,
                project_id=registration.project_id,
                repository_id=repository_id,
                action="reuse_snapshot",
                result="SUCCESS",
                metadata={"snapshot_id": latest.id, "manifest_hash": latest.manifest_hash},
            )
            return result
        snapshot = RepositorySnapshotORM(
            id=next_id(self.session, "snapshot"),
            repository_id=repository_id,
            mode=result.mode.value,
            captured_at=utc_now(),
            branch=result.git_state.branch,
            commit_sha=result.git_state.commit_sha,
            is_dirty=result.git_state.is_dirty,
            dirty_summary=dirty_summary,
            manifest_hash=result.manifest_summary.manifest_hash,
            file_count=result.manifest_summary.file_count,
            document_count=result.manifest_summary.document_count,
            test_count=result.manifest_summary.test_count,
            technology_profile=result.repository_profile.model_dump(mode="json"),
            manifest=result.manifest.model_dump(mode="json"),
            documentation_map=[row.model_dump(mode="json") for row in result.documentation_map],
            test_map=[row.model_dump(mode="json") for row in result.test_map],
            configuration_map=[row.model_dump(mode="json") for row in result.configuration_map],
            warnings=result.warnings,
        )
        self.session.add(snapshot)
        self.session.flush()
        result.snapshot_id = snapshot.id
        audit.record(
            event_type="SNAPSHOT_CREATED",
            actor=actor,
            project_id=registration.project_id,
            repository_id=repository_id,
            action="create_snapshot",
            result="SUCCESS",
            metadata={"snapshot_id": snapshot.id, "manifest_hash": snapshot.manifest_hash},
        )
        return result


def _dirty_summary(git_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "modified": git_state.get("modified", []),
        "added": git_state.get("added", []),
        "deleted": git_state.get("deleted", []),
        "renamed": git_state.get("renamed", []),
        "untracked": git_state.get("untracked", []),
        "conflicted": git_state.get("conflicted", []),
    }


def _same_material_snapshot(
    latest: RepositorySnapshotORM,
    result: SnapshotResult,
    dirty_summary: dict[str, Any],
) -> bool:
    return (
        latest.manifest_hash == result.manifest_summary.manifest_hash
        and latest.commit_sha == result.git_state.commit_sha
        and latest.branch == result.git_state.branch
        and bool(latest.is_dirty) == result.git_state.is_dirty
        and latest.dirty_summary == dirty_summary
    )
