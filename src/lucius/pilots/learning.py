from __future__ import annotations

from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, PilotLearningStatus
from lucius.persistence.orm import PilotLearningCandidateORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.schemas import PilotLearningCandidate


class PilotLearningService:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def identify(
        self,
        *,
        statement: str,
        evidence_refs: list[str] | None = None,
        pilot_record_id: str | None = None,
        actor: Actor = Actor.SYSTEM,
    ) -> PilotLearningCandidate:
        row = PilotLearningCandidateORM(
            id=next_id(self.session, "pilot_learning"),
            pilot_record_id=pilot_record_id,
            statement=statement,
            status=PilotLearningStatus.IDENTIFIED.value,
            evidence_refs=evidence_refs or [],
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        self.audit.record(
            event_type="PILOT_LEARNING_CANDIDATE_IDENTIFIED",
            actor=actor.value,
            action="identify_pilot_learning_candidate",
            result="SUCCESS",
            metadata={"pilot_learning_candidate_id": row.id, "pilot_record_id": pilot_record_id},
        )
        return _learning_from_row(row)

    def transition(
        self,
        candidate_id: str,
        *,
        status: PilotLearningStatus,
        actor: Actor = Actor.SYSTEM,
    ) -> PilotLearningCandidate:
        row = self.session.get(PilotLearningCandidateORM, candidate_id)
        if row is None:
            raise ValueError(f"Unknown pilot learning candidate: {candidate_id}")
        row.status = status.value
        row.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="PILOT_LEARNING_CANDIDATE_UPDATED",
            actor=actor.value,
            action="transition_pilot_learning_candidate",
            result=status.value,
            metadata={"pilot_learning_candidate_id": row.id},
        )
        return _learning_from_row(row)


def _learning_from_row(row: PilotLearningCandidateORM) -> PilotLearningCandidate:
    return PilotLearningCandidate(
        id=row.id,
        pilot_record_id=row.pilot_record_id,
        statement=row.statement,
        status=row.status,
        evidence_refs=row.evidence_refs,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
