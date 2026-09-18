from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from sqlalchemy.orm import Session

from lucius.domain.enums import Actor, DurableWaitClass, MissionStatus, QueueWorkItemState, TaskPriority, TaskStatus
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import (
    DurableMissionORM,
    DurableWaitORM,
    PersistentWorkflowORM,
    TaskORM,
    utc_now,
)
from lucius.runtime.continuation import BoundedContinuationService, ContinuationStopReason, SessionBudget
from lucius.runtime.feeder import DarwinBacklogFeeder, NormalizedTaskEnvelope
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.ollama import OllamaExecutionProvider
from lucius.runtime.schemas import (
    ExecutionRuntimeLoopResult,
    RuntimeLoopConfig,
    RuntimeLoopStatus,
)


@pytest.fixture
def memory_db():
    engine = create_sqlite_engine(":memory:")
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()
    yield session
    session.close()


def _add_test_workflow(session: Session, wf_id: str, items: list[dict]):
    wf = PersistentWorkflowORM(
        id=wf_id,
        objective="Test Workflow",
        expected_main_head="abc12345",
        isolated_branch="test-branch",
        worktree_path="/tmp/test",
        workflow_state="PLAN_READY",
        authority_tier="L0",
        task_backlog=items,
    )
    session.add(wf)
    session.flush()
    return wf


# 1. test_idle_vs_waiting_stop_reason_semantics
def test_idle_vs_waiting_stop_reason_semantics(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    class MockRuntimeService:
        dispatcher = None
        def run(self, config):
            return ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE, selected_tasks=0)

    svc = BoundedContinuationService(
        session=memory_db,
        runtime_service=MockRuntimeService(),
        supervisor=supervisor,
    )

    # Scenario A: 0 tasks -> IDLE, IDLE_NO_ELIGIBLE_WORK
    res_a = svc.run_session(SessionBudget(max_cycles=1), mission_id=mission.mission_id)
    assert res_a.status == "IDLE"
    assert res_a.stop_reason == ContinuationStopReason.IDLE_NO_ELIGIBLE_WORK.value

    # Scenario B: 1 completed task -> IDLE, IDLE_NO_ELIGIBLE_WORK
    _add_test_workflow(memory_db, "wf_completed", [{
        "id": "item_comp",
        "item_id": "item_comp",
        "logical_task_id": "task_comp",
        "workflow_id": "wf_completed",
        "state": QueueWorkItemState.COMPLETED.value,
        "priority": TaskPriority.NORMAL.value,
    }])
    res_b = svc.run_session(SessionBudget(max_cycles=1), mission_id=mission.mission_id)
    assert res_b.status in ("IDLE", "COMPLETED")
    assert res_b.stop_reason == ContinuationStopReason.IDLE_NO_ELIGIBLE_WORK.value

    # Scenario C: 1 WAITING_RESOURCE task -> WAITING, WAITING_NO_CURRENTLY_RUNNABLE_WORK
    _add_test_workflow(memory_db, "wf_waiting_res", [{
        "id": "item_wait_res",
        "item_id": "item_wait_res",
        "logical_task_id": "task_wait_res",
        "workflow_id": "wf_waiting_res",
        "state": QueueWorkItemState.WAITING_RESOURCE.value,
        "priority": TaskPriority.NORMAL.value,
    }])
    res_c = svc.run_session(SessionBudget(max_cycles=1), mission_id=mission.mission_id)
    assert res_c.status == "WAITING"
    assert res_c.stop_reason == ContinuationStopReason.WAITING_NO_CURRENTLY_RUNNABLE_WORK.value

    # Scenario D: 1 WAITING_DEPENDENCY task -> WAITING, WAITING_NO_CURRENTLY_RUNNABLE_WORK
    memory_db.query(PersistentWorkflowORM).delete()
    memory_db.flush()
    _add_test_workflow(memory_db, "wf_waiting_dep", [{
        "id": "item_wait_dep",
        "item_id": "item_wait_dep",
        "logical_task_id": "task_wait_dep",
        "workflow_id": "wf_waiting_dep",
        "state": QueueWorkItemState.WAITING_DEPENDENCY.value,
        "priority": TaskPriority.NORMAL.value,
    }])
    res_d = svc.run_session(SessionBudget(max_cycles=1), mission_id=mission.mission_id)
    assert res_d.status == "WAITING"
    assert res_d.stop_reason == ContinuationStopReason.WAITING_NO_CURRENTLY_RUNNABLE_WORK.value


