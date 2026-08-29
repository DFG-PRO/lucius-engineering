from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import EvidenceStatus
from lucius.evidence.schemas import EvidenceReferenceCreate, EvidenceStatusResult
from lucius.persistence.orm import EvidenceReferenceORM, RepositoryRegistrationORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.repositories.errors import LuciusRepositoryError, RepositoryErrorCode
from lucius.repositories.hashing import hash_file
from lucius.repositories.local_git import LocalGitRepositoryAdapter
from lucius.repositories.schemas import WorkspaceContext


class EvidenceService:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def capture(self, evidence: EvidenceReferenceCreate, *, actor: str = "SYSTEM") -> EvidenceReferenceORM:
        row = EvidenceReferenceORM(
            id=next_id(self.session, "evidence"),
            project_id=evidence.project_id,
            repository_id=evidence.repository_id,
            snapshot_id=evidence.snapshot_id,
            task_id=evidence.task_id,
            task_run_id=evidence.task_run_id,
            source_type=evidence.source_type.value,
            path=evidence.path,
            line_start=evidence.line_start,
            line_end=evidence.line_end,
            content_hash=evidence.content_hash,
            snippet=evidence.snippet,
            claim=evidence.claim,
            relevance_score=evidence.relevance_score,
            match_reasons=evidence.match_reasons,
            captured_at=utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        self.audit.record(
            event_type="EVIDENCE_CAPTURED",
            actor=actor,
            project_id=row.project_id,
            repository_id=row.repository_id,
            task_id=row.task_id,
            run_id=row.task_run_id,
            action="capture_evidence",
            result="SUCCESS",
            metadata={
                "evidence_id": row.id,
                "snapshot_id": row.snapshot_id,
                "path": row.path,
                "source_type": row.source_type,
                "relevance_score": row.relevance_score,
            },
        )
        return row

    def check_status(self, evidence_id: str, workspace_context: WorkspaceContext) -> EvidenceStatusResult:
        evidence = self.session.get(EvidenceReferenceORM, evidence_id)
        if evidence is None:
            raise ValueError(f"Unknown evidence reference: {evidence_id}")
        registration = self.session.get(RepositoryRegistrationORM, evidence.repository_id)
        if registration is None:
            status = EvidenceStatus.MISSING
        else:
            adapter = LocalGitRepositoryAdapter(registration.location, workspace_context)
            try:
                path = adapter._resolve_repo_file(evidence.path)
                current_hash = hash_file(Path(path))
                status = EvidenceStatus.CURRENT if current_hash == evidence.content_hash else EvidenceStatus.STALE
            except LuciusRepositoryError as error:
                if error.code in {RepositoryErrorCode.REPOSITORY_NOT_FOUND, RepositoryErrorCode.UNSAFE_PATH}:
                    status = EvidenceStatus.MISSING
                else:
                    status = EvidenceStatus.STALE
        return EvidenceStatusResult(evidence_id=evidence.id, status=status, checked_at=utc_now())

