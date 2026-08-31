from __future__ import annotations

from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, HumanRubricCaptureStatus
from lucius.persistence.orm import HumanRubricORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.schemas import HUMAN_RUBRIC_DIMENSIONS, HumanRubric


class HumanRubricService:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def record_not_captured(
        self,
        *,
        plan_freeze_id: str | None = None,
        pilot_record_id: str | None = None,
        actor: Actor = Actor.SYSTEM,
    ) -> HumanRubric:
        return self._persist(
            plan_freeze_id=plan_freeze_id,
            pilot_record_id=pilot_record_id,
            status=HumanRubricCaptureStatus.NOT_CAPTURED,
            evaluator=None,
            scores={dimension: "NOT_CAPTURED" for dimension in HUMAN_RUBRIC_DIMENSIONS},
            comments=None,
            actor=actor,
        )

    def capture(
        self,
        *,
        evaluator: str,
        scores: dict[str, int],
        comments: str | None = None,
        plan_freeze_id: str | None = None,
        pilot_record_id: str | None = None,
        actor: Actor = Actor.HUMAN,
    ) -> HumanRubric:
        missing = sorted(set(HUMAN_RUBRIC_DIMENSIONS) - set(scores))
        extra = sorted(set(scores) - set(HUMAN_RUBRIC_DIMENSIONS))
        if missing or extra:
            raise ValueError(f"Rubric scores must match required dimensions; missing={missing}, extra={extra}")
        for dimension, score in scores.items():
            if score < 1 or score > 5:
                raise ValueError(f"Rubric score for {dimension} must be 1-5")
        return self._persist(
            plan_freeze_id=plan_freeze_id,
            pilot_record_id=pilot_record_id,
            status=HumanRubricCaptureStatus.CAPTURED,
            evaluator=evaluator,
            scores=scores,
            comments=comments,
            actor=actor,
        )

    def _persist(
        self,
        *,
        plan_freeze_id: str | None,
        pilot_record_id: str | None,
        status: HumanRubricCaptureStatus,
        evaluator: str | None,
        scores: dict,
        comments: str | None,
        actor: Actor,
    ) -> HumanRubric:
        row = HumanRubricORM(
            id=next_id(self.session, "human_rubric"),
            plan_freeze_id=plan_freeze_id,
            pilot_record_id=pilot_record_id,
            status=status.value,
            evaluator=evaluator,
            scores=scores,
            comments=comments,
            captured_at=utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        self.audit.record(
            event_type="HUMAN_RUBRIC_CAPTURED",
            actor=actor.value,
            action="capture_human_rubric",
            result=status.value,
            metadata={"human_rubric_id": row.id, "plan_freeze_id": plan_freeze_id},
        )
        return HumanRubric(
            id=row.id,
            plan_freeze_id=row.plan_freeze_id,
            pilot_record_id=row.pilot_record_id,
            status=status,
            evaluator=row.evaluator,
            scores=row.scores,
            comments=row.comments,
            captured_at=row.captured_at,
        )
