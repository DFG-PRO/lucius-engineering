from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

from lucius.domain.enums import (
    Actor,
    AllowedAction,
    AuthorityLevel,
    Environment,
    PersistentWorkflowState,
    ProjectStatus,
    ProjectType,
    QueueWorkItemState,
    RepositoryAccessMode,
    RepositoryAdapterType,
    TaskComplexity,
    TaskPriority,
    TaskStatus,
)
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import (
    AuditEventORM,
    PersistentWorkflowORM,
    PlanFreezeORM,
    ProjectORM,
    ProjectRepositoryAttachmentORM,
    RepositoryRegistrationORM,
    TaskContractORM,
    TaskORM,
    utc_now,
)
from lucius.pilots.workflows import PersistentWorkflowService
from lucius.projects.service import ProjectRegistryService
from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
from lucius.runtime.schemas import ExecutionAdapterResult, RuntimeExecutionContext, RuntimeLoopConfig
from lucius.runtime.service import ExecutionRuntimeLoopService
from lucius.tasks.service import TaskService


def test_runtime_loop_plans_freezes_executes_and_continues_between_tasks(session):
    workflow = _workflow(
        session,
        project_id="DARWIN",
        backlog=[_item("DARWIN-A", order=1), _item("DARWIN-B", order=2)],
    )

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=ScriptedExecutionAdapter(
            {
                "DARWIN-A": ExecutionAdapterResult(
                    outcome="COMPLETED",
                    completed_substeps=["added small capability", "ran focused tests"],
                    evidence=[{"artifact": "target diff"}],
                    verification=[{"command": "pytest tests/focused", "result": "PASS"}],
                    documentation=[{"target": "Darwin canonical docs", "result": "UPDATED"}],
                    active_execution_seconds=2.5,
                    external_capacity_wait_seconds=0.25,
                ),
                "DARWIN-B": ExecutionAdapterResult(
                    outcome="COMPLETED",
                    completed_substeps=["updated regression coverage"],
                    evidence=[{"artifact": "test diff"}],
                    verification=[{"command": "pytest", "result": "PASS"}],
                    active_execution_seconds=1.5,
                    human_wait_seconds=0.1,
                ),
            }
        ),
    ).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=2))

    row = session.get(PersistentWorkflowORM, workflow.id)
    freeze = session.get(PlanFreezeORM, row.plan_freeze_id)
    assert result.status == "COMPLETED"
    assert result.selected_tasks == 2
    assert result.completed_tasks == 2
    assert result.provider_ids == ["scripted-execution-adapter"]
    assert result.plan_references[0].created_by_runtime is True
    assert result.active_execution_time_seconds == 4.0
    assert result.external_capacity_wait_time_seconds == 0.25
    assert result.human_wait_time_seconds == 0.1
    assert result.single_dispatcher_enforced is True
    assert row.workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value
    assert [item["state"] for item in row.task_backlog] == ["COMPLETED", "COMPLETED"]
    assert session.get(TaskORM, workflow.task_id).status == TaskStatus.COMPLETE.value
    assert freeze.plan_payload["summary"].startswith("Runtime plan")


