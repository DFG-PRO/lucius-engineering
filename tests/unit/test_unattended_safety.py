import pytest
from lucius.runtime.safety import assert_unattended_command_authorized, ExecutionSafetyError


def test_unattended_safety_deny_gate_blocks_destructive_commands():
    forbidden = [
        "rm -rf /path/to/dir",
        "rm -f sqlite:/",
        "git clean -fd",
        "git reset --hard HEAD",
        "git restore .",
        "git push origin main --force",
        "sudo rm -rf /",
        "chmod -R 777 /",
        "pip install malicious_pkg",
    ]

    for cmd in forbidden:
        with pytest.raises(ExecutionSafetyError) as exc_info:
            assert_unattended_command_authorized(cmd)
        assert exc_info.value.code == "UNATTENDED_COMMAND_DENIED"


def test_unattended_safety_deny_gate_allows_safe_commands():
    safe = [
        [".venv/bin/pytest", "tests/unit"],
        ["git", "status"],
        ["git", "log", "-n", "5"],
        ["python", "-m", "pytest"],
    ]

    for cmd in safe:
        assert_unattended_command_authorized(cmd)
