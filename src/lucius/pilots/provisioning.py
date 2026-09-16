from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.domain.enums import (
    Actor,
    AllowedAction,
    AuthorityLevel,
    Environment,
    ProjectType,
    SnapshotMode,
    TaskComplexity,
    TaskPriority,
)
from lucius.persistence.orm import PersistentWorkflowORM
from lucius.persistence.repositories import RepositorySnapshotService
from lucius.pilots.repository_state import RepositoryStateService
from lucius.pilots.workflows import PersistentWorkflowService
from lucius.projects.service import ProjectRegistryService
from lucius.repositories.schemas import WorkspaceContext
from lucius.runtime.adapters import ScriptedRuntimePlanningAdapter
from lucius.tasks.service import TaskService


READ_ONLY_CONTEXT_MAX_FILE_BYTES = 200_000
READ_ONLY_CONTEXT_MAX_TOTAL_BYTES = 40_000
HISTORICAL_WORKFLOW_IDS = {"LWORK_000153", "LWORK_000154", "LWORK_000155"}
FORBIDDEN_AUTHORITY_TERMS = {
    "mutation",
    "commit",
    "merge",
    "push",
    "deploy",
    "trading",
    "messaging",
    "credential",
}


class ReadOnlyBacklogProvisioningError(ValueError):
    pass


def provision_read_only_backlog(session: Session, specification: dict[str, Any]) -> list[dict[str, Any]]:
    savepoint = session.begin_nested()
    try:
        result = _provision_read_only_backlog(session, specification)
    except Exception:
        savepoint.rollback()
        raise
    savepoint.commit()
    return result


