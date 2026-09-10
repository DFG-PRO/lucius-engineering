from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

from lucius.runtime.schemas import (
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    RuntimeProviderModel,
    RuntimeProviderRegistration,
    RuntimeRetryability,
)


@dataclass(frozen=True)
class OllamaHttpResponse:
    status: int
    body: dict[str, Any]


class OllamaExecutionProvider:
    """Local Ollama-backed runtime worker for bounded isolated execution."""

    def __init__(
        self,
        *,
        provider_id: str = "ollama-local",
        provider_version: str = "1",
        endpoint: str = "http://127.0.0.1:11434",
        model: str = "qwen3:8b",
        timeout_seconds: int = 60,
        allowed_workspace_roots: list[str | Path] | None = None,
        max_files: int = 3,
        max_file_bytes: int = 20_000,
    ):
        self.provider_id = provider_id
        self.provider_version = provider_version
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        default_roots = [Path("/private/tmp"), Path(tempfile.gettempdir())]
        self.allowed_workspace_roots = [Path(root).resolve() for root in (allowed_workspace_roots or default_roots)]
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.registration = RuntimeProviderRegistration(
            provider_id=provider_id,
            provider_version=provider_version,
            capabilities=["code_modification", "documentation_update"],
            supported_task_classes=["engineering"],
            supports_code_modification=True,
            supported_workspace_kinds=["local_git_worktree", "local_workspace"],
            supported_isolation_modes=["ISOLATED_WORKTREE"],
            max_context_tokens=40_960,
            models=[
                RuntimeProviderModel(
                    model_id=model,
                    model_version=None,
                    capabilities=["code_modification", "documentation_update"],
                    context_limit_tokens=40_960,
                    cost_class="LOCAL_FREE",
                    latency_class="MEDIUM",
                )
            ],
            available=True,
            retry_eligible=True,
            failover_eligible=True,
            cost_class="LOCAL_FREE",
            latency_class="MEDIUM",
            policy_labels=["local", "ollama", "no-credential-required"],
            reliability_score=40,
            metadata={"endpoint": self.endpoint, "authority": "provider_worker_only"},
        )

    def is_available(self) -> bool:
        try:
            response = self._get("/api/tags", timeout_seconds=min(5, self.timeout_seconds))
        except Exception:
            return False
        models = response.body.get("models", [])
        return any(item.get("name") == self.model or item.get("model") == self.model for item in models)

    def invoke(self, runtime_request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        started = time.monotonic()
        try:
            workspace = self._validate_workspace(runtime_request)
            prompt = _build_prompt(runtime_request, workspace)
            response = self._post(
                "/api/generate",
                {
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0},
                },
                timeout_seconds=runtime_request.timeout_seconds or self.timeout_seconds,
            )
            payload = _parse_model_payload(response.body)
            written_files = self._apply_file_changes(workspace, payload)
            latency_ms = _latency_ms(response.body, started)
            prompt_tokens = _optional_int(response.body.get("prompt_eval_count"))
            output_tokens = _optional_int(response.body.get("eval_count"))
            return RuntimeExecutionResult(
                execution_id=runtime_request.execution_id,
                provider_id=self.provider_id,
                provider_version=self.provider_version,
                model_id=str(response.body.get("model") or self.model),
                model_version=self.provider_version,
                routing_decision_id=runtime_request.routing_decision_id,
                status="COMPLETED",
                provider_native_status="DONE" if response.body.get("done", True) else "PARTIAL",
                output_artifact_refs=[{"path": item["path"], "bytes": item["bytes"]} for item in written_files],
                mutation_summary=str(payload.get("summary") or "Ollama provider applied bounded file changes."),
                completed_substeps=_string_list(payload.get("completed_substeps")) or [
                    f"Applied {len(written_files)} bounded file change(s) in isolated workspace."
                ],
                evidence=[
                    {
                        "type": "ollama_runtime_execution",
                        "provider_id": self.provider_id,
                        "model": str(response.body.get("model") or self.model),
                        "workspace": str(workspace),
                        "files": written_files,
                    }
                ],
                verification=_verification(payload, written_files),
                documentation=_dict_list(payload.get("documentation")),
                active_execution_seconds=max(0.0, time.monotonic() - started),
                latency_ms=latency_ms,
                input_tokens=prompt_tokens,
                output_tokens=output_tokens,
                total_tokens=(prompt_tokens + output_tokens) if prompt_tokens is not None and output_tokens is not None else None,
                estimated_cost=0.0,
                cost_currency="USD",
                retryability=RuntimeRetryability.NON_RETRYABLE,
                verification_handoff_metadata={
                    "provider": "ollama",
                    "endpoint": self.endpoint,
                    "model_response_captured": True,
                },
            )
        except _ProviderBlocked as blocked:
            return _failed_result(
                runtime_request,
                self,
                started,
                failure_class=blocked.failure_class,
                message=blocked.message,
                retryability=blocked.retryability,
            )
        except (TimeoutError, error.URLError) as exc:
            return _failed_result(
                runtime_request,
                self,
                started,
                failure_class="OLLAMA_UNAVAILABLE",
                message=str(exc),
                retryability=RuntimeRetryability.RETRYABLE_OR_FAILOVERABLE,
            )
        except Exception as exc:
            return _failed_result(
                runtime_request,
                self,
                started,
                failure_class="OLLAMA_PROVIDER_ERROR",
                message=str(exc),
                retryability=RuntimeRetryability.NON_RETRYABLE,
            )

    def _post(self, path: str, payload: dict[str, Any], *, timeout_seconds: int) -> OllamaHttpResponse:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.endpoint}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return OllamaHttpResponse(status=resp.status, body=data)

    def _get(self, path: str, *, timeout_seconds: int) -> OllamaHttpResponse:
        req = request.Request(f"{self.endpoint}{path}", method="GET")
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return OllamaHttpResponse(status=resp.status, body=data)

    def _validate_workspace(self, runtime_request: RuntimeExecutionRequest) -> Path:
        if runtime_request.isolation_mode != "ISOLATED_WORKTREE":
            raise _ProviderBlocked("UNSUPPORTED_ISOLATION_MODE", "Ollama provider requires ISOLATED_WORKTREE.")
        workspace = Path(runtime_request.isolated_workspace).resolve()
        if not workspace.exists() or not workspace.is_dir():
            raise _ProviderBlocked("MISSING_ISOLATED_WORKSPACE", f"Workspace does not exist: {workspace}")
        if not any(_is_relative_to(workspace, root) for root in self.allowed_workspace_roots):
            raise _ProviderBlocked("WORKSPACE_OUTSIDE_ALLOWED_ROOTS", f"Workspace is outside allowed roots: {workspace}")
        return workspace

    def _apply_file_changes(self, workspace: Path, payload: dict[str, Any]) -> list[dict[str, Any]]:
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            raise _ProviderBlocked("NO_FILE_CHANGES", "Ollama response did not contain file changes.")
        if len(files) > self.max_files:
            raise _ProviderBlocked("TOO_MANY_FILE_CHANGES", "Ollama response exceeded max file changes.")
        written: list[dict[str, Any]] = []
        for item in files:
            if not isinstance(item, dict):
                raise _ProviderBlocked("MALFORMED_FILE_CHANGE", "File change must be an object.")
            raw_path = item.get("path")
            content = item.get("content")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise _ProviderBlocked("MALFORMED_FILE_CHANGE", "File change path is required.")
            if raw_path.startswith("/") or ".." in Path(raw_path).parts:
                raise _ProviderBlocked("UNSAFE_FILE_PATH", f"Unsafe file path: {raw_path}")
            if not isinstance(content, str):
                raise _ProviderBlocked("MALFORMED_FILE_CHANGE", "File change content must be a string.")
            encoded = content.encode("utf-8")
            if len(encoded) > self.max_file_bytes:
                raise _ProviderBlocked("FILE_CHANGE_TOO_LARGE", f"File change too large: {raw_path}")
            target = (workspace / raw_path).resolve()
            if not _is_relative_to(target, workspace):
                raise _ProviderBlocked("UNSAFE_FILE_PATH", f"Unsafe file path: {raw_path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append({"path": raw_path, "bytes": len(encoded)})
        return written


class _ProviderBlocked(Exception):
    def __init__(
        self,
        failure_class: str,
        message: str,
        retryability: RuntimeRetryability = RuntimeRetryability.NON_RETRYABLE,
    ):
        super().__init__(message)
        self.failure_class = failure_class
        self.message = message
        self.retryability = retryability


def _build_prompt(runtime_request: RuntimeExecutionRequest, workspace: Path) -> str:
    return "\n".join(
        [
            "You are a bounded local execution provider for Lucius.",
            "Lucius retains scheduling, lifecycle, repository selection, release, and authorization authority.",
            "Work only inside the supplied isolated workspace.",
            "Return strict JSON only, with no markdown.",
            "Schema:",
            '{"summary":"...","completed_substeps":["..."],"files":[{"path":"relative/path","content":"..."}],"verification":[{"result":"PASS","detail":"..."}],"documentation":[]}',
            f"Execution id: {runtime_request.execution_id}",
            f"Task id: {runtime_request.task_id}",
            f"Plan id: {runtime_request.plan_id}",
            f"Plan freeze id: {runtime_request.plan_freeze_id}",
            f"Allowed mutation scope: {runtime_request.allowed_mutation_scope}",
            f"Workspace: {workspace}",
            f"Task intent: {runtime_request.task_intent}",
            "Keep the change tiny, deterministic, and automatically verifiable.",
            "For evidence-sensitive work, label quantitative statements as FACT, DERIVED_VALUE, ASSUMPTION, PROPOSED_PARAMETER, or UNKNOWN.",
            "Do not present proposed protocol values, thresholds, dates, costs, markets, credentials, or performance as facts without supplied evidence.",
        ]
    )


def _parse_model_payload(body: dict[str, Any]) -> dict[str, Any]:
    raw = body.get("response")
    if not isinstance(raw, str):
        raise _ProviderBlocked("MALFORMED_OLLAMA_RESPONSE", "Ollama response missing text payload.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise _ProviderBlocked("MALFORMED_OLLAMA_JSON", "Ollama response was not valid JSON.")
        payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise _ProviderBlocked("MALFORMED_OLLAMA_JSON", "Ollama JSON response must be an object.")
    return payload


def _verification(payload: dict[str, Any], written_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    verification = _dict_list(payload.get("verification"))
    if verification:
        return verification
    return [{"result": "PASS", "detail": f"Provider wrote {len(written_files)} file(s) in isolated workspace."}]


def _failed_result(
    runtime_request: RuntimeExecutionRequest,
    provider: OllamaExecutionProvider,
    started: float,
    *,
    failure_class: str,
    message: str,
    retryability: RuntimeRetryability,
) -> RuntimeExecutionResult:
    return RuntimeExecutionResult(
        execution_id=runtime_request.execution_id,
        provider_id=provider.provider_id,
        provider_version=provider.provider_version,
        model_id=provider.model,
        model_version=provider.provider_version,
        routing_decision_id=runtime_request.routing_decision_id,
        status="FAILED",
        provider_native_status="FAILED",
        mutation_summary=message,
        active_execution_seconds=max(0.0, time.monotonic() - started),
        retryability=retryability,
        failure_class=failure_class,
        provider_error_metadata={"message": message},
    )


def _latency_ms(body: dict[str, Any], started: float) -> int:
    total_duration = body.get("total_duration")
    if isinstance(total_duration, int):
        return max(0, int(total_duration / 1_000_000))
    return max(0, int((time.monotonic() - started) * 1000))


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
