from __future__ import annotations

import json
import subprocess
import sys

from lucius.domain.enums import (
    AutonomyRecommendation,
    EngineeringPlanEvaluationMode,
    EngineeringPlanEvaluationResult,
    PersistentWorkflowState,
    QueueWorkItemState,
    RepositoryIntegrityResult,
    RepositoryStateClassification,
)
from lucius.persistence.orm import EngineeringPlanEvaluationORM, utc_now
from lucius.pilots.queue import NonBlockingQueueService, QueueStateError
from lucius.pilots.release import ReleaseGateService
from lucius.pilots.workflows import PersistentWorkflowService
from tests.integration.test_phase112_pilot_infrastructure import (
    _benchmark,
    _plan_evaluation,
    _repository_state,
)


def test_cross_project_queue_pilot_scenario(session):
    queue = NonBlockingQueueService(session)
    workflow_a = _workflow(
        session,
        project_id="PROJECT_A",
        backlog=[_item("A1", state=QueueWorkItemState.READY, priority="NORMAL", order=1)],
    )
    workflow_b = _workflow(
        session,
        project_id="PROJECT_B",
        backlog=[
            _item("B1", state=QueueWorkItemState.READY, priority="LOW", order=1),
            _item("B2", state=QueueWorkItemState.READY, priority="HIGH", order=2, dependencies=["B1"]),
            _item("B3", state=QueueWorkItemState.READY, priority="LOW", order=3, dependencies=["B2"]),
        ],
    )
    workflow_c = _workflow(
        session,
        project_id="PROJECT_C",
        backlog=[_item("C1", state=QueueWorkItemState.WAITING_EXTERNAL, priority="CRITICAL", order=1)],
    )
    workflow_ids = [workflow_a.id, workflow_b.id, workflow_c.id]

    first = queue.start_global_next(workflow_ids)
    assert first.selected_project_id == "PROJECT_A"
    assert first.selected_workflow_id == workflow_a.id
    assert first.selected_item_id == "A1"

    checkpoint = queue.block_running_item(
        workflow_a.id,
        "A1",
        blocking_state=QueueWorkItemState.WAITING_EXTERNAL,
        blocking_reason="Project A external fixture unavailable.",
        blocker_category="EXTERNAL_WAIT",
        resume_condition="project_a_fixture=true",
        work_completed=["shared.validate", "A1.service"],
        known_risks=["Project A checkpoint must not resume Project B."],
    )

    after_block = queue.inspect_global(workflow_ids)
    assert after_block.next_selection.selected_project_id == "PROJECT_B"
    assert after_block.next_selection.selected_item_id == "B1"
    assert {item.project_id for item in after_block.blocked} == {"PROJECT_A", "PROJECT_C"}
    assert [item.item_id for item in after_block.dependency_blocked] == ["B2", "B3"]

    queue.start_global_next(workflow_ids)
    assert queue.inspect(workflow_b.id).running[0]["item_id"] == "B1"
    queue.complete_item(workflow_b.id, "B1", completed_substeps=["shared.validate", "B1.docs"])

    after_b1 = queue.inspect_global(workflow_ids)
    assert after_b1.next_selection.selected_item_id == "B2"
    assert after_b1.next_selection.selected_project_id == "PROJECT_B"

    resolved = queue.resolve_blocker(
        workflow_a.id,
        "A1",
        checkpoint_id=checkpoint.id,
        expected_item_version=2,
        resolution_event="project_a_fixture=true",
    )
    assert resolved["state"] == QueueWorkItemState.READY_TO_RESUME.value

    priority_case = queue.select_global_next(workflow_ids)
    assert priority_case.selected_project_id == "PROJECT_B"
    assert priority_case.selected_item_id == "B2"
    assert priority_case.eligible_items[0].item_id == "B2"
    assert priority_case.eligible_items[1].item_id == "A1"

    queue.start_global_next(workflow_ids)
    assert queue.inspect(workflow_b.id).running[0]["item_id"] == "B2"
    no_preempt = queue.select_global_next(workflow_ids)
    assert no_preempt.selected_item_id is None
    assert no_preempt.reason == "GLOBAL_RUNNING_ITEM_ACTIVE_NO_PREEMPTION"
    assert [item.item_id for item in no_preempt.running_items] == ["B2"]

    queue.complete_item(workflow_b.id, "B2", completed_substeps=["B2.high-priority"])
    after_b2 = queue.select_global_next(workflow_ids)
    assert after_b2.selected_project_id == "PROJECT_A"
    assert after_b2.selected_item_id == "A1"
    assert after_b2.reason.startswith(f"project=PROJECT_A workflow={workflow_a.id}")

    queue.start_global_next(workflow_ids)
    assert queue.inspect(workflow_a.id).running[0]["item_id"] == "A1"
    completed_a = queue.complete_item(workflow_a.id, "A1", completed_substeps=["A1.finish"])
    assert completed_a["completed_substeps"] == ["shared.validate", "A1.service", "A1.finish"]


