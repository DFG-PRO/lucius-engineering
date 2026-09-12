from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, AllowedAction, AuthorityLevel, PersistentWorkflowState, RepositoryAccessMode
from lucius.persistence.orm import (
    ControlledCommitORM,
    EngineeringPlanORM,
    PersistentWorkflowORM,
    PlanFreezeORM,
    ProjectRepositoryAttachmentORM,
    RepositoryRegistrationORM,
    TaskContractORM,
    TaskORM,
    utc_now,
)
from lucius.persistence.repositories import next_id
from lucius.repositories.git_mutation import (
    GitMutationError,
    changed_paths,
    commit_changed_paths,
    commit_parent_shas,
    committed_file_sha256,
    create_local_commit,
    current_head,
    stage_paths,
    staged_file_sha256,
    staged_paths,
    unstage_paths,
    validate_relative_repo_path,
    workspace_file_sha256,
)
from lucius.runtime.deterministic_acceptance import DeterministicAcceptanceError, verify_deterministic_acceptance


@dataclass(frozen=True)
class ControlledCommitRequest:
    workflow_id: str
    canonical_repository_path: str | Path
    expected_baseline_commit: str
    authority_level: AuthorityLevel = AuthorityLevel.L2
    actor: Actor = Actor.LUCIUS
    commit_message: str | None = None


@dataclass(frozen=True)
class ControlledCommitResult:
    status: str
    workflow_id: str
    reason: str | None = None
    baseline_commit: str | None = None
    resulting_commit: str | None = None
    authorized_paths: list[str] = field(default_factory=list)
    committed_paths: list[str] = field(default_factory=list)
    manifest_hash: str | None = None
    record_id: str | None = None
    manual_reconciliation_required: bool = False
    unstaged_paths: list[str] = field(default_factory=list)


