from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.domain.enums import (
    Actor,
    AuthorityLevel,
    PersistentWorkflowState,
    RepositoryAccessMode,
)
from lucius.persistence.orm import (
    EngineeringPlanORM,
    PersistentWorkflowORM,
    PlanFreezeORM,
    ProjectORM,
    ProjectRepositoryAttachmentORM,
    RepositoryRegistrationORM,
    TaskContractORM,
    TaskORM,
    utc_now,
)
from lucius.persistence.repositories import next_id
from lucius.repositories.git_mutation import validate_relative_repo_path
from lucius.runtime.deterministic_acceptance import (
    DeterministicAcceptanceError,
    acceptance_check_paths,
    normalize_deterministic_acceptance_checks,
)


class ControlledMutationProvisioningError(ValueError):
    pass


def load_controlled_mutation_specification(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ControlledMutationProvisioningError(
            f"Unable to load controlled mutation specification: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise ControlledMutationProvisioningError(
            "Controlled mutation specification must be a JSON object."
        )
    return payload


def provision_controlled_mutation(
    session: Session,
    specification: dict[str, Any],
) -> list[dict[str, Any]]:
    try:
        _reject_duplicate_provisioning(
            session,
            specification.get("provisioning_id", ""),
        )
        result = _provision_controlled_mutation(session, specification)
        session.flush()
        return result
    except Exception:
        session.rollback()
        raise


def _provision_controlled_mutation(
    session: Session,
    specification: dict[str, Any],
) -> list[dict[str, Any]]:
    spec = _validate_specification(specification)

    provisioning_id = spec["provisioning_id"]
    _reject_duplicate_provisioning(session, provisioning_id)

    project_spec = spec["project"]
    repository_spec = spec["repository"]

    repository_path = Path(repository_spec["path"]).expanduser().resolve()
    workspace = Path(repository_spec["workspace"]).expanduser().resolve()
    allowed_root = Path(repository_spec["allowed_workspace_root"]).expanduser().resolve()

    _validate_repository_and_workspace(
        repository_path=repository_path,
        workspace=workspace,
        allowed_root=allowed_root,
        frozen_head=repository_spec["frozen_head"],
    )

    project = ProjectORM(
        id=next_id(session, "project"),
        name=project_spec["name"],
        slug=project_spec["slug"],
        organization=project_spec.get("organization"),
        project_type="ENGINEERING",
        status="ACTIVE",
        description=project_spec.get("description"),
        workspace_scope=str(workspace),
        documentation_policy={
            "canonical_target_documentation_required": True,
        },
        default_authority_level=AuthorityLevel.L1.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(project)
    session.flush()

    repository = RepositoryRegistrationORM(
        id=next_id(session, "repository"),
        project_id=project.id,
        name=repository_spec["name"],
        adapter_type="LOCAL_GIT",
        location=str(repository_path),
        canonical_remote=repository_spec.get("canonical_remote"),
        default_branch=repository_spec.get("default_branch", "main"),
        access_mode=RepositoryAccessMode.CANONICAL_INTEGRATION.value,
        status="ACTIVE",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(repository)
    session.flush()

    session.add(
        ProjectRepositoryAttachmentORM(
            project_id=project.id,
            repository_id=repository.id,
            attached_by=Actor.LUCIUS.value,
        )
    )
    session.flush()

    results: list[dict[str, Any]] = []

    for task_index, task_spec in enumerate(spec["tasks"], start=1):
        allowed_paths = _validated_paths(task_spec["allowed_mutation_paths"])
        checks = _validated_acceptance_checks(
            task_spec["deterministic_acceptance_checks"],
            allowed_paths,
        )

        task = TaskORM(
            id=next_id(session, "task"),
            project_id=project.id,
            title=task_spec["title"],
            objective=task_spec["objective"],
            priority=task_spec.get("priority", "HIGH"),
            complexity=task_spec.get("task_complexity", "T1"),
            authority_level=AuthorityLevel.L1.value,
            status="READY",
            created_by=Actor.LUCIUS.value,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(task)
        session.flush()

        contract = TaskContractORM(
            id=next_id(session, "task_contract"),
            task_id=task.id,
            version=1,
            objective=task.objective,
            acceptance_criteria=[
                {
                    "id": f"AC-{index}",
                    "description": criterion,
                }
                for index, criterion in enumerate(
                    task_spec["acceptance_criteria"],
                    start=1,
                )
            ],
            constraints=[
                "ONE_LOGICAL_DISPATCHER",
                "ISOLATED_WORKTREE",
                "NO_PUSH",
                "NO_MERGE",
                "NO_DEPLOY",
                "NO_LIVE_TRADING",
                "NO_EXTERNAL_MESSAGING",
                "NO_SPENDING",
                *task_spec.get("constraints", []),
            ],
            repository_ids=[repository.id],
            allowed_actions=[
                "READ_REPOSITORY",
                "READ_DOCUMENTATION",
                "RUN_TESTS",
                "CREATE_TEMP_FILES",
                "WRITE_SOURCE",
                "WRITE_TESTS",
                "WRITE_DOCUMENTATION",
            ],
            allowed_tools=task_spec.get("allowed_tools", []),
            environment="DEVELOPMENT",
            authority_level=AuthorityLevel.L1.value,
            dependencies=task_spec.get("dependencies", []),
            documentation_required=True,
            documentation_targets=task_spec.get(
                "documentation_targets",
                ["target-project canonical documentation"],
            ),
            stop_conditions=[
                "UNAUTHORIZED_PATH_REQUIRED",
                "BASELINE_DRIFT",
                "DETERMINISTIC_ACCEPTANCE_FAILURE",
                "WORKSPACE_POLICY_FAILURE",
                "AUTHORITY_ESCALATION_REQUIRED",
                *task_spec.get("stop_conditions", []),
            ],
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(contract)
        session.flush()

        affected_files = [
            {
                "path": path,
                "status": "NEW_PROPOSED",
                "evidence_ids": [],
            }
            for path in allowed_paths
        ]

        plan = EngineeringPlanORM(
            id=next_id(session, "engineering_plan"),
            task_id=task.id,
            run_id=None,
            project_id=project.id,
            task_contract_id=contract.id,
            task_contract_version=contract.version,
            version=1,
            status="READY",
            summary=f"Controlled mutation plan for {task.id}",
            objective=task.objective,
            risk_level="LOW",
            required_authority_level=AuthorityLevel.L1.value,
            repository_snapshot_ids=[],
            evidence_ids=[],
            memory_ids=[],
            model_execution_ids=[],
            assumptions=[],
            unknowns=[],
            open_questions=[],
            affected_components=task_spec.get(
                "affected_components",
                ["target_project"],
            ),
            affected_files=affected_files,
            steps=[
                {
                    "step_id": "STEP-1",
                    "sequence": 1,
                    "title": "Execute bounded controlled mutation",
                    "description": task.objective,
                    "affected_components": task_spec.get(
                        "affected_components",
                        ["target_project"],
                    ),
                    "expected_result": (
                        "Authorized target-project changes satisfy frozen "
                        "deterministic acceptance."
                    ),
                    "validation": (
                        "Lucius verifies the isolated candidate before any "
                        "canonical integration."
                    ),
                }
            ],
            acceptance_coverage=[
                {
                    "criterion_id": f"AC-{index}",
                    "status": "COVERED",
                    "step_ids": ["STEP-1"],
                    "evidence_ids": [],
                }
                for index, _ in enumerate(
                    task_spec["acceptance_criteria"],
                    start=1,
                )
            ],
            deterministic_acceptance_checks=checks,
            test_strategy=[
                {
                    "kind": "INTEGRATION",
                    "description": (
                        "Run deterministic target-project verification."
                    ),
                    "acceptance_criteria": [
                        f"AC-{index}"
                        for index, _ in enumerate(
                            task_spec["acceptance_criteria"],
                            start=1,
                        )
                    ],
                    "evidence_ids": [],
                }
            ],
            documentation_requirements=[
                {
                    "target": target,
                    "reason": (
                        "Controlled mutation may change target-project "
                        "behavior, interfaces, operation, or capability."
                    ),
                    "trigger": "retained target change",
                    "target_type": "canonical_target",
                    "exact_path_required": False,
                    "acceptable_paths": [],
                    "acceptable_categories": [
                        "canonical_project_documentation"
                    ],
                }
                for target in contract.documentation_targets
            ],
            rollback_considerations=[
                "Reject candidate and preserve canonical target on failure."
            ],
            orchestration_contract_required=False,
            orchestration_contract={},
            adversarial_probes=[],
            dependencies=[],
            risks=[],
            estimated_scope="bounded-controlled-mutation",
            confidence=1.0,
            validation_warnings=[],
            blockers=[],
            planner_version="controlled-mutation-provisioner-v1",
            created_by=Actor.LUCIUS.value,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(plan)
        session.flush()

        freeze = PlanFreezeORM(
            id=next_id(session, "plan_freeze"),
            plan_id=plan.id,
            task_id=task.id,
            project_id=project.id,
            repository_state_id=None,
            repository_snapshot_ids=[],
            evidence_ids=[],
            commit_sha=repository_spec["frozen_head"],
            planning_mode="CURRENT_STATE_PLANNING",
            evaluation_version="controlled-mutation-provisioner-v1",
            plan_payload={
                "affected_files": affected_files,
                "deterministic_acceptance_checks": checks,
            },
            frozen_at=utc_now(),
            frozen_by=Actor.LUCIUS.value,
        )
        session.add(freeze)
        session.flush()

        item_id = f"{provisioning_id}-task-{task_index}"

        queue_item = {
            "item_id": item_id,
            "provisioning_id": provisioning_id,
            "key": task_spec["key"],
            "title": task_spec["title"],
            "objective": task_spec["objective"],
            "state": "READY",
            "read_only": False,
            "mutation_allowed": True,
            "unattended": False,
            "unattended_execution": False,
            "execution_supervision": task_spec["execution_supervision"],
            "task_complexity": task_spec.get("task_complexity", "T1"),
            "task_risk": task_spec.get("task_risk", "LOW"),
            "isolation_mode": "ISOLATED_WORKTREE",
            "allowed_mutation_paths": allowed_paths,
            "deterministic_acceptance_checks": checks,
            "deterministic_verification": True,
            "required_capabilities": task_spec.get(
                "required_capabilities",
                ["code_mutation"],
            ),
            "context_paths": task_spec.get("context_paths", []),
        }

        workflow = PersistentWorkflowORM(
            id=next_id(session, "persistent_workflow"),
            project_id=project.id,
            repository_id=repository.id,
            repository_snapshot_id=None,
            task_id=task.id,
            plan_id=plan.id,
            plan_freeze_id=freeze.id,
            objective=task.objective,
            expected_main_head=repository_spec["frozen_head"],
            isolated_branch=task_spec.get(
                "isolated_branch",
                f"lucius/{provisioning_id}/{task_spec['key']}",
            ),
            worktree_path=str(workspace),
            workflow_state=PersistentWorkflowState.PLAN_READY.value,
            authority_tier=AuthorityLevel.L1.value,
            task_backlog=[queue_item],
            dependency_graph={},
            active_task_id=None,
            completed_task_ids=[],
            pending_task_ids=[item_id],
            decisions=[],
            deviations=[],
            repair_counters={},
            targeted_test_evidence=[],
            full_test_status={},
            checkpoint_history=[],
            pending_human_approvals=[],
            resume_requirements=[],
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(workflow)
        session.flush()

        results.append(
            {
                "provisioning_id": provisioning_id,
                "project_id": project.id,
                "repository_id": repository.id,
                "task_id": task.id,
                "task_contract_id": contract.id,
                "plan_id": plan.id,
                "plan_freeze_id": freeze.id,
                "workflow_id": workflow.id,
                "workspace": str(workspace),
                "frozen_head": repository_spec["frozen_head"],
                "allowed_mutation_paths": allowed_paths,
                "execution_supervision": task_spec["execution_supervision"],
            }
        )

    return results


def _validate_specification(specification: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(specification, dict):
        raise ControlledMutationProvisioningError(
            "Specification must be an object."
        )

    for field in ("provisioning_id", "project", "repository", "tasks"):
        if field not in specification:
            raise ControlledMutationProvisioningError(
                f"Specification requires {field}."
            )

    if not isinstance(specification["provisioning_id"], str) or not specification["provisioning_id"].strip():
        raise ControlledMutationProvisioningError(
            "provisioning_id must be non-empty."
        )

    if "LWORK_" in specification["provisioning_id"]:
        raise ControlledMutationProvisioningError(
            "Historical workflow identifiers cannot be reused."
        )

    project = specification["project"]
    if not isinstance(project, dict):
        raise ControlledMutationProvisioningError("project must be an object.")
    for field in ("name", "slug"):
        if not isinstance(project.get(field), str) or not project[field].strip():
            raise ControlledMutationProvisioningError(
                f"project.{field} is required."
            )

    repository = specification["repository"]
    if not isinstance(repository, dict):
        raise ControlledMutationProvisioningError(
            "repository must be an object."
        )
    for field in (
        "name",
        "path",
        "frozen_head",
        "workspace",
        "allowed_workspace_root",
    ):
        if not isinstance(repository.get(field), str) or not repository[field].strip():
            raise ControlledMutationProvisioningError(
                f"repository.{field} is explicitly required."
            )

    tasks = specification["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise ControlledMutationProvisioningError(
            "At least one task is required."
        )

    keys: set[str] = set()

    for task in tasks:
        if not isinstance(task, dict):
            raise ControlledMutationProvisioningError(
                "Each task must be an object."
            )

        for field in (
            "key",
            "title",
            "objective",
            "acceptance_criteria",
            "allowed_mutation_paths",
            "deterministic_acceptance_checks",
            "execution_supervision",
        ):
            if field not in task:
                raise ControlledMutationProvisioningError(
                    f"Each task must explicitly declare {field}."
                )

        key = task["key"]
        if not isinstance(key, str) or not key.strip():
            raise ControlledMutationProvisioningError(
                "Task key must be non-empty."
            )
        if key in keys:
            raise ControlledMutationProvisioningError(
                f"Duplicate task key: {key}"
            )
        keys.add(key)

        if task.get("read_only") is True:
            raise ControlledMutationProvisioningError(
                "Controlled mutation task cannot be read_only=true."
            )

        if task.get("mutation_allowed") is not True:
            raise ControlledMutationProvisioningError(
                "Controlled mutation task must explicitly set "
                "mutation_allowed=true."
            )

        if task.get("unattended") is True or task.get("unattended_execution") is True:
            raise ControlledMutationProvisioningError(
                "Controlled mutation provisioner v1 does not authorize "
                "unattended mutation."
            )

        if task["execution_supervision"] not in {
            "SUPERVISED",
            "HUMAN_APPROVED",
        }:
            raise ControlledMutationProvisioningError(
                "execution_supervision must be SUPERVISED or HUMAN_APPROVED."
            )

        if task.get("deterministic_verification") is not True:
            raise ControlledMutationProvisioningError(
                "Controlled mutation requires deterministic_verification=true."
            )

        criteria = task["acceptance_criteria"]
        if (
            not isinstance(criteria, list)
            or not criteria
            or not all(isinstance(item, str) and item.strip() for item in criteria)
        ):
            raise ControlledMutationProvisioningError(
                "Each task requires non-empty acceptance_criteria."
            )

    return specification


def _validate_repository_and_workspace(
    *,
    repository_path: Path,
    workspace: Path,
    allowed_root: Path,
    frozen_head: str,
) -> None:
    if not repository_path.is_dir():
        raise ControlledMutationProvisioningError(
            f"Repository does not exist: {repository_path}"
        )
    if not workspace.is_dir():
        raise ControlledMutationProvisioningError(
            f"Persistent isolated workspace does not exist: {workspace}"
        )

    try:
        workspace.relative_to(allowed_root)
    except ValueError as exc:
        raise ControlledMutationProvisioningError(
            "Persistent workspace is outside explicitly authorized root."
        ) from exc

    if workspace == repository_path:
        raise ControlledMutationProvisioningError(
            "Persistent workspace must be isolated from target repository."
        )

    repository_root = Path(
        _git(repository_path, "rev-parse", "--show-toplevel")
    ).resolve()
    workspace_root = Path(
        _git(workspace, "rev-parse", "--show-toplevel")
    ).resolve()

    if repository_root != repository_path:
        raise ControlledMutationProvisioningError(
            "Repository path is not Git repository root."
        )
    if workspace_root != workspace:
        raise ControlledMutationProvisioningError(
            "Persistent workspace is not isolated Git worktree root."
        )

    repository_head = _git(repository_path, "rev-parse", "HEAD")
    workspace_head = _git(workspace, "rev-parse", "HEAD")

    if repository_head != frozen_head:
        raise ControlledMutationProvisioningError(
            "Canonical repository HEAD does not match frozen_head."
        )
    if workspace_head != frozen_head:
        raise ControlledMutationProvisioningError(
            "Persistent workspace HEAD does not match frozen_head."
        )

    if _git(workspace, "status", "--porcelain", "--untracked-files=all"):
        raise ControlledMutationProvisioningError(
            "Persistent isolated workspace is dirty."
        )


def _validated_paths(raw_paths: Any) -> list[str]:
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ControlledMutationProvisioningError(
            "allowed_mutation_paths must be a non-empty list."
        )

    paths: list[str] = []
    for raw in raw_paths:
        try:
            path = validate_relative_repo_path(raw)
        except Exception as exc:
            raise ControlledMutationProvisioningError(
                f"Invalid allowed mutation path: {raw!r}"
            ) from exc
        if path in paths:
            raise ControlledMutationProvisioningError(
                f"Duplicate allowed mutation path: {path}"
            )
        paths.append(path)

    return paths


def _validated_acceptance_checks(
    raw_checks: Any,
    allowed_paths: list[str],
) -> list[dict[str, Any]]:
    try:
        checks = normalize_deterministic_acceptance_checks(
            raw_checks,
            authorized_paths=set(allowed_paths),
        )
        checked = set(
            acceptance_check_paths(
                checks,
                authorized_paths=set(allowed_paths),
            )
        )
    except DeterministicAcceptanceError as exc:
        raise ControlledMutationProvisioningError(
            f"Invalid deterministic acceptance checks: {exc.code}"
        ) from exc

    missing = sorted(set(allowed_paths) - checked)
    if missing:
        raise ControlledMutationProvisioningError(
            "Every allowed mutation path requires deterministic acceptance "
            f"coverage: {missing}"
        )

    return checks


def _reject_duplicate_provisioning(
    session: Session,
    provisioning_id: str,
) -> None:
    marker = f'"provisioning_id": "{provisioning_id}"'

    workflows = session.scalars(select(PersistentWorkflowORM)).all()
    for workflow in workflows:
        for item in workflow.task_backlog or []:
            if not isinstance(item, dict):
                continue
            if item.get("provisioning_id") == provisioning_id:
                raise ControlledMutationProvisioningError(
                    f"Provisioning id already exists: {provisioning_id}"
                )
            if marker in json.dumps(item, sort_keys=True):
                raise ControlledMutationProvisioningError(
                    f"Provisioning id already exists: {provisioning_id}"
                )


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ControlledMutationProvisioningError(
            f"Unable to inspect Git workspace: {path}"
        )
    return result.stdout.strip()
