from __future__ import annotations

import json
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

from lucius.repositories.git_mutation import (
    GitMutationError,
    changed_paths,
    restore_to_baseline,
    validate_relative_repo_path,
    validate_file_contents,
    write_validated_file_contents,
)
from lucius.runtime.deterministic_acceptance import (
    DeterministicAcceptanceError,
    verify_deterministic_acceptance,
)
from lucius.runtime.schemas import (
    ModelCapabilityProfile,
    ModelQualificationStatus,
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    RuntimeProviderModel,
    RuntimeProviderRegistration,
    RuntimeRetryability,
)
from lucius.runtime.schema_constraints import SKELETON_METADATA_KEY

DEFAULT_OLLAMA_NUM_PREDICT = 512
DEFAULT_MUTATION_NUM_PREDICT = 256
MAX_MUTATION_NUM_PREDICT = 4096
DEFAULT_MAX_CONTEXT_FILE_BYTES = 20_000
DEFAULT_MAX_CONTEXT_TOTAL_BYTES = 40_000


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
        mutation_num_predict: int = DEFAULT_MUTATION_NUM_PREDICT,
        max_mutation_num_predict: int = MAX_MUTATION_NUM_PREDICT,
        max_context_file_bytes: int = DEFAULT_MAX_CONTEXT_FILE_BYTES,
        max_context_total_bytes: int = DEFAULT_MAX_CONTEXT_TOTAL_BYTES,
    ):
        self.provider_id = provider_id
        self.provider_version = provider_version
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        explicit_allowed_roots = allowed_workspace_roots is not None
        default_roots = [Path("/private/tmp"), Path(tempfile.gettempdir())]
        self.allowed_workspace_roots = [Path(root).resolve() for root in (allowed_workspace_roots or default_roots)]
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_mutation_num_predict = _positive_int(max_mutation_num_predict, "max_mutation_num_predict")
        self.mutation_num_predict = _bounded_num_predict(
            mutation_num_predict,
            max_num_predict=self.max_mutation_num_predict,
        )
        self.max_context_file_bytes = _positive_int(max_context_file_bytes, "max_context_file_bytes")
        self.max_context_total_bytes = _positive_int(max_context_total_bytes, "max_context_total_bytes")
        capability_profile = _ollama_model_capability_profile(
            provider_id=provider_id,
            model=model,
            default_timeout_seconds=timeout_seconds,
        )
        self.registration = RuntimeProviderRegistration(
            provider_id=provider_id,
            provider_version=provider_version,
            capabilities=capability_profile.supported_capabilities,
            supported_task_classes=capability_profile.supported_task_classes,
            supports_code_modification=True,
            supported_workspace_kinds=["local_git_worktree", "local_workspace"],
            supported_isolation_modes=["ISOLATED_WORKTREE"],
            max_context_tokens=40_960,
            models=[
                RuntimeProviderModel(
                    model_id=model,
                    model_version=None,
                    capabilities=capability_profile.supported_capabilities,
                    context_limit_tokens=40_960,
                    cost_class="LOCAL_FREE",
                    latency_class="MEDIUM",
                    capability_profile=capability_profile,
                )
            ],
            available=True,
            retry_eligible=True,
            failover_eligible=True,
            cost_class="LOCAL_FREE",
            latency_class="MEDIUM",
            policy_labels=["local", "ollama", "no-credential-required", capability_profile.execution_tier],
            reliability_score=40,
            metadata={
                "endpoint": self.endpoint,
                "authority": "provider_worker_only",
                "allowed_workspace_roots": [str(root) for root in self.allowed_workspace_roots],
                "explicit_allowed_workspace_roots": explicit_allowed_roots,
                "model_capability_status": capability_profile.status.value,
                "mutation_num_predict": self.mutation_num_predict,
                "max_mutation_num_predict": self.max_mutation_num_predict,
                "max_context_file_bytes": self.max_context_file_bytes,
                "max_context_total_bytes": self.max_context_total_bytes,
                "read_only_worker_shape": {
                    "version": "qwen3:8b-unattended-read-only-v1",
                    "max_context_files": 3,
                    "max_context_bytes": 40_000,
                    "max_evidence_refs": 3,
                    "task_type": "inspection",
                    "task_complexity": "T1",
                    "task_risk": "LOW",
                    "required_capabilities": ["inspection_reasoning"],
                    "isolation_mode": "ISOLATED_WORKTREE",
                    "execution_supervision": "UNSUPERVISED",
                },
            },
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
        workspace: Path | None = None
        mutation_attempted = False
        try:
            workspace = self._validate_workspace(runtime_request)
            if not runtime_request.read_only:
                if not runtime_request.allowed_mutation_paths:
                    raise _ProviderBlocked(
                        "MUTATION_SCOPE_VIOLATION",
                        "Mutation request has no authorized frozen mutation paths.",
                    )
                preexisting_changes = _git_changed_paths(workspace)
                if preexisting_changes:
                    raise _ProviderBlocked(
                        "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
                        "Mutation workspace was not clean before execution: "
                        + ", ".join(preexisting_changes),
                    )
            mutation_context = (
                self._mutation_file_context(
                    workspace,
                    runtime_request.allowed_mutation_paths,
                )
                if not runtime_request.read_only
                else []
            )
            read_only_context = (
                self._read_only_file_context(
                    workspace,
                    runtime_request.context_limits.get("read_only_context_paths", []),
                )
                if runtime_request.read_only
                else []
            )
            num_predict = (
                DEFAULT_OLLAMA_NUM_PREDICT
                if runtime_request.read_only
                else self.mutation_num_predict
            )
            prompt = _build_prompt(
                runtime_request,
                workspace,
                mutation_context=mutation_context,
                read_only_context=read_only_context,
            )
            response = self._post(
                "/api/generate",
                {
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "format": (
                        {
                            "type": "object",
                            "properties": {
                                "edits": {
                                    "type": "array",
                                    "minItems": 1,
                                    "maxItems": self.max_files * 8,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "path": {"type": "string"},
                                            "old": {"type": "string"},
                                            "new": {"type": "string"},
                                        },
                                        "required": ["path", "old", "new"],
                                        "additionalProperties": False,
                                    },
                                }
                            },
                            "required": ["edits"],
                            "additionalProperties": False,
                        }
                        if not runtime_request.read_only
                        else {
                            "type": "object",
                            "properties": {
                                "summary": {"type": "string"},
                                "files": {
                                    "type": "array",
                                    "maxItems": 0,
                                },
                                "evidence_refs": {
                                    "type": "array",
                                    "minItems": 1,
                                    "maxItems": 3,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "path": {"type": "string"},
                                            "exact_fragment": {"type": "string"},
                                        },
                                        "required": ["path", "exact_fragment"],
                                        "additionalProperties": False,
                                    },
                                },
                            },
                            "required": [
                                "summary",
                                "files",
                                "evidence_refs",
                            ],
                            "additionalProperties": False,
                        }
                    ),
                    "options": {
                        "temperature": 0,
                        "num_predict": num_predict,
                    },
                },
                timeout_seconds=runtime_request.timeout_seconds or self.timeout_seconds,
            )
            payload = _parse_model_payload(response.body, num_predict=num_predict)
            if runtime_request.read_only:
                files = payload.get("files")
                if isinstance(files, list) and files:
                    raise _ProviderBlocked(
                        "READ_ONLY_MUTATION_ATTEMPT",
                        "Ollama response attempted file changes for a read-only request.",
                    )
                if files not in (None, []):
                    raise _ProviderBlocked(
                        "READ_ONLY_MUTATION_ATTEMPT",
                        "Ollama response contained malformed file-change data for a read-only request.",
                    )
                deterministic_read_only_verification = _verify_read_only_evidence(
                    workspace,
                    read_only_context,
                    payload.get("evidence_refs"),
                )
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
                    mutation_summary=str(payload.get("summary") or "Ollama provider completed read-only execution."),
                    completed_substeps=_string_list(payload.get("completed_substeps")) or [
                        "Completed read-only local model execution without file changes."
                    ],
                    evidence=[
                        {
                            "type": "ollama_read_only_runtime_execution",
                            "provider_id": self.provider_id,
                            "model": str(response.body.get("model") or self.model),
                            "workspace": str(workspace),
                            "num_predict": num_predict,
                        }
                    ],
                    verification=deterministic_read_only_verification,
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
                        "read_only": True,
                        "deterministic_read_only_verification": True,
                        "verified_evidence_refs": len(deterministic_read_only_verification),
                        "num_predict": num_predict,
                    },
                )

            mutation_attempted = True
            written_files = self._apply_file_changes(
                workspace,
                payload,
                runtime_request.allowed_mutation_paths,
            )

            actual_changed_paths = _git_changed_paths(workspace)
            authorized = set(runtime_request.allowed_mutation_paths)
            written_paths = {item["path"] for item in written_files}
            actual_paths = set(actual_changed_paths)

            unauthorized_actual = sorted(actual_paths - authorized)
            missing_written = sorted(written_paths - actual_paths)

            if unauthorized_actual or missing_written:
                details = []
                if unauthorized_actual:
                    details.append(
                        "unauthorized changed paths: " + ", ".join(unauthorized_actual)
                    )
                if missing_written:
                    details.append(
                        "provider-reported writes absent from git changes: "
                        + ", ".join(missing_written)
                    )
                raise _ProviderBlocked(
                    "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
                    "; ".join(details),
                )

            deterministic_verification = {
                "result": "PASS",
                "type": "deterministic_mutation_scope",
                "detail": "Git-observed mutation paths are within the frozen authorized scope.",
                "allowed_paths": sorted(authorized),
                "changed_paths": actual_changed_paths,
                "written_paths": sorted(written_paths),
            }

            deterministic_acceptance_verification = _verify_deterministic_acceptance(
                workspace,
                runtime_request.deterministic_acceptance_checks,
                authorized_paths=authorized,
            )

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
                        "mutation_context_files": [
                            {"path": item["path"], "state": item["state"], "bytes": item["bytes"]}
                            for item in mutation_context
                        ],
                        "num_predict": num_predict,
                    }
                ],
                verification=[
                    deterministic_verification,
                    *deterministic_acceptance_verification,
                    *_verification(payload, written_files),
                ],
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
                    "deterministic_mutation_verification": True,
                    "deterministic_acceptance_verification": bool(
                        deterministic_acceptance_verification
                    ),
                    "allowed_mutation_paths": sorted(authorized),
                    "actual_changed_paths": actual_changed_paths,
                    "num_predict": num_predict,
                    "mutation_context_files": [
                        {"path": item["path"], "state": item["state"], "bytes": item["bytes"]}
                        for item in mutation_context
                    ],
                },
            )
        except _ProviderBlocked as blocked:
            if mutation_attempted and workspace is not None:
                try:
                    _restore_clean_workspace(workspace)
                except _ProviderBlocked as rollback_failure:
                    return _failed_result(
                        runtime_request,
                        self,
                        started,
                        failure_class="DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
                        message=(
                            f"{blocked.message}; mutation rollback failed: "
                            f"{rollback_failure.message}"
                        ),
                        retryability=RuntimeRetryability.NON_RETRYABLE,
                        provider_error_metadata=blocked.provider_error_metadata,
                        verification=blocked.verification,
                    )

            return _failed_result(
                runtime_request,
                self,
                started,
                failure_class=blocked.failure_class,
                message=blocked.message,
                retryability=blocked.retryability,
                provider_error_metadata=blocked.provider_error_metadata,
                verification=blocked.verification,
            )
        except TimeoutError as exc:
            return _failed_result(
                runtime_request,
                self,
                started,
                failure_class="PROVIDER_TIMEOUT",
                message=str(exc),
                retryability=RuntimeRetryability.RETRYABLE_OR_FAILOVERABLE,
            )
        except error.URLError as exc:
            failure_class = "PROVIDER_TIMEOUT" if _url_error_is_timeout(exc) else "OLLAMA_UNAVAILABLE"
            return _failed_result(
                runtime_request,
                self,
                started,
                failure_class=failure_class,
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

    def _read_only_file_context(
        self,
        workspace: Path,
        relative_paths: list[str],
    ) -> list[dict[str, Any]]:
        if not relative_paths:
            raise _ProviderBlocked(
                "MISSING_READ_ONLY_CONTEXT",
                "Read-only repository-grounded execution requires explicit read_only_context_paths.",
            )
        root = workspace.resolve()
        context: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_path in relative_paths:
            relative_path = str(raw_path).strip()
            if not relative_path or relative_path in seen:
                continue
            path = (root / relative_path).resolve()
            if not _is_relative_to(path, root):
                raise _ProviderBlocked("UNSAFE_FILE_PATH", f"Path escapes workspace: {relative_path}")
            if not path.is_file():
                raise _ProviderBlocked("MISSING_READ_ONLY_CONTEXT", f"Context file does not exist: {relative_path}")
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise _ProviderBlocked(
                    "INVALID_READ_ONLY_CONTEXT",
                    f"Context file is not UTF-8 text: {relative_path}",
                ) from exc
            if len(content.encode("utf-8")) > 200_000:
                raise _ProviderBlocked(
                    "READ_ONLY_CONTEXT_TOO_LARGE",
                    f"Context file exceeds 200000 bytes: {relative_path}",
                )
            seen.add(relative_path)
            context.append({"path": relative_path, "content": content})
        if not context:
            raise _ProviderBlocked("MISSING_READ_ONLY_CONTEXT", "No usable read-only context files were supplied.")
        return context

    def _validate_workspace(self, runtime_request: RuntimeExecutionRequest) -> Path:
        if runtime_request.isolation_mode != "ISOLATED_WORKTREE":
            raise _ProviderBlocked("UNSUPPORTED_ISOLATION_MODE", "Ollama provider requires ISOLATED_WORKTREE.")
        workspace = Path(runtime_request.isolated_workspace).resolve()
        if not workspace.exists() or not workspace.is_dir():
            raise _ProviderBlocked("MISSING_ISOLATED_WORKSPACE", f"Workspace does not exist: {workspace}")
        if not any(_is_relative_to(workspace, root) for root in self.allowed_workspace_roots):
            raise _ProviderBlocked("WORKSPACE_OUTSIDE_ALLOWED_ROOTS", f"Workspace is outside allowed roots: {workspace}")
        return workspace

    def _apply_file_changes(
        self,
        workspace: Path,
        payload: dict[str, Any],
        allowed_mutation_paths: list[str],
    ) -> list[dict[str, Any]]:
        edits = payload.get("edits")
        files = payload.get("files")

        if edits is not None:
            if files not in (None, []):
                raise _ProviderBlocked(
                    "MALFORMED_FILE_CHANGE",
                    "Ollama response must not mix compact edits with full-file changes.",
                )
            files = self._materialize_compact_edits(
                workspace,
                edits,
                allowed_mutation_paths,
            )

        if not isinstance(files, list) or not files:
            raise _ProviderBlocked("NO_FILE_CHANGES", "Ollama response did not contain file changes.")
        if len(files) > self.max_files:
            raise _ProviderBlocked("TOO_MANY_FILE_CHANGES", "Ollama response exceeded max file changes.")

        authorized = set(allowed_mutation_paths)
        if not authorized:
            raise _ProviderBlocked(
                "MUTATION_SCOPE_VIOLATION",
                "Mutation request has no authorized frozen mutation paths.",
            )

        try:
            validated = validate_file_contents(
                workspace,
                files,
                authorized_paths=authorized,
                max_file_bytes=self.max_file_bytes,
            )
        except GitMutationError as exc:
            failure_class = {
                "INVALID_REPOSITORY_PATH": "UNSAFE_FILE_PATH",
                "DUPLICATE_FILE_CONTENT": "MUTATION_SCOPE_VIOLATION",
                "UNAUTHORIZED_FILE_CONTENT": "MUTATION_SCOPE_VIOLATION",
                "MALFORMED_FILE_CONTENT": "MALFORMED_FILE_CHANGE",
                "FILE_CONTENT_TOO_LARGE": "FILE_CHANGE_TOO_LARGE",
            }.get(exc.code, "DETERMINISTIC_MUTATION_VERIFICATION_FAILED")
            raise _ProviderBlocked(failure_class, exc.message) from exc

        return write_validated_file_contents(validated)

    def _materialize_compact_edits(
        self,
        workspace: Path,
        edits: Any,
        allowed_mutation_paths: list[str],
    ) -> list[dict[str, Any]]:
        if not isinstance(edits, list) or not edits:
            raise _ProviderBlocked(
                "NO_FILE_CHANGES",
                "Ollama response did not contain compact edits.",
            )
        if len(edits) > self.max_files * 8:
            raise _ProviderBlocked(
                "TOO_MANY_FILE_CHANGES",
                "Ollama response exceeded max compact edits.",
            )

        authorized = set(allowed_mutation_paths)
        if not authorized:
            raise _ProviderBlocked(
                "MUTATION_SCOPE_VIOLATION",
                "Mutation request has no authorized frozen mutation paths.",
            )

        root = workspace.resolve()
        working: dict[str, str] = {}
        original: dict[str, str] = {}
        existed: dict[str, bool] = {}
        ordered_paths: list[str] = []

        for edit in edits:
            if not isinstance(edit, dict) or set(edit) != {"path", "old", "new"}:
                raise _ProviderBlocked(
                    "MALFORMED_FILE_CHANGE",
                    "Each compact edit must contain exactly path, old, and new.",
                )

            raw_path = edit.get("path")
            old = edit.get("old")
            new = edit.get("new")
            if not isinstance(raw_path, str) or not isinstance(old, str) or not isinstance(new, str):
                raise _ProviderBlocked(
                    "MALFORMED_FILE_CHANGE",
                    "Compact edit path, old, and new values must be strings.",
                )

            try:
                relative_path = validate_relative_repo_path(raw_path)
            except GitMutationError as exc:
                raise _ProviderBlocked("UNSAFE_FILE_PATH", exc.message) from exc

            if relative_path not in authorized:
                raise _ProviderBlocked(
                    "MUTATION_SCOPE_VIOLATION",
                    f"Compact edit path is outside authorized mutation scope: {relative_path}",
                )

            if relative_path not in working:
                if len(ordered_paths) >= self.max_files:
                    raise _ProviderBlocked(
                        "TOO_MANY_FILE_CHANGES",
                        "Compact edits exceeded max distinct changed files.",
                    )

                relative = Path(*Path(relative_path).parts)
                lexical_target = root / relative

                if _path_contains_symlink(root, relative_path):
                    raise _ProviderBlocked(
                        "MUTATION_CONTEXT_SYMLINK_REJECTED",
                        f"Authorized mutation path contains a symlink: {relative_path}",
                    )

                target = lexical_target.resolve(strict=False)
                if not _is_relative_to(target, root):
                    raise _ProviderBlocked(
                        "UNSAFE_FILE_PATH",
                        f"Path escapes workspace: {relative_path}",
                    )

                if lexical_target.exists():
                    if not lexical_target.is_file():
                        raise _ProviderBlocked(
                            "MUTATION_CONTEXT_FILE_NOT_REGULAR",
                            f"Authorized mutation path is not a regular file: {relative_path}",
                        )
                    data = lexical_target.read_bytes()
                    if len(data) > self.max_context_file_bytes:
                        raise _ProviderBlocked(
                            "MUTATION_CONTEXT_FILE_TOO_LARGE",
                            f"Authorized mutation context file too large: {relative_path}",
                        )
                    try:
                        working[relative_path] = data.decode("utf-8")
                        original[relative_path] = working[relative_path]
                    except UnicodeError as exc:
                        raise _ProviderBlocked(
                            "MUTATION_CONTEXT_FILE_NOT_UTF8",
                            f"Authorized mutation path is not valid UTF-8: {relative_path}",
                        ) from exc
                    existed[relative_path] = True
                else:
                    working[relative_path] = ""
                    original[relative_path] = ""
                    existed[relative_path] = False

                ordered_paths.append(relative_path)

            current = working[relative_path]

            if not existed[relative_path]:
                if old != "":
                    raise _ProviderBlocked(
                        "COMPACT_EDIT_TARGET_MISMATCH",
                        f"New file compact edit requires empty old content: {relative_path}",
                    )
                working[relative_path] = new
                existed[relative_path] = True
                continue

            if old == "":
                raise _ProviderBlocked(
                    "COMPACT_EDIT_TARGET_MISMATCH",
                    f"Existing file compact edit requires non-empty old content: {relative_path}",
                )

            if old == new:
                raise _ProviderBlocked(
                    "NO_EFFECT_COMPACT_EDIT",
                    f"Compact edit would not change file content: {relative_path}",
                )

            occurrences = current.count(old)
            if occurrences != 1:
                raise _ProviderBlocked(
                    "COMPACT_EDIT_TARGET_MISMATCH",
                    f"Compact edit old content must match exactly once in {relative_path}; matched {occurrences} times.",
                )

            working[relative_path] = current.replace(old, new, 1)

        changed_paths = [
            relative_path
            for relative_path in ordered_paths
            if working[relative_path] != original[relative_path]
        ]
        if not changed_paths:
            raise _ProviderBlocked(
                "NO_EFFECT_COMPACT_EDIT",
                "Compact edits produced no effective file changes.",
            )

        return [
            {"path": relative_path, "content": working[relative_path]}
            for relative_path in changed_paths
        ]

    def _mutation_file_context(
        self,
        workspace: Path,
        allowed_mutation_paths: list[str],
    ) -> list[dict[str, Any]]:
        if len(allowed_mutation_paths) > self.max_files:
            raise _ProviderBlocked(
                "MUTATION_CONTEXT_TOO_MANY_FILES",
                f"Mutation context exceeds max authorized files: {len(allowed_mutation_paths)} > {self.max_files}.",
            )
        root = workspace.resolve()
        items: list[dict[str, Any]] = []
        total_bytes = 0
        seen: set[str] = set()
        for raw_path in allowed_mutation_paths:
            try:
                path = validate_relative_repo_path(raw_path)
            except GitMutationError as exc:
                raise _ProviderBlocked("UNSAFE_FILE_PATH", exc.message) from exc
            if path in seen:
                raise _ProviderBlocked("MUTATION_SCOPE_VIOLATION", f"Duplicate mutation context path: {path}")
            seen.add(path)
            relative = Path(*Path(path).parts)
            lexical_target = root / relative
            if _path_contains_symlink(root, path):
                raise _ProviderBlocked(
                    "MUTATION_CONTEXT_SYMLINK_REJECTED",
                    f"Authorized mutation path contains a symlink: {path}",
                )
            target = lexical_target.resolve(strict=False)
            if not _is_relative_to(target, root):
                raise _ProviderBlocked("UNSAFE_FILE_PATH", f"Path escapes workspace: {path}")
            if not lexical_target.exists():
                items.append({"path": path, "state": "NEW_FILE", "content": "", "bytes": 0})
                continue
            if not lexical_target.is_file():
                raise _ProviderBlocked(
                    "MUTATION_CONTEXT_FILE_NOT_REGULAR",
                    f"Authorized mutation path is not a regular file: {path}",
                )
            data = lexical_target.read_bytes()
            if len(data) > self.max_context_file_bytes:
                raise _ProviderBlocked(
                    "MUTATION_CONTEXT_FILE_TOO_LARGE",
                    f"Authorized mutation context file too large: {path}",
                )
            total_bytes += len(data)
            if total_bytes > self.max_context_total_bytes:
                raise _ProviderBlocked(
                    "MUTATION_CONTEXT_TOTAL_TOO_LARGE",
                    "Authorized mutation context total size exceeds configured limit.",
                )
            try:
                content = data.decode("utf-8")
            except UnicodeError as exc:
                raise _ProviderBlocked(
                    "MUTATION_CONTEXT_FILE_NOT_UTF8",
                    f"Authorized mutation path is not valid UTF-8: {path}",
                ) from exc
            items.append({"path": path, "state": "EXISTING_FILE", "content": content, "bytes": len(data)})
        return items


def _verify_deterministic_acceptance(
    workspace: Path,
    checks: list[dict[str, Any]],
    *,
    authorized_paths: set[str],
) -> list[dict[str, Any]]:
    try:
        return verify_deterministic_acceptance(
            workspace,
            checks,
            authorized_paths=authorized_paths,
        )
    except DeterministicAcceptanceError as exc:
        diagnostic = exc.diagnostics or {}
        metadata = {
            "deterministic_acceptance_code": exc.code,
        }
        verification: list[dict[str, Any]] = []
        if diagnostic:
            metadata["deterministic_acceptance_diagnostic"] = diagnostic
            verification.append(diagnostic)
        raise _ProviderBlocked(
            "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
            exc.message,
            provider_error_metadata=metadata,
            verification=verification,
        ) from exc


class _ProviderBlocked(Exception):
    def __init__(
        self,
        failure_class: str,
        message: str,
        retryability: RuntimeRetryability = RuntimeRetryability.NON_RETRYABLE,
        *,
        provider_error_metadata: dict[str, Any] | None = None,
        verification: list[dict[str, Any]] | None = None,
    ):
        super().__init__(message)
        self.failure_class = failure_class
        self.message = message
        self.retryability = retryability
        self.provider_error_metadata = provider_error_metadata or {}
        self.verification = verification or []


def _build_prompt(
    runtime_request: RuntimeExecutionRequest,
    workspace: Path,
    *,
    mutation_context: list[dict[str, Any]] | None = None,
    read_only_context: list[dict[str, Any]] | None = None,
) -> str:
    lines = [
        "You are a bounded local execution provider for Lucius.",
        "Lucius retains scheduling, lifecycle, repository selection, release, and authorization authority.",
        "Work only inside the supplied isolated workspace.",
        "Return strict JSON only, with no markdown.",
        "Schema:",
        '{"edits":[{"path":"relative/path","old":"exact existing fragment","new":"replacement fragment"}]}'
        if not runtime_request.read_only
        else '{"summary":"brief repository-grounded finding","files":[],"evidence_refs":[{"path":"authorized/relative/path","exact_fragment":"exact copied fragment"}]}',
        f"Execution id: {runtime_request.execution_id}",
        f"Task id: {runtime_request.task_id}",
        f"Plan id: {runtime_request.plan_id}",
        f"Plan freeze id: {runtime_request.plan_freeze_id}",
        f"Authority scope metadata: {runtime_request.allowed_mutation_scope}",
        "Authorized mutation paths: "
        + (
            ", ".join(runtime_request.allowed_mutation_paths)
            if runtime_request.allowed_mutation_paths
            else "<none>"
        ),
        "Frozen deterministic acceptance checks: "
        + (
            json.dumps(
                runtime_request.deterministic_acceptance_checks,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if runtime_request.deterministic_acceptance_checks
            else "<none>"
        ),
        "Mandatory literal file acceptance requirements: "
        + (
            "; ".join(
                f"{check['path']} MUST contain exactly: {check['expected_text']}"
                for check in runtime_request.deterministic_acceptance_checks
                if check.get("type") == "file_contains"
                and check.get("path")
                and check.get("expected_text")
            )
            or "<none>"
        ),
        f"Workspace: {workspace}",
        f"Task intent: {runtime_request.task_intent}",
        "Keep the change tiny, deterministic, and automatically verifiable.",
        "This is read-only. Keep summary concise. files must be []. Return 1-3 evidence_refs only."
        if runtime_request.read_only
        else (
            "Return only the minimal JSON object required by the mutation schema. "
            "Do not add summary, verification, documentation, commentary, alternative content, "
            "or extra keys. File contents must satisfy every frozen deterministic acceptance check."
        ),
        "For evidence-sensitive work, label quantitative statements as FACT, DERIVED_VALUE, ASSUMPTION, PROPOSED_PARAMETER, or UNKNOWN.",
        "Do not present proposed protocol values, thresholds, dates, costs, markets, credentials, or performance as facts without supplied evidence.",
    ]
    if runtime_request.read_only:
        lines.extend(
            [
                "Only the supplied repository context is authoritative.",
                "Support the summary with 1-3 evidence_refs using authorized paths and exact non-empty copied fragments.",
                "CRITICAL: Base your summary and evidence_refs EXCLUSIVELY on the text inside the AUTHORIZED READ-ONLY FILE section below. Ignore any topic mismatch in Task intent. exact_fragment MUST be a 10-60 character string copied VERBATIM directly from inside the AUTHORIZED READ-ONLY FILE text.",
                "Do not invent repository facts.",
            ]
        )
        for item in read_only_context or []:
            lines.extend(
                [
                    f"--- BEGIN AUTHORIZED READ-ONLY FILE: {item['path']} ---",
                    item["content"],
                    f"--- END AUTHORIZED READ-ONLY FILE: {item['path']} ---",
                ]
            )

    if not runtime_request.read_only:
        lines.extend(
            [
                "Authorized current file context follows. This is the authoritative current workspace state for the frozen mutation scope only.",
                "Modify only authorized files. Preserve unrelated behavior and content.",
                "Return compact exact-fragment edits only; never return complete files.",
                "For an existing file, old must be a non-empty exact fragment that occurs exactly once in the current authorized file content.",
                "Every compact edit must produce an effective content change; old and new must not be identical.",
                "For a new file only, use old as an empty string and new as the complete initial file content.",
                "Multiple edits to one authorized file are allowed and are applied in response order.",
                "command_succeeds acceptance checks are Lucius verification commands; do not execute, edit, or reinterpret those commands.",
            ]
        )
        for item in mutation_context or []:
            lines.extend(
                [
                    f"--- BEGIN AUTHORIZED FILE: {item['path']} ---",
                    "<NEW FILE>" if item["state"] == "NEW_FILE" else item["content"],
                    f"--- END AUTHORIZED FILE: {item['path']} ---",
                ]
            )
    skeleton = runtime_request.metadata.get(SKELETON_METADATA_KEY)
    if isinstance(skeleton, str) and skeleton.strip():
        lines.extend(
            [
                "Schema-constrained output skeleton follows. Preserve this required structure.",
                skeleton.strip(),
            ]
        )
    return "\n".join(lines)


def _ollama_model_capability_profile(
    *,
    provider_id: str,
    model: str,
    default_timeout_seconds: int,
) -> ModelCapabilityProfile:
    if model == "qwen3:8b":
        return ModelCapabilityProfile(
            provider_id=provider_id,
            model_id=model,
            execution_tier="LOCAL_TIER_1",
            is_local=True,
            supported_task_classes=["engineering", "inspection", "reasoning", "inspection_reasoning"],
            supported_capabilities=["inspection_reasoning", "documentation_update", "code_modification"],
            supports_mutation=True,
            evidence_sensitive_suitable=False,
            schema_constrained_required=False,
            deterministic_verification_required=True,
            supervision_required=False,
            unattended_eligible=True,
            unattended_mutation_eligible=False,
            max_task_complexity="T1",
            default_timeout_seconds=min(default_timeout_seconds, 120),
            max_timeout_seconds=120,
            status=ModelQualificationStatus.QUALIFIED_WITH_CONSTRAINTS,
            policy_notes=[
                "Useful local Tier-1 support model for bounded read-only and non-mutating work.",
                "Not qualified for unattended code mutation after LWORK_000147/LMEXEC_000196/LQCHK_000092 and LWORK_000148/LMEXEC_000197/LQCHK_000093 deterministic mutation failures.",
                "Not qualified for general unattended operation.",
            ],
        )
    if model == "qwen3-coder:30b":
        return ModelCapabilityProfile(
            provider_id=provider_id,
            model_id=model,
            execution_tier="TIER_2_LOCAL_SUPERVISED_SCHEMA_CONSTRAINED",
            is_local=True,
            supported_task_classes=["engineering"],
            supported_capabilities=["inspection_reasoning", "documentation_update", "code_modification"],
            supports_mutation=True,
            evidence_sensitive_suitable=True,
            schema_constrained_required=True,
            deterministic_verification_required=True,
            supervision_required=True,
            unattended_eligible=False,
            unattended_mutation_eligible=False,
            max_task_complexity="T2",
            default_timeout_seconds=default_timeout_seconds,
            max_timeout_seconds=180,
            status=ModelQualificationStatus.SUPERVISED_ONLY,
            policy_notes=[
                "Recent Tier 2 qualification failed on fabricated evidence references.",
                "May be used only under supervision with schema constraints.",
            ],
        )
    return ModelCapabilityProfile(
        provider_id=provider_id,
        model_id=model,
        execution_tier="UNQUALIFIED_LOCAL_MODEL",
        is_local=True,
        supported_task_classes=["engineering"],
        supported_capabilities=["inspection_reasoning"],
        supports_mutation=False,
        evidence_sensitive_suitable=False,
        schema_constrained_required=True,
        deterministic_verification_required=True,
        supervision_required=True,
        unattended_eligible=False,
        unattended_mutation_eligible=False,
        max_task_complexity="T0",
        default_timeout_seconds=default_timeout_seconds,
        max_timeout_seconds=60,
        status=ModelQualificationStatus.NOT_QUALIFIED,
        policy_notes=["No Lucius unattended qualification evidence is recorded for this model."],
    )


def _parse_model_payload(
    body: dict[str, Any],
    *,
    num_predict: int | None = None,
) -> dict[str, Any]:
    raw = body.get("response")
    if not isinstance(raw, str):
        raise _ProviderBlocked("MALFORMED_OLLAMA_RESPONSE", "Ollama response missing text payload.")

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        done = body.get("done")
        done_reason = body.get("done_reason")
        eval_count = _optional_int(body.get("eval_count"))

        likely_truncated = (
            done is False
            or done_reason == "length"
            or (
                num_predict is not None
                and eval_count is not None
                and eval_count >= num_predict
            )
        )

        if likely_truncated:
            raise _ProviderBlocked(
                "OLLAMA_OUTPUT_TRUNCATED",
                (
                    "Ollama output ended before valid JSON completed "
                    f"(eval_count={eval_count}, num_predict={num_predict}, "
                    f"done={done}, done_reason={done_reason!r}, response_chars={len(raw)})."
                ),
                RuntimeRetryability.RETRYABLE_OR_FAILOVERABLE,
            )

        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise _ProviderBlocked(
                "MALFORMED_OLLAMA_JSON",
                (
                    "Ollama response was not valid JSON "
                    f"(eval_count={eval_count}, done={done}, "
                    f"done_reason={done_reason!r}, response_chars={len(raw)})."
                ),
            )

        try:
            payload = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise _ProviderBlocked(
                "MALFORMED_OLLAMA_JSON",
                (
                    "Ollama response contained malformed JSON "
                    f"(eval_count={eval_count}, done={done}, "
                    f"done_reason={done_reason!r}, response_chars={len(raw)})."
                ),
            ) from exc

    if not isinstance(payload, dict):
        raise _ProviderBlocked("MALFORMED_OLLAMA_JSON", "Ollama JSON response must be an object.")
    return payload


def _positive_int(value: int, name: str) -> int:
    if not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _bounded_num_predict(value: Any, *, max_num_predict: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("mutation_num_predict must be a positive integer")
    if value > max_num_predict:
        raise ValueError("mutation_num_predict exceeds hard maximum")
    return value


def _verify_read_only_evidence(
    workspace: Path,
    read_only_context: list[dict[str, Any]],
    evidence_refs: Any,
) -> list[dict[str, Any]]:
    if not isinstance(evidence_refs, list) or not evidence_refs:
        raise _ProviderBlocked(
            "DETERMINISTIC_READ_ONLY_VERIFICATION_FAILED",
            "Read-only execution returned no repository evidence references.",
        )

    authorized = {item["path"]: item["content"] for item in read_only_context}
    verified: list[dict[str, Any]] = []

    for ref in evidence_refs:
        if not isinstance(ref, dict):
            raise _ProviderBlocked(
                "DETERMINISTIC_READ_ONLY_VERIFICATION_FAILED",
                "Repository evidence reference must be an object.",
            )
        path = ref.get("path")
        fragment = ref.get("exact_fragment")
        if not isinstance(path, str) or path not in authorized:
            raise _ProviderBlocked(
                "DETERMINISTIC_READ_ONLY_VERIFICATION_FAILED",
                f"Repository evidence path is not authorized: {path}",
            )
        if not isinstance(fragment, str) or not fragment.strip():
            raise _ProviderBlocked(
                "DETERMINISTIC_READ_ONLY_VERIFICATION_FAILED",
                f"Repository evidence fragment is empty for: {path}",
            )
        fragment_clean = fragment.strip()
        if fragment not in authorized[path] and fragment_clean not in authorized[path]:
            norm_fragment = " ".join(fragment_clean.split())
            norm_authorized = " ".join(authorized[path].split())
            if not norm_fragment or norm_fragment not in norm_authorized:
                raise _ProviderBlocked(
                    "DETERMINISTIC_READ_ONLY_VERIFICATION_FAILED",
                    f"Repository evidence fragment was not found in frozen context: {path}",
                )
        verified.append(
            {
                "result": "PASS",
                "type": "deterministic_repository_evidence",
                "path": path,
                "detail": "Exact model-cited fragment exists in the frozen authorized read-only context.",
            }
        )

    return verified


def _verification(payload: dict[str, Any], written_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    verification = _dict_list(payload.get("verification"))
    if verification:
        return verification
    return [{"result": "PASS", "detail": f"Provider wrote {len(written_files)} file(s) in isolated workspace."}]



def _restore_clean_workspace(workspace: Path) -> None:
    try:
        restore_to_baseline(workspace, _git_head(workspace))
    except GitMutationError as exc:
        raise _ProviderBlocked("DETERMINISTIC_MUTATION_VERIFICATION_FAILED", exc.message) from exc


def _git_changed_paths(workspace: Path) -> list[str]:
    try:
        return changed_paths(workspace)
    except GitMutationError as exc:
        raise _ProviderBlocked("DETERMINISTIC_MUTATION_VERIFICATION_FAILED", exc.message) from exc


def _git_head(workspace: Path) -> str:
    from lucius.repositories.git_mutation import current_head

    return current_head(workspace)

def _failed_result(
    runtime_request: RuntimeExecutionRequest,
    provider: OllamaExecutionProvider,
    started: float,
    *,
    failure_class: str,
    message: str,
    retryability: RuntimeRetryability,
    provider_error_metadata: dict[str, Any] | None = None,
    verification: list[dict[str, Any]] | None = None,
) -> RuntimeExecutionResult:
    metadata = {"message": message}
    if provider_error_metadata:
        metadata.update(provider_error_metadata)
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
        verification=verification or [],
        retryability=retryability,
        failure_class=failure_class,
        provider_error_metadata=metadata,
    )


def _latency_ms(body: dict[str, Any], started: float) -> int:
    total_duration = body.get("total_duration")
    if isinstance(total_duration, int):
        return max(0, int(total_duration / 1_000_000))
    return max(0, int((time.monotonic() - started) * 1000))


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _url_error_is_timeout(exc: error.URLError) -> bool:
    reason = getattr(exc, "reason", None)
    return isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(exc).lower()


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


def _path_contains_symlink(root: Path, relative_path: str) -> bool:
    current = root
    for part in Path(relative_path).parts:
        current = current / part
        if current.is_symlink():
            return True
        if not current.exists():
            return False
    return False
