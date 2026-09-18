from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from sqlalchemy.orm import Session

from lucius.domain.enums import Actor, DurableWaitClass, MissionStatus, QueueWorkItemState, TaskPriority
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import PersistentWorkflowORM, utc_now
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.schemas import DurableWaitRecord


@pytest.fixture
def memory_db():
    engine = create_sqlite_engine(":memory:")
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()
    yield session
    session.close()


def _add_test_workflow(session: Session, wf_id: str, items: list[dict], proj_id: str = "proj_01"):
    wf = PersistentWorkflowORM(
        id=wf_id,
        project_id=proj_id,
        objective="Shift 10I Test Workflow",
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


def test_shift_10i_waiting_resource_short(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")
    _add_test_workflow(memory_db, "wf_res_short", [{
        "id": "item_rs",
        "item_id": "item_rs",
        "logical_task_id": "task_rs",
        "workflow_id": "wf_res_short",
        "state": QueueWorkItemState.RUNNING.value,
        "priority": TaskPriority.NORMAL.value,
    }])
    wait_rec = supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE_SHORT,
        reason="Ollama temporary rate limit",
        item_id="item_rs",
        retry_after=utc_now() + timedelta(seconds=30),
    )
    assert wait_rec.wait_class == DurableWaitClass.RESOURCE_SHORT
    items = supervisor._get_all_items()
    assert items[0]["state"] == QueueWorkItemState.WAITING_RESOURCE_SHORT.value


def test_shift_10i_waiting_resource_long(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")
    _add_test_workflow(memory_db, "wf_res_long", [{
        "id": "item_rl",
        "item_id": "item_rl",
        "logical_task_id": "task_rl",
        "workflow_id": "wf_res_long",
        "state": QueueWorkItemState.RUNNING.value,
        "priority": TaskPriority.NORMAL.value,
    }])
    wait_rec = supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE_LONG,
        reason="Remote API provider daily quota exhausted",
        item_id="item_rl",
        retry_after=utc_now() + timedelta(hours=12),
    )
    assert wait_rec.wait_class == DurableWaitClass.RESOURCE_LONG
    items = supervisor._get_all_items()
    assert items[0]["state"] == QueueWorkItemState.WAITING_RESOURCE_LONG.value


def test_shift_10i_waiting_dependency(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")
    _add_test_workflow(memory_db, "wf_dep", [{
        "id": "item_dep",
        "item_id": "item_dep",
        "logical_task_id": "task_dep",
        "workflow_id": "wf_dep",
        "state": QueueWorkItemState.RUNNING.value,
        "priority": TaskPriority.NORMAL.value,
    }])
    wait_rec = supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.DEPENDENCY,
        reason="Upstream project build artifact missing",
        item_id="item_dep",
    )
    assert wait_rec.wait_class == DurableWaitClass.DEPENDENCY
    items = supervisor._get_all_items()
    assert items[0]["state"] == QueueWorkItemState.WAITING_DEPENDENCY.value


def test_shift_10i_waiting_schedule(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")
    _add_test_workflow(memory_db, "wf_sched", [{
        "id": "item_sched",
        "item_id": "item_sched",
        "logical_task_id": "task_sched",
        "workflow_id": "wf_sched",
        "state": QueueWorkItemState.RUNNING.value,
        "priority": TaskPriority.NORMAL.value,
    }])
    wait_rec = supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.SCHEDULE,
        reason="Scheduled market open window",
        item_id="item_sched",
        retry_after=utc_now() + timedelta(hours=2),
    )
    assert wait_rec.wait_class == DurableWaitClass.SCHEDULE
    items = supervisor._get_all_items()
    assert items[0]["state"] == QueueWorkItemState.WAITING_SCHEDULE.value


def test_shift_10i_blocked_authority_and_decision(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")
    _add_test_workflow(memory_db, "wf_auth_dec", [{
        "id": "item_auth",
        "item_id": "item_auth",
        "logical_task_id": "task_auth",
        "workflow_id": "wf_auth_dec",
        "state": QueueWorkItemState.RUNNING.value,
        "priority": TaskPriority.HIGH.value,
    }, {
        "id": "item_dec",
        "item_id": "item_dec",
        "logical_task_id": "task_dec",
        "workflow_id": "wf_auth_dec",
        "state": QueueWorkItemState.RUNNING.value,
        "priority": TaskPriority.NORMAL.value,
    }])
    w_auth = supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.AUTHORITY,
        reason="L2 push authority boundary",
        item_id="item_auth",
    )
    w_dec = supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.DECISION,
        reason="Architectural trade-off approval needed",
        item_id="item_dec",
    )
    assert w_auth.wait_class == DurableWaitClass.AUTHORITY
    assert w_dec.wait_class == DurableWaitClass.DECISION

    items = supervisor._get_all_items()
    states = {i["item_id"]: i["state"] for i in items}
    assert states["item_auth"] == QueueWorkItemState.BLOCKED_AUTHORITY.value
    assert states["item_dec"] == QueueWorkItemState.BLOCKED_DECISION.value


def test_shift_10i_no_work_sleeping_behavior(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")
    future_time = utc_now() + timedelta(hours=3)
    _add_test_workflow(memory_db, "wf_sleep", [{
        "id": "item_sleep",
        "item_id": "item_sleep",
        "logical_task_id": "task_sleep",
        "workflow_id": "wf_sleep",
        "state": QueueWorkItemState.RUNNING.value,
        "priority": TaskPriority.NORMAL.value,
    }])
    supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.SCHEDULE,
        reason="Nightly batch window",
        item_id="item_sleep",
        retry_after=future_time,
    )
    reconciled = supervisor.get_mission(mission.mission_id)
    assert reconciled.status == MissionStatus.SLEEPING
    assert "sleeping_metadata" in reconciled.metadata
    assert reconciled.metadata["sleeping_metadata"]["earliest_wake_at"] is not None


def test_shift_10i_provider_reavailability_auto_wake(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")
    _add_test_workflow(memory_db, "wf_prov", [{
        "id": "item_prov",
        "item_id": "item_prov",
        "logical_task_id": "task_prov",
        "workflow_id": "wf_prov",
        "state": QueueWorkItemState.WAITING_RESOURCE_SHORT.value,
        "priority": TaskPriority.NORMAL.value,
    }])
    w = supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE_SHORT,
        reason="Ollama model loading",
        item_id="item_prov",
        metadata={"provider_reavailable": True},
    )
    cleared = supervisor.reevaluate_durable_waits(mission.mission_id)
    assert len(cleared) == 1
    assert cleared[0].wait_id == w.wait_id
    items = supervisor._get_all_items()
    assert items[0]["state"] in (QueueWorkItemState.READY.value, QueueWorkItemState.READY_TO_RESUME.value)


def test_shift_10i_mission_completion_semantics(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")
    _add_test_workflow(memory_db, "wf_comp", [{
        "id": "item_comp",
        "item_id": "item_comp",
        "logical_task_id": "task_comp",
        "workflow_id": "wf_comp",
        "state": QueueWorkItemState.COMPLETED.value,
        "priority": TaskPriority.NORMAL.value,
    }])
    reconciled = supervisor.get_mission(mission.mission_id)
    assert reconciled.status == MissionStatus.COMPLETED
    assert reconciled.completed_tasks_count == 1
