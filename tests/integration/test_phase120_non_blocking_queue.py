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
from lucius.persistence.orm import EngineeringPlanEvaluationORM, PersistentWorkflowORM, utc_now
from lucius.pilots.queue import NonBlockingQueueService, QueueStateError
from lucius.pilots.release import ReleaseGateService
from lucius.pilots.workflows import PersistentWorkflowService
from tests.integration.test_phase112_pilot_infrastructure import (
    _benchmark,
    _plan_evaluation,
    _repository_state,
)


def test_non_blocking_queue_pilot_scenario(session):
    workflow = _workflow(session)
    queue = NonBlockingQueueService(session)

    first = queue.start_next(workflow.id)
    assert first.selected_item_id == "A1"
    assert first.reason == "priority=HIGH state=READY order=1"

    checkpoint = queue.block_running_item(
        workflow.id,
        "A1",
        blocking_state=QueueWorkItemState.WAITING_EXTERNAL,
        blocking_reason="Awaiting external fixture availability.",
        blocker_category="EXTERNAL_WAIT",
        resume_condition="fixture_available=true",
        work_completed=["A1.plan", "A1.service-stub"],
        implementation_head="abc123",
        pending_decision_or_dependency="External fixture availability",
        known_risks=["Do not redo completed A1 substeps after resume."],
        relevant_artifacts=["LEVID_QUEUE_A"],
        repair_counters={"total": 0, "limits": {"total": 3}},
        approval_requirements=[],
        next_safe_action="Run another eligible workstream.",
    )
    assert checkpoint.id == "LQCHK_000001"
    assert checkpoint.prior_state == QueueWorkItemState.RUNNING
    assert checkpoint.blocking_state == QueueWorkItemState.WAITING_EXTERNAL

    status = queue.inspect(workflow.id)
    assert [item["item_id"] for item in status.blocked] == ["A1"]
    assert status.next_selection.selected_item_id == "B1"
    assert status.next_selection.blocked_item_ids == ["A1", "C1"]

    second = queue.start_next(workflow.id)
    assert second.selected_item_id == "B1"
    no_preempt = queue.select_next(workflow.id)
    assert no_preempt.selected_item_id is None
    assert no_preempt.reason == "RUNNING_ITEM_ACTIVE_NO_PREEMPTION"

    queue.complete_item(workflow.id, "B1", completed_substeps=["B1.docs"])
    after_b = queue.inspect(workflow.id)
    assert [item["item_id"] for item in after_b.ready] == ["C1"]

    resolved = queue.resolve_blocker(
        workflow.id,
        "A1",
        checkpoint_id=checkpoint.id,
        expected_item_version=2,
        resolution_event="fixture_available=true",
    )
    assert resolved["state"] == QueueWorkItemState.READY_TO_RESUME.value
    assert resolved["work_completed"] == ["A1.plan", "A1.service-stub"]

    resume_status = queue.inspect(workflow.id)
    assert resume_status.next_selection.selected_item_id == "A1"
    assert resume_status.next_selection.eligible_item_ids == ["A1", "C1"]

    queue.start_next(workflow.id)
    completed = queue.complete_item(workflow.id, "A1", completed_substeps=["A1.finish"])
    assert completed["completed_substeps"] == ["A1.plan", "A1.service-stub", "A1.finish"]


def test_blocked_states_are_excluded_without_blocking_system(session):
    workflow = _workflow(
        session,
        backlog=[
            _item("A1", state=QueueWorkItemState.WAITING_HUMAN, priority="HIGH", order=1),
            _item("A2", state=QueueWorkItemState.WAITING_EXTERNAL, priority="HIGH", order=2),
            _item("A3", state=QueueWorkItemState.RETRY_LATER, priority="HIGH", order=3),
            _item("B1", state=QueueWorkItemState.READY, priority="NORMAL", order=4),
        ],
    )

    status = NonBlockingQueueService(session).inspect(workflow.id)

    assert {item["item_id"] for item in status.blocked} == {"A1", "A2", "A3"}
    assert status.next_selection.selected_item_id == "B1"


def test_ready_to_resume_waits_behind_higher_priority_ready_work(session):
    workflow = _workflow(
        session,
        backlog=[
            _item("A1", state=QueueWorkItemState.READY_TO_RESUME, priority="NORMAL", order=1),
            _item("B1", state=QueueWorkItemState.READY, priority="HIGH", order=2),
        ],
    )

    selection = NonBlockingQueueService(session).select_next(workflow.id)

    assert selection.selected_item_id == "B1"
    assert selection.eligible_item_ids == ["B1", "A1"]


