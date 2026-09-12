from __future__ import annotations

import json
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

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
        explicit_allowed_roots = allowed_workspace_roots is not None
        default_roots = [Path("/private/tmp"), Path(tempfile.gettempdir())]
        self.allowed_workspace_roots = [Path(root).resolve() for root in (allowed_workspace_roots or default_roots)]
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        capability_profile = _ollama_model_capability_profile(
            provider_id=provider_id,
            model=model,
            default_timeout_seconds=timeout_seconds,
        )
        self.registration = RuntimeProviderRegistration(
            provider_id=provider_id,
            provider_version=provider_version,
            capabilities=capability_profile.supported_capabilities,
            supported_task_classes=["engineering"],
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
            prompt = _build_prompt(runtime_request, workspace)
            response = self._post(
                "/api/generate",
                {
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0,
                        "num_predict": 256,
                    },
                },
                timeout_seconds=runtime_request.timeout_seconds or self.timeout_seconds,
            )
            payload = _parse_model_payload(response.body)
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
                        }
                    ],
                    verification=_verification(payload, []),
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
                    },
                )
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
                    )

            return _failed_result(
                runtime_request,
                self,
                started,
                failure_class=blocked.failure_class,
                message=blocked.message,
                retryability=blocked.retryability,
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
        files = payload.get("files")
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

        validated = []
        seen = set()

        for item in files:
            if not isinstance(item, dict):
                raise _ProviderBlocked("MALFORMED_FILE_CHANGE", "File change must be an object.")

            raw_path = item.get("path")
            content = item.get("content")

            if not isinstance(raw_path, str) or not raw_path.strip():
                raise _ProviderBlocked("MALFORMED_FILE_CHANGE", "File change path is required.")

            raw_path = raw_path.strip()

            if raw_path.startswith("/") or "\\" in raw_path or ".." in Path(raw_path).parts:
                raise _ProviderBlocked("UNSAFE_FILE_PATH", f"Unsafe file path: {raw_path}")

            target = (workspace / raw_path).resolve()
            if not _is_relative_to(target, workspace):
                raise _ProviderBlocked("UNSAFE_FILE_PATH", f"Unsafe file path: {raw_path}")

            if raw_path in seen:
                raise _ProviderBlocked(
                    "MUTATION_SCOPE_VIOLATION",
                    f"Duplicate proposed mutation path: {raw_path}",
                )
            seen.add(raw_path)

            if raw_path not in authorized:
                raise _ProviderBlocked(
                    "MUTATION_SCOPE_VIOLATION",
                    f"Unauthorized mutation path: {raw_path}",
                )

            if not isinstance(content, str):
                raise _ProviderBlocked("MALFORMED_FILE_CHANGE", "File change content must be a string.")

            encoded = content.encode("utf-8")
            if len(encoded) > self.max_file_bytes:
                raise _ProviderBlocked("FILE_CHANGE_TOO_LARGE", f"File change too large: {raw_path}")

            validated.append((raw_path, target, content, len(encoded)))

        written = []
        for raw_path, target, content, byte_count in validated:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append({"path": raw_path, "bytes": byte_count})

        return written


def _verify_deterministic_acceptance(
    workspace: Path,
    checks: list[dict[str, Any]],
    *,
    authorized_paths: set[str],
) -> list[dict[str, Any]]:
    verification: list[dict[str, Any]] = []

    for index, check in enumerate(checks, start=1):
        if not isinstance(check, dict):
            raise _ProviderBlocked(
                "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
                f"Deterministic acceptance check {index} is malformed.",
            )

        check_type = check.get("type")
        raw_path = check.get("path")
        expected_text = check.get("expected_text")

        if check_type != "exact_file_content":
            raise _ProviderBlocked(
                "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
                f"Unsupported deterministic acceptance check type at index {index}.",
            )

        if not isinstance(raw_path, str) or not raw_path.strip():
            raise _ProviderBlocked(
                "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
                f"Deterministic acceptance check {index} has no valid path.",
            )

        path = raw_path.strip()

        if (
            path.startswith("/")
            or "\\" in path
            or "." in Path(path).parts
            or ".." in Path(path).parts
            or path not in authorized_paths
        ):
            raise _ProviderBlocked(
                "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
                f"Deterministic acceptance path is outside frozen authorization: {path}",
            )

        if not isinstance(expected_text, str):
            raise _ProviderBlocked(
                "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
                f"Deterministic acceptance expected_text is invalid for: {path}",
            )

        target = (workspace / path).resolve()
        if not _is_relative_to(target, workspace):
            raise _ProviderBlocked(
                "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
                f"Deterministic acceptance path escapes workspace: {path}",
            )

        if not target.is_file():
            raise _ProviderBlocked(
                "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
                f"Deterministic acceptance target does not exist: {path}",
            )

        try:
            actual_text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise _ProviderBlocked(
                "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
                f"Could not read deterministic acceptance target {path}: {exc}",
            ) from exc

        if actual_text != expected_text:
            raise _ProviderBlocked(
                "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
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
    lines = [
        "You are a bounded local execution provider for Lucius.",
        "Lucius retains scheduling, lifecycle, repository selection, release, and authorization authority.",
        "Work only inside the supplied isolated workspace.",
        "Return strict JSON only, with no markdown.",
        "Schema:",
        '{"files":[{"path":"relative/path","content":"exact file contents"}]}'
        if not runtime_request.read_only
        else '{"summary":"...","completed_substeps":["..."],"files":[],"verification":[{"result":"PASS","detail":"..."}],"documentation":[]}',
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
        f"Workspace: {workspace}",
        f"Task intent: {runtime_request.task_intent}",
        "Keep the change tiny, deterministic, and automatically verifiable.",
        "This is a read-only request. Do not return file changes; files must be an empty array."
        if runtime_request.read_only
        else (
            "Return only the minimal JSON object required by the mutation schema. "
            "Do not add summary, verification, documentation, commentary, alternative content, "
            "or extra keys. File contents must satisfy the frozen deterministic acceptance checks exactly."
        ),
        "For evidence-sensitive work, label quantitative statements as FACT, DERIVED_VALUE, ASSUMPTION, PROPOSED_PARAMETER, or UNKNOWN.",
        "Do not present proposed protocol values, thresholds, dates, costs, markets, credentials, or performance as facts without supplied evidence.",
    ]
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
            supported_task_classes=["engineering"],
            supported_capabilities=["inspection_reasoning", "documentation_update", "code_modification"],
            supports_mutation=True,
            evidence_sensitive_suitable=False,
            schema_constrained_required=False,
            deterministic_verification_required=True,
            supervision_required=False,
            unattended_eligible=True,
            max_task_complexity="T1",
            default_timeout_seconds=min(default_timeout_seconds, 120),
            max_timeout_seconds=120,
            status=ModelQualificationStatus.QUALIFIED_WITH_CONSTRAINTS,
            policy_notes=[
                "Qualified only for small, bounded, highly verifiable local work.",
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
        max_task_complexity="T0",
        default_timeout_seconds=default_timeout_seconds,
        max_timeout_seconds=60,
        status=ModelQualificationStatus.NOT_QUALIFIED,
        policy_notes=["No Lucius unattended qualification evidence is recorded for this model."],
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



def _restore_clean_workspace(workspace: Path) -> None:
    commands = [
        ["git", "reset", "--hard", "HEAD"],
        ["git", "clean", "-fd"],
    ]

    for command in commands:
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            check=False,
        )

        if completed.returncode != 0:
            message = completed.stderr.decode(
                "utf-8",
                errors="replace",
            ).strip()
            raise _ProviderBlocked(
                "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
                "Unable to restore isolated workspace"
                + (f": {message}" if message else "."),
            )

    remaining_changes = _git_changed_paths(workspace)
    if remaining_changes:
        raise _ProviderBlocked(
            "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
            "Isolated workspace remained dirty after rollback: "
            + ", ".join(remaining_changes),
        )


def _git_changed_paths(workspace: Path) -> list[str]:
    commands = [
        ["git", "diff", "--name-only", "-z"],
        ["git", "diff", "--cached", "--name-only", "-z"],
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
    ]

    changed: set[str] = set()

    for command in commands:
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            check=False,
        )

        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", errors="replace").strip()
            raise _ProviderBlocked(
                "DETERMINISTIC_MUTATION_VERIFICATION_FAILED",
                "Unable to inspect Git mutation state"
                + (f": {message}" if message else "."),
            )

        for raw_path in completed.stdout.split(b"\0"):
            if not raw_path:
                continue
            changed.add(raw_path.decode("utf-8", errors="surrogateescape"))

    return sorted(changed)

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
