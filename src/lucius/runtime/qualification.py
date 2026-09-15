from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lucius.domain.enums import (
    Actor,
    AllowedAction,
    AuthorityLevel,
    Environment,
    ProjectType,
    QueueWorkItemState,
    TaskComplexity,
    TaskPriority,
)
from lucius.projects.service import ProjectRegistryService
from lucius.pilots.workflows import PersistentWorkflowService
from lucius.persistence.orm import PersistentWorkflowORM
from lucius.repositories.git_mutation import GitMutationError, validate_relative_repo_path
from lucius.repositories.schemas import WorkspaceContext
from lucius.runtime.adapters import ScriptedRuntimePlanningAdapter
from lucius.runtime.deterministic_acceptance import (
    DeterministicAcceptanceError,
    normalize_deterministic_acceptance_checks,
)
from lucius.runtime.ollama import OllamaExecutionProvider
from lucius.runtime.router import ModelExecutionRouter, RuntimeExecutionProvider, RuntimeProviderRegistry
from lucius.runtime.schemas import RuntimeExecutionSupervision, RuntimeLoopConfig
from lucius.runtime.service import ExecutionRuntimeLoopService
from lucius.tasks.service import TaskService


MODEL_MUTATION_QUALIFICATION_SCHEMA_CONSTRAINT = {
    "mode": "controlled_model_mutation_qualification",
    "provider_output": "ollama_compact_json_edit",
    "required_top_level": ["edits"],
}


@dataclass(frozen=True)
class ModelMutationQualificationConfig:
    model_id: str
    target_repository: Path
    target_baseline: str
    execution_supervision: RuntimeExecutionSupervision
    allowed_mutation_paths: list[str]
    deterministic_acceptance_checks: list[dict[str, Any]]
    immutable_verifier_checks: list[dict[str, Any]] = field(default_factory=list)
    target_branch: str = "main"
    isolated_worktree: Path | None = None
    objective: str = "Run a controlled local model code-mutation qualification task."
    project_name: str | None = None
    repository_name: str | None = None
    ollama_endpoint: str = "http://127.0.0.1:11434"
    ollama_timeout_seconds: int = 60
    ollama_mutation_num_predict: int = 1024
    execute: bool = True
    actor: Actor = Actor.LUCIUS
    execution_provider: RuntimeExecutionProvider | None = field(default=None, compare=False)


class ModelMutationQualificationResult(BaseModel):
    model_id: str
    target_repository: str
    target_branch: str
    target_baseline: str
    isolated_worktree: str
    project_id: str
    repository_id: str
    task_id: str
    workflow_id: str
    plan_id: str | None = None
    plan_freeze_id: str | None = None
    execution_supervision: str
    allowed_mutation_paths: list[str]
    deterministic_acceptance_checks: list[dict[str, Any]]
    immutable_verifier_checks: list[dict[str, Any]] = Field(default_factory=list)
    immutable_verifier_paths: list[str] = Field(default_factory=list)
    schema_constraint: dict[str, Any]
    target_environment_path: str | None = None
    worktree_environment_path: str | None = None
    runtime_result: dict[str, Any] | None = None
    worktree_clean_after_run: bool | None = None
    provider_invocation_authorized: bool | None = None
    notes: list[str] = Field(default_factory=list)


