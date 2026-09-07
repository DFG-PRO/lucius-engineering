from __future__ import annotations

from typing import Any


EXACT_DOCUMENTATION_TARGET_TYPES = {
    "EXACT_PATH",
    "REQUIRED_PATH",
    "NEW_DOCUMENT",
    "PROPOSED_PATH",
}


def canonicalize_documentation_requirements(requirements: list[Any] | None) -> list[dict[str, Any]]:
    return [
        canonicalize_documentation_requirement(requirement)
        for requirement in requirements or []
        if isinstance(requirement, dict)
    ]


def canonicalize_documentation_requirement(requirement: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy/path aliases into the canonical documentation contract."""

    canonical = dict(requirement)
    path = _clean(canonical.pop("path", None) or canonical.pop("target_path", None))
    target = _clean(canonical.get("target"))
    proposed_path = _clean(canonical.get("proposed_path"))
    canonical_target = _clean(canonical.get("canonical_target"))
    target_type = _clean(canonical.get("target_type") or canonical.get("kind")).upper()
    exact_required = bool(canonical.get("exact_path_required")) or target_type in EXACT_DOCUMENTATION_TARGET_TYPES

    if not target:
        target = path or proposed_path or canonical_target
    if path and not proposed_path and exact_required:
        proposed_path = path

    acceptable_paths = _ordered_strings(canonical.get("acceptable_paths", []))
    if path and not exact_required and path not in acceptable_paths:
        acceptable_paths.append(path)
    if target and _looks_like_documentation_path(target) and target not in acceptable_paths:
        acceptable_paths.append(target)

    canonical["target"] = target
    canonical["acceptable_paths"] = acceptable_paths
    canonical["acceptable_categories"] = _ordered_strings(canonical.get("acceptable_categories", []))
    if proposed_path:
        canonical["proposed_path"] = proposed_path
    if exact_required:
        canonical["exact_path_required"] = True
    elif "exact_path_required" in canonical:
        canonical["exact_path_required"] = False
    return canonical


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _ordered_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        cleaned = _clean(value)
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _looks_like_documentation_path(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("docs/") or lowered.endswith((".md", ".rst"))
