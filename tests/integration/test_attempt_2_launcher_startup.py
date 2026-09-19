from __future__ import annotations

from pathlib import Path
import pytest
from sqlalchemy.orm import Session

from lucius.domain.enums import Actor, MissionStatus
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.runtime.mission import DurableMissionSupervisor
from scripts.run_shift_10j_attempt_2 import get_current_canonical_sha, main, run_overnight_attempt


def test_attempt_2_supervisor_create_and_recover_signature_match(tmp_path: Path):
    """Deterministic integration test for Attempt 2 launcher startup & recovery lifecycle.

    Verifies that create_mission and recover_mission accept canonical SHA kwargs,
    dynamic canonical HEAD resolution matches git HEAD, and no TypeError is raised.
    """
    db_file = tmp_path / "test_attempt2_startup.db"
    engine = create_sqlite_engine(str(db_file))
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()

    supervisor = DurableMissionSupervisor(session, actor=Actor.LUCIUS)
    canonical_sha = get_current_canonical_sha()

    # 1. Test create_mission API
    mission = supervisor.create_mission(
        canonical_sha=canonical_sha,
        mission_id="LUCIUS_SHIFT_10J_FIRST_OVERNIGHT",
        attempt_id="LUCIUS_SHIFT_10J_ATTEMPT_2",
    )
    session.commit()

    assert mission.mission_id == "LUCIUS_SHIFT_10J_FIRST_OVERNIGHT"
    assert mission.canonical_sha == canonical_sha
    assert (mission.metadata or {}).get("current_attempt_id") == "LUCIUS_SHIFT_10J_ATTEMPT_2"

    # 2. Test recover_mission API with both current_canonical_sha and canonical_sha kwargs
    recovered1 = supervisor.recover_mission(
        "LUCIUS_SHIFT_10J_FIRST_OVERNIGHT",
        current_canonical_sha=canonical_sha,
        attempt_id="LUCIUS_SHIFT_10J_ATTEMPT_2_RECOVERED",
    )
    session.commit()
    assert (recovered1.metadata or {}).get("current_attempt_id") == "LUCIUS_SHIFT_10J_ATTEMPT_2_RECOVERED"

    recovered2 = supervisor.recover_mission(
        "LUCIUS_SHIFT_10J_FIRST_OVERNIGHT",
        canonical_sha=canonical_sha,
        attempt_id="LUCIUS_SHIFT_10J_ATTEMPT_2_RECOVERED_ALT",
    )
    session.commit()
    assert (recovered2.metadata or {}).get("current_attempt_id") == "LUCIUS_SHIFT_10J_ATTEMPT_2_RECOVERED_ALT"
    session.close()


def test_attempt_2_launcher_get_current_canonical_sha():
    sha = get_current_canonical_sha()
    assert len(sha) == 40
    # Mismatch fails closed
    with pytest.raises(ValueError, match="Canonical SHA mismatch"):
        get_current_canonical_sha(expected_sha="0000000000000000000000000000000000000000")


def test_attempt_2_run_overnight_attempt_smoke(tmp_path: Path):
    db_file = tmp_path / "smoke_attempt_2.db"
    run_overnight_attempt(
        db_path=db_file,
        mission_id="LUCIUS_SHIFT_10J_FIRST_OVERNIGHT",
        attempt_id="LUCIUS_SHIFT_10J_ATTEMPT_2_SMOKE",
        min_hours=0.0,
        target_hours=0.0,
        checkpoint_interval_minutes=0.01,
    )
    assert db_file.exists()
    engine = create_sqlite_engine(str(db_file))
    factory = make_session_factory(engine)
    session = factory()
    supervisor = DurableMissionSupervisor(session, actor=Actor.LUCIUS)
    mission = supervisor.get_mission("LUCIUS_SHIFT_10J_FIRST_OVERNIGHT")
    assert mission is not None
    assert (mission.metadata or {}).get("current_attempt_id") == "LUCIUS_SHIFT_10J_ATTEMPT_2_SMOKE"
    session.close()

