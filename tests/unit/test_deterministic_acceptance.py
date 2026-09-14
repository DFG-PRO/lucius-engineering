from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from lucius.runtime import deterministic_acceptance
from lucius.runtime.deterministic_acceptance import (
    DeterministicAcceptanceError,
    normalize_deterministic_acceptance_checks,
    verify_deterministic_acceptance,
)


def test_exact_file_content_regression(tmp_path):
    target = tmp_path / "src" / "demo.py"
    target.parent.mkdir(parents=True)
    target.write_text("VALUE = 1\n", encoding="utf-8")

    result = verify_deterministic_acceptance(
        tmp_path,
        [{"type": "exact_file_content", "path": "src/demo.py", "expected_text": "VALUE = 1\n"}],
        authorized_paths={"src/demo.py"},
    )

    assert result == [
        {
            "result": "PASS",
            "type": "deterministic_acceptance_exact_file_content",
            "path": "src/demo.py",
            "detail": "Workspace file content exactly matches the frozen acceptance value.",
            "bytes": len(b"VALUE = 1\n"),
        }
    ]


def test_file_exists_passes_for_regular_file(tmp_path):
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")

    result = verify_deterministic_acceptance(
        tmp_path,
        [{"type": "file_exists", "path": "README.md"}],
        authorized_paths={"README.md"},
    )

    assert result[0]["type"] == "deterministic_acceptance_file_exists"


def test_file_exists_missing_file_fails(tmp_path):
    with pytest.raises(DeterministicAcceptanceError, match="does not exist"):
        verify_deterministic_acceptance(
            tmp_path,
            [{"type": "file_exists", "path": "missing.txt"}],
            authorized_paths={"missing.txt"},
        )


def test_file_exists_rejects_expected_text(tmp_path):
    with pytest.raises(DeterministicAcceptanceError) as exc:
        normalize_deterministic_acceptance_checks(
            [{"type": "file_exists", "path": "README.md", "expected_text": ""}],
            authorized_paths={"README.md"},
        )

    assert exc.value.code == "INVALID_DETERMINISTIC_ACCEPTANCE_EXPECTED_TEXT"


def test_file_contains_passes_when_text_is_present(tmp_path):
    (tmp_path / "README.md").write_text("alpha beta gamma\n", encoding="utf-8")

    result = verify_deterministic_acceptance(
        tmp_path,
        [{"type": "file_contains", "path": "README.md", "expected_text": "beta"}],
        authorized_paths={"README.md"},
    )

    assert result[0]["type"] == "deterministic_acceptance_file_contains"


def test_file_contains_missing_text_fails(tmp_path):
    (tmp_path / "README.md").write_text("alpha beta gamma\n", encoding="utf-8")

    with pytest.raises(DeterministicAcceptanceError) as exc:
        verify_deterministic_acceptance(
            tmp_path,
            [{"type": "file_contains", "path": "README.md", "expected_text": "delta"}],
            authorized_paths={"README.md"},
        )

    assert exc.value.code == "DETERMINISTIC_ACCEPTANCE_REQUIRED_TEXT_MISSING"


def test_file_not_contains_passes_when_text_is_absent(tmp_path):
    (tmp_path / "README.md").write_text("alpha beta gamma\n", encoding="utf-8")

    result = verify_deterministic_acceptance(
        tmp_path,
        [{"type": "file_not_contains", "path": "README.md", "expected_text": "delta"}],
        authorized_paths={"README.md"},
    )

    assert result[0]["type"] == "deterministic_acceptance_file_not_contains"


def test_file_not_contains_forbidden_text_present_fails(tmp_path):
    (tmp_path / "README.md").write_text("alpha beta gamma\n", encoding="utf-8")

    with pytest.raises(DeterministicAcceptanceError) as exc:
        verify_deterministic_acceptance(
            tmp_path,
            [{"type": "file_not_contains", "path": "README.md", "expected_text": "beta"}],
            authorized_paths={"README.md"},
        )

    assert exc.value.code == "DETERMINISTIC_ACCEPTANCE_FORBIDDEN_TEXT_PRESENT"