def test_runtime_blocks_one_item_and_releases_capacity_for_next_workflow(session):
    blocked = _workflow(session, project_id="DARWIN", backlog=[_item("DARWIN-BLOCKED", priority="HIGH")])
    alternative = _workflow(session, project_id="DARWIN", backlog=[_item("DARWIN-ALT", priority="NORMAL")])

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=ScriptedExecutionAdapter(
            {
                "DARWIN-BLOCKED": ExecutionAdapterResult(
                    outcome="BLOCKED",
                    completed_substeps=["captured partial evidence"],
                    evidence=[{"artifact": "partial-runtime-evidence"}],
                    blocking_reason="External fixture unavailable",
                    blocker_category="EXTERNAL_DEPENDENCY",
                    resume_condition="Fixture becomes available.",
                    external_capacity_wait_seconds=3.0,
                    blocked_task_seconds=3.0,
                )
            }
        ),
    ).run(RuntimeLoopConfig(workflow_ids=[blocked.id, alternative.id], max_tasks=2))

    blocked_row = session.get(PersistentWorkflowORM, blocked.id)
    alternative_row = session.get(PersistentWorkflowORM, alternative.id)
    assert result.status == "COMPLETED"
    assert result.selected_tasks == 2
    assert result.blocked_tasks == 1
    assert result.completed_tasks == 1
    assert result.blocked_task_time_seconds == 3.0
    assert blocked_row.task_backlog[0]["state"] == QueueWorkItemState.WAITING_EXTERNAL.value
    assert blocked_row.task_backlog[0]["completed_substeps"] == ["captured partial evidence"]
    assert session.get(TaskORM, blocked.task_id).status == TaskStatus.READY.value
    assert alternative_row.task_backlog[0]["state"] == QueueWorkItemState.COMPLETED.value
    assert session.get(TaskORM, alternative.task_id).status == TaskStatus.COMPLETE.value


def test_runtime_counts_ready_to_resume_without_repeating_completed_substeps(session):
    workflow = _workflow(
        session,
        project_id="DARWIN",
        backlog=[
            _item("DARWIN-RESUME", state=QueueWorkItemState.READY_TO_RESUME)
            | {
                "completed_substeps": ["already inspected repo"],
                "current_block_checkpoint_id": "LQCHK_SYNTHETIC",
                "block_checkpoints": [{"id": "LQCHK_SYNTHETIC", "item_version": 1}],
            }
        ],
    )

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=ScriptedExecutionAdapter(
            {
                "DARWIN-RESUME": ExecutionAdapterResult(
                    outcome="COMPLETED",
                    completed_substeps=["already inspected repo", "finished resumed work"],
                    active_execution_seconds=0.5,
                )
            }
        ),
    ).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    row = session.get(PersistentWorkflowORM, workflow.id)
    assert result.resumed_tasks == 1
    assert row.workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value
    assert row.task_backlog[0]["completed_substeps"] == ["already inspected repo", "finished resumed work"]
    assert session.get(TaskORM, workflow.task_id).status == TaskStatus.COMPLETE.value


def test_runtime_execution_backend_is_replaceable(session):
    workflow = _workflow(session, project_id="DARWIN", backlog=[_item("DARWIN-CUSTOM")])
    adapter = CustomExecutionAdapter()

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=adapter,
    ).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    assert adapter.contexts[0].item_id == "DARWIN-CUSTOM"
    assert result.provider_ids == ["custom-test-adapter"]
    assert result.completed_tasks == 1


def test_runtime_blocks_fail_closed_when_execution_adapter_raises(session):
    workflow = _workflow(session, project_id="DARWIN", backlog=[_item("DARWIN-RAISES")])

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=RaisingExecutionAdapter(),
    ).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    row = session.get(PersistentWorkflowORM, workflow.id)
    assert result.status == "FAILED"
    assert result.blocked_tasks == 1
    assert result.completed_tasks == 0
    assert result.stopped_reason == "Execution adapter raised before returning a canonical result."
    assert row.active_task_id is None
    assert row.task_backlog[0]["state"] == QueueWorkItemState.WAITING_HUMAN.value
    assert row.task_backlog[0]["blocker_category"] == "EXECUTION_ADAPTER_EXCEPTION"
    assert row.task_backlog[0]["current_block_checkpoint_id"]


