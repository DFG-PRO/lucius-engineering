from __future__ import annotations

from pathlib import Path
from typing import Any

from lucius.repositories.git_mutation import GitMutationError, validate_relative_repo_path

SUPPORTED_DETERMINISTIC_ACCEPTANCE_CHECK_TYPES = {
    "exact_file_content",
    "file_exists",
    "file_contains",
    "file_not_contains",
}


class DeterministicAcceptanceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def verify_deterministic_acceptance(
    workspace: str | Path,
    checks: list[dict[str, Any]],
    *,
    authorized_paths: set[str],
) -> list[dict[str, Any]]:
    root = Path(workspace).resolve()
    verification: list[dict[str, Any]] = []
    normalized_checks = normalize_deterministic_acceptance_checks(
        checks,
        authorized_paths=authorized_paths,
    )

    for check in normalized_checks:
        check_type = check.get("type")
        path = str(check.get("path"))
        expected_text = check.get("expected_text")

        target = (root / path).resolve()
        if not _is_relative_to(target, root):
            raise DeterministicAcceptanceError(
                "INVALID_DETERMINISTIC_ACCEPTANCE_PATH",
                f"Deterministic acceptance path escapes workspace: {path}",
            )

        if not target.is_file():
            raise DeterministicAcceptanceError(
                "DETERMINISTIC_ACCEPTANCE_TARGET_MISSING",
                f"Deterministic acceptance target does not exist: {path}",
            )

        if check_type == "file_exists":
            verification.append(
                {
                    "result": "PASS",
                    "type": "deterministic_acceptance_file_exists",
                    "path": path,
                    "detail": "Workspace file exists as a regular file.",
                }
            )
            continue

        try:
            actual_text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise DeterministicAcceptanceError(
                "DETERMINISTIC_ACCEPTANCE_READ_FAILED",
                f"Could not read deterministic acceptance target {path}: {exc}",
            ) from exc

        if check_type == "exact_file_content" and actual_text != expected_text:
            raise DeterministicAcceptanceError(
                "DETERMINISTIC_ACCEPTANCE_EXACT_CONTENT_MISMATCH",
                (
                    f"Exact file content acceptance failed for {path}: "
                    f"expected {len(expected_text.encode('utf-8'))} bytes, "
                    f"observed {len(actual_text.encode('utf-8'))} bytes."
                ),
            )
        if check_type == "file_contains" and expected_text not in actual_text:
            raise DeterministicAcceptanceError(
                "DETERMINISTIC_ACCEPTANCE_REQUIRED_TEXT_MISSING",
                f"File content acceptance failed for {path}: expected text was not present.",
            )
        if check_type == "file_not_contains" and expected_text in actual_text:
            raise DeterministicAcceptanceError(
                "DETERMINISTIC_ACCEPTANCE_FORBIDDEN_TEXT_PRESENT",
                f"File content acceptance failed for {path}: forbidden text was present.",
            )

        detail_by_type = {
            "exact_file_content": "Workspace file content exactly matches the frozen acceptance value.",
            "file_contains": "Workspace file content contains the frozen acceptance text.",
            "file_not_contains": "Workspace file content does not contain the forbidden frozen text.",
        }
        verification.append(
            {
                "result": "PASS",
                "type": f"deterministic_acceptance_{check_type}",
                "path": path,
                "detail": detail_by_type[check_type],
                "bytes": len(actual_text.encode("utf-8")),
            }
        )

    return verification


