from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, AuthorityLevel, PersistentWorkflowState, RepositoryAccessMode
from lucius.persistence.orm import (
    EngineeringPlanORM,
    PersistentWorkflowORM,
    PlanFreezeORM,
    ProjectRepositoryAttachmentORM,
    RepositoryRegistrationORM,
    TaskORM,
    utc_now,
)
from lucius.repositories.git_mutation import (
    GitMutationError,
    assert_clean,
    capture_utf8_file_contents,
    changed_paths,
    current_head,
    restore_to_baseline,
    validate_file_contents,
    validate_relative_repo_path,
    write_validated_file_contents,
)
from lucius.runtime.deterministic_acceptance import DeterministicAcceptanceError, verify_deterministic_acceptance


@dataclass(frozen=True)
class CanonicalIntegrationRequest:
    workflow_id: str
    canonical_repository_path: str | Path
    candidate_workspace_path: str | Path
    expected_baseline_commit: str
    access_mode: RepositoryAccessMode = RepositoryAccessMode.CANONICAL_INTEGRATION
    authority_level: AuthorityLevel = AuthorityLevel.L1
    actor: Actor = Actor.LUCIUS


@dataclass(frozen=True)
class CanonicalIntegrationResult:
    status: str
    workflow_id: str
    reason: str | None = None
    baseline_commit: str | None = None
    canonical_head: str | None = None
    authorized_paths: list[str] = field(default_factory=list)
    candidate_changed_paths: list[str] = field(default_factory=list)
    applied_paths: list[str] = field(default_factory=list)
    actual_changed_paths: list[str] = field(default_factory=list)
    deterministic_acceptance: list[dict[str, Any]] = field(default_factory=list)
    rollback_attempted: bool = False
    rollback_succeeded: bool = False