def test_unknown_acceptance_type_rejected():
    with pytest.raises(DeterministicAcceptanceError) as exc:
        normalize_deterministic_acceptance_checks(
            [{"type": "command_exit_zero", "path": "README.md"}],
            authorized_paths={"README.md"},
        )

    assert exc.value.code == "UNSUPPORTED_DETERMINISTIC_ACCEPTANCE_CHECK"


def test_malformed_arguments_rejected():
    with pytest.raises(DeterministicAcceptanceError) as exc:
        normalize_deterministic_acceptance_checks(
            [{"type": "file_contains", "path": "README.md"}],
            authorized_paths={"README.md"},
        )

    assert exc.value.code == "INVALID_DETERMINISTIC_ACCEPTANCE_EXPECTED_TEXT"


def test_path_outside_mutation_scope_rejected():
    with pytest.raises(DeterministicAcceptanceError) as exc:
        normalize_deterministic_acceptance_checks(
            [{"type": "file_exists", "path": "README.md"}],
            authorized_paths={"src/demo.py"},
        )

    assert exc.value.code == "DETERMINISTIC_ACCEPTANCE_PATH_OUTSIDE_SCOPE"


def test_path_traversal_rejected():
    with pytest.raises(DeterministicAcceptanceError) as exc:
        normalize_deterministic_acceptance_checks(
            [{"type": "file_exists", "path": "../README.md"}],
            authorized_paths={"../README.md"},
        )

    assert exc.value.code == "INVALID_DETERMINISTIC_ACCEPTANCE_PATH"


def test_multiple_distinct_checks_on_same_file_allowed(tmp_path):
    (tmp_path / "README.md").write_text("alpha beta gamma\n", encoding="utf-8")

    result = verify_deterministic_acceptance(
        tmp_path,
        [
            {"type": "file_exists", "path": "README.md"},
            {"type": "file_contains", "path": "README.md", "expected_text": "beta"},
            {"type": "file_not_contains", "path": "README.md", "expected_text": "delta"},
        ],
        authorized_paths={"README.md"},
    )

    assert [item["type"] for item in result] == [
        "deterministic_acceptance_file_exists",
        "deterministic_acceptance_file_contains",
        "deterministic_acceptance_file_not_contains",
    ]


def test_exact_duplicate_check_rejected_deterministically():
    check = {"type": "file_contains", "path": "README.md", "expected_text": "beta"}

    with pytest.raises(DeterministicAcceptanceError) as exc:
        normalize_deterministic_acceptance_checks(
            [check, dict(check)],
            authorized_paths={"README.md"},
        )

    assert exc.value.code == "DUPLICATE_DETERMINISTIC_ACCEPTANCE_CHECK"


def test_command_succeeds_valid_pytest_command_passes(tmp_path):
    _prepare_pytest_workspace(tmp_path, "def test_ok():\n    assert True\n")

    result = verify_deterministic_acceptance(
        tmp_path,
        [_command_check([".venv/bin/python", "-m", "pytest", "tests/test_profitability.py", "-q"])],
        authorized_paths={"src/demo.py"},
    )

    assert result[0]["type"] == "deterministic_acceptance_command_succeeds"
    assert result[0]["exit_code"] == 0
    assert result[0]["argv"] == [".venv/bin/python", "-m", "pytest", "tests/test_profitability.py", "-q"]
    assert result[0]["argv_redacted"] is False
    assert result[0]["stdout_truncated"] is False
    assert result[0]["stderr_truncated"] is False


