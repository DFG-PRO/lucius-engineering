from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import (
    Actor,
    KnowledgeScope,
    MemoryType,
    PlanningBlockerCode,
    ProjectStatus,
    RetrievalWarningCode,
    TaskStatus,
    ValidationStatus,
)
from lucius.memory.service import MemoryService
from lucius.persistence.orm import EvidenceReferenceORM, MemoryEntryORM, ProjectORM, RepositorySnapshotORM, TaskContractORM, TaskORM
from lucius.planning.schemas import (
    PlanningBlocker,
    PlanningContext,
    PlanningContextBudget,
    PlanningEvidenceItem,
    PlanningMemoryItem,
)
from lucius.repositories.schemas import WorkspaceContext
from lucius.retrieval.service import RetrievalService


class PlanningContextBuilder:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def build(
        self,
        *,
        task_id: str,
        snapshot_ids: list[str],
        workspace_contexts: dict[str, WorkspaceContext],
        run_id: str | None = None,
        budget: PlanningContextBudget | None = None,
        allow_candidate_memory: bool = False,
        actor: Actor = Actor.SYSTEM,
    ) -> tuple[PlanningContext | None, list[PlanningBlocker]]:
        budget = budget or PlanningContextBudget()
        task = self.session.get(TaskORM, task_id)
        if task is None:
            return None, [PlanningBlocker(code=PlanningBlockerCode.TASK_CONTRACT_INVALID, message="Unknown task.")]
        contract = self._active_contract(task.id)
        project = self.session.get(ProjectORM, task.project_id)
        blockers = self._preflight(task, contract, project, snapshot_ids)
        if blockers:
            self._record_blocked(task, blockers, actor=actor)
            return None, blockers

        retrieval = RetrievalService(self.session)
        package = retrieval.retrieve_for_task(
            task_id=task.id,
            task_run_id=run_id,
            snapshot_ids=snapshot_ids,
            workspace_contexts=workspace_contexts,
            max_results=budget.max_evidence_items * 2,
            max_total_bytes=budget.max_total_context_bytes,
            max_snippet_bytes=budget.max_per_source_bytes,
            actor=actor,
        )
        evidence_rows = [self.session.get(EvidenceReferenceORM, evidence_id) for evidence_id in package.evidence_ids]
        evidence_rows = [row for row in evidence_rows if row is not None]
        evidence_items, evidence_warnings, omitted_evidence = _bounded_evidence(evidence_rows, budget)
        memory_items, memory_warnings, omitted_memory = self._memory_items(
            project_id=task.project_id,
            query_terms=package.query_terms,
            budget=budget,
            allow_candidate_memory=allow_candidate_memory,
        )
        warnings = list(package.warnings) + evidence_warnings + memory_warnings
        warnings.extend(_detect_memory_conflicts(memory_items, evidence_items))
        context = PlanningContext(
            task_id=task.id,
            run_id=run_id,
            project_id=task.project_id,
            task_title=task.title,
            task_objective=task.objective,
            task_status=task.status,
            task_authority_level=task.authority_level,
            task_complexity=task.complexity,
            task_contract_id=contract.id,
            task_contract_version=contract.version,
            contract_objective=contract.objective,
            acceptance_criteria=contract.acceptance_criteria,
            constraints=contract.constraints,
            allowed_actions=contract.allowed_actions,
            environment=contract.environment,
            contract_authority_level=contract.authority_level,
            documentation_required=contract.documentation_required,
            documentation_targets=contract.documentation_targets,
            snapshot_metadata=[_snapshot_metadata(self.session.get(RepositorySnapshotORM, snapshot_id)) for snapshot_id in snapshot_ids],
            evidence=evidence_items,
            memory=memory_items,
            context_warnings=warnings,
            omitted_counts={"evidence": omitted_evidence, "memory": omitted_memory},
            total_context_bytes=_context_size(evidence_items, memory_items),
        )
        if omitted_evidence or omitted_memory or any(w.get("code") == RetrievalWarningCode.CONTEXT_BUDGET_REACHED.value for w in warnings):
            self.audit.record(
                event_type="PLANNING_CONTEXT_TRUNCATED",
                actor=actor.value,
                project_id=task.project_id,
                task_id=task.id,
                run_id=run_id,
                action="build_planning_context",
                result="WARNING",
                metadata={"omitted_counts": context.omitted_counts},
            )
        if any(w.get("code") == "MEMORY_CONFLICT_WITH_REPOSITORY" for w in warnings):
            self.audit.record(
                event_type="MEMORY_CONFLICT_WITH_REPOSITORY",
                actor=actor.value,
                project_id=task.project_id,
                task_id=task.id,
                run_id=run_id,
                action="build_planning_context",
                result="WARNING",
                metadata={"memory_ids": [w.get("memory_id") for w in warnings if w.get("code") == "MEMORY_CONFLICT_WITH_REPOSITORY"]},
            )
            for warning in warnings:
                if warning.get("code") == "MEMORY_CONFLICT_WITH_REPOSITORY" and warning.get("memory_id"):
                    MemoryService(self.session).mark_requires_revalidation(
                        warning["memory_id"],
                        reason="Planning detected conflict with current repository evidence",
                        actor=actor,
                    )
        self.audit.record(
            event_type="PLANNING_CONTEXT_CREATED",
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            run_id=run_id,
            action="build_planning_context",
            result="SUCCESS",
            metadata={
                "evidence_ids": [item.evidence_id for item in evidence_items],
                "memory_ids": [item.memory_id for item in memory_items],
                "warnings": warnings,
            },
        )
        return context, []

    def _preflight(
        self,
        task: TaskORM,
        contract: TaskContractORM | None,
        project: ProjectORM | None,
        snapshot_ids: list[str],
    ) -> list[PlanningBlocker]:
        blockers: list[PlanningBlocker] = []
        if project is None or project.status != ProjectStatus.ACTIVE.value:
            blockers.append(PlanningBlocker(code=PlanningBlockerCode.PROJECT_INACTIVE, message="Project is not active."))
        if task.status not in {TaskStatus.READY.value, TaskStatus.RUNNING.value, TaskStatus.BLOCKED.value}:
            blockers.append(PlanningBlocker(code=PlanningBlockerCode.TASK_NOT_READY, message="Task must be READY or RUNNING before planning."))
        if contract is None or not contract.acceptance_criteria:
            blockers.append(PlanningBlocker(code=PlanningBlockerCode.TASK_CONTRACT_INVALID, message="Task requires a valid TaskContract."))
        if contract and contract.repository_ids and not snapshot_ids:
            blockers.append(PlanningBlocker(code=PlanningBlockerCode.REPOSITORY_SNAPSHOT_REQUIRED, message="Repository snapshots are required for repository-bound planning."))
        return blockers

    def _memory_items(
        self,
        *,
        project_id: str,
        query_terms: list[str],
        budget: PlanningContextBudget,
        allow_candidate_memory: bool,
    ) -> tuple[list[PlanningMemoryItem], list[dict[str, Any]], int]:
        memory_service = MemoryService(self.session)
        result = memory_service.retrieve_memory(project_id=project_id, query_terms=query_terms)
        rows_by_id = {row.id: row for row in self.session.scalars(select(MemoryEntryORM)).all()}
        rows = [rows_by_id[match.memory_id] for match in result.matches if match.memory_id in rows_by_id]
        rows = [row for row in rows if _memory_allowed(row, project_id=project_id, allow_candidate_memory=allow_candidate_memory)]
        rows.sort(key=_memory_sort_key)
        selected: list[PlanningMemoryItem] = []
        warnings: list[dict[str, Any]] = []
        total_bytes = 0
        for row in rows:
            statement = row.statement[: budget.max_per_source_bytes]
            item_size = len(statement.encode("utf-8"))
            if len(selected) >= budget.max_memory_items or total_bytes + item_size > budget.max_total_context_bytes:
                break
            if row.requires_revalidation:
                warnings.append({"code": "MEMORY_REVALIDATION_REQUIRED", "memory_id": row.id})
            selected.append(
                PlanningMemoryItem(
                    memory_id=row.id,
                    scope=row.scope,
                    statement=statement,
                    validation_status=row.validation_status,
                    confidence=row.confidence,
                    requires_revalidation=bool(row.requires_revalidation),
                    source_evidence_ids=row.source_evidence_ids,
                )
            )
            total_bytes += item_size
        omitted = max(0, len(rows) - len(selected))
        if omitted:
            warnings.append({"code": "PLANNING_CONTEXT_BUDGET_REACHED", "limit": "memory", "omitted": omitted})
        return selected, warnings, omitted

    def _active_contract(self, task_id: str) -> TaskContractORM | None:
        return self.session.scalar(
            select(TaskContractORM).where(TaskContractORM.task_id == task_id).order_by(TaskContractORM.version.desc()).limit(1)
        )

    def _record_blocked(self, task: TaskORM, blockers: list[PlanningBlocker], *, actor: Actor) -> None:
        self.audit.record(
            event_type="PLANNING_BLOCKED",
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            action="build_planning_context",
            result="BLOCKED",
            metadata={"blockers": [blocker.model_dump(mode="json") for blocker in blockers]},
        )


