from pathlib import Path
import pytest

from lucius.domain.enums import Actor, DurableWaitClass, MissionStatus, QueueWorkItemState
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import PersistentWorkflowORM
from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.projects.regression_guard import RegressionGuardValidator
from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
from lucius.runtime.continuation import BoundedContinuationService, ContinuationStopReason, SessionBudget
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.feeder import DarwinBacklogFeeder
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.schemas import ExecutionAdapterResult
from lucius.runtime.service import ExecutionRuntimeLoopService


def test_gate_f_wait_semantics_against_real_backlog():
    lucius_root = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering")
    darwin_root = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine")

    engine = create_sqlite_engine(":memory:")
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()

    registry_path = lucius_root / "src" / "lucius" / "projects" / "dfg_canonical_registry.json"
    registry = DFGProjectRegistry.load_json(registry_path)
    guard = RegressionGuardValidator(registry)
    feeder = DarwinBacklogFeeder(darwin_root=darwin_root, registry=registry, regression_guard=guard)

    # Ingest 3 real items from Darwin master backlog
    ingested = feeder.feed_into_queue(session, max_items=3)
    assert len(ingested) >= 1

    supervisor = DurableMissionSupervisor(session)
    mission = supervisor.create_mission(canonical_sha="HEAD", metadata={"scenario": "Gate F Real Backlog Wait"})
    mission_id = mission.mission_id

    # Place item 0 into WAITING_RESOURCE
    first_item = ingested[0]
    supervisor.record_wait(
        mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Rate limit / temporary resource constraint on item 0",
        item_id=first_item["item_id"],
        task_id=first_item["task_id"],
    )
    session.flush()

    # If there is a second item, place it into WAITING_DEPENDENCY
    if len(ingested) >= 2:
        second_item = ingested[1]
        supervisor.record_wait(
            mission_id,
            wait_class=DurableWaitClass.DEPENDENCY,
            reason="Waiting on upstream task completion",
            item_id=second_item["item_id"],
            task_id=second_item["task_id"],
        )
        session.flush()

    # If there is a third item, place it into WAITING_SCHEDULE
    if len(ingested) >= 3:
        third_item = ingested[2]
        supervisor.record_wait(
            mission_id,
            wait_class=DurableWaitClass.SCHEDULE,
            reason="Waiting on scheduled execution window",
            item_id=third_item["item_id"],
            task_id=third_item["task_id"],
        )
        session.flush()

    # Create dummy provider map (all items waiting, none runnable)
    provider_map = {
        item["item_id"]: ExecutionAdapterResult(outcome="COMPLETED")
        for item in ingested
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

    res = continuation.run_session(
        SessionBudget(max_cycles=5),
        mission_id=mission_id,
        workflow_ids=[item["workflow_id"] for item in ingested],
    )

    # Session must stop with status WAITING and stop_reason WAITING_NO_CURRENTLY_RUNNABLE_WORK
    assert res.status == "WAITING"
    assert res.stop_reason == ContinuationStopReason.WAITING_NO_CURRENTLY_RUNNABLE_WORK.value
    assert res.tasks_selected == 0
    assert res.tasks_completed == 0

    # Verify mission record status -> WAITING with waiting_tasks_count >= 1
    final_mission = supervisor.reconcile_mission_state(mission_id)
    assert final_mission.status == MissionStatus.WAITING
    assert final_mission.waiting_tasks_count == len(ingested)

    session.close()