def test_command_succeeds_nonzero_pytest_fails_closed(tmp_path):
    _prepare_pytest_workspace(tmp_path, "def test_fail():\n    assert False\n")

    with pytest.raises(DeterministicAcceptanceError) as exc:
        verify_deterministic_acceptance(
            tmp_path,
            [_command_check([".venv/bin/python", "-m", "pytest", "tests/test_profitability.py", "-q"])],
            authorized_paths={"src/demo.py"},
        )

    assert exc.value.code == "DETERMINISTIC_ACCEPTANCE_COMMAND_NONZERO_EXIT"
    diagnostic = exc.value.diagnostics
    assert diagnostic["type"] == "deterministic_acceptance_command_succeeds"
    assert diagnostic["result"] == "FAIL"
    assert diagnostic["argv"] == [".venv/bin/python", "-m", "pytest", "tests/test_profitability.py", "-q"]
    assert diagnostic["argv_redacted"] is False
    assert diagnostic["exit_code"] == 1
    assert "test_fail" in diagnostic["stdout"]


def test_command_succeeds_diagnostic_argv_redacts_secret_like_values_without_changing_execution(
    tmp_path,
    monkeypatch,
):
    _prepare_pytest_workspace(tmp_path, "def test_ok():\n    assert True\n")
    raw_secret_arg = "tests/test_profitability.py::test_token=secretvalue"
    executed: dict[str, list[str]] = {}

    def fake_run(command, **kwargs):
        executed["command"] = list(command)
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="simulated failure\n",
            stderr="",
        )

    monkeypatch.setattr(deterministic_acceptance.subprocess, "run", fake_run)

    with pytest.raises(DeterministicAcceptanceError) as exc:
        verify_deterministic_acceptance(
            tmp_path,
            [_command_check([".venv/bin/python", "-m", "pytest", raw_secret_arg, "-q"])],
            authorized_paths={"src/demo.py"},
        )

    assert executed["command"][3] == raw_secret_arg
    diagnostic = exc.value.diagnostics
    assert diagnostic["argv_redacted"] is True
    assert "secretvalue" not in diagnostic["argv"][3]
    assert diagnostic["argv"][3] == "tests/test_profitability.py::test_token=<REDACTED>"


def test_command_diagnostic_argv_redacts_common_secret_argument_forms():
    diagnostic = deterministic_acceptance._command_diagnostics(
        argv=[
            ".venv/bin/python",
            "-m",
            "pytest",
            "tests/test_profitability.py",
            "--token=secretvalue",
            "--password",
            "passwordvalue",
            "api_key=apikeyvalue",
            "credential:credentialvalue",
            "Authorization: Bearer bearer-secret-value",
        ],
        timeout_seconds=10,
        exit_code=1,
        stdout="",
        stderr="",
        result="FAIL",
        timed_out=False,
    )

    rendered = " ".join(diagnostic["argv"])
    assert diagnostic["argv_redacted"] is True
    assert "secretvalue" not in rendered
    assert "passwordvalue" not in rendered
    assert "apikeyvalue" not in rendered
    assert "credentialvalue" not in rendered
    assert "bearer-secret-value" not in rendered
    assert "--token=<REDACTED>" in diagnostic["argv"]
    assert diagnostic["argv"][diagnostic["argv"].index("--password") + 1] == "<REDACTED>"


def test_command_succeeds_exit_code_2_preserves_collection_diagnostic(tmp_path):
    _prepare_pytest_workspace(tmp_path, "def test_broken(:\n    assert True\n")

    with pytest.raises(DeterministicAcceptanceError) as exc:
        verify_deterministic_acceptance(
            tmp_path,
            [_command_check([".venv/bin/python", "-m", "pytest", "tests/test_profitability.py", "-q"])],
            authorized_paths={"src/demo.py"},
        )

    assert exc.value.code == "DETERMINISTIC_ACCEPTANCE_COMMAND_NONZERO_EXIT"
    diagnostic = exc.value.diagnostics
    assert diagnostic["exit_code"] == 2
    assert "SyntaxError" in diagnostic["stdout"] or "SyntaxError" in diagnostic["stderr"]


