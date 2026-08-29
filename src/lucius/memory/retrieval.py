from __future__ import annotations

from lucius.domain.enums import KnowledgeScope, MemoryType, ValidationStatus
from lucius.memory.schemas import MemoryMatch
from lucius.persistence.orm import MemoryEntryORM
from lucius.retrieval.query import tokenize_terms

DEFAULT_EXCLUDED_STATUSES = {
    ValidationStatus.SUPERSEDED.value,
    ValidationStatus.DEPRECATED.value,
    ValidationStatus.CONTRADICTED.value,
}

SCOPE_RANK = {
    KnowledgeScope.PROJECT.value: 50,
    KnowledgeScope.DFG.value: 35,
    KnowledgeScope.DOMAIN.value: 30,
    KnowledgeScope.GLOBAL.value: 20,
    KnowledgeScope.CLIENT.value: 15,
    KnowledgeScope.SESSION.value: 10,
}

STATUS_RANK = {
    ValidationStatus.VALIDATED.value: 30,
    ValidationStatus.CANDIDATE.value: 12,
    ValidationStatus.OBSERVED.value: 8,
    ValidationStatus.CONTRADICTED.value: -100,
    ValidationStatus.SUPERSEDED.value: -50,
    ValidationStatus.DEPRECATED.value: -50,
}


def rank_memory(
    rows: list[MemoryEntryORM],
    *,
    project_id: str | None = None,
    query_terms: list[str] | None = None,
) -> list[MemoryMatch]:
    terms = query_terms or []
    matches = [_rank_row(row, project_id=project_id, query_terms=terms) for row in rows]
    matches = [match for match in matches if match.score > 0]
    return sorted(matches, key=lambda item: (-item.score, item.memory_id))


def _rank_row(row: MemoryEntryORM, *, project_id: str | None, query_terms: list[str]) -> MemoryMatch:
    score = 0.0
    reasons: list[str] = []
    if project_id and row.project_id == project_id and row.scope == KnowledgeScope.PROJECT.value:
        score += SCOPE_RANK[KnowledgeScope.PROJECT.value]
        reasons.append("matching project memory")
    else:
        score += SCOPE_RANK.get(row.scope, 0)
        reasons.append(f"scope: {row.scope}")
    score += STATUS_RANK.get(row.validation_status, 0)
    reasons.append(f"validation status: {row.validation_status}")
    if row.superseded_by_id is None:
        score += 5
        reasons.append("not superseded")
    if row.source_evidence_ids or row.source_task_id or row.source_run_id or row.source_reference:
        score += 5
        reasons.append("provenance available")
    text_tokens = set(tokenize_terms(row.statement))
    tag_tokens = {tag.lower() for tag in (row.context_tags or []) + (row.technology_tags or [])}
    for term in query_terms:
        lower = term.lower()
        if lower in text_tokens or lower in tag_tokens or lower in row.statement.lower():
            score += 10
            reasons.append(f"query term match: {term}")
    return MemoryMatch(
        memory_id=row.id,
        score=score,
        statement=row.statement,
        memory_type=MemoryType(row.memory_type),
        validation_status=ValidationStatus(row.validation_status),
        match_reasons=sorted(set(reasons)),
    )

