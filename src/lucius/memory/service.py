from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import (
    Actor,
    EvidenceStatus,
    KnowledgeScope,
    LearningCandidateType,
    MemorySourceType,
    MemoryType,
    SanitizationStatus,
    ValidationStatus,
)
from lucius.evidence.service import EvidenceService
from lucius.learning.schemas import TaskExperienceResult
from lucius.memory.retrieval import DEFAULT_EXCLUDED_STATUSES, rank_memory
from lucius.memory.schemas import FailureExperience, MemoryRetrievalResult
from lucius.memory.validation import validate_memory_creation
from lucius.persistence.orm import EvidenceReferenceORM, MemoryEntryORM, ProjectORM, TaskRunORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.repositories.schemas import WorkspaceContext


class MemoryService:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def create_memory(
        self,
        *,
        memory_type: MemoryType,
        scope: KnowledgeScope,
        statement: str,
        confidence: float,
        validation_status: ValidationStatus = ValidationStatus.OBSERVED,
        project_id: str | None = None,
        organization_id: str | None = None,
        workspace_id: str | None = None,
        source_type: MemorySourceType | None = None,
        source_reference: str | None = None,
        source_project_id: str | None = None,
        source_task_id: str | None = None,
        source_run_id: str | None = None,
        source_evidence_ids: list[str] | None = None,
        context_tags: list[str] | None = None,
        technology_tags: list[str] | None = None,
        created_by: Actor = Actor.SYSTEM,
    ) -> MemoryEntryORM:
        evidence_ids = source_evidence_ids or []
        validate_memory_creation(
            scope=scope,
            project_id=project_id,
            validation_status=validation_status,
            confidence=confidence,
            source_reference=source_reference,
            source_task_id=source_task_id,
            source_run_id=source_run_id,
            source_evidence_ids=evidence_ids,
        )
        if scope == KnowledgeScope.GLOBAL and project_id:
            project = self.session.get(ProjectORM, project_id)
            if project and project.project_type == "CLIENT":
                self.audit.record(
                    event_type="KNOWLEDGE_PROMOTION_BLOCKED",
                    actor=created_by.value,
                    project_id=project_id,
                    action="create_memory",
                    result="BLOCKED",
                    metadata={"reason": "direct CLIENT -> GLOBAL memory blocked"},
                )
                raise ValueError("CLIENT-derived memory cannot directly become GLOBAL")
        row = MemoryEntryORM(
            id=next_id(self.session, "memory"),
            project_id=project_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            memory_type=memory_type.value,
            scope=scope.value,
            statement=statement.strip(),
            source_type=source_type.value if source_type else None,
            source_reference=source_reference,
            source_project_id=source_project_id,
            source_task_id=source_task_id,
            source_run_id=source_run_id,
            source_evidence_ids=evidence_ids,
            confidence=confidence,
            validation_status=validation_status.value,
            context_tags=context_tags or [],
            technology_tags=technology_tags or [],
            created_at=utc_now(),
            updated_at=utc_now(),
            validated_at=utc_now() if validation_status == ValidationStatus.VALIDATED else None,
            requires_revalidation=False,
            created_by=created_by.value,
            version=1,
        )
        self.session.add(row)
        self.session.flush()
        self.audit.record(
            event_type="MEMORY_CREATED",
            actor=created_by.value,
            project_id=project_id,
            task_id=source_task_id,
            run_id=source_run_id,
            action="create_memory",
            result="SUCCESS",
            metadata={"memory_id": row.id, "scope": row.scope, "memory_type": row.memory_type},
        )
        return row

    def get_memory(self, memory_id: str) -> MemoryEntryORM | None:
        return self.session.get(MemoryEntryORM, memory_id)

    def list_memory(
        self,
        *,
        project_id: str | None = None,
        scope: KnowledgeScope | None = None,
        memory_type: MemoryType | None = None,
        validation_status: ValidationStatus | None = None,
        include_excluded: bool = False,
    ) -> list[MemoryEntryORM]:
        stmt = select(MemoryEntryORM)
        if project_id:
            stmt = stmt.where(MemoryEntryORM.project_id == project_id)
        if scope:
            stmt = stmt.where(MemoryEntryORM.scope == scope.value)
        if memory_type:
            stmt = stmt.where(MemoryEntryORM.memory_type == memory_type.value)
        if validation_status:
            stmt = stmt.where(MemoryEntryORM.validation_status == validation_status.value)
        if not include_excluded:
            stmt = stmt.where(MemoryEntryORM.validation_status.not_in(DEFAULT_EXCLUDED_STATUSES))
        return list(self.session.scalars(stmt.order_by(MemoryEntryORM.id)).all())

    def retrieve_memory(
        self,
        *,
        project_id: str | None = None,
        scope: KnowledgeScope | None = None,
        memory_type: MemoryType | None = None,
        validation_status: ValidationStatus | None = None,
        technology_tags: list[str] | None = None,
        context_tags: list[str] | None = None,
        query_terms: list[str] | None = None,
        include_excluded: bool = False,
    ) -> MemoryRetrievalResult:
        stmt = select(MemoryEntryORM)
        if project_id and scope is None:
            stmt = stmt.where(
                (MemoryEntryORM.project_id == project_id)
                | (
                    (MemoryEntryORM.project_id.is_(None))
                    & (MemoryEntryORM.scope.in_([KnowledgeScope.DFG.value, KnowledgeScope.DOMAIN.value, KnowledgeScope.GLOBAL.value]))
                )
            )
        elif project_id:
            stmt = stmt.where(MemoryEntryORM.project_id == project_id)
        if scope:
            stmt = stmt.where(MemoryEntryORM.scope == scope.value)
        if memory_type:
            stmt = stmt.where(MemoryEntryORM.memory_type == memory_type.value)
        if validation_status:
            stmt = stmt.where(MemoryEntryORM.validation_status == validation_status.value)
        if not include_excluded:
            stmt = stmt.where(MemoryEntryORM.validation_status.not_in(DEFAULT_EXCLUDED_STATUSES))
        rows = list(self.session.scalars(stmt.order_by(MemoryEntryORM.id)).all())
        if technology_tags:
            wanted = {tag.lower() for tag in technology_tags}
            rows = [row for row in rows if wanted.intersection({tag.lower() for tag in row.technology_tags})]
        if context_tags:
            wanted = {tag.lower() for tag in context_tags}
            rows = [row for row in rows if wanted.intersection({tag.lower() for tag in row.context_tags})]
        matches = rank_memory(rows, project_id=project_id, query_terms=query_terms or [])
        self.audit.record(
            event_type="MEMORY_RETRIEVED",
            actor=Actor.SYSTEM.value,
            project_id=project_id,
            action="retrieve_memory",
            result="SUCCESS",
            metadata={"count": len(matches), "query_terms": query_terms or []},
        )
        return MemoryRetrievalResult(matches=matches, total=len(matches))

    def validate_memory(self, memory_id: str, *, actor: Actor = Actor.HUMAN, notes: str | None = None) -> MemoryEntryORM:
        row = self._memory(memory_id)
        if not row.source_evidence_ids and not row.source_reference and not row.source_task_id and not row.source_run_id:
            raise ValueError("VALIDATED memory requires provenance")
        row.validation_status = ValidationStatus.VALIDATED.value
        row.validated_at = utc_now()
        row.updated_at = utc_now()
        self.session.flush()
        self.audit.record(event_type="MEMORY_VALIDATED", actor=actor.value, project_id=row.project_id, action="validate_memory", result="SUCCESS", metadata={"memory_id": row.id, "notes": notes})
        return row

    def contradict_memory(self, memory_id: str, *, reason: str, actor: Actor = Actor.SYSTEM) -> MemoryEntryORM:
        row = self._memory(memory_id)
        row.validation_status = ValidationStatus.CONTRADICTED.value
        row.revalidation_reason = reason
        row.updated_at = utc_now()
        self.session.flush()
        self.audit.record(event_type="MEMORY_CONTRADICTED", actor=actor.value, project_id=row.project_id, action="contradict_memory", result="SUCCESS", metadata={"memory_id": row.id, "reason": reason})
        return row

    def supersede_memory(self, old_memory_id: str, new_memory_id: str, *, reason: str, actor: Actor = Actor.SYSTEM) -> MemoryEntryORM:
        if old_memory_id == new_memory_id:
            raise ValueError("Memory cannot supersede itself")
        old = self._memory(old_memory_id)
        new = self._memory(new_memory_id)
        if new.superseded_by_id == old.id or old.supersedes_id == new.id:
            raise ValueError("Circular supersession rejected")
        old.validation_status = ValidationStatus.SUPERSEDED.value
        old.superseded_by_id = new.id
        old.supersession_reason = reason
        old.updated_at = utc_now()
        new.supersedes_id = old.id
        new.updated_at = utc_now()
        self.session.flush()
        self.audit.record(event_type="MEMORY_SUPERSEDED", actor=actor.value, project_id=old.project_id or new.project_id, action="supersede_memory", result="SUCCESS", metadata={"old_memory_id": old.id, "new_memory_id": new.id, "reason": reason})
        return old

    def deprecate_memory(self, memory_id: str, *, reason: str, actor: Actor = Actor.SYSTEM) -> MemoryEntryORM:
        row = self._memory(memory_id)
        row.validation_status = ValidationStatus.DEPRECATED.value
        row.revalidation_reason = reason
        row.updated_at = utc_now()
        self.session.flush()
        self.audit.record(event_type="MEMORY_DEPRECATED", actor=actor.value, project_id=row.project_id, action="deprecate_memory", result="SUCCESS", metadata={"memory_id": row.id, "reason": reason})
        return row

    def mark_requires_revalidation(self, memory_id: str, *, reason: str, actor: Actor = Actor.SYSTEM) -> MemoryEntryORM:
        row = self._memory(memory_id)
        row.requires_revalidation = True
        row.revalidation_reason = reason
        row.updated_at = utc_now()
        self.session.flush()
        self.audit.record(event_type="MEMORY_REVALIDATION_REQUIRED", actor=actor.value, project_id=row.project_id, action="mark_requires_revalidation", result="SUCCESS", metadata={"memory_id": row.id, "reason": reason})
        return row

    def clear_revalidation_flag(self, memory_id: str, *, actor: Actor = Actor.HUMAN) -> MemoryEntryORM:
        row = self._memory(memory_id)
        if not row.source_evidence_ids and not row.source_reference:
            raise ValueError("Cannot clear revalidation without valid evidence or source reference")
        row.requires_revalidation = False
        row.revalidation_reason = None
        row.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="MEMORY_REVALIDATION_CLEARED",
            actor=actor.value,
            project_id=row.project_id,
            action="clear_revalidation_flag",
            result="SUCCESS",
            metadata={"memory_id": row.id},
        )
        return row

    def propagate_evidence_staleness(self, memory_id: str, workspace_contexts: dict[str, WorkspaceContext]) -> MemoryEntryORM:
        row = self._memory(memory_id)
        if not row.source_evidence_ids:
            return row
        statuses: list[EvidenceStatus] = []
        for evidence_id in row.source_evidence_ids:
            evidence = self.session.get(EvidenceReferenceORM, evidence_id)
            if evidence is None:
                statuses.append(EvidenceStatus.MISSING)
                continue
            context = workspace_contexts.get(evidence.repository_id)
            if context is None:
                statuses.append(EvidenceStatus.MISSING)
                continue
            statuses.append(EvidenceService(self.session).check_status(evidence_id, context).status)
        if statuses and all(status in {EvidenceStatus.STALE, EvidenceStatus.MISSING} for status in statuses):
            self.mark_requires_revalidation(row.id, reason="All evidence sources are stale or missing")
        elif any(status in {EvidenceStatus.STALE, EvidenceStatus.MISSING} for status in statuses):
            self.mark_requires_revalidation(row.id, reason="At least one evidence source is stale or missing; other evidence remains")
        return row

    def capture_task_experience(
        self,
        *,
        task_id: str,
        run_id: str,
        outcome: str,
        tests: str | None = None,
        summary: str | None = None,
        evidence_ids: list[str] | None = None,
        documentation_refs: list[str] | None = None,
        actor: Actor = Actor.SYSTEM,
        create_learning_candidate: bool = False,
    ) -> TaskExperienceResult:
        run = self.session.get(TaskRunORM, run_id)
        if run is None or run.task_id != task_id:
            raise ValueError("TaskRun does not belong to task")
        statement = f"Task {task_id} outcome: {outcome}. {summary or ''}".strip()
        memory = self.create_memory(
            memory_type=MemoryType.EPISODIC,
            scope=KnowledgeScope.PROJECT,
            project_id=self._task_project_id(task_id),
            statement=statement,
            confidence=0.7,
            validation_status=ValidationStatus.OBSERVED,
            source_type=MemorySourceType.TASK_RUN,
            source_task_id=task_id,
            source_run_id=run_id,
            source_evidence_ids=evidence_ids or [],
            context_tags=["task_experience"],
            created_by=actor,
        )
        candidates = []
        if create_learning_candidate:
            from lucius.learning.service import LearningService

            candidates.append(
                LearningService(self.session).create_candidate(
                    candidate_type=LearningCandidateType.PATTERN,
                    statement=statement,
                    project_id=memory.project_id,
                    task_id=task_id,
                    run_id=run_id,
                    evidence_ids=evidence_ids or [],
                    source_document_refs=documentation_refs or [],
                    proposed_scope=KnowledgeScope.PROJECT,
                    actor=actor,
                )
            )
        return TaskExperienceResult(memory=memory, learning_candidates=candidates, outcome="LEARNING_CANDIDATE_CREATED" if candidates else "NO_LEARNING_CANDIDATE")

    def capture_failure_memory(self, failure: FailureExperience, *, actor: Actor = Actor.SYSTEM) -> TaskExperienceResult:
        project_id = self._task_project_id(failure.task_id) if failure.task_id else None
        statement = f"Failure: {failure.what_failed}. Cause: {failure.root_cause or 'unknown'}. Fix: {failure.final_fix or 'unresolved'}."
        memory = self.create_memory(
            memory_type=MemoryType.EPISODIC,
            scope=KnowledgeScope.PROJECT if project_id else KnowledgeScope.SESSION,
            project_id=project_id,
            statement=statement,
            confidence=0.65,
            validation_status=ValidationStatus.OBSERVED,
            source_type=MemorySourceType.FAILURE_EXPERIENCE,
            source_task_id=failure.task_id,
            source_run_id=failure.run_id,
            source_evidence_ids=failure.evidence_ids,
            context_tags=["failure"],
            created_by=actor,
        )
        from lucius.learning.service import LearningService

        candidate = LearningService(self.session).create_candidate(
            candidate_type=LearningCandidateType.FAILURE_PATTERN,
            statement=statement,
            project_id=project_id,
            task_id=failure.task_id,
            run_id=failure.run_id,
            evidence_ids=failure.evidence_ids,
            proposed_scope=KnowledgeScope.PROJECT if project_id else KnowledgeScope.SESSION,
            actor=actor,
        )
        self.audit.record(event_type="FAILURE_MEMORY_CAPTURED", actor=actor.value, project_id=project_id, task_id=failure.task_id, run_id=failure.run_id, action="capture_failure_memory", result="SUCCESS", metadata={"memory_id": memory.id, "candidate_id": candidate.id})
        return TaskExperienceResult(memory=memory, learning_candidates=[candidate], outcome="FAILURE_PATTERN_CREATED")

    def _memory(self, memory_id: str) -> MemoryEntryORM:
        row = self.session.get(MemoryEntryORM, memory_id)
        if row is None:
            raise ValueError(f"Unknown memory entry: {memory_id}")
        return row

    def _task_project_id(self, task_id: str | None) -> str | None:
        if not task_id:
            return None
        from lucius.persistence.orm import TaskORM

        task = self.session.get(TaskORM, task_id)
        return task.project_id if task else None