def run_model_mutation_qualification(
    session: Session,
    config: ModelMutationQualificationConfig,
) -> ModelMutationQualificationResult:
    target_repository = config.target_repository.expanduser().resolve()
    if not target_repository.exists():
        raise ValueError(f"Target repository does not exist: {target_repository}")

    baseline = _verify_target_baseline(
        target_repository=target_repository,
        target_branch=config.target_branch,
        target_baseline=config.target_baseline,
    )
    allowed_paths = _normalize_allowed_mutation_paths(config.allowed_mutation_paths)
    checks = _normalize_acceptance_checks(
        config.deterministic_acceptance_checks,
        authorized_paths=set(allowed_paths),
    )
    immutable_verifier_checks = _normalize_immutable_verifier_checks(
        config.immutable_verifier_checks,
        target_repository=target_repository,
        target_baseline=baseline,
        allowed_mutation_paths=set(allowed_paths),
    )
    immutable_verifier_paths = _immutable_verifier_paths(immutable_verifier_checks)
    all_checks = [*checks, *immutable_verifier_checks]
    worktree = _ensure_isolated_worktree(
        target_repository=target_repository,
        target_baseline=baseline,
        isolated_worktree=config.isolated_worktree,
    )
    environment_link = _prepare_worktree_environment(
        target_repository=target_repository,
        worktree=worktree,
    )
    _require_clean_worktree(worktree, "isolated qualification worktree must start clean")

    project = ProjectRegistryService(session).register_project(
        config.project_name or f"Model qualification: {target_repository.name}",
        slug=f"model-qualification-{_slug(target_repository.name)}",
        project_type=ProjectType.R_AND_D,
        description="Controlled local model code-mutation qualification project.",
        default_authority_level=AuthorityLevel.L1,
        actor=config.actor,
    )
    repository = ProjectRegistryService(session).attach_repository(
        project_id=project.id,
        name=config.repository_name or f"{target_repository.name} qualification worktree",
        location=worktree,
        workspace_context=WorkspaceContext(
            workspace_id="model-mutation-qualification",
            allowed_roots=[worktree],
            authority_level=AuthorityLevel.L1,
        ),
        actor=config.actor,
    )
    task = TaskService(session).create_task(
        project_id=project.id,
        title=f"Controlled model mutation qualification for {config.model_id}",
        objective=config.objective,
        priority=TaskPriority.NORMAL,
        complexity=TaskComplexity.T1,
        authority_level=AuthorityLevel.L1,
        created_by=config.actor,
    )
    TaskService(session).create_or_update_contract(
        task_id=task.id,
        objective=config.objective,
        acceptance_criteria=[
            "Only the explicit qualification test file may be created or changed.",
            "Deterministic acceptance checks must pass in the isolated worktree.",
            "No canonical integration, commit, push, merge, or deployment is authorized.",
        ],
        constraints=[
            "CONTROLLED_MODEL_MUTATION_QUALIFICATION",
            "SUPERVISED_ONLY",
            "ISOLATED_WORKTREE_ONLY",
            "NO_CANONICAL_INTEGRATION",
            "NO_COMMIT",
            "NO_PUSH",
            "NO_MERGE",
            "NO_DEPLOY",
        ],
        repository_ids=[repository.id],
        allowed_actions=[
            AllowedAction.READ_REPOSITORY,
            AllowedAction.RUN_TESTS,
            AllowedAction.WRITE_TESTS,
        ],
        environment=Environment.DEVELOPMENT,
        authority_level=AuthorityLevel.L1,
        documentation_required=False,
        actor=config.actor,
    )
    TaskService(session).mark_ready(task.id, actor=config.actor)

    item_id = f"QUALIFY_{_slug(config.model_id).upper()}"
    queue_item = {
        "item_id": item_id,
        "logical_task_id": item_id,
        "title": f"Controlled {config.model_id} code-mutation qualification",
        "state": QueueWorkItemState.READY.value,
        "priority": "NORMAL",
        "created_order": 1,
        "dependencies": [],
        "completed_substeps": [],
        "version": 0,
        "required_capabilities": ["code_modification"],
        "task_type": "engineering",
        "task_complexity": "T1",
        "task_risk": "LOW",
        "isolation_mode": "ISOLATED_WORKTREE",
        "execution_supervision": config.execution_supervision.value,
        "qualification_mode": "controlled_model_mutation",
        "allowed_mutation_paths": allowed_paths,
        "deterministic_acceptance_checks": all_checks,
        "immutable_verifier_paths": immutable_verifier_paths,
        "context_limits": {
            "deterministic_verification": True,
            "schema_constraint": MODEL_MUTATION_QUALIFICATION_SCHEMA_CONSTRAINT,
            "qualification_mode": "controlled_model_mutation",
        },
        "timeout_seconds": config.ollama_timeout_seconds,
    }
    workflow = PersistentWorkflowService(session).create(
        objective=config.objective,
        expected_main_head=baseline,
        isolated_branch=f"lucius/model-qualification/{_slug(config.model_id)}",
        worktree_path=str(worktree),
        authority_tier="ISOLATED_DEVELOPMENT_ONLY",
        project_id=project.id,
        repository_id=repository.id,
        task_id=task.id,
        task_backlog=[queue_item],
        pending_task_ids=[item_id],
        actor=config.actor,
    )
    plan_reference = ScriptedRuntimePlanningAdapter().prepare_workflow_plan(session, workflow.id)

    runtime_result = None
    if config.execute:
        execution_provider = config.execution_provider or OllamaExecutionProvider(
            model=config.model_id,
            endpoint=config.ollama_endpoint,
            timeout_seconds=config.ollama_timeout_seconds,
            allowed_workspace_roots=[worktree],
            mutation_num_predict=config.ollama_mutation_num_predict,
        )
        router = ModelExecutionRouter(
            session,
            registry=RuntimeProviderRegistry([execution_provider]),
            actor=config.actor,
            default_execution_supervision=config.execution_supervision,
        )
        runtime_result_model = ExecutionRuntimeLoopService(
            session,
            planning_adapter=ScriptedRuntimePlanningAdapter(),
            execution_router=router,
            actor=config.actor,
        ).run(
            RuntimeLoopConfig(
                workflow_ids=[workflow.id],
                max_tasks=1,
                stop_on_block=True,
            )
        )
        runtime_result = runtime_result_model.model_dump(mode="json")

    refreshed_workflow = session.get(PersistentWorkflowORM, workflow.id)
    if refreshed_workflow is None:
        raise ValueError(f"Qualification workflow disappeared before result capture: {workflow.id}")
    return ModelMutationQualificationResult(
        model_id=config.model_id,
        target_repository=str(target_repository),
        target_branch=config.target_branch,
        target_baseline=baseline,
        isolated_worktree=str(worktree),
        project_id=project.id,
        repository_id=repository.id,
        task_id=task.id,
        workflow_id=workflow.id,
        plan_id=refreshed_workflow.plan_id or plan_reference.plan_id,
        plan_freeze_id=refreshed_workflow.plan_freeze_id or plan_reference.plan_freeze_id,
        execution_supervision=config.execution_supervision.value,
        allowed_mutation_paths=allowed_paths,
        deterministic_acceptance_checks=all_checks,
        immutable_verifier_checks=immutable_verifier_checks,
        immutable_verifier_paths=immutable_verifier_paths,
        schema_constraint=MODEL_MUTATION_QUALIFICATION_SCHEMA_CONSTRAINT,
        target_environment_path=environment_link["target"] if environment_link else None,
        worktree_environment_path=environment_link["worktree"] if environment_link else None,
        runtime_result=runtime_result,
        worktree_clean_after_run=_worktree_is_clean(worktree) if config.execute else None,
        provider_invocation_authorized=_provider_invocation_authorized(runtime_result),
        notes=[
            "Qualification mode is supervised and does not alter model profiles.",
            "No canonical integration, commit, push, merge, or deployment is performed.",
        ],
    )