class ControlledCommitService:
    """Creates one verified local Git commit from a closed, verified integration."""

    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def commit(self, request: ControlledCommitRequest) -> ControlledCommitResult:
        started = time.monotonic()
        self._audit(
            "CONTROLLED_COMMIT_REQUESTED",
            request,
            "REQUESTED",
            {"workflow_id": request.workflow_id, "authority_level": request.authority_level.value},
        )
        try:
            context = self._preflight(request)
        except _AlreadyCommitted as done:
            self._audit(
                "CONTROLLED_COMMIT_ALREADY_EXISTS",
                request,
                "ALREADY_COMMITTED",
                {"record_id": done.record.id, "resulting_commit": done.record.resulting_commit_sha},
            )
            return ControlledCommitResult(
                status="ALREADY_COMMITTED",
                workflow_id=request.workflow_id,
                baseline_commit=done.record.baseline_commit_sha,
                resulting_commit=done.record.resulting_commit_sha,
                committed_paths=list(done.record.actual_changed_paths),
                manifest_hash=done.record.manifest_hash,
                record_id=done.record.id,
            )
        except _CommitBlocked as blocked:
            self._audit("CONTROLLED_COMMIT_PREFLIGHT_FAILED", request, "FAILED_CLOSED", {"reason": blocked.reason, **blocked.metadata})
            return ControlledCommitResult(status="FAILED", workflow_id=request.workflow_id, reason=blocked.reason)
        except (GitMutationError, DeterministicAcceptanceError) as error:
            reason = getattr(error, "code", error.__class__.__name__)
            message = getattr(error, "message", str(error))
            self._audit("CONTROLLED_COMMIT_PREFLIGHT_FAILED", request, "FAILED_CLOSED", {"reason": reason, "message": message})
            return ControlledCommitResult(status="FAILED", workflow_id=request.workflow_id, reason=message)

        staged_by_operation: list[str] = []
        try:
            self._verify_pre_commit_state(context)
            staged_by_operation = list(context.manifest["actual_changed_paths"])
            stage_paths(context.canonical_root, staged_by_operation)
            self._verify_staged_manifest(context)
        except (_CommitBlocked, GitMutationError, DeterministicAcceptanceError) as error:
            unstaged = self._cleanup_staging(context.canonical_root, staged_by_operation)
            reason = error.reason if isinstance(error, _CommitBlocked) else getattr(error, "message", str(error))
            self._audit(
                "CONTROLLED_COMMIT_PRE_COMMIT_FAILED",
                request,
                "FAILED_CLOSED",
                {
                    "reason": getattr(error, "reason", getattr(error, "code", error.__class__.__name__)),
                    "message": reason,
                    "unstaged_paths": unstaged,
                },
            )
            return ControlledCommitResult(
                status="FAILED",
                workflow_id=request.workflow_id,
                reason=reason,
                baseline_commit=context.baseline_commit,
                authorized_paths=context.authorized_paths,
                manifest_hash=context.manifest_hash,
                unstaged_paths=unstaged,
            )

        try:
            resulting_commit = create_local_commit(context.canonical_root, context.commit_message)
        except GitMutationError as error:
            unstaged = self._cleanup_staging(context.canonical_root, staged_by_operation)
            self._audit(
                "CONTROLLED_COMMIT_CREATION_FAILED",
                request,
                "FAILED_CLOSED",
                {"reason": error.code, "message": error.message, "unstaged_paths": unstaged},
            )
            return ControlledCommitResult(
                status="FAILED",
                workflow_id=request.workflow_id,
                reason=error.message,
                baseline_commit=context.baseline_commit,
                authorized_paths=context.authorized_paths,
                manifest_hash=context.manifest_hash,
                unstaged_paths=unstaged,
            )

        try:
            committed_paths = self._verify_post_commit(context, resulting_commit)
            record = ControlledCommitORM(
                id=next_id(self.session, "controlled_commit"),
                workflow_id=context.workflow.id,
                project_id=context.workflow.project_id,
                repository_id=context.repository.id,
                task_id=context.task.id,
                plan_freeze_id=context.freeze.id,
                baseline_commit_sha=context.baseline_commit,
                parent_commit_sha=context.baseline_commit,
                resulting_commit_sha=resulting_commit,
                authorized_paths=context.authorized_paths,
                actual_changed_paths=committed_paths,
                path_content_hashes=context.manifest["path_content_hashes"],
                manifest_hash=context.manifest_hash,
                commit_message=context.commit_message,
                deterministic_acceptance_evidence=context.deterministic_acceptance,
                authority_level=AuthorityLevel.L2.value,
                actor=request.actor.value,
                status="COMPLETED",
                result="SUCCESS",
                created_at=utc_now(),
            )
            self.session.add(record)
            self.session.flush()
            self._audit(
                "CONTROLLED_COMMIT_COMPLETED",
                request,
                "SUCCESS",
                {
                    "record_id": record.id,
                    "baseline_commit": context.baseline_commit,
                    "resulting_commit": resulting_commit,
                    "committed_paths": committed_paths,
                    "manifest_hash": context.manifest_hash,
                    "duration_seconds": max(0.0, time.monotonic() - started),
                },
            )
            return ControlledCommitResult(
                status="COMPLETED",
                workflow_id=request.workflow_id,
                baseline_commit=context.baseline_commit,
                resulting_commit=resulting_commit,
                authorized_paths=context.authorized_paths,
                committed_paths=committed_paths,
                manifest_hash=context.manifest_hash,
                record_id=record.id,
            )
        except Exception as error:
            self._audit(
                "CONTROLLED_COMMIT_POST_COMMIT_VERIFICATION_FAILED",
                request,
                "MANUAL_RECONCILIATION_REQUIRED",
                {
                    "baseline_commit": context.baseline_commit,
                    "observed_head": _safe_current_head(context.canonical_root),
                    "intended_commit": resulting_commit,
                    "reason": getattr(error, "reason", getattr(error, "code", error.__class__.__name__)),
                    "message": getattr(error, "message", str(error)),
                },
            )
            return ControlledCommitResult(
                status="POST_COMMIT_VERIFICATION_FAILED",
                workflow_id=request.workflow_id,
                reason=getattr(error, "message", str(error)),
                baseline_commit=context.baseline_commit,
                resulting_commit=resulting_commit,
                authorized_paths=context.authorized_paths,
                manifest_hash=context.manifest_hash,
                manual_reconciliation_required=True,
            )

    def _preflight(self, request: ControlledCommitRequest) -> "_CommitContext":
        if request.authority_level != AuthorityLevel.L2:
            raise _CommitBlocked("L2_AUTHORITY_REQUIRED", {"authority_level": request.authority_level.value})

        workflow = self.session.get(PersistentWorkflowORM, request.workflow_id)
        if workflow is None:
            raise _CommitBlocked("WORKFLOW_NOT_FOUND", {})
        if workflow.workflow_state != PersistentWorkflowState.CLOSED.value:
            raise _CommitBlocked("WORKFLOW_NOT_CLOSED", {"workflow_state": workflow.workflow_state})

        task = self.session.get(TaskORM, workflow.task_id) if workflow.task_id else None
        if task is None or task.project_id != workflow.project_id:
            raise _CommitBlocked("WORKFLOW_TASK_RELATIONSHIP_INVALID", {})
        plan = self.session.get(EngineeringPlanORM, workflow.plan_id) if workflow.plan_id else None
        freeze = self.session.get(PlanFreezeORM, workflow.plan_freeze_id) if workflow.plan_freeze_id else None
        if plan is None or freeze is None:
            raise _CommitBlocked("FROZEN_PLAN_REQUIRED", {})
        if plan.task_id != task.id or freeze.task_id != task.id or freeze.plan_id != plan.id:
            raise _CommitBlocked("FROZEN_PLAN_RELATIONSHIP_INVALID", {})
        if plan.project_id != workflow.project_id or freeze.project_id != workflow.project_id:
            raise _CommitBlocked("FROZEN_PLAN_PROJECT_MISMATCH", {})

        contract = self.session.get(TaskContractORM, plan.task_contract_id)
        if contract is None or contract.task_id != task.id or contract.version != plan.task_contract_version:
            raise _CommitBlocked("TASK_CONTRACT_REQUIRED", {})
        self._verify_commit_authority(task, contract)

        repository = self.session.get(RepositoryRegistrationORM, workflow.repository_id) if workflow.repository_id else None
        if repository is None:
            raise _CommitBlocked("REPOSITORY_REQUIRED", {})
        if repository.access_mode != RepositoryAccessMode.CANONICAL_INTEGRATION.value:
            raise _CommitBlocked("REPOSITORY_ACCESS_MODE_NOT_CANONICAL_INTEGRATION", {"access_mode": repository.access_mode})
        attachment = self.session.scalar(
            select(ProjectRepositoryAttachmentORM).where(
                ProjectRepositoryAttachmentORM.project_id == workflow.project_id,
                ProjectRepositoryAttachmentORM.repository_id == repository.id,
            )
        )
        if attachment is None:
            raise _CommitBlocked("REPOSITORY_NOT_ATTACHED_TO_PROJECT", {})

        canonical_root = Path(request.canonical_repository_path).resolve()
        registered_root = Path(repository.location).resolve()
        if canonical_root != registered_root:
            raise _CommitBlocked("CANONICAL_REPOSITORY_PATH_MISMATCH", {"requested": str(canonical_root), "registered": str(registered_root)})

        if not freeze.commit_sha:
            raise _CommitBlocked("FROZEN_BASELINE_COMMIT_REQUIRED", {})
        if freeze.commit_sha != request.expected_baseline_commit:
            raise _CommitBlocked("REQUEST_BASELINE_DOES_NOT_MATCH_FREEZE", {"freeze_commit": freeze.commit_sha})

        existing = self.session.scalar(
            select(ControlledCommitORM).where(
                ControlledCommitORM.workflow_id == workflow.id,
                ControlledCommitORM.plan_freeze_id == freeze.id,
                ControlledCommitORM.status == "COMPLETED",
            )
        )
        if existing is not None:
            raise _AlreadyCommitted(existing)

        authorized_paths = _authorized_paths(freeze)
        acceptance_checks = _acceptance_checks(freeze, authorized_paths)
        canonical_head = current_head(canonical_root)
        if canonical_head != request.expected_baseline_commit:
            if _looks_like_missing_commit_record(canonical_root, canonical_head, request.expected_baseline_commit, set(authorized_paths)):
                raise _CommitBlocked("CONTROLLED_COMMIT_RECONCILIATION_REQUIRED", {"canonical_head": canonical_head})
            raise _CommitBlocked("CANONICAL_HEAD_DRIFT", {"canonical_head": canonical_head, "expected": request.expected_baseline_commit})

        preexisting_staged = staged_paths(canonical_root)
        if preexisting_staged:
            raise _CommitBlocked("PREEXISTING_STAGED_CHANGES", {"staged_paths": preexisting_staged})

        actual_changed = changed_paths(canonical_root)
        if not actual_changed:
            raise _CommitBlocked("CANONICAL_HAS_NO_CHANGES", {})
        unexpected = sorted(set(actual_changed) - set(authorized_paths))
        if unexpected:
            raise _CommitBlocked("CANONICAL_HAS_UNAUTHORIZED_CHANGES", {"unexpected_paths": unexpected})

        deterministic_acceptance = verify_deterministic_acceptance(
            canonical_root,
            acceptance_checks,
            authorized_paths=set(authorized_paths),
        )
        exact_content_paths = [check["path"] for check in acceptance_checks if check.get("type") == "exact_file_content"]
        missing_checks = sorted(set(actual_changed) - set(exact_content_paths))
        if missing_checks:
            raise _CommitBlocked("CANONICAL_CHANGE_MISSING_EXACT_CONTENT_CHECK", {"paths": missing_checks})

        commit_message = _commit_message(request.commit_message, task.id)
        path_hashes = {path: workspace_file_sha256(canonical_root, path) for path in actual_changed}
        manifest = {
            "workflow_id": workflow.id,
            "plan_freeze_id": freeze.id,
            "baseline_commit_sha": request.expected_baseline_commit,
            "authorized_paths": authorized_paths,
            "actual_changed_paths": actual_changed,
            "path_content_hashes": path_hashes,
            "deterministic_acceptance": deterministic_acceptance,
            "commit_message": commit_message,
        }
        return _CommitContext(
            workflow=workflow,
            task=task,
            plan=plan,
            freeze=freeze,
            repository=repository,
            canonical_root=canonical_root,
            baseline_commit=request.expected_baseline_commit,
            authorized_paths=authorized_paths,
            acceptance_checks=acceptance_checks,
            deterministic_acceptance=deterministic_acceptance,
            commit_message=commit_message,
            manifest=manifest,
            manifest_hash=_manifest_hash(manifest),
        )

    def _verify_commit_authority(self, task: TaskORM, contract: TaskContractORM) -> None:
        if AllowedAction.CREATE_COMMIT.value not in {str(action) for action in contract.allowed_actions}:
            raise _CommitBlocked("CREATE_COMMIT_NOT_AUTHORIZED", {})
        if _authority_rank(task.authority_level) < _authority_rank(AuthorityLevel.L2.value):
            raise _CommitBlocked("TASK_L2_AUTHORITY_REQUIRED", {"authority_level": task.authority_level})
        if _authority_rank(contract.authority_level) < _authority_rank(AuthorityLevel.L2.value):
            raise _CommitBlocked("CONTRACT_L2_AUTHORITY_REQUIRED", {"authority_level": contract.authority_level})

    def _verify_pre_commit_state(self, context: "_CommitContext") -> None:
        if current_head(context.canonical_root) != context.baseline_commit:
            raise _CommitBlocked("CANONICAL_HEAD_DRIFT", {"expected": context.baseline_commit})
        if staged_paths(context.canonical_root):
            raise _CommitBlocked("PREEXISTING_STAGED_CHANGES", {})
        if changed_paths(context.canonical_root) != context.manifest["actual_changed_paths"]:
            raise _CommitBlocked("CANONICAL_CHANGED_PATHS_DRIFT", {})
        deterministic = verify_deterministic_acceptance(
            context.canonical_root,
            context.acceptance_checks,
            authorized_paths=set(context.authorized_paths),
        )
        if deterministic != context.deterministic_acceptance:
            raise _CommitBlocked("DETERMINISTIC_ACCEPTANCE_DRIFT", {})
        for path, expected_hash in context.manifest["path_content_hashes"].items():
            if workspace_file_sha256(context.canonical_root, path) != expected_hash:
                raise _CommitBlocked("CANONICAL_CONTENT_HASH_DRIFT", {"path": path})

    def _verify_staged_manifest(self, context: "_CommitContext") -> None:
        intended = context.manifest["actual_changed_paths"]
        observed = staged_paths(context.canonical_root)
        if observed != intended:
            raise _CommitBlocked("STAGED_PATHS_DO_NOT_MATCH_MANIFEST", {"staged_paths": observed, "intended_paths": intended})
        for path, expected_hash in context.manifest["path_content_hashes"].items():
            if staged_file_sha256(context.canonical_root, path) != expected_hash:
                raise _CommitBlocked("STAGED_CONTENT_HASH_MISMATCH", {"path": path})

    def _verify_post_commit(self, context: "_CommitContext", resulting_commit: str) -> list[str]:
        if resulting_commit == context.baseline_commit:
            raise _CommitBlocked("COMMIT_DID_NOT_ADVANCE_HEAD", {})
        if current_head(context.canonical_root) != resulting_commit:
            raise _CommitBlocked("CANONICAL_HEAD_NOT_RESULTING_COMMIT", {"head": current_head(context.canonical_root)})
        parents = commit_parent_shas(context.canonical_root, resulting_commit)
        if parents != [context.baseline_commit]:
            raise _CommitBlocked("CONTROLLED_COMMIT_PARENT_MISMATCH", {"parents": parents})
        committed_paths = commit_changed_paths(context.canonical_root, resulting_commit)
        if committed_paths != context.manifest["actual_changed_paths"]:
            raise _CommitBlocked("COMMITTED_PATHS_DO_NOT_MATCH_MANIFEST", {"committed_paths": committed_paths})
        unexpected = sorted(set(committed_paths) - set(context.authorized_paths))
        if unexpected:
            raise _CommitBlocked("COMMITTED_UNAUTHORIZED_PATHS", {"unexpected_paths": unexpected})
        for path, expected_hash in context.manifest["path_content_hashes"].items():
            if committed_file_sha256(context.canonical_root, resulting_commit, path) != expected_hash:
                raise _CommitBlocked("COMMITTED_CONTENT_HASH_MISMATCH", {"path": path})
        deterministic = verify_deterministic_acceptance(
            context.canonical_root,
            context.acceptance_checks,
            authorized_paths=set(context.authorized_paths),
        )
        if deterministic != context.deterministic_acceptance:
            raise _CommitBlocked("POST_COMMIT_DETERMINISTIC_ACCEPTANCE_DRIFT", {})
        remaining = changed_paths(context.canonical_root)
        if remaining:
            raise _CommitBlocked("CANONICAL_WORKTREE_NOT_CLEAN_AFTER_COMMIT", {"changed_paths": remaining})
        if current_head(context.canonical_root) != resulting_commit:
            raise _CommitBlocked("CANONICAL_HEAD_CHANGED_AFTER_VERIFICATION", {"head": current_head(context.canonical_root)})
        return committed_paths

    def _cleanup_staging(self, root: Path, paths: list[str]) -> list[str]:
        if not paths:
            return []
        try:
            unstage_paths(root, paths)
            return paths
        except GitMutationError:
            return []

    def _audit(self, event_type: str, request: ControlledCommitRequest, result: str, metadata: dict[str, Any]) -> None:
        workflow = self.session.get(PersistentWorkflowORM, request.workflow_id)
        self.audit.record(
            event_type=event_type,
            actor=request.actor.value,
            project_id=workflow.project_id if workflow else None,
            repository_id=workflow.repository_id if workflow else None,
            task_id=workflow.task_id if workflow else None,
            authority_level=AuthorityLevel.L2,
            action="controlled_local_commit",
            result=result,
            metadata=metadata,
        )


