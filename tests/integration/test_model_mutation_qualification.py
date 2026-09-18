from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
from typing import Any

import pytest

from lucius.persistence.orm import AuditEventORM, PlanFreezeORM
from lucius.runtime.ollama import OllamaExecutionProvider
from lucius.runtime.ollama import OllamaHttpResponse
from lucius.runtime.qualification import (
    MODEL_MUTATION_QUALIFICATION_SCHEMA_CONSTRAINT,
    ModelMutationQualificationConfig,
    run_model_mutation_qualification,
)
from lucius.runtime.schemas import RuntimeExecutionRequest, RuntimeExecutionResult, RuntimeExecutionSupervision


def test_model_mutation_qualification_harness_creates_canonical_workflow_and_runs_supervised_qwen_coder(
    session,
    tmp_path: Path,
):
    target = _make_git_repo(tmp_path / "darwin")
    _add_target_venv(target)
    baseline = _run_git(target, "rev-parse", "main")
    worktree = tmp_path / "qualification-worktree"
    provider = RecordingOllamaProvider(tmp_path, model="qwen3-coder:30b")
    readme_before = (target / "README.md").read_text(encoding="utf-8")

    result = run_model_mutation_qualification(
        session,
        ModelMutationQualificationConfig(
            model_id="qwen3-coder:30b",
            target_repository=target,
            target_baseline=baseline,
            isolated_worktree=worktree,
            execution_supervision=RuntimeExecutionSupervision.SUPERVISED,
            allowed_mutation_paths=["tests/test_content_hashing_model_qualification.py"],
            deterministic_acceptance_checks=_qualification_checks(),
            execution_provider=provider,
        ),
    )

    freeze = session.get(PlanFreezeORM, result.plan_freeze_id)
    assert result.workflow_id.startswith("LWORK_")
    assert result.plan_id.startswith("LPLAN_")
    assert result.plan_freeze_id.startswith("LFREEZE_")
    assert result.runtime_result["status"] == "COMPLETED"
    assert result.provider_invocation_authorized is True
    assert result.worktree_clean_after_run is True
    assert result.target_environment_path == str((target / ".venv").resolve())
    assert result.worktree_environment_path == str(worktree / ".venv")
    assert (worktree / ".venv").is_symlink()
    assert (worktree / ".venv").resolve() == (target / ".venv").resolve()
    assert (worktree / ".venv" / "bin" / "python").exists()
    assert _run_python_version(worktree / ".venv" / "bin" / "python")
    assert _run_git(worktree, "rev-parse", "HEAD") == baseline
    assert _run_git(worktree, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert _run_git(target, "diff", "--name-only") == ""
    assert _run_git(target, "diff", "--cached", "--name-only") == ""
    assert (target / "README.md").read_text(encoding="utf-8") == readme_before
    assert provider.requests[0].execution_supervision == RuntimeExecutionSupervision.SUPERVISED
    assert provider.requests[0].context_limits["schema_constraint"] == MODEL_MUTATION_QUALIFICATION_SCHEMA_CONSTRAINT
    assert provider.requests[0].allowed_mutation_paths == ["tests/test_content_hashing_model_qualification.py"]
    assert provider.requests[0].deterministic_acceptance_checks == _qualification_checks()
    assert freeze.plan_payload["affected_files"] == [
        {
            "path": "tests/test_content_hashing_model_qualification.py",
            "status": "NEW_PROPOSED",
            "evidence_ids": [],
        }
    ]
    assert freeze.plan_payload["deterministic_acceptance_checks"] == _qualification_checks()
    assert _audit_count(session, "PERSISTENT_WORKFLOW_CREATED") == 1
    assert _audit_count(session, "ENGINEERING_PLAN_FROZEN") == 1
    assert _audit_count(session, "MODEL_EXECUTION_ROUTING_DECISION") == 1


def test_model_mutation_qualification_links_target_venv_without_execution(
    session,
    tmp_path: Path,
):
    target = _make_git_repo(tmp_path / "darwin")
    _add_target_venv(target)
    baseline = _run_git(target, "rev-parse", "main")
    worktree = tmp_path / "qualification-worktree"

    result = run_model_mutation_qualification(
        session,
        ModelMutationQualificationConfig(
            model_id="qwen3-coder:30b",
            target_repository=target,
            target_baseline=baseline,
            isolated_worktree=worktree,
            execution_supervision=RuntimeExecutionSupervision.SUPERVISED,
            allowed_mutation_paths=["tests/test_content_hashing_model_qualification.py"],
            deterministic_acceptance_checks=_qualification_checks(),
            execute=False,
        ),
    )

    assert result.target_environment_path == str((target / ".venv").resolve())
    assert result.worktree_environment_path == str(worktree / ".venv")
    assert (worktree / ".venv").is_symlink()
    assert (worktree / ".venv" / "bin" / "python").exists()
    assert _run_python_version(worktree / ".venv" / "bin" / "python")
    assert _run_git(worktree, "status", "--porcelain=v1", "--untracked-files=all") == ""


def test_model_mutation_qualification_bootstrap_only_still_freezes_plan_without_provider(
    session,
    tmp_path: Path,
):
    target = _make_git_repo(tmp_path / "darwin")
    baseline = _run_git(target, "rev-parse", "main")
    provider = RecordingOllamaProvider(tmp_path, model="qwen3-coder:30b")

    result = run_model_mutation_qualification(
        session,
        ModelMutationQualificationConfig(
            model_id="qwen3-coder:30b",
            target_repository=target,
            target_baseline=baseline,
            isolated_worktree=tmp_path / "qualification-worktree",
            execution_supervision=RuntimeExecutionSupervision.SUPERVISED,
            allowed_mutation_paths=["tests/test_content_hashing_model_qualification.py"],
            deterministic_acceptance_checks=_qualification_checks(),
            execution_provider=provider,
            execute=False,
        ),
    )

    assert result.plan_id is not None
    assert result.plan_freeze_id is not None
    assert result.runtime_result is None
    assert result.provider_invocation_authorized is None
    assert provider.requests == []


def test_model_mutation_qualification_rejects_baseline_mismatch_before_worktree(
    session,
    tmp_path: Path,
):
    target = _make_git_repo(tmp_path / "darwin")
    old_baseline = _run_git(target, "rev-parse", "main")
    (target / "README.md").write_text("# Test Repo\n\nnew main head\n", encoding="utf-8")
    _run_git(target, "add", "README.md")
    _run_git(target, "commit", "-m", "advance main")

    with pytest.raises(ValueError, match="does not match main HEAD"):
        run_model_mutation_qualification(
            session,
            ModelMutationQualificationConfig(
                model_id="qwen3-coder:30b",
                target_repository=target,
                target_baseline=old_baseline,
                isolated_worktree=tmp_path / "qualification-worktree",
                execution_supervision=RuntimeExecutionSupervision.SUPERVISED,
                allowed_mutation_paths=["tests/test_content_hashing_model_qualification.py"],
                deterministic_acceptance_checks=_qualification_checks(),
                execute=False,
            ),
        )

    assert not (tmp_path / "qualification-worktree").exists()


def test_model_mutation_qualification_rejects_conflicting_worktree_venv(
    session,
    tmp_path: Path,
):
    target = _make_git_repo(tmp_path / "darwin")
    _add_target_venv(target)
    _run_git(target, "add", ".venv/bin/python")
    _run_git(target, "commit", "-m", "track unsafe venv")
    baseline = _run_git(target, "rev-parse", "main")

    with pytest.raises(ValueError, match="Unsafe conflicting worktree .venv"):
        run_model_mutation_qualification(
            session,
            ModelMutationQualificationConfig(
                model_id="qwen3-coder:30b",
                target_repository=target,
                target_baseline=baseline,
                isolated_worktree=tmp_path / "qualification-worktree",
                execution_supervision=RuntimeExecutionSupervision.SUPERVISED,
                allowed_mutation_paths=["tests/test_content_hashing_model_qualification.py"],
                deterministic_acceptance_checks=_qualification_checks(),
                execute=False,
            ),
        )


def test_model_mutation_qualification_immutable_verifier_rejects_trivial_pass(
    session,
    tmp_path: Path,
):
    target = _make_hashing_repo(tmp_path / "darwin")
    _add_target_venv(target)
    baseline = _run_git(target, "rev-parse", "main")
    provider = PayloadOllamaProvider(
        tmp_path,
        files=[
            {
                "path": "tests/test_content_hashing_model_qualification.py",
                "content": (
                    "def test_sha256_helpers_are_deterministic_and_equivalent_for_utf8_text():\n"
                    "    pass\n"
                ),
            }
        ],
    )

    result = run_model_mutation_qualification(
        session,
        ModelMutationQualificationConfig(
            model_id="qwen3-coder:30b",
            target_repository=target,
            target_baseline=baseline,
            isolated_worktree=tmp_path / "qualification-worktree",
            execution_supervision=RuntimeExecutionSupervision.SUPERVISED,
            allowed_mutation_paths=["tests/test_content_hashing_model_qualification.py"],
            deterministic_acceptance_checks=_syntax_checks(),
            immutable_verifier_checks=_immutable_verifier_checks(),
            execution_provider=provider,
        ),
    )

    assert result.runtime_result["status"] in ("FAILED", "BLOCKED")
    assert result.provider_invocation_authorized is True
    assert result.worktree_clean_after_run is True
    assert result.immutable_verifier_paths == ["tests/test_content_hashing_model_qualification_verifier.py"]
    assert provider.requests
    audit = _latest_audit(session, "NATIVE_RUNTIME_TASK_EXECUTION_RECORDED")
    metadata = audit.event_metadata["provider_error_metadata"]
    assert metadata["deterministic_acceptance_code"] == "DETERMINISTIC_ACCEPTANCE_COMMAND_NONZERO_EXIT"
    diagnostic = metadata["deterministic_acceptance_diagnostic"]
    assert diagnostic["result"] == "FAIL"
    assert "tests/test_content_hashing_model_qualification_verifier.py" in diagnostic["argv"]


def test_model_mutation_qualification_immutable_verifier_allows_correct_implementation(
    session,
    tmp_path: Path,
):
    target = _make_hashing_repo(tmp_path / "darwin")
    _add_target_venv(target)
    baseline = _run_git(target, "rev-parse", "main")
    provider = PayloadOllamaProvider(
        tmp_path,
        files=[
            {
                "path": "tests/test_content_hashing_model_qualification.py",
                "content": _correct_hashing_qualification_test(),
            }
        ],
    )

    result = run_model_mutation_qualification(
        session,
        ModelMutationQualificationConfig(
            model_id="qwen3-coder:30b",
            target_repository=target,
            target_baseline=baseline,
            isolated_worktree=tmp_path / "qualification-worktree",
            execution_supervision=RuntimeExecutionSupervision.SUPERVISED,
            allowed_mutation_paths=["tests/test_content_hashing_model_qualification.py"],
            deterministic_acceptance_checks=_syntax_checks(),
            immutable_verifier_checks=_immutable_verifier_checks(),
            execution_provider=provider,
        ),
    )

    assert result.runtime_result["status"] == "COMPLETED"
    assert result.immutable_verifier_checks == _immutable_verifier_checks()
    assert result.immutable_verifier_paths == ["tests/test_content_hashing_model_qualification_verifier.py"]


def test_model_mutation_qualification_rejects_verifier_in_allowed_mutation_scope(
    session,
    tmp_path: Path,
):
    target = _make_hashing_repo(tmp_path / "darwin")
    _add_target_venv(target)
    baseline = _run_git(target, "rev-parse", "main")

    with pytest.raises(ValueError, match="Immutable verifier path must not be in allowed mutation scope"):
        run_model_mutation_qualification(
            session,
            ModelMutationQualificationConfig(
                model_id="qwen3-coder:30b",
                target_repository=target,
                target_baseline=baseline,
                isolated_worktree=tmp_path / "qualification-worktree",
                execution_supervision=RuntimeExecutionSupervision.SUPERVISED,
                allowed_mutation_paths=[
                    "tests/test_content_hashing_model_qualification.py",
                    "tests/test_content_hashing_model_qualification_verifier.py",
                ],
                deterministic_acceptance_checks=_syntax_checks(),
                immutable_verifier_checks=_immutable_verifier_checks(),
                execute=False,
            ),
        )

    assert not (tmp_path / "qualification-worktree").exists()


def test_model_mutation_qualification_rejects_verifier_missing_from_baseline(
    session,
    tmp_path: Path,
):
    target = _make_git_repo(tmp_path / "darwin")
    _add_target_venv(target)
    baseline = _run_git(target, "rev-parse", "main")
    (target / "tests" / "test_content_hashing_model_qualification_verifier.py").write_text(
        "def test_untracked_verifier():\n    assert True\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Immutable verifier path does not exist in target baseline"):
        run_model_mutation_qualification(
            session,
            ModelMutationQualificationConfig(
                model_id="qwen3-coder:30b",
                target_repository=target,
                target_baseline=baseline,
                isolated_worktree=tmp_path / "qualification-worktree",
                execution_supervision=RuntimeExecutionSupervision.SUPERVISED,
                allowed_mutation_paths=["tests/test_content_hashing_model_qualification.py"],
                deterministic_acceptance_checks=_syntax_checks(),
                immutable_verifier_checks=_immutable_verifier_checks(),
                execute=False,
            ),
        )

    assert not (tmp_path / "qualification-worktree").exists()


def _qualification_checks() -> list[dict[str, Any]]:
    return [
        {
            "type": "file_contains",
            "path": "tests/test_content_hashing_model_qualification.py",
            "expected_text": "def test_sha256_helpers_are_deterministic_and_equivalent_for_utf8_text(",
        },
        {
            "type": "command_succeeds",
            "argv": [
                ".venv/bin/python",
                "-m",
                "pytest",
                "tests/test_content_hashing_model_qualification.py",
                "-q",
            ],
            "timeout_seconds": 300,
        },
        {
            "type": "command_succeeds",
            "argv": [".venv/bin/python", "-m", "pytest", "-q"],
            "timeout_seconds": 300,
        },
    ]


def _syntax_checks() -> list[dict[str, Any]]:
    return [
        {
            "type": "file_contains",
            "path": "tests/test_content_hashing_model_qualification.py",
            "expected_text": "def test_sha256_helpers_are_deterministic_and_equivalent_for_utf8_text(",
        },
        {
            "type": "command_succeeds",
            "argv": [
                ".venv/bin/python",
                "-m",
                "pytest",
                "tests/test_content_hashing_model_qualification.py",
                "-q",
            ],
            "timeout_seconds": 300,
        },
    ]


def _immutable_verifier_checks() -> list[dict[str, Any]]:
    return [
        {
            "type": "command_succeeds",
            "argv": [
                ".venv/bin/python",
                "-m",
                "pytest",
                "tests/test_content_hashing_model_qualification_verifier.py",
                "-q",
            ],
            "timeout_seconds": 300,
        }
    ]


def _make_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _run_git(path, "init")
    _run_git(path, "config", "user.email", "lucius@example.test")
    _run_git(path, "config", "user.name", "Lucius Tests")
    _run_git(path, "checkout", "-b", "main")
    (path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    (path / "tests").mkdir()
    (path / "tests" / "test_sample.py").write_text("def test_sample():\n    assert True\n", encoding="utf-8")
    _run_git(path, "add", "README.md", "tests/test_sample.py")
    _run_git(path, "commit", "-m", "initial")
    return path


def _make_hashing_repo(path: Path) -> Path:
    repo = _make_git_repo(path)
    package = repo / "darwin" / "content"
    package.mkdir(parents=True)
    (repo / "darwin" / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "hashing.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "import hashlib",
                "",
                "",
                "def sha256_bytes(data: bytes) -> str:",
                "    return 'sha256:' + hashlib.sha256(data).hexdigest()",
                "",
                "",
                "def sha256_text(text: str) -> str:",
                "    return sha256_bytes(text.encode('utf-8'))",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "tests" / "test_content_hashing_model_qualification_verifier.py").write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "",
                "",
                "def test_model_qualification_test_contains_behavioral_assertions():",
                "    text = Path('tests/test_content_hashing_model_qualification.py').read_text(encoding='utf-8')",
                "    assert 'sha256_text(' in text",
                "    assert 'sha256_bytes(' in text",
                "    assert 'startswith(\"sha256:\")' in text or \"startswith('sha256:')\" in text",
                "    assert '.encode(\"utf-8\")' in text or \".encode('utf-8')\" in text",
                "    assert 'assert first == second' in text or 'assert second == first' in text",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _run_git(
        repo,
        "add",
        "darwin/__init__.py",
        "darwin/content/__init__.py",
        "darwin/content/hashing.py",
        "tests/test_content_hashing_model_qualification_verifier.py",
    )
    _run_git(repo, "commit", "-m", "add hashing qualification fixture")
    return repo


def _correct_hashing_qualification_test() -> str:
    return "\n".join(
        [
            "from darwin.content.hashing import sha256_bytes, sha256_text",
            "",
            "",
            "def test_sha256_helpers_are_deterministic_and_equivalent_for_utf8_text():",
            "    text = 'cafe'",
            "    first = sha256_text(text)",
            "    second = sha256_text(text)",
            "    assert first == second",
            "    assert first.startswith(\"sha256:\")",
            "    assert first == sha256_bytes(text.encode(\"utf-8\"))",
            "",
        ]
    )


def _add_target_venv(repo: Path) -> None:
    python = repo / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text(
        f"#!/bin/sh\nexec {shlex.quote(str(Path.cwd() / '.venv' / 'bin' / 'python'))} \"$@\"\n",
        encoding="utf-8",
    )
    python.chmod(0o755)


def _run_python_version(python: Path) -> bool:
    result = subprocess.run([str(python), "--version"], capture_output=True, text=True, check=False)
    return result.returncode == 0 and "Python" in (result.stdout + result.stderr)


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _audit_count(session, event_type: str) -> int:
    return session.query(AuditEventORM).filter(AuditEventORM.event_type == event_type).count()


def _latest_audit(session, event_type: str) -> AuditEventORM:
    return (
        session.query(AuditEventORM)
        .filter(AuditEventORM.event_type == event_type)
        .order_by(AuditEventORM.id.desc())
        .first()
    )


class RecordingOllamaProvider(OllamaExecutionProvider):
    def __init__(self, workspace_root: Path, *, model: str):
        super().__init__(
            model=model,
            allowed_workspace_roots=[workspace_root],
        )
        self.requests: list[RuntimeExecutionRequest] = []

    def is_available(self) -> bool:
        return True

    def invoke(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        self.requests.append(request)
        return RuntimeExecutionResult(
            execution_id=request.execution_id,
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            model_id=self.model,
            model_version=self.provider_version,
            routing_decision_id=request.routing_decision_id,
            status="COMPLETED",
            provider_native_status="TEST_COMPLETED",
            completed_substeps=["qualification provider accepted supervised request"],
            evidence=[{"type": "qualification_test_provider", "model": self.model}],
            verification=[{"result": "PASS", "type": "qualification_router_path"}],
            active_execution_seconds=0.001,
        )


class PayloadOllamaProvider(OllamaExecutionProvider):
    def __init__(self, workspace_root: Path, *, files: list[dict[str, str]]):
        super().__init__(
            model="qwen3-coder:30b",
            allowed_workspace_roots=[workspace_root],
        )
        self.files = files
        self.requests: list[RuntimeExecutionRequest] = []

    def is_available(self) -> bool:
        return True

    def invoke(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        self.requests.append(request)
        return super().invoke(request)

    def _post(self, path: str, payload: dict, *, timeout_seconds: int) -> OllamaHttpResponse:
        return OllamaHttpResponse(
            status=200,
            body={
                "model": self.model,
                "response": json.dumps({"files": self.files}),
                "done": True,
                "prompt_eval_count": 12,
                "eval_count": 18,
            },
        )