def _verify_target_baseline(
    *,
    target_repository: Path,
    target_branch: str,
    target_baseline: str,
) -> str:
    baseline = _git(target_repository, "rev-parse", "--verify", f"{target_baseline}^{{commit}}")
    branch_head = _git(target_repository, "rev-parse", "--verify", f"{target_branch}^{{commit}}")
    if branch_head != baseline:
        raise ValueError(
            f"Target baseline {baseline} does not match {target_branch} HEAD {branch_head}."
        )
    return baseline


def _ensure_isolated_worktree(
    *,
    target_repository: Path,
    target_baseline: str,
    isolated_worktree: Path | None,
) -> Path:
    worktree = (
        isolated_worktree.expanduser().resolve()
        if isolated_worktree is not None
        else (Path(tempfile.mkdtemp(prefix="lucius-model-qualification-")) / "worktree").resolve()
    )
    if worktree.exists() and any(worktree.iterdir()):
        raise ValueError(f"Qualification worktree already exists and is not empty: {worktree}")
    worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(target_repository, "worktree", "add", "--detach", str(worktree), target_baseline)
    actual_head = _git(worktree, "rev-parse", "--verify", "HEAD")
    if actual_head != target_baseline:
        raise ValueError(f"Qualification worktree HEAD {actual_head} does not match baseline {target_baseline}.")
    return worktree


