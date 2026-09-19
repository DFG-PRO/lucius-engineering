from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import time
import pytest
from sqlalchemy import func, select

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, DurableWaitClass, MissionStatus, QueueWorkItemState, TaskStatus
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import AuditEventORM, PersistentWorkflowORM, TaskORM, utc_now
from lucius.portfolio.schemas import NormalizedWorkPackage
from lucius.portfolio.service import GlobalWorkPortfolioService
from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
from lucius.runtime.continuation import BoundedContinuationService, SessionBudget, sleep_until_next_wake_or_recheck
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.feeder import DarwinBacklogFeeder
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.schemas import RuntimeLoopConfig
from lucius.runtime.service import ExecutionRuntimeLoopService
from scripts.run_shift_10l_travel_mode import verify_10j_acceptance_evidence


@pytest.fixture
def memory_db():
    engine = create_sqlite_engine(":memory:")
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()
    yield session
    session.close()


def _setup_services(session):
    lucius_root = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering").resolve()
    darwin_root = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine").resolve()

    registry_path = lucius_root / "src" / "lucius" / "projects" / "dfg_canonical_registry.json"
    registry = DFGProjectRegistry.load_json(registry_path) if registry_path.exists() else None

    feeder = DarwinBacklogFeeder(darwin_root=darwin_root, registry=registry)
    portfolio_service = GlobalWorkPortfolioService(registry=registry, feeder=feeder)
    supervisor = DurableMissionSupervisor(session, actor=Actor.LUCIUS)
    audit = AuditService(session)

    scripted_provider = ScriptedExecutionAdapter(provider_id="scripted-provider")
    provider_registry = RuntimeProviderRegistry([scripted_provider])
    router = ModelExecutionRouter(session, registry=provider_registry, actor=Actor.LUCIUS)
    dispatcher = MultiProjectDispatcher(session, actor=Actor.LUCIUS)

    runtime_service = ExecutionRuntimeLoopService(
        session=session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_router=router,
        dispatcher=dispatcher,
        actor=Actor.LUCIUS,
    )

    continuation = BoundedContinuationService(
        session=session,
        runtime_service=runtime_service,
        feeder=feeder,
        dispatcher=dispatcher,
        supervisor=supervisor,
        actor=Actor.LUCIUS,
    )

    return {
        "feeder": feeder,
        "portfolio_service": portfolio_service,
        "supervisor": supervisor,
        "audit": audit,
        "dispatcher": dispatcher,
        "runtime_service": runtime_service,
        "continuation": continuation,
    }


def test_shift_10l_a_no_eligible_work_bounded_sleep(memory_db):
    """A. When no eligible work is runnable, session calculates next_wake and sleep_recommended_seconds."""
    svcs = _setup_services(memory_db)
    continuation = svcs["continuation"]
    supervisor = svcs["supervisor"]

    mission = supervisor.create_mission(canonical_sha="d44ff8ce22ccdf30a15c498a772a41bc107229c3", mission_id="TEST_MIS_10L_A")

    # Record future retry wait
    future_wake = utc_now() + timedelta(seconds=120)
    supervisor.record_wait(
        mission_id=mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE_SHORT,
        reason="Rate limit backoff",
        retry_after=future_wake,
    )

    budget = SessionBudget(max_cycles=1, max_wall_seconds=10.0)
    res = continuation.run_session(budget, mission_id=mission.mission_id, recheck_interval_seconds=300.0)

    assert res.status in ("WAITING", "SLEEPING")
    assert res.tasks_selected == 0
    assert res.next_wake is not None
    assert abs((res.next_wake - future_wake).total_seconds()) < 5.0
    assert res.sleep_recommended_seconds > 0.0


