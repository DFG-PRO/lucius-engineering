from pathlib import Path
import pytest

from lucius.domain.enums import Actor, QueueWorkItemState
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import PersistentWorkflowORM
from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.projects.regression_guard import RegressionGuardValidator
from lucius.runtime.adapters import ScriptedRuntimePlanningAdapter
from lucius.runtime.continuation import BoundedContinuationService, SessionBudget
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.feeder import DarwinBacklogFeeder
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.ollama import OllamaExecutionProvider
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.service import ExecutionRuntimeLoopService


def test_gate_e_real_ollama_execution_regression():
    # Verify preflight/ollama availability before running real Ollama test
    lucius_root = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering")
    darwin_root = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine")
    billy_root = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Billy Production Engine/billy-production-engine")

    engine = create_sqlite_engine(":memory:")
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()

    registry_path = lucius_root / "src" / "lucius" / "projects" / "dfg_canonical_registry.json"
    registry = DFGProjectRegistry.load_json(registry_path)
    guard = RegressionGuardValidator(registry)
    feeder = DarwinBacklogFeeder(darwin_root=darwin_root, registry=registry, regression_guard=guard)

    ingested = feeder.feed_into_queue(session, max_items=1)
    assert len(ingested) == 1
    item_info = ingested[0]
    assert item_info["item_id"] == "FEED-RBACK-MON-001"

    supervisor = DurableMissionSupervisor(session)
    mission = supervisor.create_mission(canonical_sha="HEAD", metadata={"scenario": "Gate E Real Ollama"})
    mission_id = mission.mission_id

    # Configure real local Ollama provider with qwen3:8b
    ollama_provider = OllamaExecutionProvider(
        provider_id="ollama-local",
        model="qwen3:8b",
        timeout_seconds=600,
        allowed_workspace_roots=[lucius_root, darwin_root, billy_root],
    )
    provider_registry = RuntimeProviderRegistry([ollama_provider])

    planning_adapter = ScriptedRuntimePlanningAdapter()
    execution_router = ModelExecutionRouter(
        session,
        registry=provider_registry,
        actor=Actor.LUCIUS,
    )
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
        feeder=feeder,
    )

    res = continuation.run_session(
        SessionBudget(max_cycles=1),
        mission_id=mission_id,
        workflow_ids=[item_info["workflow_id"]],
    )

    assert res.tasks_selected == 1
    assert res.tasks_completed == 1
    assert len(res.cycle_records) == 1

    cycle = res.cycle_records[0]
    assert cycle.task_id == "FEED-RBACK-MON-001"
    assert cycle.outcome == "COMPLETED"
    assert cycle.duration_seconds > 0.0

    # Verify state in DB backlog -> COMPLETED
    wf = session.get(PersistentWorkflowORM, item_info["workflow_id"])
    target_item = next(i for i in wf.task_backlog if i["item_id"] == "FEED-RBACK-MON-001")
    assert target_item["state"] == QueueWorkItemState.COMPLETED.value

    # Verify mission record updated -> COMPLETED
    final_mission = supervisor.reconcile_mission_state(mission_id)
    assert final_mission.status.value == "COMPLETED"
    assert final_mission.completed_tasks_count == 1

    session.close()
