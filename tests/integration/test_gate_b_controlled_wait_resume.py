from datetime import datetime
import pytest
from sqlalchemy.orm import Session

from lucius.domain.enums import (
    AllowedAction,
    Actor,
    AuthorityLevel,
    DurableWaitClass,
    Environment,
    MissionStatus,
    QueueWorkItemState,
    TaskComplexity,
    TaskPriority,
)
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import PersistentWorkflowORM, ProjectORM, ProjectRepositoryAttachmentORM, RepositoryRegistrationORM
from lucius.pilots.queue import NonBlockingQueueService
from lucius.pilots.workflows import PersistentWorkflowService
from lucius.tasks.service import TaskService
from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
from lucius.runtime.continuation import BoundedContinuationService, ContinuationStopReason, SessionBudget
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.schemas import ExecutionAdapterResult, ExecutionRuntimeLoopResult, RuntimeLoopConfig, RuntimeLoopStatus
from lucius.runtime.service import ExecutionRuntimeLoopService
from tests.integration.test_native_execution_runtime_loop import _item, _workflow


def test_gate_b_controlled_wait_other_work_resume():
    engine = create_sqlite_engine(":memory:")
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()

    item_a = _item("task_gate_a", order=1)
    item_b = _item("task_gate_b", order=2)
    wf = _workflow(session, project_id="GATEB", backlog=[item_a, item_b])

    supervisor = DurableMissionSupervisor(session)
    mission = supervisor.create_mission(canonical_sha="HEAD", metadata={"scenario": "Gate B"})
    mission_id = mission.mission_id

    # Step 1: Task A is selected, but provider/resource is initially unavailable
    # Record wait on Task A
    wait_record = supervisor.record_wait(
        mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Resource initially unavailable for Task A",
        item_id="task_gate_a",
        task_id="task_gate_a",
    )
    session.flush()

    # Verify Task A state -> WAITING_RESOURCE
    wf = session.get(PersistentWorkflowORM, wf.id)
    items = wf.task_backlog
    item_a_record = next(i for i in items if i["item_id"] == "task_gate_a")
    assert item_a_record["state"] == QueueWorkItemState.WAITING_RESOURCE.value

    # Verify Mission state -> ACTIVE or WAITING (NOT COMPLETED, NOT FAILED)
    mission_rec = supervisor.get_mission(mission_id)
    assert mission_rec.status in (MissionStatus.ACTIVE, MissionStatus.WAITING)
    assert mission_rec.status not in (MissionStatus.COMPLETED, MissionStatus.FAILED)
    assert mission_rec.waiting_tasks_count >= 1

    # Step 2: Dispatcher runs next cycle. Task B is independently runnable.
    provider_map = {
        "task_gate_b": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["Task B executed independently"],
            evidence=[{"item": "b_pass"}],
            verification=[{"type": "scripted_verification", "result": "PASS"}],
        ),
        "task_gate_a": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["Task A executed after wake"],
            evidence=[{"item": "a_pass"}],
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

    # Run session cycle for Task B
    res_b = continuation.run_session(SessionBudget(max_cycles=1), mission_id=mission_id, workflow_ids=[wf.id])
    assert res_b.tasks_selected == 1
    assert res_b.tasks_completed == 1

    # Verify Task B -> COMPLETED
    wf = session.get(PersistentWorkflowORM, wf.id)
    item_b_record = next(i for i in wf.task_backlog if i["item_id"] == "task_gate_b")
    assert item_b_record["state"] == QueueWorkItemState.COMPLETED.value

    # Mission status becomes WAITING because Task B is COMPLETED and Task A is still in WAITING_RESOURCE
    mission_rec = supervisor.get_mission(mission_id)
    assert mission_rec.status == MissionStatus.WAITING

    # Step 3: Make Task A's compatible QUALIFIED resource available (clear wait checkpoint)
    cleared = supervisor.clear_wait(wait_record.wait_id)
    assert cleared.is_cleared is True

    # Verify Task A state restored -> READY
    wf = session.get(PersistentWorkflowORM, wf.id)
    item_a_record = next(i for i in wf.task_backlog if i["item_id"] == "task_gate_a")
    assert item_a_record["state"] == QueueWorkItemState.READY.value

    # Step 4: Run session cycle for Task A to resume and complete without operator intervention
    res_a = continuation.run_session(SessionBudget(max_cycles=1), mission_id=mission_id, workflow_ids=[wf.id])
    assert res_a.tasks_selected == 1
    assert res_a.tasks_completed == 1

    # Verify Task A -> COMPLETED
    wf = session.get(PersistentWorkflowORM, wf.id)
    item_a_record = next(i for i in wf.task_backlog if i["item_id"] == "task_gate_a")
    assert item_a_record["state"] == QueueWorkItemState.COMPLETED.value

    # Mission status -> COMPLETED
    final_mission = supervisor.reconcile_mission_state(mission_id)
    assert final_mission.status == MissionStatus.COMPLETED
    assert final_mission.completed_tasks_count == 2
    assert final_mission.waiting_tasks_count == 0

    session.close()
