from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lucius.runtime.schemas import RuntimeExecutionRequest, RuntimeExecutionResult


CLAIM_CLASSIFICATIONS = {
    "FACT",
    "DERIVED_VALUE",
    "ASSUMPTION",
    "PROPOSED_PARAMETER",
    "UNKNOWN",
}

_EVIDENCE_SENSITIVE_TERMS = {
    "evidence",
    "protocol",
    "profitability",
    "backtest",
    "oos",
    "out-of-sample",
    "walk-forward",
    "paper validation",
    "paper-validation",
    "capital",
    "notional",
    "strategy",
}
_QUANTITATIVE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:\d{4}-\d{2}-\d{2}|\$?\d+(?:,\d{3})*(?:\.\d+)?%?|\d+x|[A-Fa-f0-9]{8,})(?![A-Za-z0-9_])"
)
_SOURCE_TERMS = ("source", "evidence", "verified", "commit", "schema", "code", "artifact", "path", "repository", "from ", "per ")


def evidence_sensitive_provider_quality_issues(
    request: RuntimeExecutionRequest,
    result: RuntimeExecutionResult,
) -> list[dict[str, Any]]:
    """Return fail-closed issues for provider-written evidence-sensitive text."""

    if not _is_evidence_sensitive(request):
        return []
    workspace = Path(request.isolated_workspace).resolve()
    issues: list[dict[str, Any]] = []
    for relative_path in _written_paths(result):
        if not _is_text_path(relative_path):
            continue
        path = (workspace / relative_path).resolve()
        if not _is_relative_to(path, workspace) or not path.exists():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped or not _QUANTITATIVE_PATTERN.search(stripped):
                continue
            classification = _line_classification(stripped)
            if classification is None:
                issues.append(
                    {
                        "path": relative_path,
                        "line": line_number,
                        "reason": "QUANTITATIVE_CLAIM_MISSING_CLASSIFICATION",
                        "required_classifications": sorted(CLAIM_CLASSIFICATIONS),
                    }
                )
                continue
            if classification in {"FACT", "DERIVED_VALUE"} and not _has_source(stripped):
                issues.append(
                    {
                        "path": relative_path,
                        "line": line_number,
                        "reason": "FACT_OR_DERIVED_VALUE_MISSING_EVIDENCE_REFERENCE",
                        "classification": classification,
                    }
                )
    return issues


def _is_evidence_sensitive(request: RuntimeExecutionRequest) -> bool:
    limits = request.context_limits or {}
    if limits.get("evidence_sensitive") is True:
        return True
    haystack = " ".join(
        [
            request.task_intent or "",
            request.logical_task_id or "",
            " ".join(request.required_capabilities or []),
        ]
    ).lower()
    return any(term in haystack for term in _EVIDENCE_SENSITIVE_TERMS)


def _written_paths(result: RuntimeExecutionResult) -> list[str]:
    paths: list[str] = []
    for item in [*result.output_artifact_refs, *result.evidence]:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("path")
        if isinstance(raw_path, str):
            paths.append(raw_path)
        for file_item in item.get("files", []) if isinstance(item.get("files"), list) else []:
            if isinstance(file_item, dict) and isinstance(file_item.get("path"), str):
                paths.append(file_item["path"])
    return list(dict.fromkeys(paths))


def _is_text_path(path: str) -> bool:
    return Path(path).suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}


def _line_classification(line: str) -> str | None:
    normalized = line.upper()
    for classification in CLAIM_CLASSIFICATIONS:
        if re.search(rf"\b{classification}\b", normalized):
            return classification
    return None


def _has_source(line: str) -> bool:
    lowered = line.lower()
    return any(term in lowered for term in _SOURCE_TERMS)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