@dataclass(frozen=True)
class _CommitContext:
    workflow: PersistentWorkflowORM
    task: TaskORM
    plan: EngineeringPlanORM
    freeze: PlanFreezeORM
    repository: RepositoryRegistrationORM
    canonical_root: Path
    baseline_commit: str
    authorized_paths: list[str]
    acceptance_checks: list[dict[str, Any]]
    deterministic_acceptance: list[dict[str, Any]]
    commit_message: str
    manifest: dict[str, Any]
    manifest_hash: str


class _CommitBlocked(RuntimeError):
    def __init__(self, reason: str, metadata: dict[str, Any]):
        super().__init__(reason)
        self.reason = reason
        self.metadata = metadata


class _AlreadyCommitted(RuntimeError):
    def __init__(self, record: ControlledCommitORM):
        super().__init__("ALREADY_COMMITTED")
        self.record = record


def _authorized_paths(freeze: PlanFreezeORM) -> list[str]:
    payload = freeze.plan_payload if isinstance(freeze.plan_payload, dict) else {}
    raw = payload.get("affected_files")
    if not isinstance(raw, list):
        raise _CommitBlocked("INVALID_FROZEN_AFFECTED_FILES", {})
    paths: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise _CommitBlocked("INVALID_FROZEN_AFFECTED_FILES", {})
        try:
            path = validate_relative_repo_path(item.get("path"))
        except GitMutationError as exc:
            raise _CommitBlocked("INVALID_FROZEN_AFFECTED_FILES", {"message": exc.message}) from exc
        if path not in seen:
            seen.add(path)
            paths.append(path)
    if not paths:
        raise _CommitBlocked("EMPTY_FROZEN_AFFECTED_FILES", {})
    return paths


