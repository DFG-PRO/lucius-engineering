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


def test_legacy_backlog_without_state_is_inspectable_but_not_globally_schedulable(session):
    queue = NonBlockingQueueService(session)
    workflow = _workflow(
        session,
        project_id="LEGACY_PROJECT",
        backlog=[_legacy_item("LEGACY_DONE")],
        completed_task_ids=["LEGACY_DONE"],
        pending_task_ids=[],
    )
    before = list(session.get(PersistentWorkflowORM, workflow.id).task_backlog)

    status = queue.inspect_global([workflow.id])
    selection = queue.select_global_next([workflow.id])
    after = session.get(PersistentWorkflowORM, workflow.id).task_backlog

    assert status.observed_workflow_ids == [workflow.id]
    assert status.workflow_ids == [workflow.id]
    assert [item.item_id for item in status.legacy_unschedulable] == ["LEGACY_DONE"]
    assert status.legacy_unschedulable[0].exclusion_reason == "LEGACY_UNSCHEDULABLE:MISSING_STATE"
    assert selection.selected_item_id is None
    assert [item.item_id for item in selection.excluded_items] == ["LEGACY_DONE"]
    assert after == before


def test_phase121c_global_start_dispatches_exact_selected_item_not_legacy_hijacker(session):
    queue = NonBlockingQueueService(session)
    workflow = _workflow(
        session,
        project_id="MIXED",
        backlog=[
            _legacy_item("legacy_high") | {"priority": "HIGH", "created_order": 0},
            _item("modern_low", state=QueueWorkItemState.READY, priority="LOW", order=2),
        ],
    )

    selection = queue.select_global_next([workflow.id])
    started = queue.start_global_next([workflow.id])
    row = session.get(PersistentWorkflowORM, workflow.id)

    assert selection.selected_item_id == "modern_low"
    assert selection.selected_item_version == 0
    assert started.selected_item_id == "modern_low"
    assert started.started_item_id == "modern_low"
    assert started.mutation_identity_matches_selection is True
    assert row.active_task_id == "modern_low"
    by_id = {_item_id(item): item for item in row.task_backlog}
    assert "state" not in by_id["legacy_high"]
    assert by_id["modern_low"]["state"] == QueueWorkItemState.RUNNING.value
    assert by_id["modern_low"]["version"] == 1


def test_unscoped_global_next_ignores_legacy_historical_backlog(session):
    queue = NonBlockingQueueService(session)
    legacy = _workflow(
        session,
        project_id="OLD_PROJECT",
        backlog=[_legacy_item("OLD_DONE")],
        workflow_state=PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION,
        completed_task_ids=["OLD_DONE"],
        pending_task_ids=[],
    )
    active = _workflow(
        session,
        project_id="ACTIVE_PROJECT",
        backlog=[_item("ACTIVE_READY", state=QueueWorkItemState.READY, priority="LOW")],
    )

    selection = queue.select_global_next()
    status = queue.inspect_global()

    assert selection.selected_workflow_id == active.id
    assert selection.selected_item_id == "ACTIVE_READY"
    assert legacy.id in status.observed_workflow_ids
    assert legacy.id not in status.workflow_ids
    assert status.lifecycle_excluded[0].workflow_id == legacy.id
    assert status.lifecycle_excluded[0].items[0].item_id == "OLD_DONE"


def test_completed_pending_and_closed_workflows_are_lifecycle_excluded(session):
    queue = NonBlockingQueueService(session)
    completed_pending = _workflow(
        session,
        project_id="COMPLETE_PENDING",
        backlog=[_item("B3", state=QueueWorkItemState.READY, priority="CRITICAL")],
        workflow_state=PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION,
    )
    closed = _workflow(
        session,
        project_id="CLOSED",
        backlog=[_item("CLOSED_READY", state=QueueWorkItemState.READY, priority="CRITICAL")],
        workflow_state=PersistentWorkflowState.CLOSED,
    )
    active = _workflow(
        session,
        project_id="ACTIVE",
        backlog=[_item("ACTIVE_LOW", state=QueueWorkItemState.READY, priority="LOW")],
    )

    status = queue.inspect_global([completed_pending.id, closed.id, active.id])

    assert status.next_selection.selected_item_id == "ACTIVE_LOW"
    assert {item.workflow_id for item in status.lifecycle_excluded} == {completed_pending.id, closed.id}
    assert {item.reason for item in status.lifecycle_excluded} == {
        "WORKFLOW_LIFECYCLE_INELIGIBLE:COMPLETED_PENDING_INTEGRATION",
        "WORKFLOW_LIFECYCLE_INELIGIBLE:CLOSED",
    }