def test_runtime_plan_and_result_survive_fresh_session(tmp_path: Path):
    database = tmp_path / "native-runtime.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    Session = make_session_factory(engine)

    with Session() as session:
        workflow = _workflow(session, project_id="DARWIN", backlog=[_item("DARWIN-PERSIST")])
        workflow_id = workflow.id
        result = ExecutionRuntimeLoopService(
            session,
            planning_adapter=ScriptedRuntimePlanningAdapter(),
            execution_adapter=ScriptedExecutionAdapter(),
        ).run(RuntimeLoopConfig(workflow_ids=[workflow_id], max_tasks=1))
        freeze_id = result.plan_references[0].plan_freeze_id
        session.commit()

    with Session() as session:
        row = session.get(PersistentWorkflowORM, workflow_id)
        assert row.task_backlog[0]["state"] == QueueWorkItemState.COMPLETED.value
        assert row.workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value
        assert session.get(TaskORM, row.task_id).status == TaskStatus.COMPLETE.value
        assert row.plan_freeze_id == freeze_id
        assert session.get(PlanFreezeORM, freeze_id).plan_payload["task_id"] == row.task_id
        assert (
            session.query(AuditEventORM)
            .filter(AuditEventORM.event_type == "NATIVE_RUNTIME_LOOP_COMPLETED")
            .count()
            == 1
        )


def test_runtime_cli_entrypoint_runs_bounded_loop(tmp_path: Path):
    database = tmp_path / "native-runtime-cli.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    Session = make_session_factory(engine)

    with Session() as session:
        workflow = _workflow(session, project_id="DARWIN", backlog=[_item("DARWIN-CLI")])
        workflow_id = workflow.id
        session.commit()

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "lucius.pilots.cli",
            "--database",
            str(database),
            "run-runtime-loop",
            workflow_id,
            "--max-tasks",
            "1",
        ],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert '"completed_tasks": 1' in completed.stdout


def test_runtime_rejects_multi_dispatcher_configuration(session):
    workflow = _workflow(session, project_id="DARWIN", backlog=[_item("DARWIN-SINGLE-DISPATCHER")])

    try:
        RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1, dispatcher_count=2)
    except ValueError as error:
        assert "exactly one logical dispatcher" in str(error)
    else:
        raise AssertionError("multi-dispatcher runtime configuration should be rejected")


def test_runtime_missing_repository_blocks_before_provider_dispatch(session):
    workflow = _workflow(
        session,
        project_id="DARWIN",
        backlog=[_item("DARWIN-MISSING-REPO")],
        attach_repository=False,
    )
    adapter = CountingExecutionAdapter()

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=adapter,
    ).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    row = session.get(PersistentWorkflowORM, workflow.id)
    assert result.status == "FAILED"
    assert result.blocked_tasks == 1
    assert result.completed_tasks == 0
    assert adapter.calls == []
    assert result.provider_ids == []
    assert row.task_backlog[0]["state"] == QueueWorkItemState.WAITING_HUMAN.value
    assert "not READY" in result.stopped_reason


def test_runtime_repository_attachment_without_rereadiness_still_blocks_dispatch(session):
    workflow = _workflow(
        session,
        project_id="DARWIN",
        backlog=[_item("DARWIN-ATTACH-NO-REREADY")],
        attach_repository=False,
    )
    ProjectRegistryService(session).attach_repository(
        project_id=workflow.project_id,
        repository_id=workflow.repository_id,
        actor=Actor.LUCIUS,
    )
    adapter = CountingExecutionAdapter()

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=adapter,
    ).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    assert result.status == "FAILED"
    assert adapter.calls == []
    assert "not READY" in result.stopped_reason


def test_runtime_explicit_rereadiness_after_repository_attachment_allows_dispatch(session):
    workflow = _workflow(
        session,
        project_id="DARWIN",
        backlog=[_item("DARWIN-REREADY")],
        attach_repository=False,
    )
    ProjectRegistryService(session).attach_repository(
        project_id=workflow.project_id,
        repository_id=workflow.repository_id,
        actor=Actor.LUCIUS,
    )
    ready = TaskService(session).mark_ready(workflow.task_id, actor=Actor.LUCIUS)
    adapter = CountingExecutionAdapter()

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=adapter,
    ).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    assert ready.valid is True
    assert result.status == "COMPLETED"
    assert result.completed_tasks == 1
    assert adapter.calls == ["DARWIN-REREADY"]