def test_shift_10l_b_next_wake_persistence(memory_db):
    """B. Verify next_wake is persisted in mission sleeping_metadata."""
    svcs = _setup_services(memory_db)
    continuation = svcs["continuation"]
    supervisor = svcs["supervisor"]

    mission = supervisor.create_mission(canonical_sha="d44ff8ce22ccdf30a15c498a772a41bc107229c3", mission_id="TEST_MIS_10L_B")

    future_wake = utc_now() + timedelta(seconds=180)
    supervisor.record_wait(
        mission_id=mission.mission_id,
        wait_class=DurableWaitClass.SCHEDULE,
        reason="Scheduled maintenance window",
        retry_after=future_wake,
    )

    budget = SessionBudget(max_cycles=1, max_wall_seconds=10.0)
    continuation.run_session(budget, mission_id=mission.mission_id)

    updated_mission = supervisor.get_mission(mission.mission_id)
    assert updated_mission is not None
    assert updated_mission.status in (MissionStatus.WAITING, MissionStatus.SLEEPING)
    sleeping_meta = updated_mission.metadata.get("sleeping_metadata", {})
    assert "earliest_wake_at" in sleeping_meta
    assert sleeping_meta["earliest_wake_at"] is not None


def test_shift_10l_c_periodic_recheck_interval(memory_db):
    """C. When no specific future retry_after exists, periodic recheck interval is used."""
    svcs = _setup_services(memory_db)
    continuation = svcs["continuation"]
    supervisor = svcs["supervisor"]

    mission = supervisor.create_mission(canonical_sha="d44ff8ce22ccdf30a15c498a772a41bc107229c3", mission_id="TEST_MIS_10L_C")

    # Record wait with no explicit retry_after timestamp
    supervisor.record_wait(
        mission_id=mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Waiting for external GPU resource",
    )

    budget = SessionBudget(max_cycles=1)
    res = continuation.run_session(budget, mission_id=mission.mission_id, recheck_interval_seconds=300.0)

    assert res.next_wake is not None
    assert res.sleep_recommended_seconds == 300.0


def test_shift_10l_d_sleep_helper_ticks_and_interruption():
    """D. sleep_until_next_wake_or_recheck sleeps in ticks and responds immediately to interruption signal."""
    # Test sleep tick completion
    target_wake = utc_now() + timedelta(seconds=0.2)
    slept = sleep_until_next_wake_or_recheck(target_wake, recheck_interval_seconds=1.0, tick_seconds=0.05)
    assert slept >= 0.15

    # Test interruption signal stopping sleep immediately
    interrupted = True
    slept_int = sleep_until_next_wake_or_recheck(
        utc_now() + timedelta(seconds=100.0),
        recheck_interval_seconds=100.0,
        check_interrupted_fn=lambda: interrupted,
        tick_seconds=0.05,
    )
    assert slept_int < 0.1


def test_shift_10l_e_no_model_calls_during_sleep(memory_db):
    """E. Sleeping / waiting sessions make zero model provider calls."""
    svcs = _setup_services(memory_db)
    continuation = svcs["continuation"]
    supervisor = svcs["supervisor"]

    mission = supervisor.create_mission(canonical_sha="d44ff8ce22ccdf30a15c498a772a41bc107229c3", mission_id="TEST_MIS_10L_E")
    supervisor.record_wait(
        mission_id=mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE_LONG,
        reason="Database lock contention",
    )

    budget = SessionBudget(max_cycles=1)
    res = continuation.run_session(budget, mission_id=mission.mission_id)

    assert res.provider_calls == 0
    model_events = memory_db.scalar(
        select(func.count()).select_from(AuditEventORM).where(
            AuditEventORM.event_type == "MODEL_EXECUTION_ROUTING_DECISION"
        )
    ) or 0
    assert model_events == 0