def test_active_queue_enabled_workflow_priority_and_resume_rules_remain_schedulable(session):
    queue = NonBlockingQueueService(session)
    resume = _workflow(
        session,
        project_id="RESUME",
        backlog=[_item("A1", state=QueueWorkItemState.READY_TO_RESUME, priority="NORMAL")],
    )
    high_ready = _workflow(
        session,
        project_id="HIGH",
        backlog=[_item("B1", state=QueueWorkItemState.READY, priority="HIGH")],
    )
    normal_ready = _workflow(
        session,
        project_id="NORMAL",
        backlog=[_item("C1", state=QueueWorkItemState.READY, priority="NORMAL")],
    )

    assert queue.select_global_next([resume.id, high_ready.id]).selected_item_id == "B1"
    assert queue.select_global_next([resume.id, normal_ready.id]).selected_item_id == "A1"


def test_phase121_replay_dependency_no_preemption_and_blocked_control(session):
    queue = NonBlockingQueueService(session)
    workflow_a = _workflow(
        session,
        project_id="PROJECT_A",
        backlog=[_item("A1", state=QueueWorkItemState.READY, priority="NORMAL", logical_task_id="same-substep")],
    )
    workflow_b = _workflow(
        session,
        project_id="PROJECT_B",
        backlog=[
            _item("B1", state=QueueWorkItemState.READY, priority="LOW", logical_task_id="same-substep"),
            _item("B2", state=QueueWorkItemState.READY, priority="HIGH", dependencies=["B1"]),
        ],
    )
    workflow_c = _workflow(
        session,
        project_id="PROJECT_C",
        backlog=[_item("C1", state=QueueWorkItemState.WAITING_EXTERNAL, priority="CRITICAL")],
    )
    workflow_ids = [workflow_a.id, workflow_b.id, workflow_c.id]

    started_a = queue.start_global_next(workflow_ids)
    assert started_a.selected_item_id == "A1"
    assert started_a.started_item_id == "A1"
    checkpoint = queue.block_running_item(
        workflow_a.id,
        "A1",
        blocking_state=QueueWorkItemState.WAITING_EXTERNAL,
        blocking_reason="Project A waits externally.",
        blocker_category="EXTERNAL_WAIT",
        resume_condition="a_ready=true",
    )

    after_block = queue.inspect_global(workflow_ids)
    assert after_block.next_selection.selected_item_id == "B1"
    assert [item.item_id for item in after_block.dependency_blocked] == ["B2"]
    assert {item.item_id for item in after_block.blocked} == {"A1", "C1"}

    started_b1 = queue.start_global_next(workflow_ids)
    assert started_b1.selected_item_id == "B1"
    assert started_b1.started_item_id == "B1"
    queue.complete_item(workflow_b.id, "B1", completed_substeps=["B1.done"])
    queue.resolve_blocker(
        workflow_a.id,
        "A1",
        checkpoint_id=checkpoint.id,
        expected_item_version=2,
        resolution_event="a_ready=true",
    )

    started_b2 = queue.start_global_next(workflow_ids)
    assert started_b2.selected_item_id == "B2"
    assert started_b2.started_item_id == "B2"
    no_preempt = queue.select_global_next(workflow_ids)
    assert no_preempt.reason == "GLOBAL_RUNNING_ITEM_ACTIVE_NO_PREEMPTION"