def test_runtime_stale_readiness_after_contract_change_blocks_dispatch(session):
    workflow = _workflow(session, project_id="DARWIN", backlog=[_item("DARWIN-STALE-READY")])
    task = session.get(TaskORM, workflow.task_id)
    contract = session.query(TaskContractORM).filter(TaskContractORM.task_id == task.id).one()
    TaskService(session).create_or_update_contract(
        task_id=task.id,
        objective=contract.objective,
        acceptance_criteria=contract.acceptance_criteria,
        constraints=contract.constraints,
        repository_ids=contract.repository_ids,
        allowed_actions=contract.allowed_actions,
        environment=Environment(contract.environment),
        authority_level=AuthorityLevel(contract.authority_level),
        documentation_required=contract.documentation_required,
        documentation_targets=contract.documentation_targets,
        actor=Actor.LUCIUS,
    )
    adapter = CountingExecutionAdapter()

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=adapter,
    ).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    assert result.status == "FAILED"
    assert adapter.calls == []
    assert "readiness is stale" in result.stopped_reason


def test_runtime_selected_task_identity_mismatch_blocks_before_provider_dispatch(session):
    workflow = _workflow(
        session,
        project_id="DARWIN",
        backlog=[_item("DARWIN-MISMATCH") | {"mutates_item_id": "DARWIN-OTHER"}],
    )
    adapter = CountingExecutionAdapter()

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=adapter,
    ).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    assert result.status == "FAILED"
    assert adapter.calls == []
    assert "mutation identity does not match" in result.stopped_reason


def test_runtime_resume_blocked_interval_uses_canonical_queue_timestamps(session):
    workflow = _workflow(
        session,
        project_id="DARWIN",
        backlog=[
            _item("DARWIN-TIMED-RESUME", state=QueueWorkItemState.READY_TO_RESUME)
            | {
                "blocked_at": "2026-09-08T10:00:00+00:00",
                "resolved_at": "2026-09-08T10:02:30+00:00",
                "current_block_checkpoint_id": "LQCHK_TIMED",
                "block_checkpoints": [{"id": "LQCHK_TIMED", "item_version": 1}],
            }
        ],
    )

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=ScriptedExecutionAdapter(),
    ).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    assert result.status == "COMPLETED"
    assert result.resumed_tasks == 1
    assert result.blocked_task_time_seconds == 150.0


def test_runtime_partial_workflow_does_not_normalize_parent_task_early(session):
    workflow = _workflow(
        session,
        project_id="DARWIN",
        backlog=[_item("DARWIN-PARTIAL-A", order=1), _item("DARWIN-PARTIAL-B", order=2)],
    )

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=ScriptedExecutionAdapter(),
    ).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    row = session.get(PersistentWorkflowORM, workflow.id)
    assert result.completed_tasks == 1
    assert [item["state"] for item in row.task_backlog] == ["COMPLETED", "READY"]
    assert row.workflow_state == PersistentWorkflowState.PLAN_READY.value
    assert session.get(TaskORM, workflow.task_id).status == TaskStatus.READY.value


def test_runtime_completed_workflow_reconciliation_is_idempotent(session):
    workflow = _workflow(session, project_id="DARWIN", backlog=[_item("DARWIN-IDEMPOTENT")])
    service = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=ScriptedExecutionAdapter(),
    )
    first = service.run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))
    audit_count = (
        session.query(AuditEventORM)
        .filter(AuditEventORM.event_type == "NATIVE_RUNTIME_WORKFLOW_PARENT_TASK_RECONCILED")
        .count()
    )

    second = service.run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    row = session.get(PersistentWorkflowORM, workflow.id)
    assert first.status == "COMPLETED"
    assert second.status == "IDLE"
    assert session.get(TaskORM, workflow.task_id).status == TaskStatus.COMPLETE.value
    assert row.workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value
    assert (
        session.query(AuditEventORM)
        .filter(AuditEventORM.event_type == "NATIVE_RUNTIME_WORKFLOW_PARENT_TASK_RECONCILED")
        .count()
        == audit_count
    )