def test_ready_to_resume_wins_within_same_priority(session):
    workflow = _workflow(
        session,
        backlog=[
            _item("A1", state=QueueWorkItemState.READY, priority="NORMAL", order=1),
            _item("B1", state=QueueWorkItemState.READY_TO_RESUME, priority="NORMAL", order=2),
        ],
    )

    selection = NonBlockingQueueService(session).select_next(workflow.id)

    assert selection.selected_item_id == "B1"


def test_deterministic_ordering_uses_creation_order_then_id(session):
    workflow = _workflow(
        session,
        backlog=[
            _item("B1", state=QueueWorkItemState.READY, priority="NORMAL", order=2),
            _item("A1", state=QueueWorkItemState.READY, priority="NORMAL", order=2),
            _item("C1", state=QueueWorkItemState.READY, priority="NORMAL", order=3),
        ],
    )

    selection = NonBlockingQueueService(session).select_next(workflow.id)

    assert selection.selected_item_id == "A1"
    assert selection.eligible_item_ids == ["A1", "B1", "C1"]


def test_dependency_blocked_item_becomes_ready_after_dependency_completion(session):
    workflow = _workflow(session)
    queue = NonBlockingQueueService(session)

    queue.start_next(workflow.id)
    queue.block_running_item(
        workflow.id,
        "A1",
        blocking_state=QueueWorkItemState.WAITING_EXTERNAL,
        blocking_reason="External A wait.",
        blocker_category="EXTERNAL_WAIT",
        resume_condition="external_ready=true",
    )
    queue.start_next(workflow.id)
    status_before = queue.inspect(workflow.id)

    assert [item["item_id"] for item in status_before.dependency_blocked] == ["C1"]
    queue.complete_item(workflow.id, "B1", completed_substeps=["B1.done"])
    status_after = queue.inspect(workflow.id)
    assert [item["item_id"] for item in status_after.ready] == ["C1"]


def test_duplicate_running_logical_task_is_rejected(session):
    workflow = _workflow(
        session,
        backlog=[
            _item("A1", state=QueueWorkItemState.RUNNING, logical_task_id="same"),
            _item("A1-copy", state=QueueWorkItemState.RUNNING, logical_task_id="same"),
        ],
    )

    queue = NonBlockingQueueService(session)
    try:
        queue.select_next(workflow.id)
    except QueueStateError as error:
        assert "Duplicate RUNNING logical task" in str(error)
    else:
        raise AssertionError("Duplicate RUNNING logical tasks should be rejected")


def test_duplicate_item_id_is_rejected(session):
    workflow = _workflow(
        session,
        backlog=[
            _item("A1", state=QueueWorkItemState.READY),
            _item("A1", state=QueueWorkItemState.READY),
        ],
    )

    try:
        NonBlockingQueueService(session).select_next(workflow.id)
    except QueueStateError as error:
        assert "Duplicate queue work item id" in str(error)
    else:
        raise AssertionError("Duplicate work item IDs should be rejected")


def test_stale_checkpoint_cannot_resume_newer_state(session):
    workflow = _workflow(session)
    queue = NonBlockingQueueService(session)
    queue.start_next(workflow.id)
    checkpoint = queue.block_running_item(
        workflow.id,
        "A1",
        blocking_state=QueueWorkItemState.WAITING_EXTERNAL,
        blocking_reason="External dependency unavailable.",
        blocker_category="EXTERNAL_WAIT",
        resume_condition="dependency_ready=true",
    )

    try:
        queue.resolve_blocker(
            workflow.id,
            "A1",
            checkpoint_id=checkpoint.id,
            expected_item_version=1,
            resolution_event="dependency_ready=true",
        )
    except QueueStateError as error:
        assert "Stale item version" in str(error)
    else:
        raise AssertionError("Stale item version should block resume")