class CanonicalIntegrationService:
    """Applies frozen, deterministic runtime output to a canonical working tree."""

    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def integrate(self, request: CanonicalIntegrationRequest) -> CanonicalIntegrationResult:
        started = time.monotonic()
        self._audit(
            "CANONICAL_INTEGRATION_REQUESTED",
            request,
            "REQUESTED",
            {"workflow_id": request.workflow_id, "access_mode": request.access_mode.value},
        )
        try:
            context = self._preflight(request)
        except (_IntegrationBlocked, GitMutationError, DeterministicAcceptanceError) as blocked:
            reason = blocked.reason if isinstance(blocked, _IntegrationBlocked) else blocked.code
            message = blocked.reason if isinstance(blocked, _IntegrationBlocked) else blocked.message
            metadata = blocked.metadata if isinstance(blocked, _IntegrationBlocked) else {"message": blocked.message}
            self._audit(
                "CANONICAL_INTEGRATION_PREFLIGHT_FAILED",
                request,
                "FAILED_CLOSED",
                {"reason": reason, **metadata},
            )
            return CanonicalIntegrationResult(status="FAILED", workflow_id=request.workflow_id, reason=message)

        mutation_started = False
        try:
            validated_files = validate_file_contents(
                context.canonical_root,
                context.captured_file_contents,
                authorized_paths=set(context.authorized_paths),
            )
            intended_paths = sorted(item.path for item in validated_files)
            if not intended_paths:
                raise _IntegrationBlocked("NO_INTEGRATION_CONTENT", {})

            self._audit(
                "CANONICAL_INTEGRATION_PREFLIGHT_PASSED",
                request,
                "SUCCESS",
                {
                    "baseline_commit": context.baseline_commit,
                    "canonical_head": context.canonical_head,
                    "authorized_paths": context.authorized_paths,
                    "candidate_changed_paths": context.candidate_changed_paths,
                    "intended_paths": intended_paths,
                    "candidate_head": context.candidate_head,
                    "candidate_acceptance": context.candidate_acceptance,
                },
            )

            mutation_started = True
            written = write_validated_file_contents(validated_files)
            actual_changed = changed_paths(context.canonical_root)
            self._verify_post_apply(context, intended_paths, actual_changed)
            deterministic = verify_deterministic_acceptance(
                context.canonical_root,
                context.acceptance_checks,
                authorized_paths=set(context.authorized_paths),
            )

            workflow = self.session.get(PersistentWorkflowORM, request.workflow_id)
            assert workflow is not None
            previous_state = workflow.workflow_state
            workflow.workflow_state = PersistentWorkflowState.CLOSED.value
            workflow.updated_at = utc_now()
            self.session.flush()
            self._audit(
                "CANONICAL_INTEGRATION_WORKFLOW_CLOSED",
                request,
                "SUCCESS",
                {
                    "previous_workflow_state": previous_state,
                    "new_workflow_state": workflow.workflow_state,
                    "baseline_commit": context.baseline_commit,
                    "actual_changed_paths": actual_changed,
                    "deterministic_acceptance": deterministic,
                    "duration_seconds": max(0.0, time.monotonic() - started),
                },
            )
            return CanonicalIntegrationResult(
                status="COMPLETED",
                workflow_id=request.workflow_id,
                baseline_commit=context.baseline_commit,
                canonical_head=current_head(context.canonical_root),
                authorized_paths=context.authorized_paths,
                candidate_changed_paths=context.candidate_changed_paths,
                applied_paths=[str(item["path"]) for item in written],
                actual_changed_paths=actual_changed,
                deterministic_acceptance=deterministic,
            )
        except (GitMutationError, DeterministicAcceptanceError, _IntegrationBlocked) as error:
            reason = error.reason if isinstance(error, _IntegrationBlocked) else getattr(error, "code", error.__class__.__name__)
            message = error.reason if isinstance(error, _IntegrationBlocked) else str(error)
            rollback_succeeded = False
            if mutation_started:
                self._audit(
                    "CANONICAL_INTEGRATION_ROLLBACK_ATTEMPTED",
                    request,
                    "STARTED",
                    {"reason": reason, "baseline_commit": context.baseline_commit},
                )
                try:
                    restore_to_baseline(context.canonical_root, context.baseline_commit)
                    rollback_succeeded = True
                    self._audit(
                        "CANONICAL_INTEGRATION_ROLLBACK_COMPLETED",
                        request,
                        "SUCCESS",
                        {"baseline_commit": context.baseline_commit, "changed_paths": changed_paths(context.canonical_root)},
                    )
                except GitMutationError as rollback_error:
                    self._audit(
                        "CANONICAL_INTEGRATION_ROLLBACK_FAILED",
                        request,
                        "FAILED_CLOSED",
                        {"reason": rollback_error.code, "message": rollback_error.message},
                    )
                    return CanonicalIntegrationResult(
                        status="ROLLBACK_FAILED",
                        workflow_id=request.workflow_id,
                        reason=f"{message}; rollback failed: {rollback_error.message}",
                        baseline_commit=context.baseline_commit,
                        canonical_head=current_head(context.canonical_root),
                        authorized_paths=context.authorized_paths,
                        candidate_changed_paths=context.candidate_changed_paths,
                        rollback_attempted=True,
                        rollback_succeeded=False,
                    )
            self._audit(
                "CANONICAL_INTEGRATION_FAILED",
                request,
                "FAILED_CLOSED",
                {
                    "reason": reason,
                    "message": message,
                    "rollback_attempted": mutation_started,
                    "rollback_succeeded": rollback_succeeded,
                },
            )
            return CanonicalIntegrationResult(
                status="FAILED",
                workflow_id=request.workflow_id,
                reason=message,
                baseline_commit=context.baseline_commit,
                canonical_head=current_head(context.canonical_root),
                authorized_paths=context.authorized_paths,
                candidate_changed_paths=context.candidate_changed_paths,
                rollback_attempted=mutation_started,
                rollback_succeeded=rollback_succeeded,
            )

    def _preflight(self, request: CanonicalIntegrationRequest) -> "_IntegrationContext":
        if request.access_mode != RepositoryAccessMode.CANONICAL_INTEGRATION:
            raise _IntegrationBlocked("CANONICAL_INTEGRATION_ACCESS_MODE_REQUIRED", {})
        if request.authority_level != AuthorityLevel.L1:
            raise _IntegrationBlocked("L1_AUTHORITY_REQUIRED", {"authority_level": request.authority_level.value})

        workflow = self.session.get(PersistentWorkflowORM, request.workflow_id)
        if workflow is None:
            raise _IntegrationBlocked("WORKFLOW_NOT_FOUND", {})
        if workflow.workflow_state != PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value:
            raise _IntegrationBlocked("WORKFLOW_NOT_COMPLETED_PENDING_INTEGRATION", {"workflow_state": workflow.workflow_state})

        task = self.session.get(TaskORM, workflow.task_id) if workflow.task_id else None
        if task is None or task.project_id != workflow.project_id:
            raise _IntegrationBlocked("WORKFLOW_TASK_RELATIONSHIP_INVALID", {})
        plan = self.session.get(EngineeringPlanORM, workflow.plan_id) if workflow.plan_id else None
        freeze = self.session.get(PlanFreezeORM, workflow.plan_freeze_id) if workflow.plan_freeze_id else None
        if plan is None or freeze is None:
            raise _IntegrationBlocked("FROZEN_PLAN_REQUIRED", {})
        if plan.task_id != task.id or freeze.task_id != task.id or freeze.plan_id != plan.id:
            raise _IntegrationBlocked("FROZEN_PLAN_RELATIONSHIP_INVALID", {})
        if plan.project_id != workflow.project_id or freeze.project_id != workflow.project_id:
            raise _IntegrationBlocked("FROZEN_PLAN_PROJECT_MISMATCH", {})
        if _authority_rank(plan.required_authority_level) > _authority_rank(AuthorityLevel.L1.value):
            raise _IntegrationBlocked("PLAN_AUTHORITY_EXCEEDS_L1", {"required_authority_level": plan.required_authority_level})
        if workflow.authority_tier != AuthorityLevel.L1.value:
            raise _IntegrationBlocked("WORKFLOW_L1_AUTHORITY_REQUIRED", {"authority_tier": workflow.authority_tier})

        repository = self.session.get(RepositoryRegistrationORM, workflow.repository_id) if workflow.repository_id else None
        if repository is None:
            raise _IntegrationBlocked("REPOSITORY_REQUIRED", {})
        if repository.access_mode != RepositoryAccessMode.CANONICAL_INTEGRATION.value:
            raise _IntegrationBlocked("REPOSITORY_ACCESS_MODE_NOT_CANONICAL_INTEGRATION", {"access_mode": repository.access_mode})
        attachment = self.session.scalar(
            select(ProjectRepositoryAttachmentORM).where(
                ProjectRepositoryAttachmentORM.project_id == workflow.project_id,
                ProjectRepositoryAttachmentORM.repository_id == repository.id,
            )
        )
        if attachment is None:
            raise _IntegrationBlocked("REPOSITORY_NOT_ATTACHED_TO_PROJECT", {})

        canonical_root = Path(request.canonical_repository_path).resolve()
        registered_root = Path(repository.location).resolve()
        if canonical_root != registered_root:
            raise _IntegrationBlocked(
                "CANONICAL_REPOSITORY_PATH_MISMATCH",
                {"requested": str(canonical_root), "registered": str(registered_root)},
            )
        candidate_root = Path(request.candidate_workspace_path).resolve()
        if candidate_root != Path(workflow.worktree_path).resolve():
            raise _IntegrationBlocked("CANDIDATE_WORKSPACE_MISMATCH", {"candidate": str(candidate_root), "workflow": workflow.worktree_path})
        if candidate_root == canonical_root:
            raise _IntegrationBlocked("CANDIDATE_WORKSPACE_MUST_BE_ISOLATED", {})

        if not freeze.commit_sha:
            raise _IntegrationBlocked("FROZEN_BASELINE_COMMIT_REQUIRED", {})
        if freeze.commit_sha != request.expected_baseline_commit:
            raise _IntegrationBlocked("REQUEST_BASELINE_DOES_NOT_MATCH_FREEZE", {"freeze_commit": freeze.commit_sha})
        canonical_head = current_head(canonical_root)
        if canonical_head != request.expected_baseline_commit:
            raise _IntegrationBlocked("CANONICAL_HEAD_DRIFT", {"canonical_head": canonical_head, "expected": request.expected_baseline_commit})
        assert_clean(canonical_root)

        authorized_paths = _authorized_paths(freeze)
        acceptance_checks = _acceptance_checks(freeze, authorized_paths)
        candidate_head = current_head(candidate_root)
        if candidate_head != request.expected_baseline_commit:
            raise _IntegrationBlocked(
                "CANDIDATE_HEAD_BASELINE_MISMATCH",
                {"candidate_head": candidate_head, "expected": request.expected_baseline_commit},
            )
        candidate_changed = changed_paths(candidate_root)
        unexpected_candidate = sorted(set(candidate_changed) - set(authorized_paths))
        if unexpected_candidate:
            raise _IntegrationBlocked("CANDIDATE_HAS_UNAUTHORIZED_CHANGES", {"unexpected_paths": unexpected_candidate})
        if not candidate_changed:
            raise _IntegrationBlocked("CANDIDATE_HAS_NO_GIT_CHANGES", {})

        candidate_acceptance = verify_deterministic_acceptance(
            candidate_root,
            acceptance_checks,
            authorized_paths=set(authorized_paths),
        )
        exact_content_paths = [check["path"] for check in acceptance_checks if check.get("type") == "exact_file_content"]
        missing_candidate_checks = sorted(set(candidate_changed) - set(exact_content_paths))
        if missing_candidate_checks:
            raise _IntegrationBlocked(
                "CANDIDATE_CHANGE_MISSING_EXACT_CONTENT_CHECK",
                {"paths": missing_candidate_checks},
            )
        captured_file_contents = capture_utf8_file_contents(
            candidate_root,
            exact_content_paths,
            authorized_paths=set(authorized_paths),
        )
        _verify_captured_content_matches_acceptance(captured_file_contents, acceptance_checks)

        return _IntegrationContext(
            workflow=workflow,
            plan=plan,
            freeze=freeze,
            repository=repository,
            canonical_root=canonical_root,
            candidate_root=candidate_root,
            baseline_commit=request.expected_baseline_commit,
            canonical_head=canonical_head,
            candidate_head=candidate_head,
            authorized_paths=authorized_paths,
            candidate_changed_paths=candidate_changed,
            acceptance_checks=acceptance_checks,
            candidate_acceptance=candidate_acceptance,
            captured_file_contents=captured_file_contents,
        )

    def _verify_post_apply(
        self,
        context: "_IntegrationContext",
        intended_paths: list[str],
        actual_changed_paths: list[str],
    ) -> None:
        authorized = set(context.authorized_paths)
        actual = set(actual_changed_paths)
        intended = set(intended_paths)
        unauthorized = sorted(actual - authorized)
        missing = sorted(intended - actual)
        if unauthorized:
            raise _IntegrationBlocked("CANONICAL_UNAUTHORIZED_CHANGED_PATHS", {"unexpected_paths": unauthorized})
        if missing:
            raise _IntegrationBlocked("CANONICAL_INTENDED_WRITES_NOT_OBSERVED", {"missing_paths": missing})

    def _audit(
        self,
        event_type: str,
        request: CanonicalIntegrationRequest,
        result: str,
        metadata: dict[str, Any],
    ) -> None:
        workflow = self.session.get(PersistentWorkflowORM, request.workflow_id)
        self.audit.record(
            event_type=event_type,
            actor=request.actor.value,
            project_id=workflow.project_id if workflow else None,
            repository_id=workflow.repository_id if workflow else None,
            task_id=workflow.task_id if workflow else None,
            authority_level=AuthorityLevel.L1,
            action="controlled_canonical_integration",
            result=result,
            metadata=metadata,
        )


