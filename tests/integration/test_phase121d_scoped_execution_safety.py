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
from lucius.pilots.queue import (
    NonBlockingQueueService,
    QueueStateError,
    workflow_execution_exclusion_reason,
    workflow_is_execution_eligible,
)
from lucius.pilots.release import ReleaseGateService
from lucius.pilots.workflows import PersistentWorkflowService
from tests.integration.test_phase112_pilot_infrastructure import (
    _benchmark,
    _plan_evaluation,
    _repository_state,
)


def test_phase121d_start_next_rejects_lifecycle_ineligible_workflows(session):
    queue = NonBlockingQueueService(session)
    blocked_states = [
        PersistentWorkflowState.CLOSED,
        PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION,
        PersistentWorkflowState.PAUSED,
        PersistentWorkflowState.OBJECTIVE_ACCEPTED,
        PersistentWorkflowState.PLANNING,
    ]

    for state in blocked_states:
        workflow = _workflow(
            session,
            project_id=f"SCOPED_{state.value}",
            workflow_state=state,
            backlog=[_item("A", state=QueueWorkItemState.READY, priority="CRITICAL")],
        )
        before = list(session.get(PersistentWorkflowORM, workflow.id).task_backlog)

        try:
            queue.start_next(workflow.id)
        except QueueStateError as error:
            assert str(error) == f"Workflow is not executable: WORKFLOW_LIFECYCLE_INELIGIBLE:{state.value}"
        else:
            raise AssertionError(f"{state.value} workflow should not execute scoped work")

        row = session.get(PersistentWorkflowORM, workflow.id)
        assert row.active_task_id is None
        assert row.task_backlog == before


def test_phase121d_valid_scoped_workflow_states_can_start_ready_items(session):
    queue = NonBlockingQueueService(session)
    allowed_states = [
        PersistentWorkflowState.PLAN_READY,
        PersistentWorkflowState.IMPLEMENTING,
        PersistentWorkflowState.VERIFYING,
        PersistentWorkflowState.RESUME_VALIDATION,
        PersistentWorkflowState.APPROVED_TO_CONTINUE,
    ]

    for state in allowed_states:
        workflow = _workflow(
            session,
            project_id=f"VALID_{state.value}",
            workflow_state=state,
            backlog=[_item("A", state=QueueWorkItemState.READY)],
        )

        selection = queue.start_next(workflow.id)

        assert selection.selected_item_id == "A"
        row = session.get(PersistentWorkflowORM, workflow.id)
        assert row.active_task_id == "A"
        assert row.task_backlog[0]["state"] == QueueWorkItemState.RUNNING.value


def test_phase121d_scoped_malformed_and_legacy_state_are_structured_and_immutable(session):
    queue = NonBlockingQueueService(session)
    cases = [
        ("missing_state", _legacy_item("missing_state"), "LEGACY_UNSCHEDULABLE:MISSING_STATE"),
        ("null_state", _item("null_state", state=QueueWorkItemState.READY) | {"state": None}, "MALFORMED_UNSCHEDULABLE:NULL_STATE"),
        (
            "unknown_state",
            _item("unknown_state", state=QueueWorkItemState.READY) | {"state": "TOTALLY_UNKNOWN"},
            "MALFORMED_UNSCHEDULABLE:UNKNOWN_STATE:TOTALLY_UNKNOWN",
        ),
        (
            "bad_priority",
            _item("bad_priority", state=QueueWorkItemState.READY, priority="WILD"),
            "MALFORMED_UNSCHEDULABLE:UNKNOWN_PRIORITY:WILD",
        ),
    ]

    for project_id, item, expected_reason in cases:
        workflow = _workflow(session, project_id=project_id, backlog=[item])
        before = list(session.get(PersistentWorkflowORM, workflow.id).task_backlog)

        status = queue.inspect(workflow.id)
        selection = queue.select_next(workflow.id)
        try:
            queue.start_next(workflow.id)
        except QueueStateError as error:
            assert str(error) == f"Queue work item is not executable: {expected_reason}"
        else:
            raise AssertionError(f"{project_id} should not execute")

        assert selection.reason == "NO_ELIGIBLE_WORK"
        assert status.excluded[0]["exclusion_reason"] == expected_reason
        row = session.get(PersistentWorkflowORM, workflow.id)
        assert row.active_task_id is None
        assert row.task_backlog == before