def test_runtime_startup_reconciles_preexisting_completed_workflow_parent_task(session):
    workflow = _workflow(session, project_id="DARWIN", backlog=[_item("DARWIN-STALE-COMPLETE")])
    row = session.get(PersistentWorkflowORM, workflow.id)
    item = dict(row.task_backlog[0])
    item["state"] = QueueWorkItemState.COMPLETED.value
    item["completed_at"] = "2026-09-08T10:00:00+00:00"
    row.task_backlog = [item]
    row.completed_task_ids = ["DARWIN-STALE-COMPLETE"]
    row.pending_task_ids = []
    row.workflow_state = PersistentWorkflowState.PLAN_READY.value
    session.flush()

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=ScriptedExecutionAdapter(),
    ).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    row = session.get(PersistentWorkflowORM, workflow.id)
    assert result.status == "IDLE"
    assert result.completed_tasks == 0
    assert row.workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value
    assert session.get(TaskORM, workflow.task_id).status == TaskStatus.COMPLETE.value
    assert result.plan_references == []


def test_runtime_completed_workflow_with_missing_required_docs_becomes_documentation_pending(session):
    workflow = _workflow(
        session,
        project_id="DARWIN",
        backlog=[_item("DARWIN-DOCS-PENDING")],
        documentation_required=True,
    )

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=ScriptedExecutionAdapter(
            {
                "DARWIN-DOCS-PENDING": ExecutionAdapterResult(
                    outcome="COMPLETED",
                    completed_substeps=["completed implementation without canonical documentation evidence"],
                    active_execution_seconds=0.25,
                )
            }
        ),
    ).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    row = session.get(PersistentWorkflowORM, workflow.id)
    assert result.status == "COMPLETED"
    assert row.workflow_state == PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION.value
    assert session.get(TaskORM, workflow.task_id).status == TaskStatus.DOCUMENTATION_PENDING.value


def test_runtime_completed_workflow_reconciliation_preserves_historical_rows_without_bound_task(session):
    workflow = _workflow(session, project_id="DARWIN", backlog=[_item("DARWIN-HISTORICAL")])
    workflow_row = session.get(PersistentWorkflowORM, workflow.id)
    workflow_row.task_id = None
    session.flush()

    result = ExecutionRuntimeLoopService(
        session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_adapter=ScriptedExecutionAdapter(),
    ).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    row = session.get(PersistentWorkflowORM, workflow.id)
    assert result.status == "FAILED"
    assert result.completed_tasks == 0
    assert row.task_backlog[0]["state"] == QueueWorkItemState.WAITING_HUMAN.value
    assert session.get(TaskORM, workflow.task_id).status == TaskStatus.READY.value


class CustomExecutionAdapter:
    provider_id = "custom-test-adapter"

    def __init__(self):
        self.contexts: list[RuntimeExecutionContext] = []

    def execute(self, context: RuntimeExecutionContext) -> ExecutionAdapterResult:
        self.contexts.append(context)
        return ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["custom adapter completed work"],
            active_execution_seconds=0.25,
        )


class RaisingExecutionAdapter:
    provider_id = "raising-test-adapter"

    def execute(self, context: RuntimeExecutionContext) -> ExecutionAdapterResult:
        raise RuntimeError(f"boom for {context.item_id}")


class CountingExecutionAdapter:
    provider_id = "counting-test-adapter"

    def __init__(self):
        self.calls: list[str] = []

    def execute(self, context: RuntimeExecutionContext) -> ExecutionAdapterResult:
        self.calls.append(context.item_id)
        return ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=[f"{context.item_id}: safely dispatched"],
            active_execution_seconds=0.25,
        )


