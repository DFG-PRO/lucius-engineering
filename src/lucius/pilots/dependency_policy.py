from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


EXECUTABLE_DEPENDENCY_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")


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
    return bool(plan_payload.get("dependencies", []))


def _item_id(item: dict[str, Any]) -> str:
    return str(item.get("item_id") or item.get("task_id") or "")