def _acceptance_checks(freeze: PlanFreezeORM, authorized_paths: list[str]) -> list[dict[str, Any]]:
    payload = freeze.plan_payload if isinstance(freeze.plan_payload, dict) else {}
    raw = payload.get("deterministic_acceptance_checks")
    if not isinstance(raw, list) or not raw:
        raise _CommitBlocked("FROZEN_DETERMINISTIC_ACCEPTANCE_REQUIRED", {})
    authorized = set(authorized_paths)
    checks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise _CommitBlocked("INVALID_FROZEN_DETERMINISTIC_ACCEPTANCE_CHECK", {})
        if item.get("type") != "exact_file_content":
            raise _CommitBlocked("UNSUPPORTED_FROZEN_DETERMINISTIC_ACCEPTANCE_CHECK", {"type": item.get("type")})
        try:
            path = validate_relative_repo_path(item.get("path"))
        except GitMutationError as exc:
            raise _CommitBlocked("INVALID_FROZEN_DETERMINISTIC_ACCEPTANCE_PATH", {"message": exc.message}) from exc
        if path not in authorized:
            raise _CommitBlocked("DETERMINISTIC_ACCEPTANCE_PATH_OUTSIDE_AFFECTED_FILES", {"path": path})
        if path in seen:
            raise _CommitBlocked("DUPLICATE_FROZEN_DETERMINISTIC_ACCEPTANCE_PATH", {"path": path})
        expected_text = item.get("expected_text")
        if not isinstance(expected_text, str):
            raise _CommitBlocked("INVALID_FROZEN_DETERMINISTIC_ACCEPTANCE_EXPECTED_TEXT", {"path": path})
        seen.add(path)
        checks.append({"type": "exact_file_content", "path": path, "expected_text": expected_text})
    return checks


def _commit_message(message: str | None, task_id: str) -> str:
    value = message if message is not None else f"lucius: complete {task_id}"
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise _CommitBlocked("INVALID_COMMIT_MESSAGE", {})
    if len(value) > 120 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise _CommitBlocked("INVALID_COMMIT_MESSAGE", {})
    return value


def _manifest_hash(manifest: dict[str, Any]) -> str:
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _authority_rank(value: str) -> int:
    return {AuthorityLevel.L0.value: 0, AuthorityLevel.L1.value: 1, AuthorityLevel.L2.value: 2, AuthorityLevel.L3.value: 3}.get(value, -1)


def _looks_like_missing_commit_record(root: Path, head: str, baseline: str, authorized_paths: set[str]) -> bool:
    try:
        parents = commit_parent_shas(root, head)
        paths = commit_changed_paths(root, head)
    except GitMutationError:
        return False
    return parents == [baseline] and bool(paths) and not (set(paths) - authorized_paths)


def _safe_current_head(root: Path) -> str | None:
    try:
        return current_head(root)
    except GitMutationError:
        return None