def test_phase121d_scoped_start_excludes_malformed_sibling_and_starts_valid_item(session):
    queue = NonBlockingQueueService(session)
    workflow = _workflow(
        session,
        project_id="SCOPED_MIXED",
        backlog=[
            _item("unknown_state", state=QueueWorkItemState.READY, priority="CRITICAL")
            | {"state": "TOTALLY_UNKNOWN"},
            _item("bad_priority", state=QueueWorkItemState.READY, priority="WILD"),
            _item("valid_low", state=QueueWorkItemState.READY, priority="LOW", order=3),
        ],
    )

    selection = queue.start_next(workflow.id)

    assert selection.selected_item_id == "valid_low"
    row = session.get(PersistentWorkflowORM, workflow.id)
    by_id = {_item_id(item): item for item in row.task_backlog}
    assert by_id["valid_low"]["state"] == QueueWorkItemState.RUNNING.value
    assert by_id["unknown_state"]["state"] == "TOTALLY_UNKNOWN"
    assert by_id["bad_priority"]["state"] == QueueWorkItemState.READY.value


def test_phase121d_scoped_and_global_lifecycle_and_malformed_classification_agree(session):
    queue = NonBlockingQueueService(session)
    closed = _workflow(
        session,
        project_id="CLOSED_PARITY",
        workflow_state=PersistentWorkflowState.CLOSED,
        backlog=[_item("closed_ready", state=QueueWorkItemState.READY, priority="CRITICAL")],
    )
    malformed = _workflow(
        session,
        project_id="MALFORMED_PARITY",
        backlog=[_item("bad", state=QueueWorkItemState.READY) | {"state": "ALIEN"}],
    )

    closed_row = session.get(PersistentWorkflowORM, closed.id)
    assert workflow_is_execution_eligible(closed_row) is False
    assert workflow_execution_exclusion_reason(closed_row) == "WORKFLOW_LIFECYCLE_INELIGIBLE:CLOSED"
    assert queue.inspect_global([closed.id]).lifecycle_excluded[0].reason == "WORKFLOW_LIFECYCLE_INELIGIBLE:CLOSED"
    try:
        queue.start_next(closed.id)
    except QueueStateError as error:
        assert "WORKFLOW_LIFECYCLE_INELIGIBLE:CLOSED" in str(error)
    else:
        raise AssertionError("Scoped lifecycle classification should reject the closed workflow")

    scoped_status = queue.inspect(malformed.id)
    global_status = queue.inspect_global([malformed.id])
    assert scoped_status.excluded[0]["exclusion_reason"] == "MALFORMED_UNSCHEDULABLE:UNKNOWN_STATE:ALIEN"
    assert (
        global_status.next_selection.excluded_items[0].exclusion_reason
        == "MALFORMED_UNSCHEDULABLE:UNKNOWN_STATE:ALIEN"
    )


def test_phase121d_dependency_and_no_preemption_scoped_safety_remain_intact(session):
    queue = NonBlockingQueueService(session)
    workflow = _workflow(
        session,
        project_id="SCOPED_DEP",
        backlog=[
            _item("running", state=QueueWorkItemState.RUNNING),
            _item("blocked_dep", state=QueueWorkItemState.READY, priority="CRITICAL", dependencies=["missing"]),
        ],
    )

    selection = queue.select_next(workflow.id)
    started = queue.start_next(workflow.id)

    assert selection.reason == "RUNNING_ITEM_ACTIVE_NO_PREEMPTION"
    assert selection.running_item_ids == ["running"]
    assert started.reason == "RUNNING_ITEM_ACTIVE_NO_PREEMPTION"
    assert session.get(PersistentWorkflowORM, workflow.id).active_task_id is None

    workflow_dep = _workflow(
        session,
        project_id="SCOPED_DEP_ONLY",
        backlog=[_item("blocked_dep", state=QueueWorkItemState.READY, dependencies=["missing"])],
    )
    assert queue.select_next(workflow_dep.id).reason == "NO_ELIGIBLE_WORK"
    assert queue.start_next(workflow_dep.id).reason == "NO_ELIGIBLE_WORK"


