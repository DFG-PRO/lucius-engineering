import pytest
from sqlalchemy.orm import Session

from lucius.domain.enums import (
    Actor,
    DurableWaitClass,
    MissionStatus,
    QueueWorkItemState,
)
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import PersistentWorkflowORM
from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
from lucius.runtime.continuation import BoundedContinuationService, ContinuationStopReason, SessionBudget
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.schemas import ExecutionAdapterResult
from lucius.runtime.service import ExecutionRuntimeLoopService
from tests.integration.test_native_execution_runtime_loop import _item, _workflow


def test_gate_g_short_endurance_controlled_mission():
    engine = create_sqlite_engine(":memory:")
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()

    # Create multi-project backlog across DARWIN, LUCIUS, BILLY
    darwin_a = _item("DARWIN-ENDURANCE-01", order=1)
    darwin_b = _item("DARWIN-ENDURANCE-02", order=2)
    wf_darwin = _workflow(session, project_id="DARWIN", backlog=[darwin_a, darwin_b])

    lucius_a = _item("LUCIUS-ENDURANCE-01", order=1)
    lucius_b = _item("LUCIUS-ENDURANCE-02", order=2)
    wf_lucius = _workflow(session, project_id="LUCIUS", backlog=[lucius_a, lucius_b])

    billy_a = _item("BILLY-ENDURANCE-01", order=1)
    wf_billy = _workflow(session, project_id="BILLY", backlog=[billy_a])

    supervisor = DurableMissionSupervisor(session)
    mission = supervisor.create_mission(canonical_sha="HEAD", metadata={"scenario": "Gate G Endurance"})
    mission_id = mission.mission_id

    # Record a temporary wait on LUCIUS-ENDURANCE-02
    wait_rec = supervisor.record_wait(
        mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Resource temporarily busy during endurance run",
        item_id="LUCIUS-ENDURANCE-02",
        task_id="LUCIUS-ENDURANCE-02",
    )
    session.flush()

    provider_map = {
        "DARWIN-ENDURANCE-01": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["Darwin 01 executed"],
            evidence=[{"item": "d1_pass"}],
            verification=[{"type": "scripted_verification", "result": "PASS"}],
        ),
        "DARWIN-ENDURANCE-02": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["Darwin 02 executed"],
            evidence=[{"item": "d2_pass"}],
            verification=[{"type": "scripted_verification", "result": "PASS"}],
        ),
        "LUCIUS-ENDURANCE-01": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["Lucius 01 executed"],
            evidence=[{"item": "l1_pass"}],
            verification=[{"type": "scripted_verification", "result": "PASS"}],
        ),
        "LUCIUS-ENDURANCE-02": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["Lucius 02 executed after resource clear"],
            evidence=[{"item": "l2_pass"}],
            verification=[{"type": "scripted_verification", "result": "PASS"}],
        ),
        "BILLY-ENDURANCE-01": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["Billy 01 executed"],
            evidence=[{"item": "b1_pass"}],
            verification=[{"type": "scripted_verification", "result": "PASS"}],
        ),
    }

    planning_adapter = ScriptedRuntimePlanningAdapter()
    provider = ScriptedExecutionAdapter(provider_map)
    provider_registry = RuntimeProviderRegistry([provider])
    execution_router = ModelExecutionRouter(session, registry=provider_registry, actor=Actor.LUCIUS)
    dispatcher = MultiProjectDispatcher(session)
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

    # Segment 1: Run up to 10 cycles while LUCIUS-ENDURANCE-02 is waiting
    workflow_ids = [wf_darwin.id, wf_lucius.id, wf_billy.id]
    res1 = continuation.run_session(
        SessionBudget(max_cycles=10, max_wall_seconds=300.0),
        mission_id=mission_id,
        workflow_ids=workflow_ids,
    )

    assert res1.tasks_selected == 4
    assert res1.tasks_completed == 4
    assert res1.operator_corrections == 0
    assert res1.post_launch_external_instructions == 0
    assert res1.wall_clock_duration_seconds > 0.0

    # Verify mission is in WAITING state because LUCIUS-ENDURANCE-02 is still waiting
    m1 = supervisor.get_mission(mission_id)
    assert m1.status == MissionStatus.WAITING
    assert m1.completed_tasks_count == 4
    assert m1.waiting_tasks_count >= 1

    # Segment 2: Clear wait checkpoint without human intervention
    cleared = supervisor.clear_wait(wait_rec.wait_id)
    assert cleared.is_cleared is True

    # Segment 3: Resume continuation session automatically
    res2 = continuation.run_session(
        SessionBudget(max_cycles=10, max_wall_seconds=300.0),
        mission_id=mission_id,
        workflow_ids=workflow_ids,
    )

    assert res2.tasks_selected == 1
    assert res2.tasks_completed == 1
    assert res2.operator_corrections == 0
    assert res2.post_launch_external_instructions == 0

    # Final mission state must be COMPLETED with 5 tasks completed across 3 projects
    final_mission = supervisor.reconcile_mission_state(mission_id)
    assert final_mission.status == MissionStatus.COMPLETED
    assert final_mission.completed_tasks_count == 5
    assert final_mission.waiting_tasks_count == 0
    assert final_mission.blocked_tasks_count == 0

    session.close()