def test_checkpoint_id_must_match_current_block(session):
    workflow = _workflow(session)
    queue = NonBlockingQueueService(session)
    queue.start_next(workflow.id)
    checkpoint = queue.block_running_item(
        workflow.id,
        "A1",
        blocking_state=QueueWorkItemState.WAITING_EXTERNAL,
        blocking_reason="External dependency unavailable.",
        blocker_category="EXTERNAL_WAIT",
        resume_condition="dependency_ready=true",
    )

    try:
        queue.resolve_blocker(
            workflow.id,
            "A1",
            checkpoint_id=f"{checkpoint.id}-stale",
            expected_item_version=2,
            resolution_event="dependency_ready=true",
        )
    except QueueStateError as error:
        assert "Stale checkpoint" in str(error)
    else:
        raise AssertionError("Stale checkpoint ID should block resume")


def test_project_workflow_isolation(session):
    queue = NonBlockingQueueService(session)
    workflow_a = _workflow(session, backlog=[_item("A1", state=QueueWorkItemState.READY, priority="HIGH")])
    workflow_b = _workflow(session, backlog=[_item("B1", state=QueueWorkItemState.READY, priority="HIGH")])

    queue.start_next(workflow_a.id)

    status_a = queue.inspect(workflow_a.id)
    status_b = queue.inspect(workflow_b.id)
    assert [item["item_id"] for item in status_a.running] == ["A1"]
    assert status_b.next_selection.selected_item_id == "B1"


def test_fresh_context_reconstructs_next_schedulable_work(tmp_path):
    from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory

    database = tmp_path / "queue.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    Session = make_session_factory(engine)
    with Session() as session:
        workflow = _workflow(session)
        queue = NonBlockingQueueService(session)
        queue.start_next(workflow.id)
        queue.block_running_item(
            workflow.id,
            "A1",
            blocking_state=QueueWorkItemState.WAITING_EXTERNAL,
            blocking_reason="External fixture unavailable.",
            blocker_category="EXTERNAL_WAIT",
            resume_condition="fixture_available=true",
        )
        session.commit()
        workflow_id = workflow.id

    engine = create_sqlite_engine(database)
    Session = make_session_factory(engine)
    with Session() as fresh_session:
        status = NonBlockingQueueService(fresh_session).validate_fresh_reconstruction(workflow_id)

    assert [item["item_id"] for item in status.blocked] == ["A1"]
    assert status.next_selection.selected_item_id == "B1"
    assert status.next_selection.reason == "priority=NORMAL state=READY order=2"


def test_queue_cli_status_and_next_are_read_only(tmp_path):
    from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory

    database = tmp_path / "queue-cli.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    Session = make_session_factory(engine)
    with Session() as session:
        workflow = _workflow(session)
        session.commit()
        workflow_id = workflow.id

    status_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lucius.pilots.cli",
            "--database",
            str(database),
            "queue-status",
            workflow_id,
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
            "queue-next",
            workflow_id,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert status_result.returncode == 0
    assert next_result.returncode == 0
    status_payload = json.loads(status_result.stdout)
    next_payload = json.loads(next_result.stdout)
    assert status_payload["workflow_id"] == workflow_id
    assert status_payload["next_selection"]["selected_item_id"] == "A1"
    assert next_payload["selected_item_id"] == "A1"


def test_non_blocking_queue_release_gate_recommends_multi_project_queue(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _queue_evaluation_row(session, EngineeringPlanEvaluationResult.PASS)
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


def test_non_blocking_queue_release_gate_blocks_stage_specific_failures(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _queue_evaluation_row(session, EngineeringPlanEvaluationResult.FAIL)
    before = _benchmark(session, score=100.0, failed=0)

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=before.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
    )

    assert result.recommendation == AutonomyRecommendation.NOT_READY_FOR_NON_BLOCKING_PROJECT_QUEUE


def _workflow(session, *, backlog=None):
    workflow = PersistentWorkflowService(session).create(
        objective="Phase 1.20 non-blocking queue scenario.",
        expected_main_head="lucius-head",
        isolated_branch="lucius/phase-1.20-queue",
        worktree_path="/tmp/lucius-phase-1.20",
        authority_tier="READY_FOR_NON_BLOCKING_PROJECT_QUEUE_PILOT",
        task_backlog=backlog
        or [
            _item("A1", state=QueueWorkItemState.READY, priority="HIGH", order=1),
            _item("B1", state=QueueWorkItemState.READY, priority="NORMAL", order=2),
            _item("C1", state=QueueWorkItemState.READY, priority="LOW", order=3, dependencies=["B1"]),
        ],
        dependency_graph={"A1": [], "B1": [], "C1": ["B1"]},
        active_task_id=None,
        completed_task_ids=[],
        pending_task_ids=["A1", "B1", "C1"],
    )
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
        "project_id": "LPROJ_QUEUE",
        "workflow_id": "LWORK_QUEUE",
        "title": item_id,
        "state": state.value,
        "priority": priority,
        "created_order": order,
        "dependencies": dependencies or [],
        "completed_substeps": [],
        "version": 0,
    }


