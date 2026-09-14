from __future__ import annotations

from pathlib import Path
from argparse import Namespace
import os
import subprocess
from urllib import error

import pytest
from sqlalchemy import select

from lucius.persistence.orm import AuditEventORM, ModelExecutionORM, PlanFreezeORM
from lucius.pilots.cli import _ollama_allowed_workspace_roots
from lucius.runtime.ollama import OllamaExecutionProvider, OllamaHttpResponse
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.schema_constraints import SKELETON_METADATA_KEY
from lucius.runtime.schemas import RuntimeExecutionContext, RuntimeExecutionRequest, RuntimeRetryability


def test_ollama_provider_applies_model_generated_file_change_in_isolated_workspace(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = FakeOllamaProvider(tmp_path)
    request = _request(tmp_path)

    result = provider.invoke(request)

    assert result.status == "COMPLETED"
    assert result.provider_id == "ollama-local"
    assert result.model_id == "qwen3:8b"
    assert result.estimated_cost == 0.0
    assert result.input_tokens == 12
    assert result.output_tokens == 18
    assert (tmp_path / "provider-proof.txt").read_text(encoding="utf-8") == "created by local ollama\n"
    assert result.evidence[0]["type"] == "ollama_runtime_execution"
    assert result.verification[0]["result"] == "PASS"


def test_ollama_provider_blocks_workspace_outside_allowed_roots(tmp_path):
    provider = FakeOllamaProvider(tmp_path / "allowed")
    outside = tmp_path / "outside"
    outside.mkdir()

    result = provider.invoke(_request(outside))

    assert result.status == "FAILED"
    assert result.failure_class == "WORKSPACE_OUTSIDE_ALLOWED_ROOTS"
    assert not provider.generate_called


def test_ollama_provider_blocks_unsafe_model_file_path(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = FakeOllamaProvider(tmp_path, files=[{"path": "../escape.txt", "content": "no"}])

    result = provider.invoke(_request(tmp_path))

    assert result.status == "FAILED"
    assert result.failure_class == "UNSAFE_FILE_PATH"
    assert not (tmp_path.parent / "escape.txt").exists()



def test_ollama_provider_rejects_model_path_outside_authorized_mutation_scope(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = FakeOllamaProvider(
        tmp_path,
        files=[{"path": "unauthorized.txt", "content": "must not be written\n"}],
    )

    result = provider.invoke(_request(tmp_path))

    assert result.status == "FAILED"
    assert result.failure_class == "MUTATION_SCOPE_VIOLATION"
    assert not (tmp_path / "unauthorized.txt").exists()


def test_ollama_provider_validates_all_paths_before_any_write(tmp_path):
    _prepare_git_workspace(tmp_path)

    authorized = tmp_path / "provider-proof.txt"
    authorized.write_bytes(b"original bytes\n")

    for command in [
        ["git", "add", "provider-proof.txt"],
        ["git", "commit", "-m", "add authorized baseline"],
    ]:
        completed = subprocess.run(
            command,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr

    before = authorized.read_bytes()

    provider = FakeOllamaProvider(
        tmp_path,
        files=[
            {"path": "provider-proof.txt", "content": "changed bytes\n"},
            {"path": "unauthorized.txt", "content": "must not be written\n"},
        ],
    )

    result = provider.invoke(_request(tmp_path))

    assert result.status == "FAILED"
    assert result.failure_class == "MUTATION_SCOPE_VIOLATION"
    assert authorized.read_bytes() == before
    assert not (tmp_path / "unauthorized.txt").exists()


def test_ollama_provider_rejects_duplicate_model_paths_before_write(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = FakeOllamaProvider(
        tmp_path,
        files=[
            {"path": "provider-proof.txt", "content": "first\n"},
            {"path": "provider-proof.txt", "content": "second\n"},
        ],
    )

    result = provider.invoke(_request(tmp_path))

    assert result.status == "FAILED"
    assert result.failure_class == "MUTATION_SCOPE_VIOLATION"
    assert not (tmp_path / "provider-proof.txt").exists()


def test_ollama_provider_records_deterministic_git_mutation_verification(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = FakeOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "COMPLETED"
    deterministic = result.verification[0]
    assert deterministic["result"] == "PASS"
    assert deterministic["type"] == "deterministic_mutation_scope"
    assert deterministic["allowed_paths"] == ["provider-proof.txt"]
    assert deterministic["changed_paths"] == ["provider-proof.txt"]
    assert deterministic["written_paths"] == ["provider-proof.txt"]


def test_ollama_provider_records_exact_file_content_acceptance_pass(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = FakeOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "COMPLETED"

    acceptance = [
        item
        for item in result.verification
        if item.get("type") == "deterministic_acceptance_exact_file_content"
    ]
    assert len(acceptance) == 1
    assert acceptance[0]["result"] == "PASS"
    assert acceptance[0]["path"] == "provider-proof.txt"
    assert acceptance[0]["bytes"] == len(b"created by local ollama\n")
    assert result.verification_handoff_metadata[
        "deterministic_acceptance_verification"
    ] is True


def test_ollama_provider_rejects_wrong_content_even_when_model_claims_pass(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = FakeOllamaProvider(
        tmp_path,
        files=[
            {
                "path": "provider-proof.txt",
                "content": "wrong model-generated content\n",
            }
        ],
    )
    request = _request(tmp_path).model_copy(
        update={
            "deterministic_acceptance_checks": [
                {
                    "type": "exact_file_content",
                    "path": "provider-proof.txt",
                    "expected_text": "phase 1.30b autonomous mutation passed\n",
                }
            ]
        }
    )

    result = provider.invoke(request)

    assert result.status == "FAILED"
    assert result.failure_class == "DETERMINISTIC_MUTATION_VERIFICATION_FAILED"
    assert result.retryability == RuntimeRetryability.NON_RETRYABLE
    assert "Exact file content acceptance failed" in result.provider_error_metadata["message"]

    # The fake model explicitly claimed PASS, but Lucius-owned verification wins.
    assert provider.last_payload is not None

    # Failed mutations must be rolled back to the original clean workspace.
    assert not (tmp_path / "provider-proof.txt").exists()
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout == ""


def test_ollama_provider_preserves_command_failure_diagnostics_and_rolls_back(tmp_path):
    _prepare_git_workspace(tmp_path)
    _add_tracked_python_runtime(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_profitability.py").write_text(
        (
            "import sys\n\n"
            "def test_command_diagnostic_failure():\n"
            "    print('COMMAND_STDOUT_MARKER')\n"
            "    print('COMMAND_STDERR_MARKER', file=sys.stderr)\n"
            "    assert False\n"
        ),
        encoding="utf-8",
    )
    _git(tmp_path, ["add", "tests/test_profitability.py"])
    _git(tmp_path, ["commit", "-m", "add failing command verifier"])
    provider = FakeOllamaProvider(tmp_path)
    request = _request(tmp_path).model_copy(
        update={
            "deterministic_acceptance_checks": [
                {
                    "type": "command_succeeds",
                    "argv": [".venv/bin/python", "-m", "pytest", "tests/test_profitability.py", "-s", "-q"],
                    "timeout_seconds": 10,
                }
            ]
        }
    )

    result = provider.invoke(request)

    assert result.status == "FAILED"
    assert result.failure_class == "DETERMINISTIC_MUTATION_VERIFICATION_FAILED"
    assert result.verification
    diagnostic = result.provider_error_metadata["deterministic_acceptance_diagnostic"]
    assert diagnostic["type"] == "deterministic_acceptance_command_succeeds"
    assert diagnostic["argv"] == [".venv/bin/python", "-m", "pytest", "tests/test_profitability.py", "-s", "-q"]
    assert diagnostic["exit_code"] == 1
    assert "COMMAND_STDOUT_MARKER" in diagnostic["stdout"]
    assert "COMMAND_STDERR_MARKER" in diagnostic["stderr"]
    assert result.verification[0] == diagnostic
    assert not (tmp_path / "provider-proof.txt").exists()
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout == ""


def test_ollama_provider_fails_when_post_write_git_state_contains_unexpected_path(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = UnexpectedSideEffectOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "FAILED"
    assert result.failure_class == "DETERMINISTIC_MUTATION_VERIFICATION_FAILED"
    assert "unexpected.txt" in result.provider_error_metadata["message"]
    assert not (tmp_path / "provider-proof.txt").exists()
    assert not (tmp_path / "unexpected.txt").exists()

    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stdout == ""


def test_ollama_provider_fails_closed_on_noop_reported_write(tmp_path):
    _prepare_git_workspace(tmp_path)

    proof = tmp_path / "provider-proof.txt"
    proof.write_text("created by local ollama\n", encoding="utf-8")

    for command in [
        ["git", "add", "provider-proof.txt"],
        ["git", "commit", "-m", "add proof baseline"],
    ]:
        completed = subprocess.run(
            command,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr

    provider = FakeOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "FAILED"
    assert result.failure_class == "DETERMINISTIC_MUTATION_VERIFICATION_FAILED"
    assert "provider-reported writes absent from" in result.provider_error_metadata["message"]


def test_router_prefers_cheaper_capable_provider_over_premium(session, tmp_path):
    _prepare_git_workspace(tmp_path)
    _install_plan_freeze(session)
    local = FakeOllamaProvider(tmp_path)
    premium = FakeOllamaProvider(tmp_path, provider_id="premium-provider")
    premium.registration = premium.registration.model_copy(
        update={"cost_class": "PREMIUM", "reliability_score": 100}
    )
    local.registration = local.registration.model_copy(
        update={"cost_class": "LOCAL_FREE", "reliability_score": 10}
    )

    result = ModelExecutionRouter(
        session,
        registry=RuntimeProviderRegistry([premium, local]),
    ).execute(_context(tmp_path))

    assert result.outcome == "COMPLETED"
    assert result.provider_id == "ollama-local"
    assert local.generate_called is True
    assert premium.generate_called is False


def test_router_selected_ollama_without_codex_registered(session, tmp_path):
    _prepare_git_workspace(tmp_path)
    _install_plan_freeze(session)
    provider = FakeOllamaProvider(tmp_path)

    result = ModelExecutionRouter(
        session,
        registry=RuntimeProviderRegistry([provider]),
    ).execute(_context(tmp_path))

    assert result.outcome == "COMPLETED"
    assert result.provider_id == "ollama-local"
    assert (tmp_path / "provider-proof.txt").exists()


def test_router_rejects_unattended_ollama_without_explicit_allowed_roots(session, tmp_path):
    _prepare_git_workspace(tmp_path)
    _install_plan_freeze(session)
    provider = FakeOllamaProvider(tmp_path)
    provider.registration = provider.registration.model_copy(
        update={
            "metadata": {
                **provider.registration.metadata,
                "explicit_allowed_workspace_roots": False,
            }
        }
    )

    result = ModelExecutionRouter(session, registry=RuntimeProviderRegistry([provider])).execute(
        _unattended_context(tmp_path)
    )

    assert result.outcome == "FAILED"
    assert result.blocker_category == "NO_ELIGIBLE_PROVIDER"
    assert provider.generate_called is False


def test_router_rejects_unattended_mutation_without_frozen_acceptance_before_provider(
    session,
    tmp_path,
):
    _prepare_git_workspace(tmp_path)
    _install_plan_freeze(session)

    freeze = session.get(PlanFreezeORM, "LFREEZE_TEST")
    freeze.plan_payload = {
        "affected_files": [
            {"path": "provider-proof.txt"},
        ]
    }
    session.flush()

    provider = FakeOllamaProvider(tmp_path)

    result = ModelExecutionRouter(
        session,
        registry=RuntimeProviderRegistry([provider]),
    ).execute(_unattended_context(tmp_path))

    assert result.outcome == "FAILED"
    assert result.failure_class == "MUTATION_SCOPE_VIOLATION"
    assert provider.generate_called is False

    rows = list(
        session.scalars(
            select(ModelExecutionORM).where(
                ModelExecutionORM.request_id == result.execution_id
            )
        )
    )
    assert rows == []


def test_router_rejects_qwen3_8b_unattended_mutation_even_with_explicit_allowed_root(session, tmp_path):
    _prepare_git_workspace(tmp_path)
    _install_plan_freeze(session)
    provider = FakeOllamaProvider(tmp_path)

    result = ModelExecutionRouter(session, registry=RuntimeProviderRegistry([provider])).execute(
        _unattended_context(tmp_path)
    )

    assert result.outcome == "FAILED"
    assert result.blocker_category == "NO_ELIGIBLE_PROVIDER"
    assert provider.generate_called is False
    candidates = session.scalars(
        select(AuditEventORM)
        .where(AuditEventORM.event_type == "MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED")
        .order_by(AuditEventORM.id.desc())
    ).first()
    assert candidates is not None
    assert "MODEL_NOT_QUALIFIED_FOR_UNATTENDED_MUTATION" in candidates.event_metadata["candidates"][0]["reasons"]


def test_ollama_prompt_includes_schema_constrained_skeleton(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = FakeOllamaProvider(tmp_path)
    request = _request(tmp_path).model_copy(
        update={
            "metadata": {
                SKELETON_METADATA_KEY: (
                    "File: docs/runtime/readiness.md\n"
                    "## Current Verified Facts\n"
                    "- DERIVED_VALUE: NOT_ESTABLISHED"
                )
            }
        }
    )

    result = provider.invoke(request)

    assert result.status == "COMPLETED"
    assert provider.last_payload is not None
    assert "Schema-constrained output skeleton follows" in provider.last_payload["prompt"]
    assert "DERIVED_VALUE: NOT_ESTABLISHED" in provider.last_payload["prompt"]
    assert '"edits":[{"path":"relative/path","old":"exact existing fragment","new":"replacement fragment"}]' in provider.last_payload["prompt"]
    assert "Frozen deterministic acceptance checks:" in provider.last_payload["prompt"]
    assert '"expected_text":"created by local ollama\\n"' in provider.last_payload["prompt"]
    assert provider.last_payload["options"]["num_predict"] == 256
    assert provider.last_payload["think"] is False


def test_ollama_mutation_prompt_includes_existing_authorized_file_content(tmp_path):
    _prepare_git_workspace(tmp_path)
    _write_and_commit(tmp_path, "provider-proof.txt", "CURRENT = 'old'\n")
    provider = FakeOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "COMPLETED"
    assert provider.last_payload is not None
    prompt = provider.last_payload["prompt"]
    assert "--- BEGIN AUTHORIZED FILE: provider-proof.txt ---" in prompt
    assert "CURRENT = 'old'\n" in prompt
    assert "--- END AUTHORIZED FILE: provider-proof.txt ---" in prompt
    assert "supplied file contents are authoritative" not in prompt
    assert "authoritative current workspace state" in prompt


def test_ollama_mutation_prompt_represents_new_authorized_file_safely(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = FakeOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "COMPLETED"
    assert provider.last_payload is not None
    prompt = provider.last_payload["prompt"]
    assert "--- BEGIN AUTHORIZED FILE: provider-proof.txt ---\n<NEW FILE>\n--- END AUTHORIZED FILE: provider-proof.txt ---" in prompt


def test_ollama_mutation_prompt_does_not_include_unauthorized_repo_files(tmp_path):
    _prepare_git_workspace(tmp_path)
    _write_and_commit(tmp_path, "provider-proof.txt", "authorized current text\n")
    _write_and_commit(tmp_path, ".env", "SECRET_TOKEN=must-not-leak\n")
    _write_and_commit(tmp_path, "notes/private.txt", "private unrelated text\n")
    provider = FakeOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "COMPLETED"
    assert provider.last_payload is not None
    prompt = provider.last_payload["prompt"]
    assert "authorized current text" in prompt
    assert "SECRET_TOKEN=must-not-leak" not in prompt
    assert "private unrelated text" not in prompt
    assert ".git" not in prompt


def test_ollama_mutation_prompt_multiple_authorized_files_stay_bounded(tmp_path):
    _prepare_git_workspace(tmp_path)
    _write_and_commit(tmp_path, "provider-proof.txt", "first authorized file\n")
    _write_and_commit(tmp_path, "readiness.md", "second authorized file\n")
    _write_and_commit(tmp_path, "protocol.md", "third authorized file\n")
    provider = FakeOllamaProvider(tmp_path)
    request = _request(tmp_path).model_copy(
        update={
            "allowed_mutation_paths": ["provider-proof.txt", "readiness.md", "protocol.md"],
            "deterministic_acceptance_checks": [],
        }
    )

    result = provider.invoke(request)

    assert result.status == "COMPLETED"
    prompt = provider.last_payload["prompt"]
    assert "first authorized file" in prompt
    assert "second authorized file" in prompt
    assert "third authorized file" in prompt


def test_ollama_mutation_context_rejects_oversized_individual_file(tmp_path):
    _prepare_git_workspace(tmp_path)
    _write_and_commit(tmp_path, "provider-proof.txt", "0123456789\n")
    provider = FakeOllamaProvider(tmp_path, max_context_file_bytes=5)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "FAILED"
    assert result.failure_class == "MUTATION_CONTEXT_FILE_TOO_LARGE"
    assert provider.generate_called is False


def test_ollama_mutation_context_rejects_oversized_aggregate(tmp_path):
    _prepare_git_workspace(tmp_path)
    _write_and_commit(tmp_path, "provider-proof.txt", "12345")
    _write_and_commit(tmp_path, "readiness.md", "67890")
    provider = FakeOllamaProvider(tmp_path, max_context_total_bytes=8)
    request = _request(tmp_path).model_copy(
        update={
            "allowed_mutation_paths": ["provider-proof.txt", "readiness.md"],
            "deterministic_acceptance_checks": [],
        }
    )

    result = provider.invoke(request)

    assert result.status == "FAILED"
    assert result.failure_class == "MUTATION_CONTEXT_TOTAL_TOO_LARGE"
    assert provider.generate_called is False


def test_ollama_mutation_context_rejects_symlink_escape(tmp_path):
    _prepare_git_workspace(tmp_path)
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("outside secret\n", encoding="utf-8")
    (tmp_path / "provider-proof.txt").symlink_to(outside)
    _git(tmp_path, ["add", "provider-proof.txt"])
    _git(tmp_path, ["commit", "-m", "add authorized symlink"])
    provider = FakeOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "FAILED"
    assert result.failure_class == "MUTATION_CONTEXT_SYMLINK_REJECTED"
    assert provider.generate_called is False


def test_ollama_mutation_context_rejects_non_utf8_authorized_file(tmp_path):
    _prepare_git_workspace(tmp_path)
    (tmp_path / "provider-proof.txt").write_bytes(b"\xff\xfe\x00")
    _git(tmp_path, ["add", "provider-proof.txt"])
    _git(tmp_path, ["commit", "-m", "add binary authorized file"])
    provider = FakeOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "FAILED"
    assert result.failure_class == "MUTATION_CONTEXT_FILE_NOT_UTF8"
    assert provider.generate_called is False


def test_ollama_mutation_prompt_does_not_leak_analysis_or_output(tmp_path):
    _prepare_git_workspace(tmp_path)
    _write_and_commit(tmp_path, "provider-proof.txt", "authorized only\n")
    _write_and_commit(tmp_path, "analysis/secret.txt", "analysis secret must not leak\n")
    _write_and_commit(tmp_path, "output/unattended/secret.log", "output secret must not leak\n")
    provider = FakeOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "COMPLETED"
    prompt = provider.last_payload["prompt"]
    assert "authorized only" in prompt
    assert "analysis secret must not leak" not in prompt
    assert "output secret must not leak" not in prompt


def test_ollama_configured_mutation_output_budget_reaches_request(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = FakeOllamaProvider(tmp_path, mutation_num_predict=2048)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "COMPLETED"
    assert provider.last_payload["options"]["num_predict"] == 2048
    assert result.evidence[0]["num_predict"] == 2048
    assert result.verification_handoff_metadata["num_predict"] == 2048


def test_ollama_model_response_cannot_choose_token_budget(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = BudgetChoosingOllamaProvider(tmp_path, mutation_num_predict=1024)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "COMPLETED"
    assert provider.last_payload["options"]["num_predict"] == 1024
    assert result.verification_handoff_metadata["num_predict"] == 1024


def test_ollama_mutation_output_budget_hard_maximum_enforced(tmp_path):
    with pytest.raises(ValueError, match="hard maximum"):
        FakeOllamaProvider(tmp_path, mutation_num_predict=4097)


def test_ollama_default_mutation_output_budget_remains_256(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = FakeOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "COMPLETED"
    assert provider.last_payload["options"]["num_predict"] == 256


def test_ollama_malformed_json_protection_remains(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = MalformedJsonOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "FAILED"
    assert result.failure_class == "MALFORMED_OLLAMA_JSON"
    assert provider.generate_called is True


def test_ollama_read_only_request_rejects_file_changes_without_writing(tmp_path):
    provider = FakeOllamaProvider(tmp_path)
    request = _request(tmp_path).model_copy(update={"read_only": True, "required_capabilities": ["inspection_reasoning"]})

    result = provider.invoke(request)

    assert result.status == "FAILED"
    assert result.failure_class == "READ_ONLY_MUTATION_ATTEMPT"
    assert not (tmp_path / "provider-proof.txt").exists()
    assert provider.last_payload is not None
    assert "read-only request" in provider.last_payload["prompt"]


def test_ollama_timeout_has_distinct_failure_class(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = TimeoutOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "FAILED"
    assert result.failure_class == "PROVIDER_TIMEOUT"


def test_router_records_ollama_timeout_distinctly(session, tmp_path):
    _prepare_git_workspace(tmp_path)
    _install_plan_freeze(session)
    provider = TimeoutOllamaProvider(tmp_path)

    result = ModelExecutionRouter(session, registry=RuntimeProviderRegistry([provider])).execute(_context(tmp_path))

    row = session.scalar(select(ModelExecutionORM).where(ModelExecutionORM.request_id == result.execution_id))
    assert result.outcome == "FAILED"
    assert result.failure_class == "PROVIDER_TIMEOUT"
    assert row is not None
    assert row.error_code == "PROVIDER_TIMEOUT"


def test_ollama_operator_workspace_root_configuration_reaches_provider(tmp_path, monkeypatch):
    cli_root = tmp_path / "cli-root"
    env_root = tmp_path / "env-root"
    cli_root.mkdir()
    env_root.mkdir()
    monkeypatch.setenv("LUCIUS_OLLAMA_ALLOWED_WORKSPACE_ROOTS", str(env_root))
    roots = _ollama_allowed_workspace_roots(Namespace(ollama_allowed_workspace_root=[str(cli_root)]))

    provider = OllamaExecutionProvider(allowed_workspace_roots=roots)

    assert cli_root.resolve() in provider.allowed_workspace_roots
    assert env_root.resolve() in provider.allowed_workspace_roots


def test_ollama_qwen3_coder_profile_is_supervised_only(tmp_path):
    provider = OllamaExecutionProvider(model="qwen3-coder:30b", allowed_workspace_roots=[tmp_path])

    profile = provider.registration.models[0].capability_profile
    assert profile is not None
    assert profile.execution_tier == "TIER_2_LOCAL_SUPERVISED_SCHEMA_CONSTRAINED"
    assert profile.status == "SUPERVISED_ONLY"
    assert profile.unattended_eligible is False
    assert profile.schema_constrained_required is True


def test_ollama_qwen3_8b_profile_disallows_unattended_mutation_but_keeps_read_only_support(tmp_path):
    provider = OllamaExecutionProvider(model="qwen3:8b", allowed_workspace_roots=[tmp_path])

    profile = provider.registration.models[0].capability_profile
    assert profile is not None
    assert profile.execution_tier == "LOCAL_TIER_1"
    assert profile.status == "QUALIFIED_WITH_CONSTRAINTS"
    assert profile.unattended_eligible is True
    assert profile.unattended_mutation_eligible is False
    assert "inspection_reasoning" in profile.supported_capabilities
    assert provider.registration.available is True
    assert "LWORK_000147" in " ".join(profile.policy_notes)
    assert "LWORK_000148" in " ".join(profile.policy_notes)


class FakeOllamaProvider(OllamaExecutionProvider):
    def __init__(
        self,
        workspace: Path,
        *,
        provider_id: str = "ollama-local",
        files: list[dict] | None = None,
        mutation_num_predict: int = 256,
        max_context_file_bytes: int = 20_000,
        max_context_total_bytes: int = 40_000,
    ):
        super().__init__(
            provider_id=provider_id,
            allowed_workspace_roots=[workspace],
            mutation_num_predict=mutation_num_predict,
            max_context_file_bytes=max_context_file_bytes,
            max_context_total_bytes=max_context_total_bytes,
        )
        self.files = files or [{"path": "provider-proof.txt", "content": "created by local ollama\n"}]
        self.generate_called = False
        self.last_payload: dict | None = None

    def is_available(self) -> bool:
        return True

    def _post(self, path: str, payload: dict, *, timeout_seconds: int) -> OllamaHttpResponse:
        assert path == "/api/generate"
        assert payload["model"] == "qwen3:8b"
        self.generate_called = True
        self.last_payload = payload
        file_payload = ",".join(
            '{"path":' + repr(item["path"]).replace("'", '"') + ',"content":' + repr(item["content"]).replace("'", '"') + "}"
            for item in self.files
        )
        return OllamaHttpResponse(
            status=200,
            body={
                "model": "qwen3:8b",
                "done": True,
                "response": (
                    f'{{"files":[{file_payload}]}}'
                ),
                "total_duration": 21_000_000,
                "prompt_eval_count": 12,
                "eval_count": 18,
            },
        )


class BudgetChoosingOllamaProvider(FakeOllamaProvider):
    def _post(self, path: str, payload: dict, *, timeout_seconds: int) -> OllamaHttpResponse:
        response = super()._post(path, payload, timeout_seconds=timeout_seconds)
        response.body["response"] = (
            '{"files":[{"path":"provider-proof.txt","content":"created by local ollama\\n"}],"num_predict":999999}'
        )
        return response


class MalformedJsonOllamaProvider(FakeOllamaProvider):
    def _post(self, path: str, payload: dict, *, timeout_seconds: int) -> OllamaHttpResponse:
        assert path == "/api/generate"
        self.generate_called = True
        self.last_payload = payload
        return OllamaHttpResponse(
            status=200,
            body={
                "model": "qwen3:8b",
                "done": True,
                "response": "not-json",
            },
        )



class UnexpectedSideEffectOllamaProvider(FakeOllamaProvider):
    def _apply_file_changes(
        self,
        workspace: Path,
        payload: dict,
        allowed_mutation_paths: list[str],
    ) -> list[dict]:
        written = super()._apply_file_changes(
            workspace,
            payload,
            allowed_mutation_paths,
        )
        (workspace / "unexpected.txt").write_text(
            "unexpected side effect\n",
            encoding="utf-8",
        )
        return written


class TimeoutOllamaProvider(FakeOllamaProvider):
    def _post(self, path: str, payload: dict, *, timeout_seconds: int) -> OllamaHttpResponse:
        raise error.URLError(TimeoutError("timed out"))



def _prepare_git_workspace(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)

    commands = [
        ["git", "init"],
        ["git", "config", "user.email", "lucius@example.test"],
        ["git", "config", "user.name", "Lucius Tests"],
    ]

    for command in commands:
        result = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    baseline = workspace / "README.md"
    baseline.write_text("# Ollama Runtime Test\n", encoding="utf-8")

    for command in [
        ["git", "add", "README.md"],
        ["git", "commit", "-m", "initial"],
    ]:
        result = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def _git(workspace: Path, args: list[str]) -> None:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _write_and_commit(workspace: Path, path: str, content: str) -> None:
    target = workspace / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(workspace, ["add", path])
    _git(workspace, ["commit", "-m", f"add {path}"])


def _add_tracked_python_runtime(workspace: Path) -> None:
    os.symlink(Path.cwd() / ".venv", workspace / ".venv")
    _git(workspace, ["add", ".venv"])
    _git(workspace, ["commit", "-m", "add local python runtime"])


def _install_plan_freeze(session) -> None:
    session.add(
        PlanFreezeORM(
            id="LFREEZE_TEST",
            plan_id="LPLAN_TEST",
            task_id="LTASK_TEST",
            project_id="PROJECT_TEST",
            repository_state_id=None,
            repository_snapshot_ids=[],
            evidence_ids=[],
            commit_sha=None,
            planning_mode="CURRENT_STATE_PLANNING",
            evaluation_version="phase-1.30-test",
            plan_payload={
                "affected_files": [
                    {"path": "provider-proof.txt"},
                ],
                "deterministic_acceptance_checks": [
                    {
                        "type": "exact_file_content",
                        "path": "provider-proof.txt",
                        "expected_text": "created by local ollama\n",
                    }
                ],
            },
            frozen_by="SYSTEM",
        )
    )
    session.flush()

def _request(workspace: Path) -> RuntimeExecutionRequest:
    return RuntimeExecutionRequest(
        execution_id="LEXEC_TEST",
        task_id="LTASK_TEST",
        workflow_id="LWORK_TEST",
        item_id="ITEM_TEST",
        logical_task_id="ITEM_TEST",
        plan_id="LPLAN_TEST",
        plan_freeze_id="LFREEZE_TEST",
        isolated_workspace=str(workspace),
        task_intent="Create provider-proof.txt with deterministic proof text.",
        allowed_mutation_scope="ISOLATED_DEVELOPMENT_ONLY",
        allowed_mutation_paths=["provider-proof.txt"],
        deterministic_acceptance_checks=[
            {
                "type": "exact_file_content",
                "path": "provider-proof.txt",
                "expected_text": "created by local ollama\n",
            }
        ],
        required_capabilities=["code_modification"],
    )


def _context(workspace: Path) -> RuntimeExecutionContext:
    return RuntimeExecutionContext(
        workflow_id="LWORK_TEST",
        item_id="ITEM_TEST",
        logical_task_id="ITEM_TEST",
        project_id="PROJECT_TEST",
        repository_id="REPO_TEST",
        workflow_task_id="LTASK_TEST",
        title="Create provider proof",
        workflow_objective="Prove local provider can execute an isolated bounded task.",
        worktree_path=str(workspace),
        authority_tier="ISOLATED_DEVELOPMENT_ONLY",
        plan_id="LPLAN_TEST",
        plan_freeze_id="LFREEZE_TEST",
        queue_item={
            "item_id": "ITEM_TEST",
            "state": "RUNNING",
            "priority": "NORMAL",
            "version": 1,
            "required_capabilities": ["code_modification"],
            "task_type": "engineering",
        },
    )


def _unattended_context(workspace: Path) -> RuntimeExecutionContext:
    context = _context(workspace)
    queue_item = {
        **context.queue_item,
        "unattended": True,
        "task_complexity": "T1",
        "task_risk": "LOW",
        "isolation_mode": "ISOLATED_WORKTREE",
        "context_limits": {"deterministic_verification": True},
    }
    return context.model_copy(update={"queue_item": queue_item})

def test_ollama_mutation_request_uses_structured_json_schema(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = FakeOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "COMPLETED"
    schema = provider.last_payload["format"]
    assert isinstance(schema, dict)
    assert schema["type"] == "object"
    assert schema["required"] == ["edits"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["edits"]["type"] == "array"
    item_schema = schema["properties"]["edits"]["items"]
    assert item_schema["required"] == ["path", "old", "new"]
    assert item_schema["additionalProperties"] is False



def test_ollama_compact_edit_replaces_unique_authorized_fragment(tmp_path):
    _prepare_git_workspace(tmp_path)
    target = tmp_path / "provider-proof.txt"
    target.write_text("alpha\nold value\nomega\n", encoding="utf-8")
    _git(tmp_path, ["add", "provider-proof.txt"])
    _git(tmp_path, ["commit", "-m", "compact edit baseline"])

    provider = FakeOllamaProvider(tmp_path)
    written = provider._apply_file_changes(
        tmp_path,
        {
            "edits": [
                {
                    "path": "provider-proof.txt",
                    "old": "old value",
                    "new": "new value",
                }
            ]
        },
        ["provider-proof.txt"],
    )

    assert target.read_text(encoding="utf-8") == "alpha\nnew value\nomega\n"
    assert written[0]["path"] == "provider-proof.txt"


def test_ollama_compact_edit_rejects_no_effect_change(tmp_path):
    workspace = tmp_path / "repo"
    workspace.mkdir()
    target = workspace / "example.txt"
    target.write_text("alpha\nbeta\n")

    provider = OllamaExecutionProvider()

    try:
        provider._materialize_compact_edits(
            workspace,
            [
                {
                    "path": "example.txt",
                    "old": "beta",
                    "new": "beta",
                }
            ],
            ["example.txt"],
        )
    except Exception as exc:
        assert getattr(exc, "failure_class", None) == "NO_EFFECT_COMPACT_EDIT"
    else:
        raise AssertionError("Expected NO_EFFECT_COMPACT_EDIT")


def test_ollama_compact_edit_rejects_non_unique_fragment(tmp_path):
    _prepare_git_workspace(tmp_path)
    target = tmp_path / "provider-proof.txt"
    target.write_text("same\nsame\n", encoding="utf-8")
    _git(tmp_path, ["add", "provider-proof.txt"])
    _git(tmp_path, ["commit", "-m", "ambiguous compact edit baseline"])

    provider = FakeOllamaProvider(tmp_path)
    result = provider.invoke(
        _request(tmp_path).model_copy(
            update={
                "metadata": {
                    **_request(tmp_path).metadata,
                }
            }
        )
    )
    assert result.status == "COMPLETED"

    try:
        provider._apply_file_changes(
            tmp_path,
            {
                "edits": [
                    {
                        "path": "provider-proof.txt",
                        "old": "same",
                        "new": "different",
                    }
                ]
            },
            ["provider-proof.txt"],
        )
    except Exception as exc:
        assert getattr(exc, "failure_class", None) == "COMPACT_EDIT_TARGET_MISMATCH"
    else:
        raise AssertionError("ambiguous compact edit should fail closed")


def test_ollama_compact_edit_rejects_unauthorized_path(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = FakeOllamaProvider(tmp_path)

    try:
        provider._apply_file_changes(
            tmp_path,
            {
                "edits": [
                    {
                        "path": "unauthorized.txt",
                        "old": "",
                        "new": "should never be written\n",
                    }
                ]
            },
            ["provider-proof.txt"],
        )
    except Exception as exc:
        assert getattr(exc, "failure_class", None) == "MUTATION_SCOPE_VIOLATION"
    else:
        raise AssertionError("unauthorized compact edit should fail closed")

    assert not (tmp_path / "unauthorized.txt").exists()


def test_ollama_compact_edit_supports_new_authorized_file(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = FakeOllamaProvider(tmp_path)

    written = provider._apply_file_changes(
        tmp_path,
        {
            "edits": [
                {
                    "path": "new-file.txt",
                    "old": "",
                    "new": "created compactly\n",
                }
            ]
        },
        ["new-file.txt"],
    )

    assert (tmp_path / "new-file.txt").read_text(encoding="utf-8") == "created compactly\n"
    assert written[0]["path"] == "new-file.txt"


class TruncatedJsonOllamaProvider(FakeOllamaProvider):
    def _post(self, path: str, payload: dict, *, timeout_seconds: int) -> OllamaHttpResponse:
        assert path == "/api/generate"
        self.generate_called = True
        self.last_payload = payload
        return OllamaHttpResponse(
            status=200,
            body={
                "model": "qwen3:8b",
                "done": False,
                "done_reason": "length",
                "eval_count": payload["options"]["num_predict"],
                "response": '{"files":[{"path":"provider-proof.txt","content":"unfinished',
            },
        )


def test_ollama_truncated_json_has_distinct_failure_class(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = TruncatedJsonOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "FAILED"
    assert result.failure_class == "OLLAMA_OUTPUT_TRUNCATED"
    assert result.retryability.value == "RETRYABLE_OR_FAILOVERABLE"
    assert "eval_count=256" in result.mutation_summary
    assert "num_predict=256" in result.mutation_summary


class BrokenBraceJsonOllamaProvider(FakeOllamaProvider):
    def _post(self, path: str, payload: dict, *, timeout_seconds: int) -> OllamaHttpResponse:
        self.generate_called = True
        self.last_payload = payload
        return OllamaHttpResponse(
            status=200,
            body={
                "model": "qwen3:8b",
                "done": True,
                "eval_count": 20,
                "response": 'prefix {"files":[}',
            },
        )


def test_ollama_broken_brace_json_remains_fail_closed(tmp_path):
    _prepare_git_workspace(tmp_path)
    provider = BrokenBraceJsonOllamaProvider(tmp_path)

    result = provider.invoke(_request(tmp_path))

    assert result.status == "FAILED"
    assert result.failure_class == "MALFORMED_OLLAMA_JSON"
