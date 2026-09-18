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
from lucius.runtime.launcher import TravelLauncher
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.schemas import DurableMissionRecord, DurableWaitRecord


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


def test_1_create_mission_persists_durable_record(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345", metadata={"project": "test"})
    assert mission.mission_id.startswith("mis_")
    assert mission.canonical_sha == "abc12345"
    assert mission.status == MissionStatus.ACTIVE
    assert mission.completed_tasks_count == 0

    fetched = supervisor.get_mission(mission.mission_id)
    assert fetched is not None
    assert fetched.canonical_sha == "abc12345"


def test_2_record_wait_resource(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    _add_test_workflow(memory_db, "wf_01", [{
        "id": "item_01",
        "item_id": "item_01",
        "logical_task_id": "task_01",
        "workflow_id": "wf_01",
        "state": QueueWorkItemState.RUNNING.value,
        "priority": TaskPriority.NORMAL.value,
    }])

    wait_rec = supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Ollama capacity busy",
        item_id="item_01",
    )
    assert wait_rec.wait_class == DurableWaitClass.RESOURCE

    items = supervisor._get_all_items()
    assert items[0]["state"] == QueueWorkItemState.WAITING_RESOURCE.value

    reconciled = supervisor.get_mission(mission.mission_id)
    assert reconciled.status == MissionStatus.WAITING
    assert reconciled.waiting_tasks_count >= 1


def test_3_record_wait_dependency(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    _add_test_workflow(memory_db, "wf_02", [{
        "id": "item_02",
        "item_id": "item_02",
        "logical_task_id": "task_02",
        "workflow_id": "wf_02",
        "state": QueueWorkItemState.RUNNING.value,
        "priority": TaskPriority.NORMAL.value,
    }])

    wait_rec = supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.DEPENDENCY,
        reason="Missing provenance context",
        item_id="item_02",
    )
    assert wait_rec.wait_class == DurableWaitClass.DEPENDENCY

    items = supervisor._get_all_items()
    assert items[0]["state"] == QueueWorkItemState.WAITING_DEPENDENCY.value

    reconciled = supervisor.get_mission(mission.mission_id)
    assert reconciled.status == MissionStatus.WAITING


def test_4_record_wait_authority_block_immutability(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    _add_test_workflow(memory_db, "wf_03", [{
        "id": "item_03",
        "item_id": "item_03",
        "logical_task_id": "task_03",
        "workflow_id": "wf_03",
        "state": QueueWorkItemState.RUNNING.value,
        "priority": TaskPriority.HIGH.value,
    }])

    wait_rec = supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.AUTHORITY,
        reason="L0 plan freeze immutability boundary",
        item_id="item_03",
    )
    assert wait_rec.wait_class == DurableWaitClass.AUTHORITY

    items = supervisor._get_all_items()
    assert items[0]["state"] == QueueWorkItemState.BLOCKED_AUTHORITY.value

    cleared = supervisor.clear_wait(wait_rec.wait_id)
    assert cleared is not None
    assert cleared.is_cleared is True

    items = supervisor._get_all_items()
    assert items[0]["state"] == QueueWorkItemState.BLOCKED_AUTHORITY.value


def test_5_clear_wait_restores_ready_state(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    _add_test_workflow(memory_db, "wf_04", [{
        "id": "item_04",
        "item_id": "item_04",
        "logical_task_id": "task_04",
        "workflow_id": "wf_04",
        "state": QueueWorkItemState.WAITING_RESOURCE.value,
        "priority": TaskPriority.NORMAL.value,
    }])

    wait_rec = supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Temporary timeout",
        item_id="item_04",
    )
    cleared = supervisor.clear_wait(wait_rec.wait_id)
    assert cleared.is_cleared is True

    items = supervisor._get_all_items()
    assert items[0]["state"] == QueueWorkItemState.READY.value


def test_6_retry_after_expiry_on_recovery(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    past_time = utc_now() - timedelta(seconds=10)
    _add_test_workflow(memory_db, "wf_05", [{
        "id": "item_05",
        "item_id": "item_05",
        "logical_task_id": "task_05",
        "workflow_id": "wf_05",
        "state": QueueWorkItemState.WAITING_RESOURCE.value,
        "priority": TaskPriority.NORMAL.value,
    }])

    supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Rate limit",
        item_id="item_05",
        retry_after=past_time,
    )

    recovered = supervisor.recover_mission(mission.mission_id, current_canonical_sha="abc12345")
    items = supervisor._get_all_items()
    assert items[0]["state"] == QueueWorkItemState.READY.value


def test_7_future_retry_after_remains_waiting(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    future_time = utc_now() + timedelta(hours=1)
    _add_test_workflow(memory_db, "wf_06", [{
        "id": "item_06",
        "item_id": "item_06",
        "logical_task_id": "task_06",
        "workflow_id": "wf_06",
        "state": QueueWorkItemState.WAITING_RESOURCE.value,
        "priority": TaskPriority.NORMAL.value,
    }])

    supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Resource backoff",
        item_id="item_06",
        retry_after=future_time,
    )

    recovered = supervisor.recover_mission(mission.mission_id, current_canonical_sha="abc12345")
    items = supervisor._get_all_items()
    assert items[0]["state"] == QueueWorkItemState.WAITING_RESOURCE.value
    assert recovered.status == MissionStatus.WAITING


def test_8_recover_mission_canonical_sha_mismatch_raises(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="sha_original")

    with pytest.raises(ValueError, match="Canonical SHA mismatch"):
        supervisor.recover_mission(mission.mission_id, current_canonical_sha="sha_diverged")


def test_9_reconcile_mission_state_waiting(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    _add_test_workflow(memory_db, "wf_07", [{
        "id": "item_07",
        "item_id": "item_07",
        "logical_task_id": "task_07",
        "workflow_id": "wf_07",
        "state": QueueWorkItemState.WAITING_RESOURCE.value,
        "priority": TaskPriority.NORMAL.value,
    }])

    rec = supervisor.reconcile_mission_state(mission.mission_id)
    assert rec.status == MissionStatus.WAITING
    assert rec.waiting_tasks_count == 1


def test_10_reconcile_mission_state_completed(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    _add_test_workflow(memory_db, "wf_08", [{
        "id": "item_08",
        "item_id": "item_08",
        "logical_task_id": "task_08",
        "workflow_id": "wf_08",
        "state": QueueWorkItemState.COMPLETED.value,
        "priority": TaskPriority.NORMAL.value,
    }])

    rec = supervisor.reconcile_mission_state(mission.mission_id)
    assert rec.status == MissionStatus.COMPLETED
    assert rec.completed_tasks_count == 1


def test_11_reconcile_mission_state_failed(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    _add_test_workflow(memory_db, "wf_09", [{
        "id": "item_09",
        "item_id": "item_09",
        "logical_task_id": "task_09",
        "workflow_id": "wf_09",
        "state": QueueWorkItemState.BLOCKED_AUTHORITY.value,
        "priority": TaskPriority.NORMAL.value,
    }])

    rec = supervisor.reconcile_mission_state(mission.mission_id)
    assert rec.status == MissionStatus.FAILED
    assert rec.blocked_tasks_count == 1


def test_12_continuation_session_stops_with_waiting_status(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    _add_test_workflow(memory_db, "wf_10", [{
        "id": "item_10",
        "item_id": "item_10",
        "logical_task_id": "task_10",
        "workflow_id": "wf_10",
        "state": QueueWorkItemState.WAITING_RESOURCE.value,
        "priority": TaskPriority.NORMAL.value,
    }])

    class MockRuntimeService:
        dispatcher = None
        def run(self, config):
            from lucius.runtime.schemas import ExecutionRuntimeLoopResult, RuntimeLoopStatus
            return ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE, selected_tasks=0)

    svc = BoundedContinuationService(
        session=memory_db,
        runtime_service=MockRuntimeService(),
        supervisor=supervisor,
    )

    res = svc.run_session(SessionBudget(max_cycles=1), mission_id=mission.mission_id)
    assert res.status == "WAITING"
    assert res.stop_reason == ContinuationStopReason.WAITING_NO_CURRENTLY_RUNNABLE_WORK.value


def test_13_continuation_session_stops_with_idle_status(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    class MockRuntimeService:
        dispatcher = None
        def run(self, config):
            from lucius.runtime.schemas import ExecutionRuntimeLoopResult, RuntimeLoopStatus
            return ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE, selected_tasks=0)

    svc = BoundedContinuationService(
        session=memory_db,
        runtime_service=MockRuntimeService(),
        supervisor=supervisor,
    )

    res = svc.run_session(SessionBudget(max_cycles=1), mission_id=mission.mission_id)
    assert res.status == "IDLE"
    assert res.stop_reason == ContinuationStopReason.IDLE_NO_ELIGIBLE_WORK.value


def test_14_process_restart_recovery_workflow(tmp_path: Path):
    db_file = tmp_path / "test_restart.db"

    # Process 1: Create mission and record wait
    engine1 = create_sqlite_engine(db_file)
    create_all(engine1)
    factory1 = make_session_factory(engine1)
    session1 = factory1()

    supervisor1 = DurableMissionSupervisor(session1)
    mission1 = supervisor1.create_mission(canonical_sha="sha_persistent")
    mission_id = mission1.mission_id

    _add_test_workflow(session1, "wf_restart", [{
        "id": "item_restart",
        "item_id": "item_restart",
        "logical_task_id": "task_restart",
        "workflow_id": "wf_restart",
        "state": QueueWorkItemState.WAITING_RESOURCE.value,
        "priority": TaskPriority.NORMAL.value,
    }])

    wait_rec = supervisor1.record_wait(
        mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Rate limit before crash",
        item_id="item_restart",
    )
    session1.commit()
    session1.close()

    # Process 2: Recover from SQLite database
    engine2 = create_sqlite_engine(db_file)
    factory2 = make_session_factory(engine2)
    session2 = factory2()

    supervisor2 = DurableMissionSupervisor(session2)
    recovered = supervisor2.recover_mission(mission_id, current_canonical_sha="sha_persistent")
    assert recovered.status == MissionStatus.WAITING

    cleared = supervisor2.clear_wait(wait_rec.wait_id)
    assert cleared.is_cleared is True
    reconciled = supervisor2.get_mission(mission_id)
    assert reconciled.status == MissionStatus.ACTIVE
    session2.close()


def test_15_no_task_duplication_on_resume(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")

    _add_test_workflow(memory_db, "wf_unique", [{
        "id": "item_unique",
        "item_id": "item_unique",
        "logical_task_id": "task_unique",
        "workflow_id": "wf_unique",
        "state": QueueWorkItemState.READY.value,
        "priority": TaskPriority.NORMAL.value,
    }])

    count_before = len(supervisor._get_all_items())
    supervisor.recover_mission(mission.mission_id, current_canonical_sha="abc12345")
    count_after = len(supervisor._get_all_items())

    assert count_before == count_after == 1


def test_16_existing_travel_mode_regression(memory_db: Session):
    launcher = TravelLauncher()
    preflight = launcher.preflight()
    assert preflight.repository_checks["lucius"] is True
    assert preflight.repository_checks["darwin"] is True
    assert preflight.repository_checks["billy"] is True
    assert preflight.registry_ok is True
