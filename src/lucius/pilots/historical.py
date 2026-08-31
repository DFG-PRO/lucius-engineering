from __future__ import annotations

from sqlalchemy.orm import Session

from lucius.domain.enums import PlanningEvidenceMode, RepositoryAccessMode
from lucius.persistence.orm import RepositoryRegistrationORM, RepositorySnapshotORM
from lucius.repositories.schemas import WorkspaceContext


class HistoricalPlanningContextService:
    def __init__(self, session: Session):
        self.session = session

    def current_context(
        self,
        *,
        repository_id: str,
        base_context: WorkspaceContext,
    ) -> WorkspaceContext:
        self._repository(repository_id)
        return base_context.model_copy(
            update={
                "repository_id": repository_id,
                "access_mode": RepositoryAccessMode.READ_ONLY,
                "repository_ref": None,
                "planning_mode": PlanningEvidenceMode.CURRENT_STATE_PLANNING.value,
            }
        )

    def historical_context_for_ref(
        self,
        *,
        repository_id: str,
        repository_ref: str,
        base_context: WorkspaceContext,
    ) -> WorkspaceContext:
        self._repository(repository_id)
        return base_context.model_copy(
            update={
                "repository_id": repository_id,
                "access_mode": RepositoryAccessMode.READ_ONLY,
                "repository_ref": repository_ref,
                "planning_mode": PlanningEvidenceMode.HISTORICAL_STATE_PLANNING.value,
            }
        )

    def historical_context_for_snapshot(
        self,
        *,
        repository_id: str,
        snapshot_id: str,
        base_context: WorkspaceContext,
    ) -> WorkspaceContext:
        snapshot = self.session.get(RepositorySnapshotORM, snapshot_id)
        if snapshot is None:
            raise ValueError(f"Unknown repository snapshot: {snapshot_id}")
        if snapshot.repository_id != repository_id:
            raise ValueError("Snapshot does not belong to repository")
        self._repository(repository_id)
        return base_context.model_copy(
            update={
                "repository_id": repository_id,
                "access_mode": RepositoryAccessMode.READ_ONLY,
                "repository_ref": snapshot.commit_sha,
                "planning_mode": PlanningEvidenceMode.HISTORICAL_STATE_PLANNING.value,
            }
        )

    def _repository(self, repository_id: str) -> RepositoryRegistrationORM:
        row = self.session.get(RepositoryRegistrationORM, repository_id)
        if row is None:
            raise ValueError(f"Unknown repository registration: {repository_id}")
        return row