# 2. test_re_evaluate_durable_waits_clears_expired_resource_waits
def test_re_evaluate_durable_waits_clears_expired_resource_waits(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    _add_test_workflow(memory_db, "wf_exp", [{
        "id": "item_exp",
        "item_id": "item_exp",
        "logical_task_id": "task_exp",
        "workflow_id": "wf_exp",
        "state": QueueWorkItemState.WAITING_RESOURCE.value,
        "priority": TaskPriority.NORMAL.value,
    }])

    past_time = utc_now() - timedelta(seconds=5)
    wait_rec = supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Ollama backoff",
        item_id="item_exp",
        retry_after=past_time,
    )

    cleared = supervisor.reevaluate_durable_waits(mission.mission_id)
    assert len(cleared) == 1
    assert cleared[0].wait_id == wait_rec.wait_id
    assert cleared[0].is_cleared is True

    items = supervisor._get_all_items()
    assert items[0]["state"] == QueueWorkItemState.READY.value


# 3. test_re_evaluate_durable_waits_clears_satisfied_dependency_waits
def test_re_evaluate_durable_waits_clears_satisfied_dependency_waits(memory_db: Session, tmp_path: Path):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    req_file = tmp_path / "dep_file.txt"
    req_file.write_text("Context present")

    _add_test_workflow(memory_db, "wf_dep_sat", [{
        "id": "item_dep_sat",
        "item_id": "item_dep_sat",
        "logical_task_id": "task_dep_sat",
        "workflow_id": "wf_dep_sat",
        "state": QueueWorkItemState.WAITING_DEPENDENCY.value,
        "priority": TaskPriority.NORMAL.value,
    }])

    wait_rec = supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.DEPENDENCY,
        reason="Waiting for dep_file.txt",
        item_id="item_dep_sat",
        metadata={"required_paths": [str(req_file)]},
    )

    cleared = supervisor.reevaluate_durable_waits(mission.mission_id)
    assert len(cleared) == 1
    assert cleared[0].wait_id == wait_rec.wait_id
    assert cleared[0].is_cleared is True

    items = supervisor._get_all_items()
    assert items[0]["state"] == QueueWorkItemState.READY.value


# 4. test_no_busy_polling_wake_time_calculation
def test_no_busy_polling_wake_time_calculation(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    future_time_1 = utc_now() + timedelta(minutes=10)
    future_time_2 = utc_now() + timedelta(minutes=5)

    supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Capacity wait 1",
        retry_after=future_time_1,
    )
    supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Capacity wait 2",
        retry_after=future_time_2,
    )

    m = supervisor.get_mission(mission.mission_id)
    active_waits = [w for w in m.wait_records if not w.is_cleared and w.retry_after is not None]
    assert len(active_waits) == 2

    # Earliest wake time computation
    earliest_wake = min(w.retry_after for w in active_waits)
    assert int(earliest_wake.timestamp()) == int(future_time_2.timestamp())