def test_phase121d_fresh_context_mixed_execution_uses_only_valid_target(tmp_path):
    from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory

    database = tmp_path / "phase121d-mixed.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    Session = make_session_factory(engine)
    with Session() as session:
        closed = _workflow(
            session,
            project_id="CLOSED_FRESH",
            workflow_state=PersistentWorkflowState.CLOSED,
            backlog=[_item("closed_high", state=QueueWorkItemState.READY, priority="HIGH")],
        )
        completed_pending = _workflow(
            session,
            project_id="COMPLETED_PENDING_FRESH",
            workflow_state=PersistentWorkflowState.COMPLETED_PENDING_INTEGRATION,
            backlog=[_item("completed_critical", state=QueueWorkItemState.READY, priority="CRITICAL")],
        )
        malformed = _workflow(
            session,
            project_id="MALFORMED_FRESH",
            backlog=[_item("bad", state=QueueWorkItemState.READY, priority="CRITICAL") | {"state": None}],
        )
        valid = _workflow(
            session,
            project_id="VALID_FRESH",
            backlog=[_item("valid_low", state=QueueWorkItemState.READY, priority="LOW")],
        )
        session.commit()
        ids = {
            "closed": closed.id,
            "completed_pending": completed_pending.id,
            "malformed": malformed.id,
            "valid": valid.id,
        }

    global_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lucius.pilots.cli",
            "--database",
            str(database),
            "queue-global-start",
            ids["closed"],
            ids["completed_pending"],
            ids["malformed"],
            ids["valid"],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    closed_result = subprocess.run(
        [sys.executable, "-m", "lucius.pilots.cli", "--database", str(database), "queue-start", ids["closed"]],
        capture_output=True,
        text=True,
        check=False,
    )
    malformed_result = subprocess.run(
        [sys.executable, "-m", "lucius.pilots.cli", "--database", str(database), "queue-start", ids["malformed"]],
        capture_output=True,
        text=True,
        check=False,
    )

    assert global_result.returncode == 0
    assert json.loads(global_result.stdout)["started_item_id"] == "valid_low"
    assert closed_result.returncode == 1
    assert json.loads(closed_result.stdout)["reason"] == (
        "Workflow is not executable: WORKFLOW_LIFECYCLE_INELIGIBLE:CLOSED"
    )
    assert malformed_result.returncode == 1
    assert json.loads(malformed_result.stdout)["reason"] == (
        "Queue work item is not executable: MALFORMED_UNSCHEDULABLE:NULL_STATE"
    )

    with Session() as session:
        assert session.get(PersistentWorkflowORM, ids["closed"]).task_backlog[0]["state"] == QueueWorkItemState.READY.value
        assert (
            session.get(PersistentWorkflowORM, ids["completed_pending"]).task_backlog[0]["state"]
            == QueueWorkItemState.READY.value
        )
        assert session.get(PersistentWorkflowORM, ids["malformed"]).task_backlog[0]["state"] is None
        assert session.get(PersistentWorkflowORM, ids["valid"]).task_backlog[0]["state"] == QueueWorkItemState.RUNNING.value


