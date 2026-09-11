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
_CANONICAL_EVIDENCE_REFERENCE_PATTERN = re.compile(
    r"\b(?:LEVID_\d{6}|LPLAN_\d{6}_EVIDENCE_\d{3,6})\b"
)
_STRUCTURAL_JSON_COUNTER_PATTERN = re.compile(
    r'^\s*"?(?P<key>[A-Za-z_][A-Za-z0-9_-]*)"?\s*:\s*\d+(?:\.\d+)?\s*,?\s*$'
)
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_LONG_HEX_PATTERN = re.compile(r"^[0-9a-fA-F]{8,128}$")
_VERSION_TOKEN_PATTERN = re.compile(r"^v?\d+(?:\.\d+)*(?:[-_][A-Za-z0-9]+)*$", re.IGNORECASE)
_PROVENANCE_IDENTIFIER_CONTEXT_TERMS = {
    "artifact",
    "audit",
    "checksum",
    "commit",
    "digest",
    "freeze",
    "hash",
    "id",
    "identifier",
    "manifest",
    "plan",
    "repository",
    "repo",
    "revision",
    "sha",
    "snapshot",
    "task",
    "version",
    "workflow",
}
_SOURCE_TERMS = ("source", "evidence", "verified", "commit", "schema", "code", "artifact", "path", "repository", "from ", "per ")
_STRUCTURAL_NUMERIC_KEYS = {
    "categorized_count",
    "schema_version",
    "total_statements",
    "total_items",
    "total_records",
    "total_sections",
    "validation_count",
}