def test_command_succeeds_failure_captures_stdout_and_stderr(tmp_path):
    _prepare_pytest_workspace(
        tmp_path,
        (
            "import sys\n\n"
            "def test_output_then_fail():\n"
            "    print('DIAGNOSTIC_STDOUT')\n"
            "    print('DIAGNOSTIC_STDERR', file=sys.stderr)\n"
            "    assert False\n"
        ),
    )

    with pytest.raises(DeterministicAcceptanceError) as exc:
        verify_deterministic_acceptance(
            tmp_path,
            [_command_check([".venv/bin/python", "-m", "pytest", "tests/test_profitability.py", "-s", "-q"])],
            authorized_paths={"src/demo.py"},
        )

    diagnostic = exc.value.diagnostics
    assert diagnostic["exit_code"] == 1
    assert "DIAGNOSTIC_STDOUT" in diagnostic["stdout"]
    assert "DIAGNOSTIC_STDERR" in diagnostic["stderr"]


def test_command_succeeds_failure_bounds_and_marks_truncated_output(tmp_path):
    _prepare_pytest_workspace(
        tmp_path,
        (
            "def test_noisy_failure():\n"
            "    print('A' * 5000)\n"
            "    assert False\n"
        ),
    )

    with pytest.raises(DeterministicAcceptanceError) as exc:
        verify_deterministic_acceptance(
            tmp_path,
            [_command_check([".venv/bin/python", "-m", "pytest", "tests/test_profitability.py", "-s", "-q"])],
            authorized_paths={"src/demo.py"},
        )

    diagnostic = exc.value.diagnostics
    assert diagnostic["stdout_truncated"] is True
    assert diagnostic["output_limit_bytes"] == 4096
    assert diagnostic["stdout"].endswith("[truncated]")
    assert len(diagnostic["stdout"].encode("utf-8")) < diagnostic["stdout_bytes"]


def test_command_succeeds_failure_redacts_secret_like_output(tmp_path):
    _prepare_pytest_workspace(
        tmp_path,
        (
            "def test_secret_output_failure():\n"
            "    print('API_TOKEN=super-secret-value')\n"
            "    assert False\n"
        ),
    )

    with pytest.raises(DeterministicAcceptanceError) as exc:
        verify_deterministic_acceptance(
            tmp_path,
            [_command_check([".venv/bin/python", "-m", "pytest", "tests/test_profitability.py", "-s", "-q"])],
            authorized_paths={"src/demo.py"},
        )

    diagnostic = exc.value.diagnostics
    assert diagnostic["stdout_redacted"] is True
    assert "super-secret-value" not in diagnostic["stdout"]
    assert "API_TOKEN=<REDACTED>" in diagnostic["stdout"]


def test_command_succeeds_timeout_fails_closed(tmp_path):
    _prepare_pytest_workspace(
        tmp_path,
        "import time\n\ndef test_slow():\n    time.sleep(2)\n",
    )

    with pytest.raises(DeterministicAcceptanceError) as exc:
        verify_deterministic_acceptance(
            tmp_path,
            [_command_check([".venv/bin/python", "-m", "pytest", "tests/test_profitability.py", "-q"], timeout_seconds=1)],
            authorized_paths={"src/demo.py"},
        )

    assert exc.value.code == "DETERMINISTIC_ACCEPTANCE_COMMAND_TIMEOUT"
    assert exc.value.diagnostics["timed_out"] is True
    assert exc.value.diagnostics["exit_code"] is None


def test_command_succeeds_shell_metacharacters_are_rejected_not_interpreted(tmp_path):
    _prepare_pytest_workspace(tmp_path, "def test_ok():\n    assert True\n")

    with pytest.raises(DeterministicAcceptanceError) as exc:
        verify_deterministic_acceptance(
            tmp_path,
            [_command_check([".venv/bin/python", "-m", "pytest", "tests/test_profitability.py;touch tests/pwned.txt", "-q"])],
            authorized_paths={"src/demo.py"},
        )

    assert exc.value.code == "DETERMINISTIC_ACCEPTANCE_COMMAND_SHELL_METACHARACTER"
    assert not (tmp_path / "tests" / "pwned.txt").exists()