def _workflow(
    session,
    *,
    project_id: str,
    backlog: list[dict],
    attach_repository: bool = True,
    documentation_required: bool = False,
):
    project = _ensure_project(session, project_id)
    first_item_id = str(backlog[0].get("item_id") or backlog[0].get("task_id"))
    repository = _ensure_repository(session, project.id, f"{first_item_id}_REPOSITORY")
    if attach_repository:
        _ensure_attachment(session, project.id, repository.id)
    task = TaskService(session).create_task(
        project_id=project.id,
        title=f"Runtime task {first_item_id}",
        objective="Execute a bounded Darwin runtime task.",
        priority=TaskPriority.NORMAL,
        complexity=TaskComplexity.T1,
        authority_level=AuthorityLevel.L1,
        created_by=Actor.LUCIUS,
    )
    TaskService(session).create_or_update_contract(
        task_id=task.id,
        objective=task.objective,
        acceptance_criteria=["Runtime dispatch must pass canonical pre-mutation readiness."],
        constraints=["ONE_LOGICAL_DISPATCHER", "NO_PUSH", "NO_MERGE", "NO_DEPLOY"],
        repository_ids=[repository.id],
        allowed_actions=[
            AllowedAction.READ_REPOSITORY,
            AllowedAction.RUN_TESTS,
            AllowedAction.WRITE_SOURCE,
            AllowedAction.WRITE_TESTS,
            AllowedAction.WRITE_DOCUMENTATION,
        ],
        environment=Environment.DEVELOPMENT,
        authority_level=AuthorityLevel.L1,
        documentation_required=documentation_required,
        documentation_targets=["docs/runtime.md"] if documentation_required else [],
        actor=Actor.LUCIUS,
    )
    TaskService(session).mark_ready(task.id, actor=Actor.LUCIUS)
    return PersistentWorkflowService(session).create(
        objective="Execute a bounded Darwin runtime task.",
        expected_main_head="HEAD",
        isolated_branch="lucius/native-runtime-test",
        worktree_path="/private/tmp/darwin-runtime-test",
        authority_tier="ISOLATED_DEVELOPMENT_ONLY",
        project_id=project.id,
        repository_id=repository.id,
        task_id=task.id,
        task_backlog=backlog,
        pending_task_ids=[str(item.get("item_id") or item.get("task_id")) for item in backlog],
    )


def _ensure_project(session, project_id: str) -> ProjectORM:
    project = session.get(ProjectORM, project_id)
    if project is not None:
        return project
    project = ProjectORM(
        id=project_id,
        name=f"Project {project_id}",
        slug=project_id.lower(),
        project_type=ProjectType.DFG_INTERNAL.value,
        status=ProjectStatus.ACTIVE.value,
        documentation_policy={},
        default_authority_level=AuthorityLevel.L1.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(project)
    session.flush()
    return project


def _ensure_repository(session, project_id: str, repository_id: str) -> RepositoryRegistrationORM:
    repository = session.get(RepositoryRegistrationORM, repository_id)
    if repository is not None:
        return repository
    repository = RepositoryRegistrationORM(
        id=repository_id,
        project_id=project_id,
        name=f"Repository {repository_id}",
        adapter_type=RepositoryAdapterType.LOCAL_GIT.value,
        location=f"/private/tmp/{repository_id.lower()}",
        default_branch="main",
        access_mode=RepositoryAccessMode.READ_ONLY.value,
        status="ACTIVE",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(repository)
    session.flush()
    return repository


def _ensure_attachment(session, project_id: str, repository_id: str) -> None:
    existing = session.scalar(
        select(ProjectRepositoryAttachmentORM).where(
            ProjectRepositoryAttachmentORM.project_id == project_id,
            ProjectRepositoryAttachmentORM.repository_id == repository_id,
        )
    )
    if existing is not None:
        return
    ProjectRegistryService(session).attach_repository(
        project_id=project_id,
        repository_id=repository_id,
        actor=Actor.LUCIUS,
    )


def _item(
    item_id: str,
    *,
    state: QueueWorkItemState = QueueWorkItemState.READY,
    priority: str = "NORMAL",
    order: int = 1,
) -> dict:
    return {
        "item_id": item_id,
        "logical_task_id": item_id,
        "title": f"Runtime task {item_id}",
        "state": state.value,
        "priority": priority,
        "created_order": order,
        "dependencies": [],
        "completed_substeps": [],
        "version": 0,
    }
