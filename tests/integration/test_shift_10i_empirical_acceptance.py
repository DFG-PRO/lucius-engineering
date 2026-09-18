from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from sqlalchemy.orm import Session

from lucius.domain.enums import Actor, DurableWaitClass, MissionStatus, QueueWorkItemState, TaskPriority
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import PersistentWorkflowORM, utc_now
from lucius.runtime.mission import DurableMissionSupervisor


@pytest.fixture
def empirical_db(tmp_path: Path):
    db_file = tmp_path / "shift_10i_empirical.db"
    engine = create_sqlite_engine(db_file)
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()
    yield session, db_file
    session.close()


def test_shift_10i_empirical_acceptance_mission(empirical_db):
    session, db_file = empirical_db
    supervisor = DurableMissionSupervisor(session)
    mission = supervisor.create_mission(canonical_sha="907ae05b37799fe0726ecc40a00329d519abf22c", mission_id="LUCIUS_SHIFT_10I_EMPIRICAL_MISSION")
    mission_id = mission.mission_id

    # 1. Setup multiple real project workflows
    # Project 1: Darwin (Completes a task)
    wf_darwin = PersistentWorkflowORM(
        id="LWORK_10I_DARWIN",
        project_id="darwin-research-engine",
        objective="Darwin Research Task",
        expected_main_head="907ae05b37799fe0726ecc40a00329d519abf22c",
        isolated_branch="main",
        worktree_path="/tmp/darwin",
        workflow_state="PLAN_READY",
        authority_tier="L0",
        task_backlog=[{
            "id": "ITEM_DARWIN_01",
            "item_id": "ITEM_DARWIN_01",
            "logical_task_id": "TASK_DARWIN_01",
            "workflow_id": "LWORK_10I_DARWIN",
            "state": QueueWorkItemState.COMPLETED.value,
            "priority": TaskPriority.HIGH.value,
            "title": "Darwin Research Synthesis",
        }],
    )

    # Project 2: Billy (Enters resource wait)
    wf_billy = PersistentWorkflowORM(
        id="LWORK_10I_BILLY",
        project_id="billy-production-engine",
        objective="Billy Asset Lifecycle",
        expected_main_head="907ae05b37799fe0726ecc40a00329d519abf22c",
        isolated_branch="main",
        worktree_path="/tmp/billy",
        workflow_state="PLAN_READY",
        authority_tier="L0",
        task_backlog=[{
            "id": "ITEM_BILLY_01",
            "item_id": "ITEM_BILLY_01",
            "logical_task_id": "TASK_BILLY_01",
            "workflow_id": "LWORK_10I_BILLY",
            "state": QueueWorkItemState.RUNNING.value,
            "priority": TaskPriority.NORMAL.value,
            "title": "Billy Phase 6.5 Asset Verification",
        }],
    )

    # Project 3: DFG Binance Executor (Enters authority / decision block)
    wf_trade = PersistentWorkflowORM(
        id="LWORK_10I_TRADE",
        project_id="dfg-binance-executor",
        objective="Binance Execution Hardening",
        expected_main_head="907ae05b37799fe0726ecc40a00329d519abf22c",
        isolated_branch="main",
        worktree_path="/tmp/trade",
        workflow_state="PLAN_READY",
        authority_tier="L0",
        task_backlog=[{
            "id": "ITEM_TRADE_01",
            "item_id": "ITEM_TRADE_01",
            "logical_task_id": "TASK_TRADE_01",
            "workflow_id": "LWORK_10I_TRADE",
            "state": QueueWorkItemState.RUNNING.value,
            "priority": TaskPriority.CRITICAL.value,
            "title": "Live Trade Execution Signoff",
        }],
    )

    session.add_all([wf_darwin, wf_billy, wf_trade])
    session.flush()

    # 2. Record Resource Wait Transition (Billy)
    wait_resource = supervisor.record_wait(
        mission_id,
        wait_class=DurableWaitClass.RESOURCE_SHORT,
        reason="Ollama qwen3:8b provider temporarily busy during peak load (Fault-Injection Test)",
        item_id="ITEM_BILLY_01",
        project_id="billy-production-engine",
        dependency_or_resource="ollama:qwen3:8b",
        retry_after=utc_now() + timedelta(seconds=120),
        attempts=1,
    )

    # 3. Record Authority / Decision Wait Transition (Binance Executor)
    wait_authority = supervisor.record_wait(
        mission_id,
        wait_class=DurableWaitClass.AUTHORITY,
        reason="L2 live capital trading signoff required",
        item_id="ITEM_TRADE_01",
        project_id="dfg-binance-executor",
        dependency_or_resource="daniel_authority_approval",
        attempts=1,
    )

    session.commit()

    # Verify Run 1 persisted state
    m1 = supervisor.get_mission(mission_id)
    assert m1.completed_tasks_count >= 1
    assert m1.waiting_tasks_count >= 1
    assert m1.blocked_tasks_count >= 1
    assert m1.status in (MissionStatus.WAITING, MissionStatus.SLEEPING)

    # 4. Controlled Process Termination & Restart
    session.close()

    engine2 = create_sqlite_engine(db_file)
    factory2 = make_session_factory(engine2)
    session2 = factory2()

    supervisor2 = DurableMissionSupervisor(session2)
    recovered_mission = supervisor2.recover_mission(mission_id, current_canonical_sha="907ae05b37799fe0726ecc40a00329d519abf22c")
    assert recovered_mission.mission_id == mission_id

    # Verify no task duplication
    all_items = supervisor2._get_all_items()
    item_ids = [i["item_id"] for i in all_items]
    assert len(item_ids) == len(set(item_ids)) == 3

    # 5. Clear Resource Wait (Auto-Wake / Auto-Resume)
    cleared_wait = supervisor2.clear_wait(wait_resource.wait_id)
    assert cleared_wait.is_cleared is True

    items_after_wake = supervisor2._get_all_items()
    billy_item = next(i for i in items_after_wake if i["item_id"] == "ITEM_BILLY_01")
    assert billy_item["state"] in (QueueWorkItemState.READY.value, QueueWorkItemState.READY_TO_RESUME.value)

    # Reconcile final durable state
    final_mission = supervisor2.get_mission(mission_id)
    assert final_mission.status == MissionStatus.ACTIVE
    session2.close()
