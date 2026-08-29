from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, AuthorityLevel, ProjectStatus, ProjectType, RepositoryAccessMode
from lucius.persistence.orm import (
    ProjectORM,
    ProjectRepositoryAttachmentORM,
    RepositoryRegistrationORM,
    utc_now,
)
from lucius.persistence.repositories import RepositoryRegistrationService, next_id
from lucius.repositories.schemas import WorkspaceContext


def slugify_project(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise ValueError("Project slug cannot be blank")
    return slug


class ProjectRegistryService:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def register_project(
        self,
        name: str,
        *,
        slug: str | None = None,
        organization: str | None = None,
        project_type: ProjectType = ProjectType.DFG_INTERNAL,
        description: str | None = None,
        workspace_scope: str | None = None,
        documentation_policy: dict[str, Any] | None = None,
        default_authority_level: AuthorityLevel = AuthorityLevel.L0,
        actor: Actor = Actor.SYSTEM,
    ) -> ProjectORM:
        project_slug = slugify_project(slug or name)
        existing = self.session.scalar(select(ProjectORM).where(ProjectORM.slug == project_slug))
        if existing:
            return existing
        project = ProjectORM(
            id=next_id(self.session, "project"),
            name=name,
            slug=project_slug,
            organization=organization,
            project_type=project_type.value,
            status=ProjectStatus.ACTIVE.value,
            description=description,
            workspace_scope=workspace_scope,
            documentation_policy=documentation_policy or {},
            default_authority_level=default_authority_level.value,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(project)
        self.session.flush()
        self.audit.record(
            event_type="PROJECT_REGISTERED",
            actor=actor.value,
            project_id=project.id,
            action="register_project",
            result="SUCCESS",
            metadata={"name": name, "slug": project.slug, "project_type": project.project_type},
        )
        return project

    def get_project(self, project_id: str) -> ProjectORM | None:
        return self.session.get(ProjectORM, project_id)

    def list_projects(self) -> list[ProjectORM]:
        return list(self.session.scalars(select(ProjectORM).order_by(ProjectORM.id)).all())

    def pause_project(self, project_id: str, *, actor: Actor = Actor.SYSTEM) -> ProjectORM:
        return self._transition_project(project_id, ProjectStatus.PAUSED, "PROJECT_PAUSED", "pause_project", actor)

    def activate_project(self, project_id: str, *, actor: Actor = Actor.SYSTEM) -> ProjectORM:
        return self._transition_project(project_id, ProjectStatus.ACTIVE, "PROJECT_ACTIVATED", "activate_project", actor)

    def archive_project(self, project_id: str, *, actor: Actor = Actor.SYSTEM) -> ProjectORM:
        return self._transition_project(project_id, ProjectStatus.ARCHIVED, "PROJECT_ARCHIVED", "archive_project", actor)

    def attach_repository(
        self,
        *,
        project_id: str,
        repository_id: str | None = None,
        name: str | None = None,
        location: str | Path | None = None,
        workspace_context: WorkspaceContext | None = None,
        actor: Actor = Actor.SYSTEM,
    ) -> RepositoryRegistrationORM:
        project = self._active_project(project_id)
        if repository_id is None:
            if location is None or workspace_context is None:
                raise ValueError("location and workspace_context are required when registering a repository attachment")
            registration = RepositoryRegistrationService(self.session).register_local_git_repository(
                project_id=project.id,
                name=name or project.name,
                location=location,
                workspace_context=workspace_context.model_copy(update={"access_mode": RepositoryAccessMode.READ_ONLY}),
                actor=actor.value,
            ).registration
        else:
            registration = self.session.get(RepositoryRegistrationORM, repository_id)
            if registration is None:
                raise ValueError(f"Unknown repository registration: {repository_id}")
            if registration.project_id != project.id:
                raise ValueError("Repository registration does not belong to the target project")

        existing = self.session.scalar(
            select(ProjectRepositoryAttachmentORM).where(
                ProjectRepositoryAttachmentORM.project_id == project.id,
                ProjectRepositoryAttachmentORM.repository_id == registration.id,
            )
        )
        if existing:
            return registration
        attachment = ProjectRepositoryAttachmentORM(
            project_id=project.id,
            repository_id=registration.id,
            attached_at=utc_now(),
            attached_by=actor.value,
        )
        self.session.add(attachment)
        self.session.flush()
        self.audit.record(
            event_type="PROJECT_REPOSITORY_ATTACHED",
            actor=actor.value,
            project_id=project.id,
            repository_id=registration.id,
            action="attach_repository",
            result="SUCCESS",
            metadata={"repository_name": registration.name},
        )
        return registration

    def list_project_repositories(self, project_id: str) -> list[RepositoryRegistrationORM]:
        if self.session.get(ProjectORM, project_id) is None:
            raise ValueError(f"Unknown project: {project_id}")
        rows = self.session.scalars(
            select(RepositoryRegistrationORM)
            .join(ProjectRepositoryAttachmentORM, ProjectRepositoryAttachmentORM.repository_id == RepositoryRegistrationORM.id)
            .where(ProjectRepositoryAttachmentORM.project_id == project_id)
            .order_by(RepositoryRegistrationORM.id)
        )
        return list(rows.all())

    def _transition_project(
        self,
        project_id: str,
        status: ProjectStatus,
        event_type: str,
        action: str,
        actor: Actor,
    ) -> ProjectORM:
        project = self.session.get(ProjectORM, project_id)
        if project is None:
            raise ValueError(f"Unknown project: {project_id}")
        project.status = status.value
        project.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type=event_type,
            actor=actor.value,
            project_id=project.id,
            action=action,
            result="SUCCESS",
        )
        return project

    def _active_project(self, project_id: str) -> ProjectORM:
        project = self.session.get(ProjectORM, project_id)
        if project is None:
            raise ValueError(f"Unknown project: {project_id}")
        if project.status != ProjectStatus.ACTIVE.value:
            raise ValueError("Project must be ACTIVE to receive new active work")
        return project