def _prepare_worktree_environment(
    *,
    target_repository: Path,
    worktree: Path,
) -> dict[str, str] | None:
    target_env = target_repository / ".venv"
    if not target_env.exists() and not target_env.is_symlink():
        return None
    if target_env.is_symlink() or not target_env.is_dir():
        raise ValueError(f"Unsafe target repository .venv; expected a real directory: {target_env}")

    target_env_resolved = target_env.resolve(strict=True)
    if not _is_relative_to(target_env_resolved, target_repository):
        raise ValueError(f"Unsafe target repository .venv resolves outside target repository: {target_env}")

    target_python = target_env_resolved / "bin" / "python"
    if not target_python.exists() or not target_python.is_file():
        raise ValueError(f"Target repository .venv does not provide .venv/bin/python: {target_env}")

    worktree_env = worktree / ".venv"
    if worktree_env.is_symlink():
        try:
            existing_target = worktree_env.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"Unsafe broken worktree .venv symlink: {worktree_env}") from exc
        if existing_target != target_env_resolved:
            raise ValueError(
                f"Unsafe worktree .venv symlink target {existing_target} does not match {target_env_resolved}."
            )
    elif worktree_env.exists():
        raise ValueError(f"Unsafe conflicting worktree .venv exists and is not the qualification symlink: {worktree_env}")
    else:
        worktree_env.symlink_to(target_env_resolved, target_is_directory=True)

    if not worktree_env.is_symlink() or worktree_env.resolve(strict=True) != target_env_resolved:
        raise ValueError(f"Qualification worktree .venv symlink verification failed: {worktree_env}")
    worktree_python = worktree_env / "bin" / "python"
    if not worktree_python.exists() or not worktree_python.is_file():
        raise ValueError(f"Qualification worktree .venv/bin/python is not usable: {worktree_python}")

    _exclude_worktree_environment_from_git(worktree)
    return {
        "target": str(target_env_resolved),
        "worktree": str(worktree_env),
    }


def _exclude_worktree_environment_from_git(worktree: Path) -> None:
    raw_git_exclude = _git(worktree, "rev-parse", "--git-path", "info/exclude")
    git_exclude = Path(raw_git_exclude)
    if not git_exclude.is_absolute():
        git_exclude = (worktree / git_exclude).resolve()
    git_exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = git_exclude.read_text(encoding="utf-8") if git_exclude.exists() else ""
    lines = {line.strip() for line in existing.splitlines()}
    if "/.venv" in lines:
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    git_exclude.write_text(f"{existing}{prefix}/.venv\n", encoding="utf-8")


def _normalize_allowed_mutation_paths(paths: list[str]) -> list[str]:
    if not paths:
        raise ValueError("At least one allowed mutation path is required.")
    normalized: list[str] = []
    for path in paths:
        try:
            normalized.append(validate_relative_repo_path(path))
        except GitMutationError as exc:
            raise ValueError(f"Invalid allowed mutation path: {path!r}") from exc
    return list(dict.fromkeys(normalized))