@dataclass(frozen=True)
class _IntegrationContext:
    workflow: PersistentWorkflowORM
    plan: EngineeringPlanORM
    freeze: PlanFreezeORM
    repository: RepositoryRegistrationORM
    canonical_root: Path
    candidate_root: Path
    baseline_commit: str
    canonical_head: str
    candidate_head: str
    authorized_paths: list[str]
    candidate_changed_paths: list[str]
    acceptance_checks: list[dict[str, Any]]
    candidate_acceptance: list[dict[str, Any]]
    captured_file_contents: list[dict[str, str]]


class _IntegrationBlocked(RuntimeError):
    def __init__(self, reason: str, metadata: dict[str, Any]):
        super().__init__(reason)
        self.reason = reason
        self.metadata = metadata


def _authorized_paths(freeze: PlanFreezeORM) -> list[str]:
    payload = freeze.plan_payload if isinstance(freeze.plan_payload, dict) else {}
    raw = payload.get("affected_files")
    if not isinstance(raw, list):
        raise _IntegrationBlocked("INVALID_FROZEN_AFFECTED_FILES", {})
    paths: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise _IntegrationBlocked("INVALID_FROZEN_AFFECTED_FILES", {})
        try:
            path = validate_relative_repo_path(item.get("path"))
        except GitMutationError as exc:
            raise _IntegrationBlocked("INVALID_FROZEN_AFFECTED_FILES", {"message": exc.message}) from exc
        if path not in seen:
            seen.add(path)
            paths.append(path)
    if not paths:
        raise _IntegrationBlocked("EMPTY_FROZEN_AFFECTED_FILES", {})
    return paths


