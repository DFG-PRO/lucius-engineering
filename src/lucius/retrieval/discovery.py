from __future__ import annotations

from pathlib import PurePosixPath

from lucius.domain.enums import RetrievalWarningCode, SourceType
from lucius.repositories.errors import LuciusRepositoryError, RepositoryErrorCode
from lucius.repositories.inspection import classify_config, classify_document, classify_test
from lucius.repositories.local_git import LocalGitRepositoryAdapter
from lucius.retrieval.schemas import CandidateSource, RetrievalRequest, SearchMatch


def source_type_for_path(path: str) -> SourceType:
    if classify_test(path):
        return SourceType.TEST
    if classify_document(path):
        return SourceType.DOCUMENTATION
    if classify_config(path):
        return SourceType.CONFIG
    return SourceType.CODE


def discover_candidates(
    *,
    request: RetrievalRequest,
    repository_id: str,
    snapshot_id: str,
    manifest_files: list[dict],
    adapter: LocalGitRepositoryAdapter,
) -> tuple[list[CandidateSource], list[dict]]:
    candidates: list[CandidateSource] = []
    warnings: list[dict] = []
    for record in sorted(manifest_files, key=lambda row: row["path"]):
        path = record["path"]
        source_type = source_type_for_path(path)
        if not _source_type_allowed(source_type, request):
            continue
        if record.get("is_sensitive"):
            warnings.append({"code": RetrievalWarningCode.SOURCE_SKIPPED_SENSITIVE.value, "path": path})
            continue
        if record.get("is_binary"):
            warnings.append({"code": RetrievalWarningCode.SOURCE_SKIPPED_BINARY.value, "path": path})
            continue
        if record.get("content_hash") is None:
            warnings.append({"code": RetrievalWarningCode.SOURCE_SKIPPED_LARGE.value, "path": path})
            continue
        try:
            text = adapter.read_file(path)
        except LuciusRepositoryError as error:
            code = _warning_for_error(error)
            warnings.append({"code": code.value, "path": path})
            continue
        matches = _matches_for_text(
            repository_id=repository_id,
            snapshot_id=snapshot_id,
            path=path,
            text=text,
            content_hash=record["content_hash"],
            query_terms=request.query_terms,
            max_snippet_bytes=request.max_snippet_bytes,
            source_type=source_type,
        )
        candidate = CandidateSource(
            repository_id=repository_id,
            snapshot_id=snapshot_id,
            path=path,
            source_type=source_type,
            size_bytes=record["size_bytes"],
            content_hash=record["content_hash"],
            matches=matches,
        )
        candidates.append(candidate)
    return candidates, warnings


def _source_type_allowed(source_type: SourceType, request: RetrievalRequest) -> bool:
    if request.requested_source_types and source_type not in request.requested_source_types:
        return False
    if source_type == SourceType.TEST and not request.include_tests:
        return False
    if source_type == SourceType.DOCUMENTATION and not request.include_documentation:
        return False
    if source_type == SourceType.CONFIG and not request.include_config:
        return False
    return source_type not in {SourceType.MEMORY, SourceType.EXTERNAL}


def _matches_for_text(
    *,
    repository_id: str,
    snapshot_id: str,
    path: str,
    text: str,
    content_hash: str,
    query_terms: list[str],
    max_snippet_bytes: int,
    source_type: SourceType,
) -> list[SearchMatch]:
    matches: list[SearchMatch] = []
    lower_path = path.lower()
    basename = PurePosixPath(path).name.lower()
    for line_number, line in enumerate(text.splitlines(), start=1):
        lower_line = line.lower()
        for term in query_terms:
            lower = term.lower()
            if lower not in lower_line and lower not in lower_path and lower not in basename:
                continue
            match_type = "content"
            if _is_technical_identifier(term) and lower in lower_line:
                match_type = "technical_identifier"
            elif source_type == SourceType.DOCUMENTATION and line.lstrip().startswith(("#", "=")):
                match_type = "heading"
            snippet = _bounded_snippet(line.strip(), max_snippet_bytes)
            matches.append(
                SearchMatch(
                    repository_id=repository_id,
                    snapshot_id=snapshot_id,
                    path=path,
                    line_start=line_number,
                    line_end=line_number,
                    matched_term=term,
                    match_type=match_type,
                    snippet=snippet,
                    content_hash=content_hash,
                )
            )
    if not matches:
        for term in query_terms:
            lower = term.lower()
            if lower in lower_path or lower in basename:
                matches.append(
                    SearchMatch(
                        repository_id=repository_id,
                        snapshot_id=snapshot_id,
                        path=path,
                        matched_term=term,
                        match_type="path",
                        snippet=None,
                        content_hash=content_hash,
                    )
                )
    return matches[:20]


def _bounded_snippet(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _is_technical_identifier(term: str) -> bool:
    return "_" in term or "." in term or "/" in term or any(char.isdigit() for char in term) or len(term) > 14


def _warning_for_error(error: LuciusRepositoryError) -> RetrievalWarningCode:
    if error.code == RepositoryErrorCode.BINARY_FILE:
        return RetrievalWarningCode.SOURCE_SKIPPED_BINARY
    if error.code == RepositoryErrorCode.FILE_TOO_LARGE:
        return RetrievalWarningCode.SOURCE_SKIPPED_LARGE
    if error.code == RepositoryErrorCode.PERMISSION_DENIED:
        return RetrievalWarningCode.SOURCE_SKIPPED_SENSITIVE
    return RetrievalWarningCode.SOURCE_MISSING