def test_equal_priority_resume_wins_then_stable_tie_breaking(session):
    queue = NonBlockingQueueService(session)
    workflow_a = _workflow(
        session,
        project_id="PROJECT_A",
        backlog=[_item("A1", state=QueueWorkItemState.READY_TO_RESUME, priority="NORMAL", order=3)],
    )
    workflow_b = _workflow(
        session,
        project_id="PROJECT_B",
        backlog=[_item("B1", state=QueueWorkItemState.READY, priority="NORMAL", order=1)],
    )
    workflow_c = _workflow(
        session,
        project_id="PROJECT_C",
        backlog=[_item("C1", state=QueueWorkItemState.READY, priority="NORMAL", order=1)],
    )

    resume_first = queue.select_global_next([workflow_a.id, workflow_b.id])
    assert resume_first.selected_item_id == "A1"

    stable_tie = queue.select_global_next([workflow_b.id, workflow_c.id])
    assert stable_tie.selected_project_id == "PROJECT_B"
    assert stable_tie.selected_workflow_id == workflow_b.id


def test_stale_project_a_checkpoint_cannot_mutate_project_b(session):
    queue = NonBlockingQueueService(session)
    workflow_a = _workflow(
        session,
        project_id="PROJECT_A",
        backlog=[_item("A1", state=QueueWorkItemState.READY)],
    )
    workflow_b = _workflow(
        session,
        project_id="PROJECT_B",
        backlog=[_item("B1", state=QueueWorkItemState.READY)],
    )
    queue.start_next(workflow_a.id)
    checkpoint = queue.block_running_item(
        workflow_a.id,
        "A1",
        blocking_state=QueueWorkItemState.WAITING_EXTERNAL,
        blocking_reason="Project A external wait.",
        blocker_category="EXTERNAL_WAIT",
        resume_condition="ready=true",
    )

    try:
        queue.resolve_blocker(
            workflow_b.id,
            "B1",
            checkpoint_id=checkpoint.id,
            expected_item_version=2,
            resolution_event="ready=true",
        )
    except QueueStateError as error:
        assert "Stale checkpoint" in str(error) or "Blocked item required" in str(error)
    else:
        raise AssertionError("Project A checkpoint should not mutate Project B")

    assert queue.inspect(workflow_b.id).ready[0]["item_id"] == "B1"


def test_global_duplicate_running_identity_is_project_scoped(session):
    queue = NonBlockingQueueService(session)
    workflow_a = _workflow(
        session,
        project_id="PROJECT_A",
        backlog=[_item("A1", state=QueueWorkItemState.RUNNING, logical_task_id="same-logical")],
    )
    workflow_b = _workflow(
        session,
        project_id="PROJECT_B",
        backlog=[_item("B1", state=QueueWorkItemState.RUNNING, logical_task_id="same-logical")],
    )
    allowed = queue.select_global_next([workflow_a.id, workflow_b.id])
    assert allowed.reason == "GLOBAL_RUNNING_ITEM_ACTIVE_NO_PREEMPTION"

    workflow_c = _workflow(
        session,
        project_id="PROJECT_A",
        backlog=[_item("A1-copy", state=QueueWorkItemState.RUNNING, logical_task_id="same-logical")],
    )
    try:
        queue.select_global_next([workflow_a.id, workflow_c.id])
    except QueueStateError as error:
        assert "Duplicate global RUNNING logical work item" in str(error)
    else:
        raise AssertionError("Duplicate project-scoped running identity should fail")


def test_terminal_and_no_work_projects_are_excluded(session):
    queue = NonBlockingQueueService(session)
    ready = _workflow(
        session,
        project_id="PROJECT_READY",
        backlog=[_item("R1", state=QueueWorkItemState.READY)],
    )
    terminal = _workflow(
        session,
        project_id="PROJECT_TERMINAL",
        backlog=[_item("T1", state=QueueWorkItemState.READY, priority="CRITICAL")],
    )
    empty = _workflow(session, project_id="PROJECT_EMPTY", backlog=[])
    from lucius.persistence.orm import PersistentWorkflowORM

    terminal_row = session.get(PersistentWorkflowORM, terminal.id)
    terminal_row.workflow_state = PersistentWorkflowState.CLOSED.value
    session.flush()

    status = queue.inspect_global([ready.id, terminal.id, empty.id])

    assert status.workflow_ids == [ready.id]
    assert status.next_selection.selected_item_id == "R1"
    assert status.next_selection.selected_project_id == "PROJECT_READY"


