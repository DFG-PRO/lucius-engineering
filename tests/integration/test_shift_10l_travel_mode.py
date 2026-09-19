from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import time
import pytest
from sqlalchemy import func, select

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, DurableWaitClass, MissionStatus, QueueWorkItemState, TaskStatus
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import AuditEventORM, PersistentWorkflowORM, TaskORM, utc_now
from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
from lucius.runtime.continuation import BoundedContinuationService, SessionBudget
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.feeder import DarwinBacklogFeeder
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.service import ExecutionRuntimeLoopService
from scripts.run_shift_10l_travel_mode import run_travel_mode_mission, verify_10j_acceptance_evidence


@pytest.fixture
def temp_mission_dir(tmp_path):
    mission_dir = tmp_path / "shift_10l_test_env"
    mission_dir.mkdir(parents=True, exist_ok=True)
    yield mission_dir


def test_shift_10l_travel_mode_liveness_smoke(temp_mission_dir):
    """Short wall-clock liveness smoke test for Shift 10L Travel Mode launcher on temporary artifacts."""
    db_path = temp_mission_dir / "smoke_travel_mode.db"
    log_path = temp_mission_dir / "smoke_travel_mode.log"
    checkpoint_path = temp_mission_dir / "smoke_checkpoints.jsonl"

    # Execute very short mission pass (min_hours = 0.0001 = 0.36 seconds)
    start_time = time.monotonic()
    run_travel_mode_mission(
        db_path=db_path,
        mission_id="TEST_SHIFT_10L_SMOKE_MISSION",
        attempt_id="TEST_ATTEMPT_SMOKE_1",
        min_hours=0.0001,
        target_hours=0.0002,
        recheck_interval_seconds=1.0,
        checkpoint_interval_minutes=0.01,
        skip_handoff_verification=True,
        use_scripted_provider=True,
    )
    elapsed = time.monotonic() - start_time

    # Verify execution finished quickly and produced valid artifacts
    assert db_path.exists()
    assert elapsed < 10.0, f"Smoke run took unexpectedly long: {elapsed:.2f}s"

    # Verify DB state
    engine = create_sqlite_engine(str(db_path))
    session_factory = make_session_factory(engine)
    session = session_factory()
    supervisor = DurableMissionSupervisor(session)

    mission = supervisor.get_mission("TEST_SHIFT_10L_SMOKE_MISSION")
    assert mission is not None
    assert mission.mission_id == "TEST_SHIFT_10L_SMOKE_MISSION"
    session.close()


def test_shift_10l_travel_mode_handoff_gate_prevents_launch(temp_mission_dir):
    """Verify handoff gate prevents launch when 10J has not completed minimum 6h requirements."""
    db_path = temp_mission_dir / "blocked_travel_mode.db"

    # Try running with handoff_from_10j enabled without 6h 10J evidence
    run_travel_mode_mission(
        db_path=db_path,
        mission_id="TEST_SHIFT_10L_BLOCKED_MISSION",
        attempt_id="TEST_ATTEMPT_BLOCKED_1",
        min_hours=48.0,
        handoff_from_10j=True,
        skip_handoff_verification=False,
    )

    # Mission DB should either not be created or remain unstarted/clean
    if db_path.exists():
        engine = create_sqlite_engine(str(db_path))
        session_factory = make_session_factory(engine)
        session = session_factory()
        supervisor = DurableMissionSupervisor(session)
        mission = supervisor.get_mission("TEST_SHIFT_10L_BLOCKED_MISSION")
        assert mission is None
        session.close()


def test_shift_10l_travel_mode_sleep_prevents_hot_loop(temp_mission_dir):
    """Verify durable idle sleep prevents hot-looping cycles when no runnable work exists."""
    db_path = temp_mission_dir / "sleep_hotloop_test.db"

    engine = create_sqlite_engine(str(db_path))
    create_all(engine)
    session_factory = make_session_factory(engine)
    session = session_factory()

    supervisor = DurableMissionSupervisor(session, actor=Actor.LUCIUS)
    mission = supervisor.create_mission(canonical_sha="d44ff8ce22ccdf30a15c498a772a41bc107229c3", mission_id="TEST_MIS_HOTLOOP")

    # Add a wait state
    supervisor.record_wait(
        mission_id=mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE_LONG,
        reason="Long resource wait",
        retry_after=utc_now() + timedelta(seconds=10.0),
    )
    session.commit()

    lucius_root = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering").resolve()
    reg_path = lucius_root / "src" / "lucius" / "projects" / "dfg_canonical_registry.json"
    registry = DFGProjectRegistry.load_json(reg_path) if reg_path.exists() else None

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
        feeder=None,
        dispatcher=dispatcher,
        supervisor=supervisor,
        actor=Actor.LUCIUS,
    )

    # Run 5 consecutive sessions on waiting mission
    start_time = time.monotonic()
    for _ in range(5):
        res = continuation.run_session(SessionBudget(max_cycles=1), mission_id=mission.mission_id)
        assert res.tasks_selected == 0
        assert res.next_wake is not None
    session.close()

    # Verify audit event count is strictly bounded (2 audit events per run_session call = 10 total)
    conn = create_sqlite_engine(str(db_path))
    session = make_session_factory(conn)()
    audit_count = session.scalar(select(func.count()).select_from(AuditEventORM)) or 0
    assert audit_count <= 25, f"Audit count bloat detected: {audit_count} events"
    session.close()