def test_phase121c_stale_selected_item_rejects_instead_of_falling_back(session):
    queue = NonBlockingQueueService(session)
    workflow = _workflow(
        session,
        project_id="STALE",
        backlog=[
            _item("selected", state=QueueWorkItemState.READY, priority="HIGH", order=1),
            _item("fallback", state=QueueWorkItemState.READY, priority="LOW", order=2),
        ],
    )
    selection = queue.select_global_next([workflow.id])
    row = session.get(PersistentWorkflowORM, workflow.id)
    backlog = list(row.task_backlog)
    backlog[0]["state"] = QueueWorkItemState.COMPLETED.value
    backlog[0]["version"] = 1
    row.task_backlog = backlog
    session.flush()

    try:
        queue.start_selected_global_item(selection, workflow_ids=[workflow.id])
    except QueueStateError as error:
        assert "Stale global selection" in str(error)
    else:
        raise AssertionError("Stale selected item should reject without fallback")

    row = session.get(PersistentWorkflowORM, workflow.id)
    assert row.active_task_id is None
    assert {_item_id(item): item.get("state") for item in row.task_backlog} == {
        "selected": QueueWorkItemState.COMPLETED.value,
        "fallback": QueueWorkItemState.READY.value,
    }


def test_phase121c_blocked_dependency_lifecycle_and_ordering_staleness_reject(session):
    queue = NonBlockingQueueService(session)
    blocked = _workflow(session, project_id="BLOCKED", backlog=[_item("A", state=QueueWorkItemState.READY)])
    blocked_selection = queue.select_global_next([blocked.id])
    row = session.get(PersistentWorkflowORM, blocked.id)
    backlog = list(row.task_backlog)
    backlog[0]["state"] = QueueWorkItemState.WAITING_EXTERNAL.value
    backlog[0]["version"] = 1
    row.task_backlog = backlog
    session.flush()
    try:
        queue.start_selected_global_item(blocked_selection, workflow_ids=[blocked.id])
    except QueueStateError as error:
        assert "Stale global selection" in str(error)
    else:
        raise AssertionError("Blocked selected item should reject")

    dependency = _workflow(session, project_id="DEPENDENCY", backlog=[_item("A", state=QueueWorkItemState.READY)])
    dependency_selection = queue.select_global_next([dependency.id])
    row = session.get(PersistentWorkflowORM, dependency.id)
    backlog = list(row.task_backlog)
    backlog[0]["dependencies"] = ["missing"]
    backlog[0]["version"] = 1
    row.task_backlog = backlog
    session.flush()
    try:
        queue.start_selected_global_item(dependency_selection, workflow_ids=[dependency.id])
    except QueueStateError as error:
        assert "Stale global selection" in str(error)
    else:
        raise AssertionError("New dependency should reject stale selected item")

    lifecycle = _workflow(session, project_id="LIFECYCLE", backlog=[_item("A", state=QueueWorkItemState.READY)])
    lifecycle_selection = queue.select_global_next([lifecycle.id])
    session.get(PersistentWorkflowORM, lifecycle.id).workflow_state = PersistentWorkflowState.CLOSED.value
    session.flush()
    try:
        queue.start_selected_global_item(lifecycle_selection, workflow_ids=[lifecycle.id])
    except QueueStateError as error:
        assert "workflow is no longer schedulable" in str(error)
    else:
        raise AssertionError("Lifecycle-ineligible selected workflow should reject")

    ordering = _workflow(
        session,
        project_id="ORDERING",
        backlog=[
            _item("A", state=QueueWorkItemState.READY, priority="NORMAL", order=1),
            _item("B", state=QueueWorkItemState.READY, priority="LOW", order=2),
        ],
    )
    ordering_selection = queue.select_global_next([ordering.id])
    row = session.get(PersistentWorkflowORM, ordering.id)
    backlog = list(row.task_backlog)
    backlog[1]["priority"] = "CRITICAL"
    backlog[1]["version"] = 1
    row.task_backlog = backlog
    session.flush()
    try:
        queue.start_selected_global_item(ordering_selection, workflow_ids=[ordering.id])
    except QueueStateError as error:
        assert "Stale global selection" in str(error)
    else:
        raise AssertionError("Higher-priority stale ordering should reject")


