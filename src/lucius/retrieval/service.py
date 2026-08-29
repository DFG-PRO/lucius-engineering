from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, RetrievalWarningCode, SourceType
from lucius.domain.ids import format_public_id
from lucius.evidence.schemas import EvidenceReferenceCreate
from lucius.evidence.service import EvidenceService
from lucius.persistence.orm import (
    EvidenceReferenceORM,
    ProjectRepositoryAttachmentORM,
    RepositoryRegistrationORM,
    RepositorySnapshotORM,
    TaskContractORM,
    TaskORM,
    TaskRunORM,
    utc_now,
)
from lucius.repositories.local_git import LocalGitRepositoryAdapter
from lucius.repositories.schemas import WorkspaceContext
from lucius.retrieval.discovery import discover_candidates
from lucius.retrieval.query import derive_query_terms
from lucius.retrieval.ranking import WEIGHTS, rank_candidates
from lucius.retrieval.schemas import CandidateSource, CoverageItem, RankedSource, RetrievalRequest, SearchMatch, TechnicalContextPackage


class RetrievalService:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)
        self.evidence = EvidenceService(session)

    def build_request(
        self,
        *,
        task_id: str,
        snapshot_ids: list[str],
        task_run_id: str | None = None,
        explicit_terms: list[str] | None = None,
        requested_source_types: list[SourceType] | None = None,
        max_results: int = 10,
        max_total_bytes: int = 24_000,
        max_snippet_bytes: int = 600,
        include_tests: bool = True,
        include_documentation: bool = True,
        include_config: bool = True,
    ) -> RetrievalRequest:
        task = self._task(task_id)
        contract = self._active_contract(task.id)
        if contract is None:
            raise ValueError("Task requires an active TaskContract for retrieval")
        repository_ids = list(dict.fromkeys(contract.repository_ids))
        query_terms = derive_query_terms(
            task_title=task.title,
            task_objective=task.objective,
            contract_objective=contract.objective,
            acceptance_criteria=contract.acceptance_criteria,
            explicit_terms=explicit_terms,
        )
        return RetrievalRequest(
            task_id=task.id,
            task_run_id=task_run_id,
            project_id=task.project_id,
            repository_ids=repository_ids,
            snapshot_ids=list(dict.fromkeys(snapshot_ids)),
            query_terms=query_terms,
            requested_source_types=requested_source_types or [SourceType.CODE, SourceType.DOCUMENTATION, SourceType.TEST, SourceType.CONFIG, SourceType.GIT],
            max_results=max_results,
            max_total_bytes=max_total_bytes,
            max_snippet_bytes=max_snippet_bytes,
            include_tests=include_tests,
            include_documentation=include_documentation,
            include_config=include_config,
            created_at=utc_now(),
        )

    def retrieve_for_task(
        self,
        *,
        task_id: str,
        snapshot_ids: list[str],
        workspace_contexts: dict[str, WorkspaceContext],
        task_run_id: str | None = None,
        explicit_terms: list[str] | None = None,
        requested_source_types: list[SourceType] | None = None,
        max_results: int = 10,
        max_total_bytes: int = 24_000,
        max_snippet_bytes: int = 600,
        include_tests: bool = True,
        include_documentation: bool = True,
        include_config: bool = True,
        actor: Actor = Actor.SYSTEM,
    ) -> TechnicalContextPackage:
        request = self.build_request(
            task_id=task_id,
            task_run_id=task_run_id,
            snapshot_ids=snapshot_ids,
            explicit_terms=explicit_terms,
            requested_source_types=requested_source_types,
            max_results=max_results,
            max_total_bytes=max_total_bytes,
            max_snippet_bytes=max_snippet_bytes,
            include_tests=include_tests,
            include_documentation=include_documentation,
            include_config=include_config,
        )
        return self.retrieve(request=request, workspace_contexts=workspace_contexts, actor=actor)

    def retrieve(
        self,
        *,
        request: RetrievalRequest,
        workspace_contexts: dict[str, WorkspaceContext],
        actor: Actor = Actor.SYSTEM,
    ) -> TechnicalContextPackage:
        task = self._task(request.task_id)
        if task.project_id != request.project_id:
            raise ValueError("RetrievalRequest project does not match Task project")
        if request.task_run_id:
            run = self.session.get(TaskRunORM, request.task_run_id)
            if run is None or run.task_id != task.id:
                raise ValueError("TaskRun does not belong to retrieval Task")
        self.audit.record(
            event_type="RETRIEVAL_STARTED",
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            run_id=request.task_run_id,
            action="retrieve",
            result="STARTED",
            metadata={"snapshot_ids": request.snapshot_ids, "query_terms": request.query_terms},
        )
        candidates = []
        warnings: list[dict[str, Any]] = []
        for snapshot_id in request.snapshot_ids:
            snapshot = self._authorized_snapshot(task, snapshot_id)
            registration = self.session.get(RepositoryRegistrationORM, snapshot.repository_id)
            if registration is None:
                raise ValueError(f"Repository missing for snapshot: {snapshot_id}")
            workspace_context = workspace_contexts.get(registration.id)
            if workspace_context is None:
                raise ValueError(f"Missing WorkspaceContext for repository: {registration.id}")
            adapter = LocalGitRepositoryAdapter(registration.location, workspace_context)
            stale_warning = self._snapshot_stale_warning(snapshot, adapter)
            if stale_warning:
                warnings.append(stale_warning)
                self.audit.record(
                    event_type="SNAPSHOT_STALE_DETECTED",
                    actor=actor.value,
                    project_id=task.project_id,
                    repository_id=registration.id,
                    task_id=task.id,
                    run_id=request.task_run_id,
                    action="retrieve",
                    result="WARNING",
                    metadata={"snapshot_id": snapshot.id},
                )
                continue
            if snapshot.is_dirty:
                warnings.append({"code": RetrievalWarningCode.REPOSITORY_DIRTY.value, "snapshot_id": snapshot.id})
            git_candidate = self._git_candidate(snapshot, registration.id, request)
            if git_candidate:
                candidates.append(git_candidate)
            manifest_files = snapshot.manifest.get("files", [])
            discovered, discovery_warnings = discover_candidates(
                request=request,
                repository_id=registration.id,
                snapshot_id=snapshot.id,
                manifest_files=manifest_files,
                adapter=adapter,
            )
            candidates.extend(discovered)
            warnings.extend(discovery_warnings)

        ranked = rank_candidates(candidates, request)
        selected, budget_warnings = self._apply_budget(ranked, request)
        warnings.extend(budget_warnings)
        evidence_rows = self.capture_evidence(request=request, ranked_sources=selected, actor=actor)
        package = self.build_context_package(request=request, ranked_sources=selected, evidence_rows=evidence_rows, warnings=warnings)
        final_event = "RETRIEVAL_COMPLETED" if evidence_rows else "RETRIEVAL_EMPTY"
        if not evidence_rows:
            package.warnings.append({"code": RetrievalWarningCode.NO_RELEVANT_EVIDENCE.value})
        self.audit.record(
            event_type=final_event,
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            run_id=request.task_run_id,
            action="retrieve",
            result="SUCCESS" if evidence_rows else "EMPTY",
            metadata={
                "evidence_count": len(evidence_rows),
                "total_sources": package.total_sources,
                "warnings": package.warnings,
            },
        )
        self.audit.record(
            event_type="CONTEXT_PACKAGE_CREATED",
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            run_id=request.task_run_id,
            action="build_context_package",
            result="SUCCESS",
            metadata={"context_package_id": package.id, "evidence_ids": package.evidence_ids},
        )
        return package

    def capture_evidence(
        self,
        *,
        request: RetrievalRequest,
        ranked_sources: list[RankedSource],
        actor: Actor = Actor.SYSTEM,
    ) -> list[EvidenceReferenceORM]:
        rows: list[EvidenceReferenceORM] = []
        for source in ranked_sources:
            rows.append(
                self.evidence.capture(
                    EvidenceReferenceCreate(
                        project_id=request.project_id,
                        repository_id=source.repository_id,
                        snapshot_id=source.snapshot_id,
                        task_id=request.task_id,
                        task_run_id=request.task_run_id,
                        source_type=source.source_type,
                        path=source.path,
                        line_start=source.line_start,
                        line_end=source.line_end,
                        content_hash=source.content_hash,
                        snippet=source.snippet,
                        relevance_score=source.score,
                        match_reasons=source.match_reasons,
                    ),
                    actor=actor.value,
                )
            )
        return rows

    def build_context_package(
        self,
        *,
        request: RetrievalRequest,
        ranked_sources: list[RankedSource],
        evidence_rows: list[EvidenceReferenceORM],
        warnings: list[dict[str, Any]],
    ) -> TechnicalContextPackage:
        evidence_ids = [row.id for row in evidence_rows]
        package_payload = {
            "task_id": request.task_id,
            "task_run_id": request.task_run_id,
            "project_id": request.project_id,
            "snapshot_ids": request.snapshot_ids,
            "query_terms": request.query_terms,
            "evidence_paths": [source.path for source in ranked_sources],
        }
        package_id = format_public_id("context_package", int(_stable_small_number(package_payload)))
        total_bytes = sum(len((source.snippet or "").encode("utf-8")) for source in ranked_sources)
        return TechnicalContextPackage(
            id=package_id,
            task_id=request.task_id,
            task_run_id=request.task_run_id,
            project_id=request.project_id,
            snapshot_ids=request.snapshot_ids,
            query_terms=request.query_terms,
            evidence_ids=evidence_ids,
            ranked_sources=ranked_sources,
            total_sources=len(ranked_sources),
            total_bytes=total_bytes,
            coverage_summary=self._coverage_summary(request, ranked_sources, evidence_ids),
            warnings=warnings,
            created_at=utc_now(),
        )

    def _apply_budget(self, ranked: list[RankedSource], request: RetrievalRequest) -> tuple[list[RankedSource], list[dict]]:
        selected: list[RankedSource] = []
        total = 0
        warnings: list[dict] = []
        for source in ranked:
            if len(selected) >= request.max_results:
                warnings.append({"code": RetrievalWarningCode.CONTEXT_BUDGET_REACHED.value, "limit": "max_results"})
                break
            snippet_bytes = len((source.snippet or "").encode("utf-8"))
            if snippet_bytes > request.max_snippet_bytes:
                source.snippet = source.snippet.encode("utf-8")[: request.max_snippet_bytes].decode("utf-8", errors="ignore")
                snippet_bytes = len(source.snippet.encode("utf-8"))
            if total + snippet_bytes > request.max_total_bytes:
                warnings.append({"code": RetrievalWarningCode.CONTEXT_BUDGET_REACHED.value, "limit": "max_total_bytes"})
                break
            selected.append(source)
            total += snippet_bytes
        return selected, warnings

    def _coverage_summary(
        self,
        request: RetrievalRequest,
        ranked_sources: list[RankedSource],
        evidence_ids: list[str],
    ) -> dict[str, list[CoverageItem] | dict[str, int]]:
        contract = self._active_contract(request.task_id)
        ac_items: list[CoverageItem] = []
        if contract:
            for ac in contract.acceptance_criteria:
                ac_terms = set(derive_query_terms(
                    task_title="",
                    task_objective="",
                    contract_objective="",
                    acceptance_criteria=[ac],
                    explicit_terms=[],
                ))
                ac_terms -= {"evidence", "found", "required", "requires", "does", "not", "no", "exist", "exists"}
                matched = sorted(ac_terms.intersection({term for source in ranked_sources for term in source.matched_terms}))
                ac_items.append(
                    CoverageItem(
                        key=str(ac.get("id", "")),
                        status="EVIDENCE_FOUND" if matched else "NO_EVIDENCE_FOUND",
                        evidence_ids=evidence_ids if matched else [],
                        matched_terms=matched,
                    )
                )
        term_counts = Counter(term for source in ranked_sources for term in source.matched_terms)
        return {"acceptance_criteria": ac_items, "query_terms": dict(sorted(term_counts.items()))}

    def _authorized_snapshot(self, task: TaskORM, snapshot_id: str) -> RepositorySnapshotORM:
        snapshot = self.session.get(RepositorySnapshotORM, snapshot_id)
        if snapshot is None:
            raise ValueError(f"Unknown repository snapshot: {snapshot_id}")
        attachment = self.session.scalar(
            select(ProjectRepositoryAttachmentORM).where(
                ProjectRepositoryAttachmentORM.project_id == task.project_id,
                ProjectRepositoryAttachmentORM.repository_id == snapshot.repository_id,
            )
        )
        if attachment is None:
            raise ValueError("Snapshot repository is not attached to the Task project")
        return snapshot

    def _snapshot_stale_warning(
        self,
        snapshot: RepositorySnapshotORM,
        adapter: LocalGitRepositoryAdapter,
    ) -> dict[str, Any] | None:
        current = adapter.build_snapshot()
        if (
            current.git_state.commit_sha != snapshot.commit_sha
            or current.git_state.branch != snapshot.branch
            or current.manifest_summary.manifest_hash != snapshot.manifest_hash
            or current.git_state.is_dirty != bool(snapshot.is_dirty)
        ):
            return {"code": RetrievalWarningCode.SNAPSHOT_STALE.value, "snapshot_id": snapshot.id}
        return None

    def _git_candidate(
        self,
        snapshot: RepositorySnapshotORM,
        repository_id: str,
        request: RetrievalRequest,
    ) -> CandidateSource | None:
        if request.requested_source_types and SourceType.GIT not in request.requested_source_types:
            return None
        text = f"branch {snapshot.branch or 'DETACHED'} commit {snapshot.commit_sha} dirty {bool(snapshot.is_dirty)}"
        matches = []
        for term in request.query_terms:
            if term.lower() in text.lower() or term.lower() in {"git", "branch", "commit", "dirty"}:
                matches.append(
                    SearchMatch(
                        repository_id=repository_id,
                        snapshot_id=snapshot.id,
                        path="GIT_STATE",
                        matched_term=term,
                        match_type="content",
                        snippet=text,
                        content_hash=snapshot.manifest_hash,
                    )
                )
        if not matches:
            return None
        return CandidateSource(
            repository_id=repository_id,
            snapshot_id=snapshot.id,
            path="GIT_STATE",
            source_type=SourceType.GIT,
            size_bytes=len(text.encode("utf-8")),
            content_hash=snapshot.manifest_hash,
            matches=matches,
        )

    def _task(self, task_id: str) -> TaskORM:
        task = self.session.get(TaskORM, task_id)
        if task is None:
            raise ValueError(f"Unknown task: {task_id}")
        return task

    def _active_contract(self, task_id: str) -> TaskContractORM | None:
        return self.session.scalar(
            select(TaskContractORM)
            .where(TaskContractORM.task_id == task_id)
            .order_by(TaskContractORM.version.desc())
            .limit(1)
        )


def _stable_small_number(payload: dict[str, Any]) -> int:
    import hashlib
    import json

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return int(hashlib.sha256(encoded).hexdigest()[:12], 16) % 999999 + 1
