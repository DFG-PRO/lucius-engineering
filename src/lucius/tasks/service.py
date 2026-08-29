from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import (
    AcceptanceCriterionStatus,
    Actor,
    AllowedAction,
    AuthorityLevel,
    BlockerCode,
    Environment,
    ProjectStatus,
    TaskComplexity,
    TaskPriority,
    TaskRunStatus,
    TaskStatus,
)
from lucius.persistence.orm import (
    DocumentationCompletionORM,
    ProjectORM,
    ProjectRepositoryAttachmentORM,
    RepositoryRegistrationORM,
    RepositorySnapshotORM,
    TaskContractORM,
    TaskORM,
    TaskRunORM,
    utc_now,
)
from lucius.persistence.repositories import next_id
from lucius.tasks.policy import validate_authority
from lucius.tasks.schemas import ContractValidationResult, StructuredBlocker

TERMINAL_STATUSES = {TaskStatus.COMPLETE.value, TaskStatus.CANCELLED.value}


class TaskService:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def create_task(
        self,
        *,
        project_id: str,
        title: str,
        objective: str,
        priority: TaskPriority = TaskPriority.NORMAL,
        complexity: TaskComplexity = TaskComplexity.T0,
        authority_level: AuthorityLevel = AuthorityLevel.L0,
        created_by: Actor = Actor.SYSTEM,
    ) -> TaskORM:
        project = self._project(project_id)
        if project.status != ProjectStatus.ACTIVE.value:
            raise ValueError("Project must be ACTIVE to receive new active work")
        task = TaskORM(
            id=next_id(self.session, "task"),
            project_id=project.id,
            title=title,
            objective=objective,
            priority=priority.value,
            complexity=complexity.value,
            authority_level=authority_level.value,
            status=TaskStatus.DRAFT.value,
            created_by=created_by.value,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(task)
        self.session.flush()
        self.audit.record(
            event_type="TASK_CREATED",
            actor=created_by.value,
            project_id=project.id,
            task_id=task.id,
            action="create_task",
            result="SUCCESS",
            metadata={"title": title, "authority_level": task.authority_level},
        )
        return task

    def create_or_update_contract(
        self,
        *,
        task_id: str,
        objective: str,
        acceptance_criteria: list[str | dict[str, Any]],
        constraints: list[str] | None = None,
        repository_ids: list[str] | None = None,
        allowed_actions: list[AllowedAction | str] | None = None,
        allowed_tools: list[str] | None = None,
        environment: Environment = Environment.SANDBOX,
        authority_level: AuthorityLevel = AuthorityLevel.L0,
        dependencies: list[str] | None = None,
        documentation_required: bool = False,
        documentation_targets: list[str] | None = None,
        stop_conditions: list[str] | None = None,
        actor: Actor = Actor.SYSTEM,
    ) -> TaskContractORM:
        task = self._task(task_id)
        latest = self._active_contract(task.id)
        event_type = "TASK_CONTRACT_UPDATED" if latest else "TASK_CONTRACT_CREATED"
        if latest is None:
            version = 1
            contract = TaskContractORM(
                id=next_id(self.session, "task_contract"),
                task_id=task.id,
                version=version,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            self.session.add(contract)
        else:
            contract = latest
        contract.objective = objective
        contract.acceptance_criteria = _normalize_acceptance_criteria(acceptance_criteria)
        contract.constraints = constraints or []
        contract.repository_ids = repository_ids or []
        contract.allowed_actions = [_coerce_allowed_action(action).value for action in (allowed_actions or [])]
        contract.allowed_tools = allowed_tools or []
        contract.environment = environment.value
        contract.authority_level = authority_level.value
        contract.dependencies = dependencies or []
        contract.documentation_required = documentation_required
        contract.documentation_targets = documentation_targets or []
        contract.stop_conditions = stop_conditions or []
        contract.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type=event_type,
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            action="create_or_update_contract",
            result="SUCCESS",
            metadata={"contract_id": contract.id, "version": contract.version},
        )
        return contract

    def validate_contract(self, task_id: str) -> ContractValidationResult:
        task = self._task(task_id)
        blockers: list[StructuredBlocker] = []
        project = self.session.get(ProjectORM, task.project_id)
        contract = self._active_contract(task.id)
        if project is None:
            blockers.append(StructuredBlocker(code=BlockerCode.INVALID_CONTRACT, message="Task project does not exist."))
        elif project.status != ProjectStatus.ACTIVE.value:
            blockers.append(
                StructuredBlocker(
                    code=BlockerCode.PROJECT_INACTIVE,
                    message="Project must be ACTIVE before a Task can become READY.",
                    metadata={"project_status": project.status},
                )
            )
        if contract is None:
            blockers.append(StructuredBlocker(code=BlockerCode.INVALID_CONTRACT, message="Task has no active contract."))
            return ContractValidationResult(valid=False, blockers=blockers)
        if not contract.objective.strip():
            blockers.append(StructuredBlocker(code=BlockerCode.INVALID_CONTRACT, message="TaskContract objective is blank."))
        if not contract.acceptance_criteria:
            blockers.append(
                StructuredBlocker(code=BlockerCode.INVALID_CONTRACT, message="TaskContract requires acceptance criteria.")
            )
        if contract.documentation_required and not contract.documentation_targets:
            blockers.append(
                StructuredBlocker(
                    code=BlockerCode.DOCUMENTATION_REQUIRED,
                    message="Documentation targets are required when documentation_required is true.",
                )
            )
        blockers.extend(self._validate_repositories(task, contract))
        blockers.extend(self._validate_dependencies(task, contract))
        blockers.extend(
            validate_authority(
                task_authority=AuthorityLevel(task.authority_level),
                contract_authority=AuthorityLevel(contract.authority_level),
                actions=[AllowedAction(action) for action in contract.allowed_actions],
                environment=Environment(contract.environment),
            )
        )
        return ContractValidationResult(valid=not blockers, blockers=blockers)

    def mark_ready(self, task_id: str, *, actor: Actor = Actor.SYSTEM) -> ContractValidationResult:
        task = self._task(task_id)
        self._ensure_transition(task, {TaskStatus.DRAFT, TaskStatus.BLOCKED, TaskStatus.FAILED})
        result = self.validate_contract(task.id)
        if not result.valid:
            self._apply_blocker(task, result.blockers[0])
            self.audit.record(
                event_type="POLICY_BLOCK",
                actor=actor.value,
                project_id=task.project_id,
                task_id=task.id,
                action="mark_ready",
                result="BLOCKED",
                metadata={"blockers": [blocker.model_dump(mode="json") for blocker in result.blockers]},
            )
            self.audit.record(
                event_type="TASK_BLOCKED",
                actor=actor.value,
                project_id=task.project_id,
                task_id=task.id,
                action="mark_ready",
                result="BLOCKED",
                metadata={"blocker_code": task.blocker_code},
            )
            return result
        task.status = TaskStatus.READY.value
        task.blocker_code = None
        task.blocker_message = None
        task.blocked_at = None
        task.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="TASK_READY",
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            action="mark_ready",
            result="SUCCESS",
        )
        return result

    def start_run(
        self,
        task_id: str,
        *,
        snapshot_id: str | None = None,
        actor: Actor = Actor.SYSTEM,
        model_provider: str | None = None,
        model_name: str | None = None,
        input_summary: str | None = None,
    ) -> TaskRunORM:
        task = self._task(task_id)
        if task.status != TaskStatus.READY.value:
            raise ValueError("Task must be READY before starting a run")
        if snapshot_id is not None:
            self._validate_snapshot_binding(task, snapshot_id)
        run = TaskRunORM(
            id=next_id(self.session, "task_run"),
            task_id=task.id,
            snapshot_id=snapshot_id,
            status=TaskRunStatus.RUNNING.value,
            actor=actor.value,
            model_provider=model_provider,
            model_name=model_name,
            started_at=utc_now(),
            input_summary=input_summary,
        )
        self.session.add(run)
        task.status = TaskStatus.RUNNING.value
        task.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="TASK_RUN_STARTED",
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            run_id=run.id,
            action="start_run",
            result="SUCCESS",
            metadata={"snapshot_id": snapshot_id},
        )
        return run

    def block_task(
        self,
        task_id: str,
        *,
        blocker_code: BlockerCode,
        blocker_message: str,
        actor: Actor = Actor.SYSTEM,
    ) -> TaskORM:
        task = self._task(task_id)
        self._ensure_transition(task, {TaskStatus.DRAFT, TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.BLOCKED})
        self._apply_blocker(task, StructuredBlocker(code=blocker_code, message=blocker_message))
        self.audit.record(
            event_type="TASK_BLOCKED",
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            action="block_task",
            result="BLOCKED",
            metadata={"blocker_code": task.blocker_code},
        )
        return task

    def fail_task(
        self,
        task_id: str,
        *,
        failure_reason: str,
        run_id: str | None = None,
        actor: Actor = Actor.SYSTEM,
    ) -> TaskORM:
        task = self._task(task_id)
        self._ensure_transition(task, {TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.READY})
        task.status = TaskStatus.FAILED.value
        task.failure_reason = failure_reason
        task.updated_at = utc_now()
        if run_id:
            run = self.session.get(TaskRunORM, run_id)
            if run:
                run.status = TaskRunStatus.FAILED.value
                run.finished_at = utc_now()
                run.failure_reason = failure_reason
        self.session.flush()
        self.audit.record(
            event_type="TASK_FAILED",
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            run_id=run_id,
            action="fail_task",
            result="FAILED",
            metadata={"failure_reason": failure_reason},
        )
        return task

    def mark_implementation_complete(
        self,
        task_id: str,
        *,
        run_id: str | None = None,
        output_summary: str | None = None,
        actor: Actor = Actor.SYSTEM,
    ) -> TaskORM:
        task = self._task(task_id)
        self._ensure_transition(task, {TaskStatus.RUNNING})
        task.status = TaskStatus.IMPLEMENTATION_COMPLETE.value
        task.updated_at = utc_now()
        if run_id:
            run = self.session.get(TaskRunORM, run_id)
            if run:
                run.status = TaskRunStatus.SUCCEEDED.value
                run.finished_at = utc_now()
                run.output_summary = output_summary
        self.session.flush()
        self.audit.record(
            event_type="TASK_IMPLEMENTATION_COMPLETE",
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            run_id=run_id,
            action="mark_implementation_complete",
            result="SUCCESS",
        )
        return task

    def mark_documentation_pending(self, task_id: str, *, actor: Actor = Actor.SYSTEM) -> TaskORM:
        task = self._task(task_id)
        self._ensure_transition(task, {TaskStatus.IMPLEMENTATION_COMPLETE})
        task.status = TaskStatus.DOCUMENTATION_PENDING.value
        task.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="TASK_DOCUMENTATION_PENDING",
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            action="mark_documentation_pending",
            result="SUCCESS",
        )
        return task

    def record_documentation_completion(
        self,
        task_id: str,
        *,
        targets_completed: list[str],
        evidence_references: list[str],
        completed_by: Actor,
    ) -> DocumentationCompletionORM:
        task = self._task(task_id)
        existing = self.session.scalar(
            select(DocumentationCompletionORM).where(DocumentationCompletionORM.task_id == task.id)
        )
        if existing is None:
            existing = DocumentationCompletionORM(
                id=next_id(self.session, "documentation_completion"),
                task_id=task.id,
            )
            self.session.add(existing)
        existing.targets_completed = targets_completed
        existing.evidence_references = evidence_references
        existing.completed_at = utc_now()
        existing.completed_by = completed_by.value
        self.session.flush()
        return existing

    def complete_task(self, task_id: str, *, actor: Actor = Actor.SYSTEM) -> ContractValidationResult:
        task = self._task(task_id)
        self._ensure_transition(task, {TaskStatus.IMPLEMENTATION_COMPLETE, TaskStatus.DOCUMENTATION_PENDING})
        contract = self._active_contract(task.id)
        if contract and contract.documentation_required:
            completion = self.session.scalar(
                select(DocumentationCompletionORM).where(DocumentationCompletionORM.task_id == task.id)
            )
            if task.status != TaskStatus.DOCUMENTATION_PENDING.value or completion is None or not completion.evidence_references:
                blocker = StructuredBlocker(
                    code=BlockerCode.DOCUMENTATION_REQUIRED,
                    message="Documentation evidence is required before Task completion.",
                )
                self.audit.record(
                    event_type="POLICY_BLOCK",
                    actor=actor.value,
                    project_id=task.project_id,
                    task_id=task.id,
                    action="complete_task",
                    result="BLOCKED",
                    metadata={"blocker": blocker.model_dump(mode="json")},
                )
                return ContractValidationResult(valid=False, blockers=[blocker])
            missing_targets = sorted(set(contract.documentation_targets) - set(completion.targets_completed))
            if missing_targets:
                blocker = StructuredBlocker(
                    code=BlockerCode.DOCUMENTATION_REQUIRED,
                    message="Documentation targets are incomplete.",
                    metadata={"missing_targets": missing_targets},
                )
                return ContractValidationResult(valid=False, blockers=[blocker])
        task.status = TaskStatus.COMPLETE.value
        task.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="TASK_COMPLETE",
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            action="complete_task",
            result="SUCCESS",
        )
        return ContractValidationResult(valid=True)

    def cancel_task(self, task_id: str, *, actor: Actor = Actor.SYSTEM) -> TaskORM:
        task = self._task(task_id)
        self._ensure_transition(task, {TaskStatus.DRAFT, TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.BLOCKED})
        task.status = TaskStatus.CANCELLED.value
        task.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="TASK_CANCELLED",
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            action="cancel_task",
            result="SUCCESS",
        )
        return task

    def _validate_repositories(self, task: TaskORM, contract: TaskContractORM) -> list[StructuredBlocker]:
        blockers: list[StructuredBlocker] = []
        for repository_id in contract.repository_ids:
            registration = self.session.get(RepositoryRegistrationORM, repository_id)
            attachment = self.session.scalar(
                select(ProjectRepositoryAttachmentORM).where(
                    ProjectRepositoryAttachmentORM.project_id == task.project_id,
                    ProjectRepositoryAttachmentORM.repository_id == repository_id,
                )
            )
            if registration is None or attachment is None:
                blockers.append(
                    StructuredBlocker(
                        code=BlockerCode.MISSING_REPOSITORY,
                        message="TaskContract repository is not attached to the Task project.",
                        metadata={"repository_id": repository_id},
                    )
                )
        return blockers

    def _validate_dependencies(self, task: TaskORM, contract: TaskContractORM) -> list[StructuredBlocker]:
        blockers: list[StructuredBlocker] = []
        for dependency_id in contract.dependencies:
            dependency = self.session.get(TaskORM, dependency_id)
            if dependency_id == task.id:
                blockers.append(
                    StructuredBlocker(code=BlockerCode.DEPENDENCY_BLOCKED, message="Task cannot depend on itself.")
                )
                continue
            if dependency is None:
                blockers.append(
                    StructuredBlocker(
                        code=BlockerCode.DEPENDENCY_BLOCKED,
                        message="Task dependency does not exist.",
                        metadata={"dependency_id": dependency_id},
                    )
                )
                continue
            dependency_contract = self._active_contract(dependency.id)
            if dependency_contract and task.id in dependency_contract.dependencies:
                blockers.append(
                    StructuredBlocker(
                        code=BlockerCode.DEPENDENCY_BLOCKED,
                        message="Direct task dependency cycle detected.",
                        metadata={"dependency_id": dependency_id},
                    )
                )
            if dependency.status != TaskStatus.COMPLETE.value:
                blockers.append(
                    StructuredBlocker(
                        code=BlockerCode.DEPENDENCY_BLOCKED,
                        message="Task dependency is not COMPLETE.",
                        metadata={"dependency_id": dependency_id, "dependency_status": dependency.status},
                    )
                )
        return blockers

    def _validate_snapshot_binding(self, task: TaskORM, snapshot_id: str) -> None:
        snapshot = self.session.get(RepositorySnapshotORM, snapshot_id)
        if snapshot is None:
            raise ValueError(f"Unknown repository snapshot: {snapshot_id}")
        attachment = self.session.scalar(
            select(ProjectRepositoryAttachmentORM).where(
                ProjectRepositoryAttachmentORM.project_id == task.project_id,
                ProjectRepositoryAttachmentORM.repository_id == snapshot.repository_id,
            )
        )
        if attachment is None:
            raise ValueError("RepositorySnapshot does not belong to a repository attached to the Task project")

    def _project(self, project_id: str) -> ProjectORM:
        project = self.session.get(ProjectORM, project_id)
        if project is None:
            raise ValueError(f"Unknown project: {project_id}")
        return project

    def _task(self, task_id: str) -> TaskORM:
        task = self.session.get(TaskORM, task_id)
        if task is None:
            raise ValueError(f"Unknown task: {task_id}")
        return task

    def _active_contract(self, task_id: str) -> TaskContractORM | None:
        return self.session.scalar(
            select(TaskContractORM)
            .where(TaskContractORM.task_id == task_id)
            .order_by(TaskContractORM.version.desc())
            .limit(1)
        )

    def _ensure_not_terminal(self, task: TaskORM) -> None:
        if task.status in TERMINAL_STATUSES:
            raise ValueError("Task is terminal for Phase 1.5")

    def _ensure_transition(self, task: TaskORM, allowed_from: set[TaskStatus]) -> None:
        self._ensure_not_terminal(task)
        if TaskStatus(task.status) not in allowed_from:
            raise ValueError(f"Invalid Task transition from {task.status}")

    def _apply_blocker(self, task: TaskORM, blocker: StructuredBlocker) -> None:
        task.status = TaskStatus.BLOCKED.value
        task.blocker_code = blocker.code.value
        task.blocker_message = blocker.message
        task.blocked_at = utc_now()
        task.updated_at = utc_now()
        self.session.flush()


def _normalize_acceptance_criteria(criteria: list[str | dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(criteria, start=1):
        if isinstance(item, str):
            statement = item.strip()
            status = AcceptanceCriterionStatus.PENDING.value
            key = f"AC-{index:03d}"
        else:
            statement = str(item.get("statement", "")).strip()
            status = AcceptanceCriterionStatus(item.get("status", AcceptanceCriterionStatus.PENDING.value)).value
            key = str(item.get("id") or item.get("key") or f"AC-{index:03d}")
        if statement:
            normalized.append({"id": key, "statement": statement, "status": status})
    return normalized


def _coerce_allowed_action(action: AllowedAction | str) -> AllowedAction:
    return action if isinstance(action, AllowedAction) else AllowedAction(action)
