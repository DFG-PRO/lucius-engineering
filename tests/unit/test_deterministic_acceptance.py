from __future__ import annotations

import pytest

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