def normalize_deterministic_acceptance_checks(
    checks: list[dict[str, Any]],
    *,
    authorized_paths: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(checks, list):
        raise DeterministicAcceptanceError(
            "INVALID_DETERMINISTIC_ACCEPTANCE_CHECKS",
            "deterministic_acceptance_checks must be a list.",
        )

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()
    for index, check in enumerate(checks, start=1):
        normalized_check = normalize_deterministic_acceptance_check(
            check,
            index=index,
            authorized_paths=authorized_paths,
        )
        signature = (
            str(normalized_check["type"]),
            str(normalized_check["path"]),
            normalized_check.get("expected_text"),
        )
        if signature in seen:
            raise DeterministicAcceptanceError(
                "DUPLICATE_DETERMINISTIC_ACCEPTANCE_CHECK",
                f"Duplicate deterministic acceptance check at index {index}: {normalized_check['type']} {normalized_check['path']}",
            )
        seen.add(signature)
        normalized.append(normalized_check)
    return normalized


def normalize_deterministic_acceptance_check(
    check: dict[str, Any],
    *,
    index: int,
    authorized_paths: set[str],
) -> dict[str, Any]:
    if not isinstance(check, dict):
        raise DeterministicAcceptanceError(
            "INVALID_DETERMINISTIC_ACCEPTANCE_CHECK",
            f"Deterministic acceptance check {index} is malformed.",
        )

    check_type = check.get("type")
    if check_type not in SUPPORTED_DETERMINISTIC_ACCEPTANCE_CHECK_TYPES:
        raise DeterministicAcceptanceError(
            "UNSUPPORTED_DETERMINISTIC_ACCEPTANCE_CHECK",
            f"Unsupported deterministic acceptance check type at index {index}.",
        )

    raw_path = check.get("path")
    try:
        path = validate_relative_repo_path(raw_path)
    except GitMutationError as exc:
        raise DeterministicAcceptanceError(
            "INVALID_DETERMINISTIC_ACCEPTANCE_PATH",
            f"Deterministic acceptance path is invalid at index {index}: {raw_path!r}",
        ) from exc

    if path not in authorized_paths:
        raise DeterministicAcceptanceError(
            "DETERMINISTIC_ACCEPTANCE_PATH_OUTSIDE_SCOPE",
            f"Deterministic acceptance path is outside frozen authorization: {path}",
        )

    if check_type == "file_exists":
        if "expected_text" in check:
            raise DeterministicAcceptanceError(
                "INVALID_DETERMINISTIC_ACCEPTANCE_EXPECTED_TEXT",
                f"file_exists must not provide expected_text: {path}",
            )
        return {"type": "file_exists", "path": path}

    expected_text = check.get("expected_text")
    if not isinstance(expected_text, str):
        raise DeterministicAcceptanceError(
            "INVALID_DETERMINISTIC_ACCEPTANCE_EXPECTED_TEXT",
            f"Deterministic acceptance expected_text is invalid for: {path}",
        )
    return {"type": str(check_type), "path": path, "expected_text": expected_text}


def verify_deterministic_acceptance_content(
    captured_file_contents: list[dict[str, str]],
    checks: list[dict[str, Any]],
    *,
    authorized_paths: set[str],
) -> list[dict[str, Any]]:
    normalized_checks = normalize_deterministic_acceptance_checks(
        checks,
        authorized_paths=authorized_paths,
    )
    content_by_path = {
        item["path"]: item["content"]
        for item in captured_file_contents
        if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("content"), str)
    }
    verification: list[dict[str, Any]] = []
    for check in normalized_checks:
        check_type = str(check["type"])
        path = str(check["path"])
        if path not in content_by_path:
            raise DeterministicAcceptanceError(
                "DETERMINISTIC_ACCEPTANCE_TARGET_MISSING",
                f"Deterministic acceptance target does not exist in captured content: {path}",
            )
        actual_text = content_by_path[path]
        expected_text = check.get("expected_text")
        if check_type == "exact_file_content" and actual_text != expected_text:
            raise DeterministicAcceptanceError(
                "DETERMINISTIC_ACCEPTANCE_EXACT_CONTENT_MISMATCH",
                f"Exact file content acceptance failed for captured content: {path}",
            )
        if check_type == "file_contains" and expected_text not in actual_text:
            raise DeterministicAcceptanceError(
                "DETERMINISTIC_ACCEPTANCE_REQUIRED_TEXT_MISSING",
                f"File content acceptance failed for captured content {path}: expected text was not present.",
            )
        if check_type == "file_not_contains" and expected_text in actual_text:
            raise DeterministicAcceptanceError(
                "DETERMINISTIC_ACCEPTANCE_FORBIDDEN_TEXT_PRESENT",
                f"File content acceptance failed for captured content {path}: forbidden text was present.",
            )
        verification.append(
            {
                "result": "PASS",
                "type": f"deterministic_acceptance_{check_type}",
                "path": path,
                "detail": "Captured file content satisfies the frozen deterministic acceptance check.",
            }
        )
    return verification


def acceptance_check_paths(checks: list[dict[str, Any]], *, authorized_paths: set[str]) -> list[str]:
    normalized = normalize_deterministic_acceptance_checks(
        checks,
        authorized_paths=authorized_paths,
    )
    return sorted({str(check["path"]) for check in normalized})


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