def _bounded_evidence(
    rows: list[EvidenceReferenceORM],
    budget: PlanningContextBudget,
) -> tuple[list[PlanningEvidenceItem], list[dict[str, Any]], int]:
    ordered = sorted(rows, key=lambda row: (-row.relevance_score, row.id))
    selected: list[PlanningEvidenceItem] = []
    total_bytes = 0
    for row in ordered:
        snippet = (row.snippet or "")[: budget.max_per_source_bytes]
        item_size = len(snippet.encode("utf-8"))
        if len(selected) >= budget.max_evidence_items or total_bytes + item_size > budget.max_total_context_bytes:
            break
        selected.append(
            PlanningEvidenceItem(
                evidence_id=row.id,
                repository_id=row.repository_id,
                snapshot_id=row.snapshot_id,
                source_type=row.source_type,
                path=row.path,
                snippet=snippet,
                relevance_score=row.relevance_score,
                match_reasons=row.match_reasons,
            )
        )
        total_bytes += item_size
    omitted = max(0, len(ordered) - len(selected))
    warnings = []
    if omitted:
        warnings.append({"code": "PLANNING_CONTEXT_BUDGET_REACHED", "limit": "evidence", "omitted": omitted})
    return selected, warnings, omitted


def _memory_allowed(row: MemoryEntryORM, *, project_id: str, allow_candidate_memory: bool) -> bool:
    if row.scope == KnowledgeScope.CLIENT.value and row.project_id != project_id:
        return False
    if row.project_id and row.project_id != project_id:
        return False
    if row.validation_status == ValidationStatus.VALIDATED.value:
        return True
    return allow_candidate_memory and row.validation_status in {ValidationStatus.CANDIDATE.value, ValidationStatus.OBSERVED.value}