def test_phase121d_release_gate_rejects_boolean_only_cross_project_evidence(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _cross_project_evaluation_row(session, EngineeringPlanEvaluationResult.PASS, evidenced=False)
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
    assert any("lacks linked evidence provenance" in blocker for blocker in result.blockers)


def test_phase121d_release_gate_requires_scoped_safety_evidence(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _cross_project_evaluation_row(session, EngineeringPlanEvaluationResult.PASS, evidenced=True)
    del evaluation.implementation_artifact["scoped_lifecycle_execution_safety"]
    before = _benchmark(session, score=100.0, failed=0)

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=before.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
    )

    assert result.passed is False
    assert "cross-project readiness claim missing or failed: scoped_lifecycle_execution_safety" in result.blockers


def test_phase121d_release_gate_blocks_unresolved_major_pass_with_warnings(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _cross_project_evaluation_row(
        session,
        EngineeringPlanEvaluationResult.PASS_WITH_WARNINGS,
        evidenced=True,
        corrections=[{"severity": "MAJOR", "dimension": "architecture_alignment", "message": "major mismatch"}],
    )
    before = _benchmark(session, score=100.0, failed=0)

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=before.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
    )

    assert result.passed is False
    assert "unresolved MAJOR/CRITICAL deterministic evaluation corrections" in result.blockers


def test_phase121d_release_gate_accepts_evidenced_major_warning_disposition(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _cross_project_evaluation_row(
        session,
        EngineeringPlanEvaluationResult.PASS_WITH_WARNINGS,
        evidenced=True,
        corrections=[{"severity": "MAJOR", "dimension": "architecture_alignment", "message": "major mismatch"}],
    )
    evaluation.implementation_artifact["evaluation_warning_disposition"] = _evidenced_claim(
        "LEVID_WARNING",
        "test_phase121d_release_gate_accepts_evidenced_major_warning_disposition",
        "MAJOR evaluator corrections are classified and linked to verification evidence.",
    ) | {"disposed_correction_dimensions": ["architecture_alignment"]}
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
    assert any("PASS_WITH_WARNINGS" in warning for warning in result.warnings)


def _workflow(
    session,
    *,
    project_id: str,
    backlog: list[dict],
    workflow_state: PersistentWorkflowState = PersistentWorkflowState.PLAN_READY,
):
    workflow = PersistentWorkflowService(session).create(
        objective=f"{project_id} Phase 1.21D queue scenario.",
        expected_main_head="lucius-head",
        isolated_branch=f"lucius/phase-1.21d-{project_id.lower()}",
        worktree_path=f"/tmp/lucius-phase-1.21d-{project_id.lower()}",
        authority_tier="READY_FOR_MULTI_PROJECT_NON_BLOCKING_QUEUE",
        project_id=project_id,
        task_backlog=backlog,
        dependency_graph={_item_id(item): item.get("dependencies", []) for item in backlog},
        active_task_id=None,
        completed_task_ids=[],
        pending_task_ids=[_item_id(item) for item in backlog],
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
    order: int = 1,
) -> dict:
    return {
        "item_id": item_id,
        "logical_task_id": item_id,
        "task_id": item_id,
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
    result: EngineeringPlanEvaluationResult,
    *,
    evidenced: bool,
    corrections: list[dict] | None = None,
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
        "scoped_lifecycle_execution_safety": {"result": "PASS"},
        "scoped_malformed_persistence_safety": {"result": "PASS"},
        "global_and_scoped_policy_parity": {"result": "PASS"},
        "no_preemption": {"result": "PASS"},
        "dependency_scope_isolation": {"result": "PASS"},
        "multi_project_non_blocking_tested": True,
    }
    if evidenced:
        for name, claim in list(row.implementation_artifact.items()):
            if isinstance(claim, dict) and claim.get("result") == "PASS":
                row.implementation_artifact[name] = _evidenced_claim(
                    f"LEVID_{name}",
                    f"test_{name}",
                    f"{name} invariant passes in Phase 1.21D regression evidence.",
                )
    row.corrections = corrections or []
    row.evaluator_version = "1.21d-test"
    row.evaluated_at = utc_now()
    session.flush()
    return row


def _evidenced_claim(evidence_id: str, test_probe_id: str, invariant: str) -> dict:
    return {
        "result": "PASS",
        "observed_result": "PASS",
        "evidence_artifact_id": evidence_id,
        "test_probe_id": test_probe_id,
        "expected_invariant": invariant,
    }