def _acceptance_checks(freeze: PlanFreezeORM, authorized_paths: list[str]) -> list[dict[str, Any]]:
    payload = freeze.plan_payload if isinstance(freeze.plan_payload, dict) else {}
    raw = payload.get("deterministic_acceptance_checks")
    if not isinstance(raw, list) or not raw:
        raise _IntegrationBlocked("FROZEN_DETERMINISTIC_ACCEPTANCE_REQUIRED", {})
    authorized = set(authorized_paths)
    checks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise _IntegrationBlocked("INVALID_FROZEN_DETERMINISTIC_ACCEPTANCE_CHECK", {})
        if item.get("type") != "exact_file_content":
            raise _IntegrationBlocked("UNSUPPORTED_FROZEN_DETERMINISTIC_ACCEPTANCE_CHECK", {"type": item.get("type")})
        try:
            path = validate_relative_repo_path(item.get("path"))
        except GitMutationError as exc:
            raise _IntegrationBlocked("INVALID_FROZEN_DETERMINISTIC_ACCEPTANCE_PATH", {"message": exc.message}) from exc
        if path not in authorized:
            raise _IntegrationBlocked("DETERMINISTIC_ACCEPTANCE_PATH_OUTSIDE_AFFECTED_FILES", {"path": path})
        if path in seen:
            raise _IntegrationBlocked("DUPLICATE_FROZEN_DETERMINISTIC_ACCEPTANCE_PATH", {"path": path})
        expected_text = item.get("expected_text")
        if not isinstance(expected_text, str):
            raise _IntegrationBlocked("INVALID_FROZEN_DETERMINISTIC_ACCEPTANCE_EXPECTED_TEXT", {"path": path})
        seen.add(path)
        checks.append({"type": "exact_file_content", "path": path, "expected_text": expected_text})
    return checks


def _verify_captured_content_matches_acceptance(
    captured_file_contents: list[dict[str, str]],
    acceptance_checks: list[dict[str, Any]],
) -> None:
    expected_by_path = {str(check["path"]): check["expected_text"] for check in acceptance_checks}
    for item in captured_file_contents:
        path = item["path"]
        if item["content"] != expected_by_path[path]:
            raise _IntegrationBlocked("CANDIDATE_CAPTURED_CONTENT_MISMATCH", {"path": path})


def _authority_rank(value: str) -> int:
    return {AuthorityLevel.L0.value: 0, AuthorityLevel.L1.value: 1, AuthorityLevel.L2.value: 2, AuthorityLevel.L3.value: 3}.get(value, 99)