def test_command_succeeds_rejects_shell_executable():
    with pytest.raises(DeterministicAcceptanceError) as exc:
        normalize_deterministic_acceptance_checks(
            [{"type": "command_succeeds", "argv": ["bash", "-lc", "pytest"], "timeout_seconds": 10}],
            authorized_paths={"src/demo.py"},
        )

    assert exc.value.code == "DETERMINISTIC_ACCEPTANCE_COMMAND_NOT_ALLOWED"


def test_command_succeeds_rejects_arbitrary_executable():
    with pytest.raises(DeterministicAcceptanceError) as exc:
        normalize_deterministic_acceptance_checks(
            [{"type": "command_succeeds", "argv": ["/usr/bin/python3", "-m", "pytest", "tests/test_profitability.py"], "timeout_seconds": 10}],
            authorized_paths={"src/demo.py"},
        )

    assert exc.value.code == "DETERMINISTIC_ACCEPTANCE_COMMAND_NOT_ALLOWED"


def test_command_succeeds_rejects_python_dash_c():
    with pytest.raises(DeterministicAcceptanceError) as exc:
        normalize_deterministic_acceptance_checks(
            [{"type": "command_succeeds", "argv": [".venv/bin/python", "-c", "print(1)"], "timeout_seconds": 10}],
            authorized_paths={"src/demo.py"},
        )

    assert exc.value.code == "DETERMINISTIC_ACCEPTANCE_COMMAND_NOT_ALLOWED"


def test_command_succeeds_rejects_git():
    with pytest.raises(DeterministicAcceptanceError) as exc:
        normalize_deterministic_acceptance_checks(
            [{"type": "command_succeeds", "argv": ["git", "status"], "timeout_seconds": 10}],
            authorized_paths={"src/demo.py"},
        )

    assert exc.value.code == "DETERMINISTIC_ACCEPTANCE_COMMAND_NOT_ALLOWED"


def test_command_succeeds_cannot_escape_workspace_assumptions():
    with pytest.raises(DeterministicAcceptanceError) as exc:
        normalize_deterministic_acceptance_checks(
            [{"type": "command_succeeds", "argv": [".venv/bin/python", "-m", "pytest", "../tests/test_profitability.py"], "timeout_seconds": 10}],
            authorized_paths={"src/demo.py"},
        )

    assert exc.value.code == "DETERMINISTIC_ACCEPTANCE_COMMAND_ARG_NOT_ALLOWED"


def test_command_succeeds_malformed_argv_rejected():
    with pytest.raises(DeterministicAcceptanceError) as exc:
        normalize_deterministic_acceptance_checks(
            [{"type": "command_succeeds", "argv": [], "timeout_seconds": 10}],
            authorized_paths={"src/demo.py"},
        )

    assert exc.value.code == "INVALID_DETERMINISTIC_ACCEPTANCE_COMMAND_ARGV"


def test_command_success_alone_does_not_count_as_file_scoped_coverage():
    from lucius.runtime.deterministic_acceptance import acceptance_check_paths

    assert acceptance_check_paths(
        [_command_check([".venv/bin/python", "-m", "pytest", "tests/test_profitability.py", "-q"])],
        authorized_paths={"src/demo.py"},
    ) == []


def _command_check(argv: list[str], *, timeout_seconds: int = 10) -> dict:
    return {"type": "command_succeeds", "argv": argv, "timeout_seconds": timeout_seconds}


def _prepare_pytest_workspace(tmp_path, test_source: str) -> None:
    os.symlink(Path.cwd() / ".venv", tmp_path / ".venv")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_profitability.py").write_text(test_source, encoding="utf-8")
