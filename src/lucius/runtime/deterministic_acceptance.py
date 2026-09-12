from __future__ import annotations

from pathlib import Path
from typing import Any

from lucius.repositories.git_mutation import GitMutationError, validate_relative_repo_path


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

    for index, check in enumerate(checks, start=1):
        if not isinstance(check, dict):
            raise DeterministicAcceptanceError(
                "INVALID_DETERMINISTIC_ACCEPTANCE_CHECK",
                f"Deterministic acceptance check {index} is malformed.",
            )

        check_type = check.get("type")
        raw_path = check.get("path")
        expected_text = check.get("expected_text")

        if check_type != "exact_file_content":
            raise DeterministicAcceptanceError(
                "UNSUPPORTED_DETERMINISTIC_ACCEPTANCE_CHECK",
                f"Unsupported deterministic acceptance check type at index {index}.",
            )

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

        if not isinstance(expected_text, str):
            raise DeterministicAcceptanceError(
                "INVALID_DETERMINISTIC_ACCEPTANCE_EXPECTED_TEXT",
                f"Deterministic acceptance expected_text is invalid for: {path}",
            )

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

        try:
            actual_text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise DeterministicAcceptanceError(
                "DETERMINISTIC_ACCEPTANCE_READ_FAILED",
                f"Could not read deterministic acceptance target {path}: {exc}",
            ) from exc

        if actual_text != expected_text:
            raise DeterministicAcceptanceError(
                "DETERMINISTIC_ACCEPTANCE_EXACT_CONTENT_MISMATCH",
                (
                    f"Exact file content acceptance failed for {path}: "
                    f"expected {len(expected_text.encode('utf-8'))} bytes, "
                    f"observed {len(actual_text.encode('utf-8'))} bytes."
                ),
            )

        verification.append(
            {
                "result": "PASS",
                "type": "deterministic_acceptance_exact_file_content",
                "path": path,
                "detail": "Workspace file content exactly matches the frozen acceptance value.",
                "bytes": len(actual_text.encode("utf-8")),
            }
        )

    return verification


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