def evidence_sensitive_provider_quality_issues(
    request: RuntimeExecutionRequest,
    result: RuntimeExecutionResult,
) -> list[dict[str, Any]]:
    """Return fail-closed issues for provider-written evidence-sensitive text."""

    required_evidence_refs = bool(request.context_limits.get("evidence_reference_validation_required"))
    evidence_sensitive = _is_evidence_sensitive(request)
    if not evidence_sensitive and not required_evidence_refs:
        return []
    workspace = Path(request.isolated_workspace).resolve()
    issues: list[dict[str, Any]] = []
    allowed_evidence_refs = _allowed_evidence_refs(request)
    issues.extend(_evidence_reference_issues_from_payload(result, allowed_evidence_refs, required_evidence_refs))
    if not evidence_sensitive:
        return issues
    for relative_path in _written_paths(result):
        if not _is_text_path(relative_path):
            continue
        path = (workspace / relative_path).resolve()
        if not _is_relative_to(path, workspace) or not path.exists():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            for reference in _canonical_evidence_references(stripped):
                if reference not in allowed_evidence_refs:
                    issues.append(
                        {
                            "path": relative_path,
                            "line": line_number,
                            "reason": "FABRICATED_OR_UNAUTHORIZED_EVIDENCE_REFERENCE",
                            "evidence_reference": reference,
                        }
                    )
            matches = _claim_quantitative_matches(stripped)
            if not stripped or not matches:
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
                continue
            if classification in {"FACT", "DERIVED_VALUE"} and required_evidence_refs and not _has_allowed_evidence_reference(stripped, allowed_evidence_refs):
                issues.append(
                    {
                        "path": relative_path,
                        "line": line_number,
                        "reason": "FACT_OR_DERIVED_VALUE_MISSING_ALLOWED_EVIDENCE_REFERENCE",
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


def _claim_quantitative_matches(line: str) -> list[re.Match[str]]:
    if _is_structural_json_counter(line):
        return []
    return [
        match
        for match in _QUANTITATIVE_PATTERN.finditer(line)
        if not _is_provenance_identifier_token(line, match)
    ]


def _is_structural_json_counter(line: str) -> bool:
    match = _STRUCTURAL_JSON_COUNTER_PATTERN.fullmatch(line)
    if not match:
        return False
    key = match.group("key").lower()
    return key in _STRUCTURAL_NUMERIC_KEYS


def _is_provenance_identifier_token(line: str, match: re.Match[str]) -> bool:
    token = match.group(0).strip("`'\".,;:()[]{}")
    if not token:
        return True
    if token.startswith("$") or token.endswith("%"):
        return False
    if token.lower().endswith("x") and token[:-1].replace(".", "", 1).isdigit():
        return False
    before = line[: match.start()].lower()
    after = line[match.end() :].lower()
    context = " ".join([before[-48:], after[:24]])
    if _UUID_PATTERN.fullmatch(token):
        return True
    if _LONG_HEX_PATTERN.fullmatch(token) and any(char.isalpha() for char in token):
        return True
    if _has_identifier_context(context):
        return True
    if _VERSION_TOKEN_PATTERN.fullmatch(token):
        return "version" in context or "v" in token.lower()
    return False


def _has_identifier_context(context: str) -> bool:
    return any(re.search(rf"\b{re.escape(term)}\b", context) for term in _PROVENANCE_IDENTIFIER_CONTEXT_TERMS)


def _line_classification(line: str) -> str | None:
    normalized = line.upper()
    for classification in CLAIM_CLASSIFICATIONS:
        if re.search(rf"\b{classification}\b", normalized):
            return classification
    return None


def _has_source(line: str) -> bool:
    lowered = line.lower()
    return any(term in lowered for term in _SOURCE_TERMS)


def _allowed_evidence_refs(request: RuntimeExecutionRequest) -> set[str]:
    refs = set(request.release_evidence_refs or [])
    limits = request.context_limits or {}
    for key in ("allowed_evidence_refs", "authorized_evidence_refs"):
        value = limits.get(key)
        if isinstance(value, list):
            refs.update(str(item) for item in value)
    records = limits.get("evidence_records")
    if isinstance(records, list):
        for record in records:
            if isinstance(record, dict):
                for key in ("id", "reference", "evidence_reference"):
                    value = record.get(key)
                    if isinstance(value, str):
                        refs.add(value)
            elif isinstance(record, str):
                refs.add(record)
    return refs


def _canonical_evidence_references(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(0) for match in _CANONICAL_EVIDENCE_REFERENCE_PATTERN.finditer(text)))


def _has_allowed_evidence_reference(line: str, allowed_evidence_refs: set[str]) -> bool:
    references = _canonical_evidence_references(line)
    return bool(references and all(reference in allowed_evidence_refs for reference in references))


def _evidence_reference_issues_from_payload(
    result: RuntimeExecutionResult,
    allowed_evidence_refs: set[str],
    required_evidence_refs: bool,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for path, value in _walk_evidence_reference_fields(
        {
            "evidence": result.evidence,
            "verification": result.verification,
            "documentation": result.documentation,
            "provider_error_metadata": result.provider_error_metadata,
        }
    ):
        if isinstance(value, str):
            references = [value]
        elif isinstance(value, list):
            references = []
            malformed = [item for item in value if not isinstance(item, str)]
            if malformed:
                issues.append(
                    {
                        "path": path,
                        "reason": "MALFORMED_EVIDENCE_REFERENCE_FIELD",
                        "expected": "string or list[string]",
                    }
                )
                continue
            references = [str(item) for item in value]
        elif value is None and not required_evidence_refs:
            references = []
        else:
            issues.append(
                {
                    "path": path,
                    "reason": "MALFORMED_EVIDENCE_REFERENCE_FIELD",
                    "expected": "string or list[string]",
                }
            )
            continue
        for reference in references:
            if reference not in allowed_evidence_refs:
                issues.append(
                    {
                        "path": path,
                        "reason": "FABRICATED_OR_UNAUTHORIZED_EVIDENCE_REFERENCE",
                        "evidence_reference": reference,
                    }
                )
    if required_evidence_refs:
        for evidence in result.evidence:
            if (
                isinstance(evidence, dict)
                and evidence.get("type") == "provider_claim"
                and not evidence.get("evidence_reference")
                and not evidence.get("evidence_references")
            ):
                issues.append({"path": "evidence", "reason": "MISSING_REQUIRED_EVIDENCE_REFERENCE"})
    return issues


def _walk_evidence_reference_fields(value: Any, path: str = "$") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            next_path = f"{path}.{key}"
            normalized_key = str(key).lower()
            if normalized_key in {"evidence_reference", "evidence_references"}:
                found.append((next_path, item))
            found.extend(_walk_evidence_reference_fields(item, next_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_walk_evidence_reference_fields(item, f"{path}[{index}]"))
    return found


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
