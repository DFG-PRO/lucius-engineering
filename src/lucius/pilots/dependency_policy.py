from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


EXECUTABLE_DEPENDENCY_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
PACKAGE_DEPENDENCY_TYPES = {
    "PACKAGE_DEPENDENCY",
    "RUNTIME_PACKAGE_DEPENDENCY",
    "BUILD_PACKAGE_DEPENDENCY",
}
NON_PACKAGE_DEPENDENCY_TYPES = {
    "ARTIFACT_DEPENDENCY",
    "ORCHESTRATION_ARTIFACT",
    "EVIDENCE_ARTIFACT",
    "PLAN_FREEZE",
    "QUEUE_STATE",
    "HUMAN_REVIEW",
    "BENCHMARK_RECORD",
}
VALID_DEPENDENCY_TYPES = PACKAGE_DEPENDENCY_TYPES | NON_PACKAGE_DEPENDENCY_TYPES


@dataclass(frozen=True)
class DependencyPolicyIssue:
    code: str
    item_id: str
    dependency: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "item_id": self.item_id,
            "dependency": self.dependency,
            "message": self.message,
        }


def validate_executable_dependencies(
    backlog: list[dict[str, Any]],
    *,
    historical_item_ids: set[str] | None = None,
) -> list[DependencyPolicyIssue]:
    """Validate queue dependency ids without interpreting prose as executable work."""

    active_item_ids = {_item_id(item) for item in backlog}
    historical = historical_item_ids or set()
    issues: list[DependencyPolicyIssue] = []
    for item in backlog:
        item_id = _item_id(item)
        for raw_dependency in item.get("dependencies", []) or []:
            dependency = str(raw_dependency).strip()
            if not dependency or not EXECUTABLE_DEPENDENCY_ID_PATTERN.match(dependency):
                issues.append(
                    DependencyPolicyIssue(
                        code="PROSE_OR_MALFORMED_EXECUTABLE_DEPENDENCY",
                        item_id=item_id,
                        dependency=dependency,
                        message="Executable queue dependencies must be canonical local item ids, not prose prerequisites or validation requirements.",
                    )
                )
            elif dependency in historical and dependency not in active_item_ids:
                issues.append(
                    DependencyPolicyIssue(
                        code="STALE_EXECUTABLE_DEPENDENCY",
                        item_id=item_id,
                        dependency=dependency,
                        message="Historical item ids must not satisfy active workflow dependencies.",
                    )
                )
            elif dependency not in active_item_ids:
                issues.append(
                    DependencyPolicyIssue(
                        code="UNKNOWN_EXECUTABLE_DEPENDENCY",
                        item_id=item_id,
                        dependency=dependency,
                        message="Executable dependency id is not present in the active local backlog.",
                    )
                )
    return issues


def package_dependency_change_expected(plan_payload: dict[str, Any], implementation_artifact: dict[str, Any]) -> bool:
    semantics = implementation_artifact.get("dependency_semantics", {})
    if "package_dependency_change_expected" in semantics:
        return bool(semantics["package_dependency_change_expected"])
    if "runtime_dependency_change_expected" in semantics:
        return bool(semantics["runtime_dependency_change_expected"])
    typed_dependencies = normalize_dependency_semantics(plan_payload.get("dependencies", []))
    if typed_dependencies:
        return any(item["type"] in PACKAGE_DEPENDENCY_TYPES for item in typed_dependencies)
    return bool(plan_payload.get("dependencies", []))


def normalize_dependency_semantics(dependencies: list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for dependency in dependencies or []:
        if not isinstance(dependency, dict):
            continue
        raw_type = str(dependency.get("type") or dependency.get("dependency_type") or "").upper()
        raw_value = str(dependency.get("id") or dependency.get("name") or dependency.get("artifact_id") or dependency.get("value") or "")
        if raw_type in VALID_DEPENDENCY_TYPES and raw_value:
            normalized.append({"type": raw_type, "value": raw_value})
    return normalized


def validate_typed_dependencies(dependencies: list[Any]) -> list[DependencyPolicyIssue]:
    issues: list[DependencyPolicyIssue] = []
    for index, dependency in enumerate(dependencies or []):
        if not isinstance(dependency, dict):
            continue
        raw_type = str(dependency.get("type") or dependency.get("dependency_type") or "").upper()
        raw_value = str(dependency.get("id") or dependency.get("name") or dependency.get("artifact_id") or dependency.get("value") or "")
        if raw_type not in VALID_DEPENDENCY_TYPES:
            issues.append(
                DependencyPolicyIssue(
                    code="UNKNOWN_TYPED_DEPENDENCY_SEMANTICS",
                    item_id=str(index),
                    dependency=raw_type,
                    message="Typed dependency semantics must distinguish package/runtime changes from orchestration artifacts.",
                )
            )
        elif not raw_value:
            issues.append(
                DependencyPolicyIssue(
                    code="MISSING_TYPED_DEPENDENCY_ID",
                    item_id=str(index),
                    dependency=raw_type,
                    message="Typed dependencies require a stable id, name, artifact_id, or value.",
                )
            )
    return issues


def _item_id(item: dict[str, Any]) -> str:
    return str(item.get("item_id") or item.get("task_id") or "")