def _memory_sort_key(row: MemoryEntryORM) -> tuple[int, int, int, str]:
    scope_rank = {
        KnowledgeScope.PROJECT.value: 0,
        KnowledgeScope.DFG.value: 1,
        KnowledgeScope.DOMAIN.value: 2,
        KnowledgeScope.GLOBAL.value: 3,
    }.get(row.scope, 9)
    status_rank = 0 if row.validation_status == ValidationStatus.VALIDATED.value else 1
    revalidation_rank = 1 if row.requires_revalidation else 0
    return (scope_rank, status_rank, revalidation_rank, row.id)


def _snapshot_metadata(snapshot: RepositorySnapshotORM | None) -> dict[str, Any]:
    if snapshot is None:
        return {}
    return {
        "snapshot_id": snapshot.id,
        "repository_id": snapshot.repository_id,
        "branch": snapshot.branch,
        "commit_sha": snapshot.commit_sha,
        "manifest_hash": snapshot.manifest_hash,
        "is_dirty": bool(snapshot.is_dirty),
    }


def _context_size(evidence: list[PlanningEvidenceItem], memory: list[PlanningMemoryItem]) -> int:
    evidence_size = sum(len((item.snippet or "").encode("utf-8")) for item in evidence)
    memory_size = sum(len(item.statement.encode("utf-8")) for item in memory)
    return evidence_size + memory_size


def _detect_memory_conflicts(
    memory_items: list[PlanningMemoryItem],
    evidence_items: list[PlanningEvidenceItem],
) -> list[dict[str, Any]]:
    evidence_text = "\n".join(
        " ".join([item.path, item.source_type, item.snippet or "", " ".join(item.match_reasons)])
        for item in evidence_items
    ).lower()
    warnings: list[dict[str, Any]] = []
    for item in memory_items:
        statement = item.statement.lower()
        match = re.search(r"uses implementation ([a-z0-9_-]+)", statement)
        if not match:
            continue
        memory_impl = match.group(1)
        evidence_impls = set(re.findall(r"implementation[_ ]([a-z0-9_-]+)", evidence_text))
        if evidence_impls and memory_impl not in evidence_impls:
            warnings.append(
                {
                    "code": "MEMORY_CONFLICT_WITH_REPOSITORY",
                    "memory_id": item.memory_id,
                    "memory_claim": item.statement,
                    "evidence_signal": sorted(evidence_impls),
                }
            )
        elif (
            "snapshot reuse" in statement
            and ("code" in evidence_text or "repositories.py" in evidence_text)
            and f"implementation {memory_impl}" not in evidence_text
        ):
            warnings.append(
                {
                    "code": "MEMORY_CONFLICT_WITH_REPOSITORY",
                    "memory_id": item.memory_id,
                    "memory_claim": item.statement,
                    "evidence_signal": ["current snapshot reuse evidence differs from memory claim"],
                }
            )
    return warnings