def test_fresh_context_global_reconstruction_and_cli(tmp_path):
    from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory

    database = tmp_path / "global-queue.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    Session = make_session_factory(engine)
    with Session() as session:
        queue = NonBlockingQueueService(session)
        workflow_a = _workflow(
            session,
            project_id="PROJECT_A",
            backlog=[_item("A1", state=QueueWorkItemState.READY, priority="NORMAL")],
        )
        workflow_b = _workflow(
            session,
            project_id="PROJECT_B",
            backlog=[_item("B1", state=QueueWorkItemState.READY, priority="HIGH")],
        )
        queue.start_next(workflow_b.id)
        queue.block_running_item(
            workflow_b.id,
            "B1",
            blocking_state=QueueWorkItemState.WAITING_EXTERNAL,
            blocking_reason="External B gate.",
            blocker_category="EXTERNAL_WAIT",
            resume_condition="b_ready=true",
        )
        session.commit()
        workflow_ids = [workflow_a.id, workflow_b.id]

    status_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lucius.pilots.cli",
            "--database",
            str(database),
            "queue-global-status",
            *workflow_ids,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    next_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lucius.pilots.cli",
            "--database",
            str(database),
            "queue-global-next",
            *workflow_ids,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert status_result.returncode == 0
    assert next_result.returncode == 0
    status_payload = json.loads(status_result.stdout)
    next_payload = json.loads(next_result.stdout)
    assert [item["item_id"] for item in status_payload["blocked"]] == ["B1"]
    assert status_payload["next_selection"]["selected_project_id"] == "PROJECT_A"
    assert next_payload["selected_item_id"] == "A1"
    assert next_payload["reason"].startswith("project=PROJECT_A")


def test_cross_project_queue_release_gate_recommends_multi_project_queue(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _cross_project_evaluation_row(session, EngineeringPlanEvaluationResult.PASS)
    before = _benchmark(session, score=100.0, failed=0)
    after = _benchmark(session, score=100.0, failed=0)

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=after.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
    )

    assert result.passed is True
    assert result.recommendation == AutonomyRecommendation.READY_FOR_MULTI_PROJECT_NON_BLOCKING_QUEUE


def test_cross_project_queue_release_gate_recommends_another_pilot_when_required_check_missing(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _cross_project_evaluation_row(session, EngineeringPlanEvaluationResult.PASS)
    evaluation.implementation_artifact["multi_project_non_blocking_tested"] = False
    before = _benchmark(session, score=100.0, failed=0)

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=before.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
    )

    assert result.passed is True
    assert result.recommendation == AutonomyRecommendation.READY_FOR_ANOTHER_CROSS_PROJECT_QUEUE_PILOT


def _workflow(session, *, project_id: str, backlog: list[dict]):
    workflow = PersistentWorkflowService(session).create(
        objective=f"{project_id} Phase 1.21 cross-project queue scenario.",
        expected_main_head="lucius-head",
        isolated_branch=f"lucius/phase-1.21-{project_id.lower()}",
        worktree_path=f"/tmp/lucius-phase-1.21-{project_id.lower()}",
        authority_tier="READY_FOR_MULTI_PROJECT_NON_BLOCKING_QUEUE",
        project_id=project_id,
        task_backlog=backlog,
        dependency_graph={item["item_id"]: item.get("dependencies", []) for item in backlog},
        active_task_id=None,
        completed_task_ids=[],
        pending_task_ids=[item["item_id"] for item in backlog],
    )
    from lucius.persistence.orm import PersistentWorkflowORM

    row = session.get(PersistentWorkflowORM, workflow.id)
    row.workflow_state = PersistentWorkflowState.PLAN_READY.value
    session.flush()
    return workflow


def _item(
    item_id: str,
    *,
    state: QueueWorkItemState,
    priority: str = "NORMAL",
    order: int = 1,
    dependencies: list[str] | None = None,
    logical_task_id: str | None = None,
) -> dict:
    return {
        "item_id": item_id,
        "logical_task_id": logical_task_id or item_id,
        "project_id": "fixture-project",
        "workflow_id": "fixture-workflow",
        "title": item_id,
        "state": state.value,
        "priority": priority,
        "created_order": order,
        "dependencies": dependencies or [],
        "completed_substeps": [],
        "version": 0,
    }


def _cross_project_evaluation_row(
    session,
    result: EngineeringPlanEvaluationResult,
) -> EngineeringPlanEvaluationORM:
    row = _plan_evaluation(session, result)
    row.evaluation_mode = EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION.value
    row.implementation_artifact = {
        "pilot_stage": "CROSS_PROJECT_NON_BLOCKING_QUEUE_PILOT",
        "cross_project_evaluation": {"result": "PASS"},
        "global_scheduler_evaluation": {"result": "PASS"},
        "fresh_context_reconstruction": {"result": "PASS"},
        "duplicate_work_protection": {"result": "PASS"},
        "project_isolation": {"result": "PASS"},
        "safe_interruption": {"result": "PASS"},
        "stale_history_safety": {"result": "PASS"},
        "lifecycle_scope_safety": {"result": "PASS"},
        "unscoped_global_reconstruction": {"result": "PASS"},
        "exact_global_dispatch": {"result": "PASS"},
        "global_selection_equals_mutation": {"result": "PASS"},
        "malformed_persistence_safety": {"result": "PASS"},
        "stale_selection_safety": {"result": "PASS"},
        "multi_project_non_blocking_tested": True,
    }
    row.evaluator_version = "1.21-test"
    row.evaluated_at = utc_now()
    session.flush()
    return row