def test_stale_checkpoint_and_cross_workflow_dependency_isolation_remain_intact(session):
    queue = NonBlockingQueueService(session)
    workflow_a = _workflow(session, project_id="PROJECT_A", backlog=[_item("A1", state=QueueWorkItemState.READY)])
    workflow_b = _workflow(session, project_id="PROJECT_B", backlog=[_item("B1", state=QueueWorkItemState.READY)])
    dep_a = _workflow(session, project_id="DEP_A", backlog=[_item("A2", state=QueueWorkItemState.READY, dependencies=["B1"])])
    dep_b = _workflow(session, project_id="DEP_B", backlog=[_item("B1", state=QueueWorkItemState.COMPLETED)])

    queue.start_next(workflow_a.id)
    checkpoint = queue.block_running_item(
        workflow_a.id,
        "A1",
        blocking_state=QueueWorkItemState.WAITING_EXTERNAL,
        blocking_reason="Project A waits externally.",
        blocker_category="EXTERNAL_WAIT",
        resume_condition="a_ready=true",
    )

    try:
        queue.resolve_blocker(
            workflow_b.id,
            "B1",
            checkpoint_id=checkpoint.id,
            expected_item_version=2,
            resolution_event="a_ready=true",
        )
    except QueueStateError as error:
        assert "Stale checkpoint" in str(error) or "Blocked item required" in str(error)
    else:
        raise AssertionError("Project A checkpoint should not mutate Project B")

    assert [item.item_id for item in queue.inspect_global([dep_a.id, dep_b.id]).dependency_blocked] == ["A2"]


def test_fresh_process_unscoped_reconstruction_is_safe_and_does_not_normalize_legacy_rows(tmp_path):
    from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory

    database = tmp_path / "phase121b.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    Session = make_session_factory(engine)
    with Session() as session:
        legacy = _workflow(
            session,
            project_id="LEGACY",
            backlog=[_legacy_item("OLD_DONE")],
            workflow_state=PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION,
            completed_task_ids=["OLD_DONE"],
            pending_task_ids=[],
        )
        active = _workflow(
            session,
            project_id="ACTIVE",
            backlog=[_item("ACTIVE_READY", state=QueueWorkItemState.READY)],
        )
        legacy_backlog = list(session.get(PersistentWorkflowORM, legacy.id).task_backlog)
        session.commit()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lucius.pilots.cli",
            "--database",
            str(database),
            "queue-global-status",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["next_selection"]["selected_workflow_id"] == active.id
    assert legacy.id in payload["observed_workflow_ids"]
    assert payload["lifecycle_excluded"][0]["workflow_id"] == legacy.id
    assert payload["lifecycle_excluded"][0]["items"][0]["state_label"] == "LEGACY_UNSCHEDULABLE"

    with Session() as session:
        assert session.get(PersistentWorkflowORM, legacy.id).task_backlog == legacy_backlog


def test_phase121c_fresh_process_global_start_dispatches_exact_item(tmp_path):
    from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory

    database = tmp_path / "phase121c-start.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    Session = make_session_factory(engine)
    with Session() as session:
        workflow = _workflow(
            session,
            project_id="FRESH_START",
            backlog=[
                _legacy_item("legacy_high") | {"priority": "HIGH", "created_order": 0},
                _item("modern_low", state=QueueWorkItemState.READY, priority="LOW", order=2),
            ],
        )
        session.commit()
        workflow_id = workflow.id

    next_result = subprocess.run(
        [sys.executable, "-m", "lucius.pilots.cli", "--database", str(database), "queue-global-next", workflow_id],
        capture_output=True,
        text=True,
        check=False,
    )
    start_result = subprocess.run(
        [sys.executable, "-m", "lucius.pilots.cli", "--database", str(database), "queue-global-start", workflow_id],
        capture_output=True,
        text=True,
        check=False,
    )

    assert next_result.returncode == 0
    assert start_result.returncode == 0
    next_payload = json.loads(next_result.stdout)
    start_payload = json.loads(start_result.stdout)
    assert next_payload["selected_item_id"] == "modern_low"
    assert start_payload["selected_item_id"] == "modern_low"
    assert start_payload["started_item_id"] == "modern_low"
    assert start_payload["mutation_identity_matches_selection"] is True

    with Session() as session:
        backlog = session.get(PersistentWorkflowORM, workflow_id).task_backlog
        by_id = {_item_id(item): item for item in backlog}
        assert "state" not in by_id["legacy_high"]
        assert by_id["modern_low"]["state"] == QueueWorkItemState.RUNNING.value