def _provision_read_only_backlog(session: Session, specification: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate and provision one bounded read-only backlog atomically."""
    spec = _validate_specification(specification)
    repository = _validate_repository_and_workspace(spec)
    _reject_duplicate_provisioning(session, spec["provisioning_id"])

    project_spec = spec["project"]
    project = ProjectRegistryService(session).register_project(
        project_spec["name"],
        slug=project_spec.get("slug"),
        organization=project_spec.get("organization"),
        project_type=ProjectType.R_AND_D,
        description=project_spec.get("description"),
        workspace_scope=str(repository["workspace"]),
        default_authority_level=AuthorityLevel.L0,
        actor=Actor.LUCIUS,
    )
    workspace_context = WorkspaceContext(
        workspace_id=f"{spec['provisioning_id']}-repository",
        allowed_roots=[repository["path"].parent],
        access_mode="READ_ONLY",
        authority_level=AuthorityLevel.L0,
        repository_ref=spec["repository"]["frozen_head"],
    )
    registration = ProjectRegistryService(session).attach_repository(
        project_id=project.id,
        name=spec["repository"].get("name") or project.name,
        location=repository["path"],
        workspace_context=workspace_context,
        actor=Actor.LUCIUS,
    )
    state = RepositoryStateService(session).inspect(
        repository_path=repository["path"],
        repository_id=registration.id,
        workspace_context=workspace_context,
        strict_canonical=True,
        actor=Actor.LUCIUS,
    )
    if state.head_commit != spec["repository"]["frozen_head"]:
        raise ReadOnlyBacklogProvisioningError("Target repository HEAD does not match frozen_head.")
    snapshot = RepositorySnapshotService(session).inspect_repository(
        repository_id=registration.id,
        workspace_context=workspace_context,
        mode=SnapshotMode.STANDARD,
        actor=Actor.LUCIUS.value,
    )
    if not snapshot.snapshot_id:
        raise ReadOnlyBacklogProvisioningError("Canonical repository snapshot was not created.")

    task_service = TaskService(session)
    workflow_service = PersistentWorkflowService(session)
    planning_adapter = ScriptedRuntimePlanningAdapter()
    results: list[dict[str, Any]] = []
    for task_spec in spec["tasks"]:
        task = task_service.create_task(
            project_id=project.id,
            title=task_spec["title"],
            objective=task_spec["objective"],
            priority=TaskPriority.NORMAL,
            complexity=TaskComplexity.T1,
            authority_level=AuthorityLevel.L0,
            created_by=Actor.LUCIUS,
        )
        task_service.create_or_update_contract(
            task_id=task.id,
            objective=task_spec["objective"],
            acceptance_criteria=task_spec["acceptance_criteria"],
            constraints=[
                "READ_ONLY_REPOSITORY",
                "NO_SOURCE_MUTATION",
                "NO_ANALYSIS_OR_OUTPUT_MUTATION",
                "NO_COMMIT_MERGE_PUSH_DEPLOY",
                "NO_TRADING_MESSAGING_OR_CREDENTIAL_ACTIONS",
            ],
            repository_ids=[registration.id],
            allowed_actions=[AllowedAction.READ_REPOSITORY, AllowedAction.READ_DOCUMENTATION],
            allowed_tools=[],
            environment=Environment.SANDBOX,
            authority_level=AuthorityLevel.L0,
            documentation_required=False,
            stop_conditions=["Evidence is absent, ambiguous, or cannot be grounded in the supplied files."],
            actor=Actor.LUCIUS,
        )
        ready = task_service.mark_ready(task.id, actor=Actor.LUCIUS)
        if not ready.valid:
            raise ReadOnlyBacklogProvisioningError(f"Task contract did not become READY: {task.id}")
        item_id = f"{spec['provisioning_id']}-{task_spec['key']}"
        queue_item = _queue_item(task_spec, item_id)
        workflow = workflow_service.create(
            objective=task_spec["objective"],
            expected_main_head=spec["repository"]["frozen_head"],
            isolated_branch=f"lucius/{spec['provisioning_id']}/{task_spec['key']}",
            worktree_path=str(repository["workspace"]),
            authority_tier=AuthorityLevel.L0.value,
            project_id=project.id,
            repository_id=registration.id,
            repository_snapshot_id=snapshot.snapshot_id,
            task_id=task.id,
            task_backlog=[queue_item],
            dependency_graph={item_id: []},
            decisions=[_provisioning_marker(spec["provisioning_id"], task_spec["key"])],
            pending_task_ids=[item_id],
            actor=Actor.LUCIUS,
        )
        plan_reference = planning_adapter.prepare_workflow_plan(session, workflow.id)
        results.append(
            {
                "task_id": task.id,
                "workflow_id": workflow.id,
                "item_id": item_id,
                "plan_id": plan_reference.plan_id,
                "plan_freeze_id": plan_reference.plan_freeze_id,
            }
        )
    return results


def load_specification(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReadOnlyBacklogProvisioningError(f"Cannot read provisioning specification: {path}") from error
    if not isinstance(payload, dict):
        raise ReadOnlyBacklogProvisioningError("Provisioning specification must be a JSON object.")
    return payload


def _validate_specification(spec: dict[str, Any]) -> dict[str, Any]:
    required = {"provisioning_id", "project", "repository", "tasks"}
    missing = sorted(required - set(spec))
    if missing:
        raise ReadOnlyBacklogProvisioningError(f"Missing specification fields: {', '.join(missing)}")
    provisioning_id = spec["provisioning_id"]
    if not isinstance(provisioning_id, str) or not provisioning_id.strip():
        raise ReadOnlyBacklogProvisioningError("provisioning_id must be a non-empty string.")
    if any(historical_id in provisioning_id for historical_id in HISTORICAL_WORKFLOW_IDS):
        raise ReadOnlyBacklogProvisioningError("Historical workflow identifiers cannot be reused.")
    project = spec["project"]
    repository = spec["repository"]
    tasks = spec["tasks"]
    if not isinstance(project, dict) or not isinstance(repository, dict) or not isinstance(tasks, list) or not tasks:
        raise ReadOnlyBacklogProvisioningError("project, repository, and non-empty tasks are required.")
    if len(tasks) > 12:
        raise ReadOnlyBacklogProvisioningError("At most 12 independent tasks may be provisioned at once.")
    if not isinstance(repository.get("path"), str) or not isinstance(repository.get("frozen_head"), str):
        raise ReadOnlyBacklogProvisioningError("repository.path and repository.frozen_head are required strings.")
    for field in ("workspace", "allowed_workspace_root"):
        if not isinstance(repository.get(field), str) or not repository[field].strip():
            raise ReadOnlyBacklogProvisioningError(f"repository.{field} is an explicitly required string.")
    if len(repository["frozen_head"]) != 40 or any(character not in "0123456789abcdef" for character in repository["frozen_head"]):
        raise ReadOnlyBacklogProvisioningError("repository.frozen_head must be a lowercase 40-character commit SHA.")
    keys: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ReadOnlyBacklogProvisioningError("Each task specification must be an object.")
        for field in ("key", "title", "objective", "context_paths", "acceptance_criteria"):
            if field not in task:
                raise ReadOnlyBacklogProvisioningError(f"Task specification is missing {field}.")
        key = task["key"]
        if not isinstance(key, str) or not key or key in keys:
            raise ReadOnlyBacklogProvisioningError("Task keys must be unique non-empty strings.")
        keys.add(key)
        if task.get("read_only") is not True or task.get("mutation_allowed") is not False:
            raise ReadOnlyBacklogProvisioningError("Every task must explicitly set read_only=true and mutation_allowed=false.")
        if task.get("unattended") is not True:
            raise ReadOnlyBacklogProvisioningError("Every task must explicitly set unattended=true.")
        required_policy_fields = {
            "task_complexity",
            "task_risk",
            "isolation_mode",
            "execution_supervision",
        }
        missing_policy_fields = sorted(required_policy_fields - set(task))
        if missing_policy_fields:
            raise ReadOnlyBacklogProvisioningError(
                "Every task must explicitly declare: " + ", ".join(missing_policy_fields)
            )
        if task["task_complexity"] != "T1" or task["task_risk"] != "LOW":
            raise ReadOnlyBacklogProvisioningError("Every task must be explicitly bounded as T1 and LOW risk.")
        if task["isolation_mode"] != "ISOLATED_WORKTREE":
            raise ReadOnlyBacklogProvisioningError("Every task must use ISOLATED_WORKTREE.")
        if task["execution_supervision"] != "UNSUPERVISED":
            raise ReadOnlyBacklogProvisioningError("Every task must use UNSUPERVISED execution.")
        if task.get("deterministic_verification") is not True:
            raise ReadOnlyBacklogProvisioningError("Every task must set deterministic_verification=true.")
        if task.get("evidence_reference_validation_required") is not True:
            raise ReadOnlyBacklogProvisioningError("Every task must require evidence reference validation.")
        capabilities = task.get("required_capabilities")
        if capabilities != ["inspection_reasoning"]:
            raise ReadOnlyBacklogProvisioningError("required_capabilities must be exactly inspection_reasoning.")
        context_paths = task["context_paths"]
        if not isinstance(context_paths, list) or not context_paths:
            raise ReadOnlyBacklogProvisioningError("Each task requires non-empty context_paths.")
        if not isinstance(task["acceptance_criteria"], list) or not task["acceptance_criteria"]:
            raise ReadOnlyBacklogProvisioningError("Each task requires acceptance_criteria.")
        authority_text = json.dumps(
            {
                "allowed_actions": task.get("allowed_actions"),
                "authority": task.get("authority"),
                "tools": task.get("tools"),
            }
        ).lower()
        if any(term in authority_text for term in FORBIDDEN_AUTHORITY_TERMS):
            raise ReadOnlyBacklogProvisioningError("Task specification contains forbidden authority language.")
    return spec


def _validate_repository_and_workspace(spec: dict[str, Any]) -> dict[str, Path]:
    repository_spec = spec["repository"]
    repository_path = Path(repository_spec["path"]).expanduser().resolve()
    workspace = Path(repository_spec["workspace"]).expanduser().resolve()
    allowed_root = Path(repository_spec["allowed_workspace_root"]).expanduser().resolve()
    if not repository_path.is_dir():
        raise ReadOnlyBacklogProvisioningError(f"Target repository does not exist: {repository_path}")
    if not workspace.is_dir():
        raise ReadOnlyBacklogProvisioningError(f"Persistent isolated workspace does not exist: {workspace}")
    if not _is_relative_to(workspace, allowed_root):
        raise ReadOnlyBacklogProvisioningError("Persistent workspace is outside the explicitly authorized root.")
    if workspace == repository_path:
        raise ReadOnlyBacklogProvisioningError("Persistent workspace must be isolated from the target repository.")
    repository_head = _git(repository_path, ["rev-parse", "HEAD"])
    if repository_head != repository_spec["frozen_head"]:
        raise ReadOnlyBacklogProvisioningError("Target repository HEAD does not match frozen_head.")
    workspace_root = Path(_git(workspace, ["rev-parse", "--show-toplevel"]))
    if workspace_root.resolve() != workspace:
        raise ReadOnlyBacklogProvisioningError("Persistent workspace is not the root of an isolated Git worktree.")
    workspace_head = _git(workspace, ["rev-parse", "HEAD"])
    if workspace_head != repository_spec["frozen_head"]:
        raise ReadOnlyBacklogProvisioningError("Persistent workspace HEAD does not match frozen_head.")
    if _git(workspace, ["status", "--porcelain", "--untracked-files=all"]):
        raise ReadOnlyBacklogProvisioningError("Persistent isolated workspace is dirty.")
    for task in spec["tasks"]:
        total_bytes = 0
        for raw_path in task["context_paths"]:
            if not isinstance(raw_path, str) or not raw_path.strip() or Path(raw_path).is_absolute():
                raise ReadOnlyBacklogProvisioningError(f"Context path must be relative: {raw_path!r}")
            context_path = (workspace / raw_path).resolve()
            if not _is_relative_to(context_path, workspace) or context_path.is_symlink() or not context_path.is_file():
                raise ReadOnlyBacklogProvisioningError(f"Context path is not a regular file inside workspace: {raw_path}")
            try:
                content = context_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise ReadOnlyBacklogProvisioningError(f"Context path is not UTF-8: {raw_path}") from error
            size = len(content.encode("utf-8"))
            if size > READ_ONLY_CONTEXT_MAX_FILE_BYTES:
                raise ReadOnlyBacklogProvisioningError(f"Context path exceeds {READ_ONLY_CONTEXT_MAX_FILE_BYTES} bytes: {raw_path}")
            total_bytes += size
        if total_bytes > READ_ONLY_CONTEXT_MAX_TOTAL_BYTES:
            raise ReadOnlyBacklogProvisioningError(f"Read-only context exceeds {READ_ONLY_CONTEXT_MAX_TOTAL_BYTES} total bytes.")
    return {"path": repository_path, "workspace": workspace}


def _reject_duplicate_provisioning(session: Session, provisioning_id: str) -> None:
    rows = session.scalars(select(PersistentWorkflowORM)).all()
    for row in rows:
        if row.id in HISTORICAL_WORKFLOW_IDS:
            continue
        for decision in row.decisions or []:
            if isinstance(decision, dict) and decision.get("provisioning_id") == provisioning_id:
                raise ReadOnlyBacklogProvisioningError(f"Provisioning id already exists: {provisioning_id}")


def _queue_item(task: dict[str, Any], item_id: str) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "logical_task_id": item_id,
        "state": "READY",
        "title": task["title"],
        "priority": "NORMAL",
        "task_type": "inspection",
        "read_only": True,
        "mutation_allowed": False,
        "unattended": True,
        "task_complexity": "T1",
        "task_risk": "LOW",
        "isolation_mode": "ISOLATED_WORKTREE",
        "execution_supervision": "UNSUPERVISED",
        "required_capabilities": ["inspection_reasoning"],
        "context_limits": {
            "deterministic_verification": True,
            "evidence_reference_validation_required": True,
            "read_only_context_paths": list(task["context_paths"]),
            "requested_evidence_refs": int(task.get("requested_evidence_refs", 3)),
        },
        "version": 0,
    }


def _provisioning_marker(provisioning_id: str, task_key: str) -> dict[str, str]:
    return {"type": "READ_ONLY_BACKLOG_PROVISIONED", "provisioning_id": provisioning_id, "task_key": task_key}


def _git(path: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=False)
    except OSError as error:
        raise ReadOnlyBacklogProvisioningError(f"Unable to inspect Git workspace: {path}") from error
    if result.returncode != 0:
        raise ReadOnlyBacklogProvisioningError(result.stderr.strip() or f"Git command failed in {path}")
    return result.stdout.strip()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True