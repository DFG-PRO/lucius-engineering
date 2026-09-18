import os
import tempfile
import pytest

from lucius.domain.enums import (
    Actor,
    DurableWaitClass,
    MissionStatus,
    QueueWorkItemState,
)
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import DurableMissionORM, PersistentWorkflowORM
from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
from lucius.runtime.continuation import BoundedContinuationService, SessionBudget
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.schemas import ExecutionAdapterResult
from lucius.runtime.service import ExecutionRuntimeLoopService
from tests.integration.test_native_execution_runtime_loop import _item, _workflow


def test_gate_c_process_restart_recovery():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_file:
        db_path = tmp_file.name

    try:
        db_url = f"sqlite:///{db_path}"
        engine1 = create_sqlite_engine(db_url)
        create_all(engine1)
        factory1 = make_session_factory(engine1)
        session1 = factory1()

        item_c = _item("task_gate_c", order=1)
        wf1 = _workflow(session1, project_id="GATEC", backlog=[item_c])
        wf1_id = wf1.id

        supervisor1 = DurableMissionSupervisor(session1)
        mission1 = supervisor1.create_mission(canonical_sha="HEAD", metadata={"scenario": "Gate C"})
        mission_id = mission1.mission_id

        # Record wait on Task C
        wait_record = supervisor1.record_wait(
            mission_id,
            wait_class=DurableWaitClass.RESOURCE,
            reason="Resource unavailable for Task C in Process 1",
            item_id="task_gate_c",
            task_id="task_gate_c",
        )
        session1.commit()
        session1.close()
        engine1.dispose()

        # Simulate process crash / termination: Process 1 is gone.

        # Process 2: Load SAME SQLite DB from disk
        engine2 = create_sqlite_engine(db_url)
        factory2 = make_session_factory(engine2)
        session2 = factory2()

        supervisor2 = DurableMissionSupervisor(session2)

        # Negative test: Recovery fails if canonical SHA mismatch
        with pytest.raises(ValueError, match="Canonical SHA mismatch"):
            supervisor2.recover_mission(mission_id, current_canonical_sha="WRONG_SHA")

        # Valid recovery with matching SHA
        recovered_mission = supervisor2.recover_mission(mission_id, current_canonical_sha="HEAD")
        assert recovered_mission.mission_id == mission_id
        assert recovered_mission.canonical_sha == "HEAD"
        assert recovered_mission.status in (MissionStatus.ACTIVE, MissionStatus.WAITING)

        # Verify no duplicate workflows created
        wf_count = session2.query(PersistentWorkflowORM).filter_by(project_id="GATEC").count()
        assert wf_count == 1

        # Clear wait checkpoint on Task C in Process 2
        cleared = supervisor2.clear_wait(wait_record.wait_id)
        assert cleared.is_cleared is True

        provider_map = {
            "task_gate_c": ExecutionAdapterResult(
                outcome="COMPLETED",
                completed_substeps=["Task C resumed and completed in Process 2"],
                evidence=[{"item": "c_pass"}],
                verification=[{"type": "scripted_verification", "result": "PASS"}],
            ),
        }

        planning_adapter = ScriptedRuntimePlanningAdapter()
        provider = ScriptedExecutionAdapter(provider_map)
        provider_registry = RuntimeProviderRegistry([provider])
        execution_router = ModelExecutionRouter(session2, registry=provider_registry, actor=Actor.LUCIUS)
        dispatcher = MultiProjectDispatcher(session2)
        runtime_service = ExecutionRuntimeLoopService(
            session=session2,
            planning_adapter=planning_adapter,
            execution_router=execution_router,
            dispatcher=dispatcher,
        )

        continuation = BoundedContinuationService(
            session=session2,
            runtime_service=runtime_service,
            supervisor=supervisor2,
        )

        res_c = continuation.run_session(SessionBudget(max_cycles=1), mission_id=mission_id, workflow_ids=[wf1_id])
        assert res_c.tasks_selected == 1
        assert res_c.tasks_completed == 1

        # Verify Task C -> COMPLETED
        wf2 = session2.get(PersistentWorkflowORM, wf1_id)
        item_c_record = next(i for i in wf2.task_backlog if i["item_id"] == "task_gate_c")
        assert item_c_record["state"] == QueueWorkItemState.COMPLETED.value

        # Verify final mission status -> COMPLETED with 0 duplicates
        final_mission = supervisor2.reconcile_mission_state(mission_id)
        assert final_mission.status == MissionStatus.COMPLETED
        assert final_mission.completed_tasks_count == 1

        session2.close()
        engine2.dispose()

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
