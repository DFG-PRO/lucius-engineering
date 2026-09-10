from __future__ import annotations

from pathlib import Path

from lucius.runtime.ollama import OllamaExecutionProvider, OllamaHttpResponse
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.schema_constraints import SKELETON_METADATA_KEY
from lucius.runtime.schemas import RuntimeExecutionContext, RuntimeExecutionRequest


def test_ollama_provider_applies_model_generated_file_change_in_isolated_workspace(tmp_path):
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
    provider = FakeOllamaProvider(tmp_path, files=[{"path": "../escape.txt", "content": "no"}])

    result = provider.invoke(_request(tmp_path))

    assert result.status == "FAILED"
    assert result.failure_class == "UNSAFE_FILE_PATH"
    assert not (tmp_path.parent / "escape.txt").exists()


def test_router_prefers_cheaper_capable_provider_over_premium(session, tmp_path):
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
    provider = FakeOllamaProvider(tmp_path)

    result = ModelExecutionRouter(
        session,
        registry=RuntimeProviderRegistry([provider]),
    ).execute(_context(tmp_path))

    assert result.outcome == "COMPLETED"
    assert result.provider_id == "ollama-local"
    assert (tmp_path / "provider-proof.txt").exists()


def test_ollama_prompt_includes_schema_constrained_skeleton(tmp_path):
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


class FakeOllamaProvider(OllamaExecutionProvider):
    def __init__(
        self,
        workspace: Path,
        *,
        provider_id: str = "ollama-local",
        files: list[dict] | None = None,
    ):
        super().__init__(provider_id=provider_id, allowed_workspace_roots=[workspace])
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
                    '{"summary":"model generated a proof file",'
                    '"completed_substeps":["generated deterministic file"],'
                    f'"files":[{file_payload}],'
                    '"verification":[{"result":"PASS","detail":"file content expected"}],'
                    '"documentation":[]}'
                ),
                "total_duration": 21_000_000,
                "prompt_eval_count": 12,
                "eval_count": 18,
            },
        )


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
