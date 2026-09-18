import pytest
from sqlalchemy.orm import Session

from lucius.domain.enums import (
    Actor,
    AllowedAction,
    AuthorityLevel,
    DurableWaitClass,
    Environment,
    MissionStatus,
    QueueWorkItemState,
    TaskComplexity,
    TaskPriority,
)
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import PersistentWorkflowORM
from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
from lucius.runtime.continuation import BoundedContinuationService, SessionBudget
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.schemas import (
    ExecutionAdapterResult,
    ModelCapabilityProfile,
    RuntimeExecutionSupervision,
    RuntimeProviderModel,
    RuntimeProviderRegistration,
)
from lucius.runtime.service import ExecutionRuntimeLoopService
from tests.integration.test_native_execution_runtime_loop import _item, _workflow


def test_gate_d_blocked_states_remain_blocked_and_supervised_only_rejected():
    engine = create_sqlite_engine(":memory:")
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()

    # Create backlog items in BLOCKED_AUTHORITY, BLOCKED_DECISION, and WAITING_DEPENDENCY
    item_authority = _item("task_blocked_authority", order=1)
    item_authority["state"] = QueueWorkItemState.BLOCKED_AUTHORITY.value

    item_decision = _item("task_blocked_decision", order=2)
    item_decision["state"] = QueueWorkItemState.BLOCKED_DECISION.value

    item_dep = _item("task_waiting_dep", order=3)
    item_dep["state"] = QueueWorkItemState.WAITING_DEPENDENCY.value

    item_runnable = _item("task_runnable", order=4)
    item_runnable["state"] = QueueWorkItemState.READY.value

    wf = _workflow(
        session,
        project_id="GATED",
        backlog=[item_authority, item_decision, item_dep, item_runnable],
    )

    supervisor = DurableMissionSupervisor(session)
    mission = supervisor.create_mission(canonical_sha="HEAD", metadata={"scenario": "Gate D"})
    mission_id = mission.mission_id

    # Prepare workflow plan so workflow is PLAN_READY with frozen plan
    planning_adapter = ScriptedRuntimePlanningAdapter()
    planning_adapter.prepare_workflow_plan(session, wf.id)

    # 1. Test Dispatcher: verify BLOCKED_AUTHORITY, BLOCKED_DECISION, WAITING_DEPENDENCY are skipped,
    # and item_runnable is selected.
    dispatcher = MultiProjectDispatcher(session)
    selection = dispatcher.start_next([wf.id])
    assert selection.selected is not None
    assert selection.selected.item_id == "task_runnable"

    # Reset task_runnable state back to READY for continuation session
    wf_obj = session.get(PersistentWorkflowORM, wf.id)
    backlog = list(wf_obj.task_backlog)
    for i in backlog:
        if i["item_id"] == "task_runnable":
            i["state"] = QueueWorkItemState.READY.value
    from lucius.persistence.json_fields import set_json_field
    set_json_field(wf_obj, "task_backlog", backlog)
    session.flush()

    # 2. Test Router & Provider Qualification: Simulate qwen3-coder:30b provider registered as SUPERVISED_ONLY
    provider_map = {
        "task_runnable": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["Runnable task executed"],
            evidence=[{"item": "runnable_pass"}],
            verification=[{"type": "scripted_verification", "result": "PASS"}],
        ),
    }

    # Setup provider representing qwen3-coder:30b with SUPERVISED_ONLY constraint
    provider_coder_30b = ScriptedExecutionAdapter(
        provider_map,
        provider_id="ollama-qwen3-coder-30b",
        model_id="qwen3-coder:30b",
    )
    # Configure capability profile to SUPERVISED_ONLY
    cap_profile = ModelCapabilityProfile(
        provider_id="ollama-qwen3-coder-30b",
        model_id="qwen3-coder:30b",
        execution_tier="SUPERVISED_ONLY",
        supervision_required=True,
        unattended_eligible=False,
        unattended_mutation_eligible=False,
    )
    provider_coder_30b.registration.models[0].capability_profile = cap_profile

    # Also register standard unsupervised worker qwen3:8b
    provider_qwen8b = ScriptedExecutionAdapter(
        provider_map,
        provider_id="ollama-qwen3-8b",
        model_id="qwen3:8b",
    )

    registry = RuntimeProviderRegistry([provider_coder_30b, provider_qwen8b])
    execution_router = ModelExecutionRouter(
        session,
        registry=registry,
        actor=Actor.LUCIUS,
        default_execution_supervision=RuntimeExecutionSupervision.UNSUPERVISED,
    )

    runtime_service = ExecutionRuntimeLoopService(
        session=session,
        planning_adapter=planning_adapter,
        execution_router=execution_router,
        dispatcher=dispatcher,
    )

    continuation = BoundedContinuationService(
        session=session,
        runtime_service=runtime_service,
        supervisor=supervisor,
    )

    res = continuation.run_session(SessionBudget(max_cycles=1), mission_id=mission_id, workflow_ids=[wf.id])

    assert res.tasks_selected == 1
    assert res.tasks_completed == 1

    # Verify that qwen3-coder:30b was NEVER selected for unattended execution
    assert res.cycle_records[0].task_id == "task_runnable"

    # Verify blocked items remain BLOCKED_AUTHORITY, BLOCKED_DECISION, WAITING_DEPENDENCY
    wf_updated = session.get(PersistentWorkflowORM, wf.id)
    backlog_states = {item["item_id"]: item["state"] for item in wf_updated.task_backlog}

    assert backlog_states["task_blocked_authority"] == QueueWorkItemState.BLOCKED_AUTHORITY.value
    assert backlog_states["task_blocked_decision"] == QueueWorkItemState.BLOCKED_DECISION.value
    assert backlog_states["task_waiting_dep"] == QueueWorkItemState.WAITING_DEPENDENCY.value
    assert backlog_states["task_runnable"] == QueueWorkItemState.COMPLETED.value

    session.close()
