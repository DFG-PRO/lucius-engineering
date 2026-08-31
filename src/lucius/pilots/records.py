from __future__ import annotations

from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, AutonomyRecommendation, RepositoryIntegrityResult, RepositoryStateClassification
from lucius.persistence.orm import PilotEvaluationRecordORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.release import ReleaseGateService
from lucius.pilots.schemas import PilotEvaluationRecord, ReleaseGateResult


class PilotRecordService:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def create(
        self,
        *,
        target_repository_id: str | None = None,
        repository_snapshot_id: str | None = None,
        repository_state_id: str | None = None,
        task_id: str | None = None,
        plan_id: str | None = None,
        plan_freeze_id: str | None = None,
        deterministic_evaluation_id: str | None = None,
        human_rubric_id: str | None = None,
        benchmark_before_id: str | None = None,
        benchmark_after_id: str | None = None,
        learning_candidate_ids: list[str] | None = None,
        corrections: list[dict] | None = None,
        repository_integrity_result: RepositoryIntegrityResult = RepositoryIntegrityResult.UNCHANGED,
        canonical_status: RepositoryStateClassification = RepositoryStateClassification.CANONICAL_CLEAN,
        actor: Actor = Actor.SYSTEM,
    ) -> PilotEvaluationRecord:
        gate = ReleaseGateService(self.session).evaluate(
            repository_state_id=repository_state_id,
            deterministic_evaluation_id=deterministic_evaluation_id,
            benchmark_before_id=benchmark_before_id,
            benchmark_after_id=benchmark_after_id,
            repository_integrity_result=repository_integrity_result,
            human_rubric_id=human_rubric_id,
        )
        row = PilotEvaluationRecordORM(
            id=next_id(self.session, "pilot_record"),
            target_repository_id=target_repository_id,
            repository_snapshot_id=repository_snapshot_id,
            repository_state_id=repository_state_id,
            task_id=task_id,
            plan_id=plan_id,
            plan_freeze_id=plan_freeze_id,
            deterministic_evaluation_id=deterministic_evaluation_id,
            human_rubric_id=human_rubric_id,
            benchmark_before_id=benchmark_before_id,
            benchmark_after_id=benchmark_after_id,
            learning_candidate_ids=learning_candidate_ids or [],
            corrections=corrections or [],
            repository_integrity_result=repository_integrity_result.value,
            canonical_status=canonical_status.value,
            autonomy_recommendation=gate.recommendation.value,
            gate_result=gate.model_dump(mode="json"),
            created_at=utc_now(),
            created_by=actor.value,
        )
        self.session.add(row)
        self.session.flush()
        self.audit.record(
            event_type="PILOT_EVALUATION_RECORD_CREATED",
            actor=actor.value,
            repository_id=target_repository_id,
            task_id=task_id,
            action="create_pilot_evaluation_record",
            result=gate.recommendation.value,
            metadata={"pilot_record_id": row.id, "gate_result": row.gate_result},
        )
        return _record_from_row(row, gate)


def _record_from_row(row: PilotEvaluationRecordORM, gate: ReleaseGateResult | None = None) -> PilotEvaluationRecord:
    gate_payload = gate.model_dump(mode="json") if gate else row.gate_result
    recommendation = gate.recommendation if gate else AutonomyRecommendation(row.autonomy_recommendation)
    return PilotEvaluationRecord(
        id=row.id,
        target_repository_id=row.target_repository_id,
        repository_snapshot_id=row.repository_snapshot_id,
        repository_state_id=row.repository_state_id,
        task_id=row.task_id,
        plan_id=row.plan_id,
        plan_freeze_id=row.plan_freeze_id,
        deterministic_evaluation_id=row.deterministic_evaluation_id,
        human_rubric_id=row.human_rubric_id,
        benchmark_before_id=row.benchmark_before_id,
        benchmark_after_id=row.benchmark_after_id,
        learning_candidate_ids=row.learning_candidate_ids,
        corrections=row.corrections,
        repository_integrity_result=row.repository_integrity_result,
        canonical_status=row.canonical_status,
        autonomy_recommendation=recommendation,
        gate_result=gate_payload,
        created_at=row.created_at,
        created_by=row.created_by,
    )