def test_phase121c_malformed_state_priority_are_inspectable_and_unschedulable(session):
    queue = NonBlockingQueueService(session)
    workflow = _workflow(
        session,
        project_id="MALFORMED",
        backlog=[
            _item("unknown_state", state=QueueWorkItemState.READY) | {"state": "TOTALLY_UNKNOWN"},
            _item("null_state", state=QueueWorkItemState.READY) | {"state": None},
            _item("bad_priority", state=QueueWorkItemState.READY, priority="NORMAL") | {"priority": "WILD"},
            _item("valid_low", state=QueueWorkItemState.READY, priority="LOW"),
        ],
    )
    before = list(session.get(PersistentWorkflowORM, workflow.id).task_backlog)

    status = queue.inspect_global([workflow.id])
    selection = queue.select_global_next([workflow.id])
    started = queue.start_global_next([workflow.id])
    after = session.get(PersistentWorkflowORM, workflow.id).task_backlog

    assert selection.selected_item_id == "valid_low"
    assert started.started_item_id == "valid_low"
    excluded = {item.item_id: item.exclusion_reason for item in status.next_selection.excluded_items}
    assert excluded["unknown_state"] == "MALFORMED_UNSCHEDULABLE:UNKNOWN_STATE:TOTALLY_UNKNOWN"
    assert excluded["null_state"] == "MALFORMED_UNSCHEDULABLE:NULL_STATE"
    assert excluded["bad_priority"] == "MALFORMED_UNSCHEDULABLE:UNKNOWN_PRIORITY:WILD"
    by_id = {_item_id(item): item for item in after}
    assert by_id["valid_low"]["state"] == QueueWorkItemState.RUNNING.value
    assert by_id["unknown_state"] == before[0]
    assert by_id["null_state"] == before[1]
    assert by_id["bad_priority"] == before[2]


def test_phase121c_scoped_start_cannot_execute_legacy_or_malformed_priority(session):
    queue = NonBlockingQueueService(session)
    legacy = _workflow(session, project_id="SCOPED_LEGACY", backlog=[_legacy_item("legacy")])
    try:
        queue.start_next(legacy.id)
    except QueueStateError as error:
        assert str(error) == "Queue work item is not executable: LEGACY_UNSCHEDULABLE:MISSING_STATE"
    else:
        raise AssertionError("Scoped start should reject missing queue state")

    malformed_priority = _workflow(
        session,
        project_id="SCOPED_PRIORITY",
        backlog=[_item("bad_priority", state=QueueWorkItemState.READY, priority="WILD")],
    )
    try:
        queue.start_next(malformed_priority.id)
    except QueueStateError as error:
        assert str(error) == "Queue work item is not executable: MALFORMED_UNSCHEDULABLE:UNKNOWN_PRIORITY:WILD"
    else:
        raise AssertionError("Scoped start should reject malformed priority")


def test_cross_project_release_gate_requires_phase121b_stale_lifecycle_evidence(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _cross_project_evaluation_row(session, include_repair_checks=False)
    before = _benchmark(session, score=100.0, failed=0)

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=before.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
    )

    assert result.passed is False
    assert result.recommendation == AutonomyRecommendation.NOT_READY_FOR_NON_BLOCKING_PROJECT_QUEUE


def test_phase121c_release_gate_requires_exact_dispatch_evidence(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _cross_project_evaluation_row(session, include_repair_checks=True)
    before = _benchmark(session, score=100.0, failed=0)

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=before.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
    )

    assert result.passed is True
    assert result.recommendation == AutonomyRecommendation.READY_FOR_MULTI_PROJECT_NON_BLOCKING_QUEUE