def _queue_evaluation_row(
    session,
    result: EngineeringPlanEvaluationResult,
) -> EngineeringPlanEvaluationORM:
    row = _plan_evaluation(session, result)
    row.evaluation_mode = EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION.value
    row.implementation_artifact = {
        "pilot_stage": "NON_BLOCKING_PROJECT_QUEUE_PILOT",
        "queue_evaluation": {"result": "PASS"},
        "fresh_context_reconstruction": {"result": "PASS"},
        "duplicate_work_protection": {"result": "PASS"},
        "project_isolation": {"result": "PASS"},
        "safe_interruption": {"result": "PASS"},
        "multi_project_non_blocking_tested": True,
    }
    row.evaluator_version = "1.20-test"
    row.evaluated_at = utc_now()
    session.flush()
    return row


def test_blocked_item_configuration_repair_is_bounded_and_does_not_resume(session):
    workflow = _workflow(session)
    queue = NonBlockingQueueService(session)
    queue.start_next(workflow.id)
    checkpoint = queue.block_running_item(
        workflow.id,
        "A1",
        blocking_state=QueueWorkItemState.WAITING_HUMAN,
        blocking_reason="Runtime configuration requires repair.",
        blocker_category="NO_ELIGIBLE_PROVIDER",
        resume_condition="Repair configuration and revalidate.",
    )

    repaired = queue.repair_blocked_item_configuration(
        workflow.id,
        "A1",
        checkpoint_id=checkpoint.id,
        expected_item_version=2,
        timeout_seconds=120,
        context_limits_patch={"deterministic_verification": True},
    )

    assert repaired["state"] == QueueWorkItemState.WAITING_HUMAN.value
    assert repaired["version"] == 3
    assert repaired["timeout_seconds"] == 120
    assert repaired["context_limits"]["deterministic_verification"] is True
    assert repaired["current_block_checkpoint_id"] == checkpoint.id

    resolved = queue.resolve_blocker(
        workflow.id,
        "A1",
        checkpoint_id=checkpoint.id,
        expected_item_version=3,
        resolution_event="configuration_repaired_and_revalidated",
    )
    assert resolved["state"] == QueueWorkItemState.READY_TO_RESUME.value
    assert resolved["version"] == 4


def test_blocked_item_configuration_repair_rejects_unsafe_context_changes(session):
    workflow = _workflow(session)
    queue = NonBlockingQueueService(session)
    queue.start_next(workflow.id)
    checkpoint = queue.block_running_item(
        workflow.id,
        "A1",
        blocking_state=QueueWorkItemState.WAITING_HUMAN,
        blocking_reason="Runtime configuration requires repair.",
        blocker_category="NO_ELIGIBLE_PROVIDER",
        resume_condition="Repair configuration and revalidate.",
    )

    try:
        queue.repair_blocked_item_configuration(
            workflow.id,
            "A1",
            checkpoint_id=checkpoint.id,
            expected_item_version=2,
            context_limits_patch={"external_side_effects": True},
        )
    except QueueStateError as error:
        assert "Unsafe context_limits repair keys" in str(error)
    else:
        raise AssertionError("Unsafe context repair should fail closed")


def test_blocked_item_configuration_repair_rejects_stale_version(session):
    workflow = _workflow(session)
    queue = NonBlockingQueueService(session)
    queue.start_next(workflow.id)
    checkpoint = queue.block_running_item(
        workflow.id,
        "A1",
        blocking_state=QueueWorkItemState.WAITING_HUMAN,
        blocking_reason="Runtime configuration requires repair.",
        blocker_category="NO_ELIGIBLE_PROVIDER",
        resume_condition="Repair configuration and revalidate.",
    )

    try:
        queue.repair_blocked_item_configuration(
            workflow.id,
            "A1",
            checkpoint_id=checkpoint.id,
            expected_item_version=1,
            timeout_seconds=120,
        )
    except QueueStateError as error:
        assert "Stale item version" in str(error)
    else:
        raise AssertionError("Stale repair should fail closed")
