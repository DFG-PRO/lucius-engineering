from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from lucius.repositories.git_mutation import GitMutationError, validate_relative_repo_path

SUPPORTED_DETERMINISTIC_ACCEPTANCE_CHECK_TYPES = {
    "exact_file_content",
    "file_exists",
    "file_contains",
    "file_not_contains",
    "command_succeeds",
}

MAX_COMMAND_TIMEOUT_SECONDS = 600
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300
MAX_COMMAND_OUTPUT_BYTES = 4_096
_ALLOWED_COMMAND_EXECUTABLES = {".venv/bin/python", ".venv/bin/python3"}
_ALLOWED_PYTEST_OPTIONS = {
    "-q",
    "-s",
    "-x",
    "--quiet",
    "--verbose",
    "--disable-warnings",
    "--tb=short",
    "--tb=auto",
    "--tb=long",
    "--maxfail=1",
}
_SHELL_METACHARS = set("|&;<>$`\\\n\r")


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
        if check_type == "command_succeeds":
            verification.append(_verify_command_succeeds(root, check))
            continue
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
    seen: set[tuple[Any, ...]] = set()
    for index, check in enumerate(checks, start=1):
        normalized_check = normalize_deterministic_acceptance_check(
            check,
            index=index,
            authorized_paths=authorized_paths,
        )
        signature = _check_signature(normalized_check)
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

    if check_type == "command_succeeds":
        return _normalize_command_succeeds_check(check, index=index)

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
        if check_type == "command_succeeds":
            continue
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
    return sorted({str(check["path"]) for check in normalized if "path" in check})


def _normalize_command_succeeds_check(check: dict[str, Any], *, index: int) -> dict[str, Any]:
    if "path" in check or "expected_text" in check:
        raise DeterministicAcceptanceError(
            "INVALID_DETERMINISTIC_ACCEPTANCE_COMMAND",
            f"command_succeeds check {index} must not provide path or expected_text.",
        )
    argv = check.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item for item in argv)
    ):
        raise DeterministicAcceptanceError(
            "INVALID_DETERMINISTIC_ACCEPTANCE_COMMAND_ARGV",
            f"command_succeeds check {index} argv must be a non-empty list of strings.",
        )
    timeout = check.get("timeout_seconds", DEFAULT_COMMAND_TIMEOUT_SECONDS)
    if not isinstance(timeout, int) or timeout <= 0 or timeout > MAX_COMMAND_TIMEOUT_SECONDS:
        raise DeterministicAcceptanceError(
            "INVALID_DETERMINISTIC_ACCEPTANCE_COMMAND_TIMEOUT",
            f"command_succeeds check {index} timeout_seconds must be 1..{MAX_COMMAND_TIMEOUT_SECONDS}.",
        )
    _validate_allowed_command_argv(argv, index=index)
    return {"type": "command_succeeds", "argv": list(argv), "timeout_seconds": timeout}


def _validate_allowed_command_argv(argv: list[str], *, index: int) -> None:
    executable = argv[0]
    if executable not in _ALLOWED_COMMAND_EXECUTABLES:
        raise DeterministicAcceptanceError(
            "DETERMINISTIC_ACCEPTANCE_COMMAND_NOT_ALLOWED",
            f"command_succeeds check {index} executable is not allowlisted.",
        )
    if any(any(char in _SHELL_METACHARS for char in arg) for arg in argv):
        raise DeterministicAcceptanceError(
            "DETERMINISTIC_ACCEPTANCE_COMMAND_SHELL_METACHARACTER",
            f"command_succeeds check {index} contains a rejected shell metacharacter.",
        )
    if len(argv) < 3 or argv[1:3] != ["-m", "pytest"]:
        raise DeterministicAcceptanceError(
            "DETERMINISTIC_ACCEPTANCE_COMMAND_NOT_ALLOWED",
            f"command_succeeds check {index} must invoke pytest via python -m pytest.",
        )
    for arg in argv[3:]:
        if arg in _ALLOWED_PYTEST_OPTIONS:
            continue
        if arg.startswith("--maxfail="):
            value = arg.split("=", 1)[1]
            if value.isdigit() and 1 <= int(value) <= 10:
                continue
        if arg.startswith("-"):
            raise DeterministicAcceptanceError(
                "DETERMINISTIC_ACCEPTANCE_COMMAND_ARG_NOT_ALLOWED",
                f"command_succeeds check {index} pytest option is not allowlisted: {arg}",
            )
        _validate_pytest_target_arg(arg, index=index)


def _validate_pytest_target_arg(arg: str, *, index: int) -> None:
    path_part = arg.split("::", 1)[0]
    try:
        path = validate_relative_repo_path(path_part)
    except GitMutationError as exc:
        raise DeterministicAcceptanceError(
            "DETERMINISTIC_ACCEPTANCE_COMMAND_ARG_NOT_ALLOWED",
            f"command_succeeds check {index} pytest target is invalid: {arg}",
        ) from exc
    if path != "tests" and not path.startswith("tests/"):
        raise DeterministicAcceptanceError(
            "DETERMINISTIC_ACCEPTANCE_COMMAND_ARG_NOT_ALLOWED",
            f"command_succeeds check {index} pytest target must be under tests/: {arg}",
        )


def _verify_command_succeeds(root: Path, check: dict[str, Any]) -> dict[str, Any]:
    argv = list(check["argv"])
    executable = root / argv[0]
    if not executable.exists() or not executable.is_file():
        raise DeterministicAcceptanceError(
            "DETERMINISTIC_ACCEPTANCE_COMMAND_EXECUTABLE_MISSING",
            f"command_succeeds executable does not exist: {argv[0]}",
        )
    command = [str(executable), *argv[1:]]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            shell=False,
            capture_output=True,
            text=True,
            timeout=int(check["timeout_seconds"]),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DeterministicAcceptanceError(
            "DETERMINISTIC_ACCEPTANCE_COMMAND_TIMEOUT",
            f"command_succeeds timed out after {check['timeout_seconds']} seconds.",
        ) from exc
    except OSError as exc:
        raise DeterministicAcceptanceError(
            "DETERMINISTIC_ACCEPTANCE_COMMAND_EXECUTION_FAILED",
            f"command_succeeds could not execute: {exc}",
        ) from exc
    if completed.returncode != 0:
        raise DeterministicAcceptanceError(
            "DETERMINISTIC_ACCEPTANCE_COMMAND_NONZERO_EXIT",
            f"command_succeeds failed with exit code {completed.returncode}.",
        )
    return {
        "result": "PASS",
        "type": "deterministic_acceptance_command_succeeds",
        "argv": argv,
        "timeout_seconds": check["timeout_seconds"],
        "exit_code": completed.returncode,
        "stdout": _bounded_output(completed.stdout),
        "stderr": _bounded_output(completed.stderr),
    }


def _bounded_output(value: str) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_COMMAND_OUTPUT_BYTES:
        return value
    truncated = encoded[:MAX_COMMAND_OUTPUT_BYTES].decode("utf-8", errors="replace")
    return truncated + "\n[truncated]"


def _check_signature(check: dict[str, Any]) -> tuple[Any, ...]:
    if check.get("type") == "command_succeeds":
        return (
            "command_succeeds",
            tuple(check.get("argv", [])),
            check.get("timeout_seconds"),
        )
    return (
        str(check["type"]),
        str(check["path"]),
        check.get("expected_text"),
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
