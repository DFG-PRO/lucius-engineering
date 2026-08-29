from __future__ import annotations

from pathlib import PurePosixPath

from lucius.domain.enums import SourceType
from lucius.retrieval.schemas import CandidateSource, RankedSource, RetrievalRequest

WEIGHTS = {
    "exact_path": 70,
    "basename": 48,
    "path_token": 14,
    "technical_identifier": 24,
    "content_match": 2,
    "documentation": 5,
    "test": 8,
    "config": 6,
}

SOURCE_TYPE_WEIGHTS = {
    SourceType.CODE: 6,
    SourceType.TEST: 5,
    SourceType.DOCUMENTATION: 4,
    SourceType.CONFIG: 4,
    SourceType.GIT: 3,
}


def rank_candidates(candidates: list[CandidateSource], request: RetrievalRequest) -> list[RankedSource]:
    ranked = [_rank(candidate, request) for candidate in candidates]
    ranked = [item for item in ranked if item.score > 0]
    return sorted(ranked, key=lambda item: (-item.score, item.path, item.repository_id, item.snapshot_id))


def _rank(candidate: CandidateSource, request: RetrievalRequest) -> RankedSource:
    path = candidate.path.lower()
    basename = PurePosixPath(candidate.path).name.lower()
    path_parts = set(PurePosixPath(candidate.path).parts)
    path_tokens = {part.lower() for part in path_parts}
    score = SOURCE_TYPE_WEIGHTS.get(candidate.source_type, 0)
    reasons: list[str] = [f"source type: {candidate.source_type.value}"]
    matched_terms: set[str] = set()

    for term in request.query_terms:
        lower = term.lower()
        if lower == path or lower in {candidate.path.lower(), basename}:
            score += WEIGHTS["exact_path"]
            reasons.append(f"exact path/name match: {term}")
            matched_terms.add(term)
        elif lower == basename or lower in basename:
            score += WEIGHTS["basename"]
            reasons.append(f"basename match: {term}")
            matched_terms.add(term)
        elif lower in path_tokens or lower in path:
            score += WEIGHTS["path_token"]
            reasons.append(f"path token match: {term}")
            matched_terms.add(term)

    for match in candidate.matches:
        matched_terms.add(match.matched_term)
        if match.match_type == "technical_identifier":
            score += WEIGHTS["technical_identifier"]
            reasons.append(f"technical identifier match: {match.matched_term}")
        elif match.match_type == "heading":
            score += WEIGHTS["documentation"]
            reasons.append(f"title/heading match: {match.matched_term}")
        else:
            score += WEIGHTS["content_match"]
            reasons.append(f"content match: {match.matched_term}")

    if candidate.source_type == SourceType.TEST:
        score += WEIGHTS["test"]
        reasons.append("test source relationship")
    elif candidate.source_type == SourceType.DOCUMENTATION:
        score += WEIGHTS["documentation"]
        reasons.append("documentation relationship")
    elif candidate.source_type == SourceType.CONFIG:
        score += WEIGHTS["config"]
        reasons.append("configuration relationship")

    best_match = candidate.matches[0] if candidate.matches else None
    normalized = score / 100.0
    return RankedSource(
        repository_id=candidate.repository_id,
        snapshot_id=candidate.snapshot_id,
        source_type=candidate.source_type,
        path=candidate.path,
        score=round(normalized, 4),
        matched_terms=sorted(matched_terms),
        match_reasons=sorted(set(reasons)),
        line_start=best_match.line_start if best_match else None,
        line_end=best_match.line_end if best_match else None,
        snippet=best_match.snippet if best_match else None,
        content_hash=candidate.content_hash,
        size_bytes=candidate.size_bytes,
    )