def _normalize_acceptance_checks(
    checks: list[dict[str, Any]],
    *,
    authorized_paths: set[str],
) -> list[dict[str, Any]]:
    try:
        return normalize_deterministic_acceptance_checks(checks, authorized_paths=authorized_paths)
    except DeterministicAcceptanceError as exc:
        raise ValueError(f"Invalid deterministic acceptance checks: {exc.code}") from exc


def _normalize_immutable_verifier_checks(
    checks: list[dict[str, Any]],
    *,
    target_repository: Path,
    target_baseline: str,
    allowed_mutation_paths: set[str],
) -> list[dict[str, Any]]:
    if not checks:
        return []
    try:
        normalized = normalize_deterministic_acceptance_checks(checks, authorized_paths=allowed_mutation_paths)
    except DeterministicAcceptanceError as exc:
        raise ValueError(f"Invalid immutable verifier checks: {exc.code}") from exc

    verifier_paths: set[str] = set()
    for check in normalized:
        if check.get("type") != "command_succeeds":
            raise ValueError("Immutable verifier checks must use command_succeeds.")
        paths = _pytest_target_paths_from_command_check(check)
        if not paths:
            raise ValueError("Immutable verifier command_succeeds checks must name an explicit pytest verifier file.")
        for path in paths:
            if path in allowed_mutation_paths:
                raise ValueError(f"Immutable verifier path must not be in allowed mutation scope: {path}")
            if not _path_exists_in_git_commit(
                repository=target_repository,
                commit=target_baseline,
                path=path,
            ):
                raise ValueError(f"Immutable verifier path does not exist in target baseline: {path}")
            verifier_paths.add(path)
    if not verifier_paths:
        raise ValueError("At least one immutable verifier path is required when immutable verifier checks are supplied.")
    return normalized


def _immutable_verifier_paths(checks: list[dict[str, Any]]) -> list[str]:
    paths: set[str] = set()
    for check in checks:
        paths.update(_pytest_target_paths_from_command_check(check))
    return sorted(paths)


def _pytest_target_paths_from_command_check(check: dict[str, Any]) -> list[str]:
    argv = check.get("argv")
    if not isinstance(argv, list) or len(argv) < 3 or argv[1:3] != ["-m", "pytest"]:
        return []
    paths: list[str] = []
    for arg in argv[3:]:
        if not isinstance(arg, str):
            continue
        if arg.startswith("-"):
            continue
        path_part = arg.split("::", 1)[0]
        try:
            path = validate_relative_repo_path(path_part)
        except GitMutationError as exc:
            raise ValueError(f"Invalid immutable verifier pytest target: {arg}") from exc
        if path != path_part or not path.startswith("tests/") or not path.endswith(".py"):
            raise ValueError(f"Immutable verifier pytest target must be an explicit tests/*.py file: {arg}")
        paths.append(path)
    return list(dict.fromkeys(paths))


def _path_exists_in_git_commit(
    *,
    repository: Path,
    commit: str,
    path: str,
) -> bool:
    try:
        return _git(repository, "cat-file", "-t", f"{commit}:{path}") == "blob"
    except ValueError:
        return False


def _require_clean_worktree(worktree: Path, message: str) -> None:
    status = _git(worktree, "status", "--porcelain=v1", "--untracked-files=all")
    if status.strip():
        raise ValueError(f"{message}: {status}")


def _worktree_is_clean(worktree: Path) -> bool:
    return not _git(worktree, "status", "--porcelain=v1", "--untracked-files=all").strip()


def _provider_invocation_authorized(runtime_result: dict[str, Any] | None) -> bool | None:
    if runtime_result is None:
        return None
    return bool(runtime_result.get("provider_ids"))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or completed.stdout.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "model"
