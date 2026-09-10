from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from lucius.runtime.schemas import RuntimeExecutionOutcome, RuntimeExecutionRequest, RuntimeExecutionResult, RuntimeRetryability


SCHEMA_CONSTRAINT_CONTEXT_KEY = "schema_constraint"
SKELETON_METADATA_KEY = "schema_constrained_output_skeleton"


def schema_constrained_output_skeleton(context_limits: dict[str, Any]) -> str | None:
    contract = _contract(context_limits)
    if not contract:
        return None
    parts: list[str] = []
    for file_contract in _file_contracts(contract):
        path = file_contract.get("path")
        if not isinstance(path, str) or not path:
            continue
        sections = _string_list(file_contract.get("required_sections"))
        labels = _string_list(file_contract.get("required_labels"))
        parts.append(f"File: {path}")
        for section in sections:
            parts.append(section)
            for label in labels:
                parts.append(_placeholder_line(label))
            parts.append("")
    return "\n".join(parts).strip() or None


def enforce_schema_constrained_output(
    request: RuntimeExecutionRequest,
    result: RuntimeExecutionResult,
) -> RuntimeExecutionResult:
    if result.status.value != "COMPLETED":
        return result
    contract = _contract(request.context_limits)
    if not contract:
        return result

    workspace = Path(request.isolated_workspace).resolve()
    issues: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    for file_contract in _file_contracts(contract):
        file_issues, file_repairs = _enforce_file_contract(workspace, file_contract)
        issues.extend(file_issues)
        repairs.extend(file_repairs)

    if issues:
        return result.model_copy(
            update={
                "status": RuntimeExecutionOutcome.FAILED,
                "provider_native_status": result.provider_native_status or "COMPLETED_WITH_SCHEMA_CONSTRAINT_FAILURE",
                "retryability": RuntimeRetryability.NON_RETRYABLE,
                "failure_class": "SCHEMA_CONSTRAINT_VALIDATION_FAILED",
                "mutation_summary": "Provider output failed deterministic schema-constrained structural validation.",
                "provider_error_metadata": {
                    **result.provider_error_metadata,
                    "reason": "Schema-constrained execution requires mandatory structure without unsafe semantic repair.",
                    "schema_constraint_issues": issues,
                    "schema_constraint_repairs": repairs,
                },
            }
        )

    if repairs:
        metadata = {
            **result.verification_handoff_metadata,
            "schema_constraint": {"status": "PASS_WITH_STRUCTURAL_REPAIR", "repairs": repairs},
        }
        evidence = [
            *result.evidence,
            {
                "type": "schema_constrained_structural_repair",
                "repairs": repairs,
                "semantic_repair": False,
            },
        ]
        return result.model_copy(
            update={
                "provider_native_status": result.provider_native_status or "DONE_WITH_STRUCTURAL_REPAIR",
                "evidence": evidence,
                "verification_handoff_metadata": metadata,
            }
        )
    return result.model_copy(
        update={
            "verification_handoff_metadata": {
                **result.verification_handoff_metadata,
                "schema_constraint": {"status": "PASS", "repairs": []},
            }
        }
    )


def _enforce_file_contract(
    workspace: Path,
    file_contract: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    raw_path = file_contract.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return ([{"reason": "MALFORMED_SCHEMA_CONSTRAINT", "field": "path"}], repairs)
    target = (workspace / raw_path).resolve()
    if not _is_relative_to(target, workspace):
        return ([{"path": raw_path, "reason": "SCHEMA_CONSTRAINT_PATH_OUTSIDE_WORKSPACE"}], repairs)
    if not target.exists():
        return ([{"path": raw_path, "reason": "MISSING_REQUIRED_OUTPUT_FILE"}], repairs)
    content = target.read_text(encoding="utf-8")
    unsafe = _unsafe_content_issues(raw_path, content, file_contract)
    if unsafe:
        return (unsafe, repairs)

    required_sections = _string_list(file_contract.get("required_sections"))
    required_labels = _string_list(file_contract.get("required_labels"))
    repair_allowed = bool(file_contract.get("allow_safe_structural_repair"))
    missing_sections = [section for section in required_sections if section not in content]
    missing_labels = [label for label in required_labels if not re.search(rf"\b{re.escape(label)}\b", content)]
    if missing_sections or missing_labels:
        if not repair_allowed:
            return (
                [
                    {
                        "path": raw_path,
                        "reason": "MISSING_REQUIRED_STRUCTURE",
                        "missing_sections": missing_sections,
                        "missing_labels": missing_labels,
                    }
                ],
                repairs,
            )
        additions = _safe_structural_additions(missing_sections, missing_labels)
        if additions:
            target.write_text(_append_structural_additions(content, additions), encoding="utf-8")
            repairs.append(
                {
                    "path": raw_path,
                    "reason": "SAFE_STRUCTURAL_PLACEHOLDER_REPAIR",
                    "missing_sections": missing_sections,
                    "missing_labels": missing_labels,
                }
            )
        content = target.read_text(encoding="utf-8")
    remaining_sections = [section for section in required_sections if section not in content]
    remaining_labels = [label for label in required_labels if not re.search(rf"\b{re.escape(label)}\b", content)]
    if remaining_sections or remaining_labels:
        issues.append(
            {
                "path": raw_path,
                "reason": "MISSING_REQUIRED_STRUCTURE_AFTER_REPAIR",
                "missing_sections": remaining_sections,
                "missing_labels": remaining_labels,
            }
        )
    return (issues, repairs)


def _safe_structural_additions(missing_sections: list[str], missing_labels: list[str]) -> list[str]:
    additions: list[str] = []
    if missing_sections:
        additions.extend(missing_sections)
    if missing_labels:
        if not missing_sections:
            additions.append("## Structural Placeholders")
        additions.extend(_placeholder_line(label) for label in missing_labels)
    return additions


def _placeholder_line(label: str) -> str:
    return f"- {label}: NOT_ESTABLISHED; provider did not supply verified content for this required category."


def _append_structural_additions(content: str, additions: list[str]) -> str:
    return content.rstrip() + "\n\n" + "\n".join(additions).rstrip() + "\n"


def _unsafe_content_issues(path: str, content: str, file_contract: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for raw_pattern in _string_list(file_contract.get("forbidden_patterns")):
        if re.search(raw_pattern, content, re.IGNORECASE):
            issues.append({"path": path, "reason": "FORBIDDEN_SCHEMA_CONSTRAINED_CONTENT", "pattern": raw_pattern})
    return issues


def _contract(context_limits: dict[str, Any]) -> dict[str, Any]:
    raw = context_limits.get(SCHEMA_CONSTRAINT_CONTEXT_KEY)
    return raw if isinstance(raw, dict) else {}


def _file_contracts(contract: dict[str, Any]) -> list[dict[str, Any]]:
    files = contract.get("files")
    return [item for item in files if isinstance(item, dict)] if isinstance(files, list) else []


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