def _workflow(
    session,
    *,
    project_id: str,
    backlog: list[dict],
    workflow_state: PersistentWorkflowState = PersistentWorkflowState.PLAN_READY,
    completed_task_ids: list[str] | None = None,
    pending_task_ids: list[str] | None = None,
):
    workflow = PersistentWorkflowService(session).create(
        objective=f"{project_id} Phase 1.21B queue scenario.",
        expected_main_head="lucius-head",
        isolated_branch=f"lucius/phase-1.21b-{project_id.lower()}",
        worktree_path=f"/tmp/lucius-phase-1.21b-{project_id.lower()}",
        authority_tier="READY_FOR_MULTI_PROJECT_NON_BLOCKING_QUEUE",
        project_id=project_id,
        task_backlog=backlog,
        dependency_graph={_item_id(item): item.get("dependencies", []) for item in backlog},
        active_task_id=None,
        completed_task_ids=completed_task_ids or [],
        pending_task_ids=pending_task_ids if pending_task_ids is not None else [_item_id(item) for item in backlog],
    )
    row = session.get(PersistentWorkflowORM, workflow.id)
    row.workflow_state = workflow_state.value
    session.flush()
    return workflow


def _item(
    item_id: str,
    *,
    state: QueueWorkItemState,
    priority: str = "NORMAL",
    dependencies: list[str] | None = None,
    logical_task_id: str | None = None,
    order: int = 1,
) -> dict:
    return {
        "item_id": item_id,
        "logical_task_id": logical_task_id or item_id,
        "task_id": logical_task_id or item_id,
        "title": item_id,
        "state": state.value,
        "priority": priority,
        "created_order": order,
        "dependencies": dependencies or [],
        "completed_substeps": [],
        "version": 0,
    }


def _legacy_item(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "title": task_id,
        "objective": f"Historical task {task_id}",
        "status": "PENDING",
    }


def _item_id(item: dict) -> str:
    return str(item.get("item_id") or item.get("task_id"))


def _cross_project_evaluation_row(
    session,
    *,
    include_repair_checks: bool,
) -> EngineeringPlanEvaluationORM:
    row = _plan_evaluation(session, EngineeringPlanEvaluationResult.PASS)
    row.evaluation_mode = EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION.value
    row.implementation_artifact = {
        "pilot_stage": "CROSS_PROJECT_NON_BLOCKING_QUEUE_PILOT",
        "cross_project_evaluation": {"result": "PASS"},
        "global_scheduler_evaluation": {"result": "PASS"},
        "fresh_context_reconstruction": {"result": "PASS"},
        "duplicate_work_protection": {"result": "PASS"},
        "project_isolation": {"result": "PASS"},
        "safe_interruption": {"result": "PASS"},
        "multi_project_non_blocking_tested": True,
    }
    if include_repair_checks:
        row.implementation_artifact.update(
            {
                "stale_history_safety": _evidenced_claim("stale_history_safety"),
                "lifecycle_scope_safety": _evidenced_claim("lifecycle_scope_safety"),
                "unscoped_global_reconstruction": _evidenced_claim("unscoped_global_reconstruction"),
                "exact_global_dispatch": _evidenced_claim("exact_global_dispatch"),
                "global_selection_equals_mutation": _evidenced_claim("global_selection_equals_mutation"),
                "malformed_persistence_safety": _evidenced_claim("malformed_persistence_safety"),
                "stale_selection_safety": _evidenced_claim("stale_selection_safety"),
                "scoped_lifecycle_execution_safety": _evidenced_claim("scoped_lifecycle_execution_safety"),
                "scoped_malformed_persistence_safety": _evidenced_claim("scoped_malformed_persistence_safety"),
                "global_and_scoped_policy_parity": _evidenced_claim("global_and_scoped_policy_parity"),
                "no_preemption": _evidenced_claim("no_preemption"),
                "dependency_scope_isolation": _evidenced_claim("dependency_scope_isolation"),
            }
        )
    row.evaluator_version = "1.21b-test"
    row.evaluated_at = utc_now()
    session.flush()
    return row


def _evidenced_claim(name: str) -> dict:
    return {
        "result": "PASS",
        "observed_result": "PASS",
        "evidence_artifact_id": f"LEVID_{name}",
        "test_probe_id": f"test_{name}",
        "expected_invariant": f"{name} invariant is verified by Phase 1.21 regression evidence.",
    }
