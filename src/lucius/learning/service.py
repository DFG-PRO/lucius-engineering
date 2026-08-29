from __future__ import annotations

from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import (
    Actor,
    KnowledgeScope,
    LearningCandidateStatus,
    LearningCandidateType,
    MemorySourceType,
    MemoryType,
    SanitizationStatus,
    ValidationStatus,
)
from lucius.memory.firewall import can_promote_candidate, classify_source, default_sanitization
from lucius.memory.validation import validate_confidence
from lucius.persistence.orm import LearningCandidateORM, ProjectORM, utc_now
from lucius.persistence.repositories import next_id


class LearningService:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def create_candidate(
        self,
        *,
        candidate_type: LearningCandidateType,
        statement: str,
        confidence: float = 0.5,
        project_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        evidence_ids: list[str] | None = None,
        source_memory_ids: list[str] | None = None,
        source_document_refs: list[str] | None = None,
        proposed_scope: KnowledgeScope = KnowledgeScope.PROJECT,
        sanitization_status: SanitizationStatus | None = None,
        actor: Actor = Actor.SYSTEM,
    ) -> LearningCandidateORM:
        validate_confidence(confidence)
        evidence = evidence_ids or []
        source_memory = source_memory_ids or []
        docs = source_document_refs or []
        if not evidence and not source_memory and not docs and candidate_type != LearningCandidateType.KNOWLEDGE_CORRECTION:
            raise ValueError("LearningCandidate requires source/provenance")
        project = self.session.get(ProjectORM, project_id) if project_id else None
        classification = classify_source(project, proposed_scope)
        sanitization = sanitization_status or default_sanitization(classification, proposed_scope)
        row = LearningCandidateORM(
            id=next_id(self.session, "learning"),
            project_id=project_id,
            run_id=run_id,
            task_id=task_id,
            candidate_type=candidate_type.value,
            statement=statement.strip(),
            evidence_ids=evidence,
            source_memory_ids=source_memory,
            source_document_refs=docs,
            confidence=confidence,
            status=LearningCandidateStatus.PENDING.value,
            proposed_scope=proposed_scope.value,
            sanitization_status=sanitization.value,
            source_classification=classification.value,
            created_at=utc_now(),
            updated_at=utc_now(),
            actor=actor.value,
        )
        self.session.add(row)
        self.session.flush()
        self.audit.record(
            event_type="LEARNING_CANDIDATE_CREATED",
            actor=actor.value,
            project_id=project_id,
            task_id=task_id,
            run_id=run_id,
            action="create_learning_candidate",
            result="SUCCESS",
            metadata={"candidate_id": row.id, "candidate_type": row.candidate_type, "proposed_scope": row.proposed_scope},
        )
        return row

    def create_from_documentation(
        self,
        *,
        project_id: str,
        source_document_ref: str,
        statement: str,
        candidate_type: LearningCandidateType,
        proposed_scope: KnowledgeScope = KnowledgeScope.PROJECT,
        evidence_ids: list[str] | None = None,
        actor: Actor = Actor.SYSTEM,
    ) -> LearningCandidateORM:
        return self.create_candidate(
            candidate_type=candidate_type,
            statement=statement,
            project_id=project_id,
            evidence_ids=evidence_ids or [],
            source_document_refs=[source_document_ref],
            proposed_scope=proposed_scope,
            actor=actor,
        )

    def create_human_correction(
        self,
        *,
        project_id: str | None,
        statement: str,
        reason: str,
        source_memory_ids: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        proposed_scope: KnowledgeScope = KnowledgeScope.PROJECT,
        actor: Actor = Actor.HUMAN,
    ) -> LearningCandidateORM:
        candidate = self.create_candidate(
            candidate_type=LearningCandidateType.KNOWLEDGE_CORRECTION,
            statement=statement,
            project_id=project_id,
            evidence_ids=evidence_ids or [],
            source_memory_ids=source_memory_ids or [],
            proposed_scope=proposed_scope,
            actor=actor,
        )
        self.audit.record(
            event_type="HUMAN_CORRECTION_CAPTURED",
            actor=actor.value,
            project_id=project_id,
            action="create_human_correction",
            result="SUCCESS",
            metadata={"candidate_id": candidate.id, "reason": reason},
        )
        return candidate

    def validate_candidate(self, candidate_id: str, *, notes: str | None = None, actor: Actor = Actor.HUMAN) -> LearningCandidateORM:
        row = self._candidate(candidate_id)
        row.status = LearningCandidateStatus.VALIDATED.value
        row.validated_at = utc_now()
        row.validation_notes = notes
        row.updated_at = utc_now()
        self.session.flush()
        self.audit.record(event_type="LEARNING_CANDIDATE_VALIDATED", actor=actor.value, project_id=row.project_id, task_id=row.task_id, run_id=row.run_id, action="validate_learning_candidate", result="SUCCESS", metadata={"candidate_id": row.id, "notes": notes})
        return row

    def reject_candidate(self, candidate_id: str, *, reason: str, actor: Actor = Actor.HUMAN) -> LearningCandidateORM:
        row = self._candidate(candidate_id)
        row.status = LearningCandidateStatus.REJECTED.value
        row.rejected_at = utc_now()
        row.rejection_reason = reason
        row.updated_at = utc_now()
        self.session.flush()
        self.audit.record(event_type="LEARNING_CANDIDATE_REJECTED", actor=actor.value, project_id=row.project_id, task_id=row.task_id, run_id=row.run_id, action="reject_learning_candidate", result="SUCCESS", metadata={"candidate_id": row.id, "reason": reason})
        return row

    def mark_needs_more_evidence(self, candidate_id: str, *, reason: str, actor: Actor = Actor.SYSTEM) -> LearningCandidateORM:
        row = self._candidate(candidate_id)
        row.status = LearningCandidateStatus.NEEDS_MORE_EVIDENCE.value
        row.rejection_reason = reason
        row.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="LEARNING_CANDIDATE_NEEDS_MORE_EVIDENCE",
            actor=actor.value,
            project_id=row.project_id,
            task_id=row.task_id,
            run_id=row.run_id,
            action="mark_needs_more_evidence",
            result="SUCCESS",
            metadata={"candidate_id": row.id, "reason": reason},
        )
        return row

    def promote_candidate(
        self,
        candidate_id: str,
        *,
        target_scope: KnowledgeScope,
        actor: Actor,
    ):
        candidate = self._candidate(candidate_id)
        allowed, reason = can_promote_candidate(candidate, target_scope=target_scope, actor=actor)
        if not allowed:
            self.audit.record(
                event_type="KNOWLEDGE_PROMOTION_BLOCKED",
                actor=actor.value,
                project_id=candidate.project_id,
                task_id=candidate.task_id,
                run_id=candidate.run_id,
                action="promote_candidate",
                result="BLOCKED",
                metadata={"candidate_id": candidate.id, "target_scope": target_scope.value, "reason": reason},
            )
            raise ValueError(reason or "Knowledge promotion blocked")
        from lucius.memory.service import MemoryService

        memory = MemoryService(self.session).create_memory(
            memory_type=MemoryType.SEMANTIC,
            scope=target_scope,
            statement=candidate.statement,
            confidence=candidate.confidence,
            validation_status=ValidationStatus.VALIDATED,
            project_id=None if target_scope == KnowledgeScope.GLOBAL else candidate.project_id,
            source_type=MemorySourceType.EVIDENCE_REFERENCE,
            source_reference=candidate.id,
            source_task_id=candidate.task_id,
            source_run_id=candidate.run_id,
            source_evidence_ids=candidate.evidence_ids,
            context_tags=["promoted"],
            created_by=actor,
        )
        self.audit.record(
            event_type="KNOWLEDGE_PROMOTED",
            actor=actor.value,
            project_id=candidate.project_id,
            task_id=candidate.task_id,
            run_id=candidate.run_id,
            action="promote_candidate",
            result="SUCCESS",
            metadata={"candidate_id": candidate.id, "memory_id": memory.id, "target_scope": target_scope.value},
        )
        return memory

    def _candidate(self, candidate_id: str) -> LearningCandidateORM:
        row = self.session.get(LearningCandidateORM, candidate_id)
        if row is None:
            raise ValueError(f"Unknown learning candidate: {candidate_id}")
        return row