def test_shift_10l_f_wake_resumes_work(memory_db):
    """F. When wait expires or is cleared, mission wakes and work resumes."""
    svcs = _setup_services(memory_db)
    continuation = svcs["continuation"]
    supervisor = svcs["supervisor"]

    mission = supervisor.create_mission(canonical_sha="d44ff8ce22ccdf30a15c498a772a41bc107229c3", mission_id="TEST_MIS_10L_F")

    # Record wait that is now expired
    past_time = utc_now() - timedelta(seconds=10)
    wait_rec = supervisor.record_wait(
        mission_id=mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE_SHORT,
        reason="Short backoff complete",
        retry_after=past_time,
    )

    cleared = supervisor.reevaluate_durable_waits(mission.mission_id)
    assert len(cleared) == 1
    assert cleared[0].wait_id == wait_rec.wait_id

    updated = supervisor.get_mission(mission.mission_id)
    assert updated.status in (MissionStatus.ACTIVE, MissionStatus.WAITING)


def test_shift_10l_g_blocked_authority_stays_fail_closed(memory_db):
    """G. Blocked authority/decision items stay fail-closed and do not trigger model retries."""
    svcs = _setup_services(memory_db)
    continuation = svcs["continuation"]
    supervisor = svcs["supervisor"]

    mission = supervisor.create_mission(canonical_sha="d44ff8ce22ccdf30a15c498a772a41bc107229c3", mission_id="TEST_MIS_10L_G")

    supervisor.record_wait(
        mission_id=mission.mission_id,
        wait_class=DurableWaitClass.AUTHORITY,
        reason="Human operator approval required",
    )

    budget = SessionBudget(max_cycles=1)
    res = continuation.run_session(budget, mission_id=mission.mission_id)

    assert res.status in ("WAITING", "BLOCKED", "IDLE")
    assert res.tasks_selected == 0
    assert res.provider_calls == 0


def test_shift_10l_h_handoff_verification_fails_closed(tmp_path):
    """H. verify_10j_acceptance_evidence fails closed when 10J DB or checkpoint evidence is missing or insufficient."""
    # Non-existent DB
    passed, reason = verify_10j_acceptance_evidence(
        db_path=tmp_path / "nonexistent.db",
        checkpoint_path=tmp_path / "nonexistent.jsonl",
    )
    assert not passed
    assert "not found" in reason.lower()

    # Incomplete checkpoint evidence (< 6.0h)
    db_file = tmp_path / "fake_10j.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE durable_missions (id TEXT PRIMARY KEY, status TEXT, created_at TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO durable_missions VALUES ('LUCIUS_SHIFT_10J_FIRST_OVERNIGHT', 'ACTIVE', '2026-09-18T19:37:09.956275-06:00', '2026-09-18T19:47:09.956275-06:00')")
    conn.commit()
    conn.close()

    chk_file = tmp_path / "fake_10j_checkpoints.jsonl"
    with open(chk_file, "w") as f:
        f.write('{"elapsed_seconds": 3600.0, "elapsed_hours": 1.0}\n')

    passed2, reason2 = verify_10j_acceptance_evidence(
        db_path=db_file,
        checkpoint_path=chk_file,
        min_required_seconds=21600.0,
    )
    assert not passed2
    assert "incomplete" in reason2.lower() or "< 6.0h" in reason2


def test_shift_10l_i_handoff_verification_passes(tmp_path):
    """I. verify_10j_acceptance_evidence passes when >= 6.0h evidence exists."""
    db_file = tmp_path / "fake_10j_pass.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE durable_missions (id TEXT PRIMARY KEY, status TEXT, created_at TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO durable_missions VALUES ('LUCIUS_SHIFT_10J_FIRST_OVERNIGHT', 'ACTIVE', '2026-09-18T19:37:09.956275-06:00', '2026-09-19T02:00:00.000000-06:00')")
    conn.commit()
    conn.close()

    chk_file = tmp_path / "fake_10j_pass_checkpoints.jsonl"
    with open(chk_file, "w") as f:
        f.write('{"elapsed_seconds": 23000.0, "elapsed_hours": 6.39}\n')

    passed, reason = verify_10j_acceptance_evidence(
        db_path=db_file,
        checkpoint_path=chk_file,
        min_required_seconds=21600.0,
    )
    assert passed
    assert "acceptance condition" in reason.lower()