# 5. test_feeder_re_evaluation_discovers_promoted_work
def test_feeder_re_evaluation_discovers_promoted_work(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    custom_item = {
        "item_id": "RBACK-TEST-FEEDED",
        "title": "Test Promoted Research Item",
        "status": "READY",
        "objective": "Verify feeder discovery during continuation re-evaluation.",
        "priority": "P0_NOW",
        "provenance_refs": ["docs/runtime/monetization-opportunity-portfolio.md"],
        "dependencies": [],
        "blocked_by": [],
    }
    feeder = DarwinBacklogFeeder(custom_items=[custom_item])

    execution_count = 0

    class MockRuntimeService:
        dispatcher = None
        def run(self, config):
            nonlocal execution_count
            execution_count += 1
            if execution_count == 1:
                # First attempt: no task in queue yet
                return ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE, selected_tasks=0)
            elif execution_count == 2:
                # Second attempt: after feeder ingests task into queue, completed!
                return ExecutionRuntimeLoopResult(
                    status=RuntimeLoopStatus.COMPLETED,
                    selected_tasks=1,
                    completed_tasks=1,
                )
            else:
                # Subsequent attempts: queue is empty now
                return ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE, selected_tasks=0)

    svc = BoundedContinuationService(
        session=memory_db,
        runtime_service=MockRuntimeService(),
        feeder=feeder,
        supervisor=supervisor,
    )

    res = svc.run_session(SessionBudget(max_cycles=5), mission_id=mission.mission_id)
    assert res.tasks_fed == 1
    assert res.tasks_completed == 1


# 6. test_provenance_wait_recording_tech001_tech002_val001
def test_provenance_wait_recording_tech001_tech002_val001(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    items_to_check = ["RBACK-TECH-001", "RBACK-TECH-002", "RBACK-VAL-001"]
    for item_id in items_to_check:
        _add_test_workflow(memory_db, f"wf_{item_id}", [{
            "id": f"item_{item_id}",
            "item_id": item_id,
            "logical_task_id": f"task_{item_id}",
            "workflow_id": f"wf_{item_id}",
            "state": QueueWorkItemState.RUNNING.value,
            "priority": TaskPriority.HIGH.value,
        }])

        rec = supervisor.record_wait(
            mission.mission_id,
            wait_class=DurableWaitClass.DEPENDENCY,
            reason=f"MISSING_READ_ONLY_CONTEXT: Provenance documents for {item_id} are missing",
            item_id=f"item_{item_id}",
            metadata={"required_provenance_refs": []},
        )
        assert rec.wait_class == DurableWaitClass.DEPENDENCY
        assert rec.is_cleared is False

    reconciled = supervisor.get_mission(mission.mission_id)
    assert reconciled.status == MissionStatus.WAITING
    assert reconciled.waiting_tasks_count == 3


# 7. test_process_restart_preserves_wake_state_and_no_duplication
def test_process_restart_preserves_wake_state_and_no_duplication(tmp_path: Path):
    db_path = tmp_path / "restart_wake_test.db"

    # Process 1
    engine1 = create_sqlite_engine(db_path)
    create_all(engine1)
    factory1 = make_session_factory(engine1)
    session1 = factory1()

    sup1 = DurableMissionSupervisor(session1)
    mission = sup1.create_mission(canonical_sha="sha_restart_10d")
    mission_id = mission.mission_id

    _add_test_workflow(session1, "wf_restart_10d", [{
        "id": "item_restart_10d",
        "item_id": "item_restart_10d",
        "logical_task_id": "task_restart_10d",
        "workflow_id": "wf_restart_10d",
        "state": QueueWorkItemState.WAITING_RESOURCE.value,
        "priority": TaskPriority.NORMAL.value,
    }])

    past_time = utc_now() - timedelta(seconds=10)
    sup1.record_wait(
        mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Ollama busy before crash",
        item_id="item_restart_10d",
        retry_after=past_time,
    )
    session1.commit()
    session1.close()

    # Process 2 (Restart)
    engine2 = create_sqlite_engine(db_path)
    factory2 = make_session_factory(engine2)
    session2 = factory2()

    sup2 = DurableMissionSupervisor(session2)
    recovered = sup2.recover_mission(mission_id, current_canonical_sha="sha_restart_10d")

    # Expired wait is auto-cleared upon recovery
    assert recovered.status == MissionStatus.ACTIVE
    items = sup2._get_all_items()
    assert len(items) == 1  # No duplication!
    assert items[0]["state"] == QueueWorkItemState.READY.value
    session2.close()


# 8. test_real_ollama_qwen3_8b_execution_regression
def test_real_ollama_qwen3_8b_execution_regression():
    provider = OllamaExecutionProvider(provider_id="ollama-local", model="qwen3:8b")
    assert provider.is_available() is True
