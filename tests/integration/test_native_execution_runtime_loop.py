from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from lucius.domain.enums import PersistentWorkflowState, QueueWorkItemState
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import AuditEventORM, PersistentWorkflowORM, PlanFreezeORM
from lucius.pilots.workflows import PersistentWorkflowService
from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
from lucius.runtime.schemas import ExecutionAdapterResult, RuntimeExecutionContext, RuntimeLoopConfig
from lucius.runtime.service import ExecutionRuntimeLoopService


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
    assert row.workflow_state == PersistentWorkflowState.PLAN_READY.value
    assert [item["state"] for item in row.task_backlog] == ["COMPLETED", "COMPLETED"]
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
    assert alternative_row.task_backlog[0]["state"] == QueueWorkItemState.COMPLETED.value


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
    assert row.task_backlog[0]["completed_substeps"] == ["already inspected repo", "finished resumed work"]


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


def _workflow(session, *, project_id: str, backlog: list[dict]):
    return PersistentWorkflowService(session).create(
        objective="Execute a bounded Darwin runtime task.",
        expected_main_head="HEAD",
        isolated_branch="lucius/native-runtime-test",
        worktree_path="/private/tmp/darwin-runtime-test",
        authority_tier="ISOLATED_DEVELOPMENT_ONLY",
        project_id=project_id,
        repository_id=f"{project_id}_REPOSITORY",
        task_id=f"{project_id}_TASK",
        task_backlog=backlog,
        pending_task_ids=[str(item.get("item_id") or item.get("task_id")) for item in backlog],
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
