from __future__ import annotations

from lucius.domain.enums import (
    AutonomyRecommendation,
    EngineeringPlanEvaluationMode,
    EngineeringPlanEvaluationResult,
    PersistentWorkflowState,
    RepositoryIntegrityResult,
    RepositoryStateClassification,
    ResumeValidationResult,
)
from lucius.persistence.orm import EngineeringPlanEvaluationORM, utc_now
from lucius.pilots.release import ReleaseGateService
from lucius.pilots.rubric import HumanRubricService
from lucius.pilots.workflows import PersistentWorkflowService

from tests.integration.test_phase112_pilot_infrastructure import (
    _benchmark,
    _plan_evaluation,
    _repository_state,
)


def test_persistent_workflow_pause_resume_artifacts_are_durable(session):
    service = PersistentWorkflowService(session)
    workflow = service.create(
        objective="Add a read-only traceability matrix.",
        expected_main_head="abc123",
        isolated_branch="lucius/phase-1.19-traceability",
        worktree_path="/tmp/worktree",
        authority_tier="READY_FOR_PERSISTENT_SUPERVISED_ENGINEERING",
        task_backlog=[
            {"task_id": "LTASK_A", "title": "schemas"},
            {"task_id": "LTASK_B", "title": "service"},
        ],
        dependency_graph={"LTASK_A": [], "LTASK_B": ["LTASK_A"]},
        active_task_id="LTASK_A",
        pending_task_ids=["LTASK_A", "LTASK_B"],
        resume_requirements=["implementation HEAD matches checkpoint"],
    )
    checkpoint = service.record_checkpoint(
        workflow.id,
        checkpoint_state=PersistentWorkflowState.PAUSED,
        implementation_head="def456",
        expected_main_head="abc123",
        branch="lucius/phase-1.19-traceability",
        worktree_path="/tmp/worktree",
        active_task_id="LTASK_B",
        completed_task_ids=["LTASK_A"],
        pending_task_ids=["LTASK_B"],
        dependency_graph={"LTASK_A": [], "LTASK_B": ["LTASK_A"]},
        decisions=[{"decision": "reuse observability module"}],
        deviations=[],
        repair_counters={"total": 0, "by_task": {}, "limits": {"per_task": 3, "total": 8}},
        latest_test_results=[{"command": "pytest tests/test_traceability.py", "result": "PASS"}],
        known_warnings=[],
        authority_tier="READY_FOR_PERSISTENT_SUPERVISED_ENGINEERING",
        pending_human_approvals=[],
        resume_conditions=["worktree exists"],
        recommended_next_action="Resume with CLI task.",
    )
    validation = service.validate_resume(
        workflow.id,
        checkpoint_id=checkpoint.id,
        checks=[
            {"name": "workflow_record_exists", "status": "PASS"},
            {"name": "implementation_head_matches", "status": "PASS"},
            {"name": "next_task_eligible", "status": "PASS"},
        ],
        next_eligible_task_id="LTASK_B",
    )

    assert workflow.id == "LWORK_000001"
    assert checkpoint.id == "LWCHK_000001"
    assert validation.id == "LRESUME_000001"
    assert validation.result == ResumeValidationResult.SAFE_TO_RESUME


def test_persistent_resume_validation_blocks_on_blocking_mismatch(session):
    service = PersistentWorkflowService(session)
    workflow = service.create(
        objective="Persist resume state.",
        expected_main_head="abc123",
        isolated_branch="lucius/test",
        worktree_path="/tmp/worktree",
        authority_tier="READY_FOR_PERSISTENT_SUPERVISED_ENGINEERING",
    )
    checkpoint = service.record_checkpoint(
        workflow.id,
        checkpoint_state=PersistentWorkflowState.PAUSED,
        implementation_head="def456",
        expected_main_head="abc123",
        branch="lucius/test",
        worktree_path="/tmp/worktree",
        active_task_id=None,
        completed_task_ids=[],
        pending_task_ids=[],
        dependency_graph={},
        decisions=[],
        deviations=[],
        repair_counters={"total": 0},
        latest_test_results=[],
        known_warnings=[],
        authority_tier="READY_FOR_PERSISTENT_SUPERVISED_ENGINEERING",
        pending_human_approvals=[],
        resume_conditions=[],
        recommended_next_action="Stop.",
    )

    validation = service.validate_resume(
        workflow.id,
        checkpoint_id=checkpoint.id,
        checks=[
            {"name": "implementation_head_matches", "status": "FAIL", "severity": "BLOCKING"},
        ],
        next_eligible_task_id=None,
    )

    assert validation.result == ResumeValidationResult.BLOCKED


def test_persistent_pause_resume_gate_can_recommend_queue_pilot(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _persistent_workflow_evaluation_row(session, EngineeringPlanEvaluationResult.PASS)
    before = _benchmark(session, score=100.0, failed=0)
    after = _benchmark(session, score=100.0, failed=0)
    rubric = HumanRubricService(session).record_not_captured()

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=after.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
        human_rubric_id=rubric.id,
    )

    assert result.passed is True
    assert result.recommendation == AutonomyRecommendation.READY_FOR_NON_BLOCKING_PROJECT_QUEUE_PILOT
    assert result.recommendation != AutonomyRecommendation.READY_FOR_BOUNDED_WRITE_AUTONOMY


def test_persistent_pause_resume_gate_blocks_with_stage_specific_not_ready(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _persistent_workflow_evaluation_row(session, EngineeringPlanEvaluationResult.FAIL)
    before = _benchmark(session, score=100.0, failed=0)

    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=before.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
    )

    assert result.recommendation == AutonomyRecommendation.NOT_READY_FOR_PERSISTENT_SUPERVISED_ENGINEERING


def _persistent_workflow_evaluation_row(
    session,
    result: EngineeringPlanEvaluationResult,
) -> EngineeringPlanEvaluationORM:
    row = _plan_evaluation(session, result)
    row.evaluation_mode = EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION.value
    row.implementation_artifact = {
        "pilot_stage": "PERSISTENT_SUPERVISED_WORKFLOW",
        "final_workflow_state": "COMPLETED_PENDING_INTEGRATION",
        "pause_resume_evaluation": {
            "result": "PASS",
            "resume_decision": "SAFE_TO_RESUME",
        },
    }
    row.evaluator_version = "1.19-test"
    row.evaluated_at = utc_now()
    session.flush()
    return row
