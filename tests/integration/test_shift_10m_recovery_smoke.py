from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
import pytest

from lucius.persistence.orm import utc_now
from lucius.runtime.recovery import RecoveryAction, RecoverySupervisor


@pytest.fixture
def temp_smoke_env(tmp_path):
    smoke_dir = tmp_path / "smoke_recovery_env"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    (smoke_dir / "src" / "lucius").mkdir(parents=True, exist_ok=True)
    (smoke_dir / ".lucius").mkdir(parents=True, exist_ok=True)
    yield smoke_dir


def test_recovery_smoke_stale_pid_recovery(temp_smoke_env):
    """Smoke test: Stale PID / Reboot recovery evaluates to RECOVER_10J_HANDOFF_WATCHDOG on accepted 10J."""
    db_10j = temp_smoke_env / ".lucius" / "state_shift_10j_attempt_2.db"
    conn = sqlite3.connect(db_10j)
    conn.execute("CREATE TABLE durable_missions (id TEXT PRIMARY KEY, status TEXT, created_at TEXT, updated_at TEXT, mission_metadata TEXT)")
    conn.execute("INSERT INTO durable_missions VALUES ('LUCIUS_SHIFT_10J_FIRST_OVERNIGHT', 'ACTIVE', '2026-09-18T19:37:09.956275-06:00', '2026-09-19T02:00:00.000000-06:00', '{}')")
    conn.commit()
    conn.close()

    chk_10j = temp_smoke_env / ".lucius" / "shift_10j_attempt_2_health_checkpoints.jsonl"
    with open(chk_10j, "w") as f:
        f.write('{"elapsed_seconds": 23000.0, "elapsed_hours": 6.39}\n')

    supervisor = RecoverySupervisor(repo_root=temp_smoke_env)
    decision = supervisor.evaluate_recovery_state(override_boot_id="SIMULATED_REBOOT_BOOT_UUID")

    if decision.action != RecoveryAction.RECOVERY_ALREADY_RUNNING:
        assert decision.action == RecoveryAction.RECOVER_10J_HANDOFF_WATCHDOG
        assert decision.storage_available


def test_recovery_smoke_simulated_boot_change_10l_resume(temp_smoke_env):
    """Smoke test: Simulated boot identity change resumes active 10L durable mission."""
    db_10j = temp_smoke_env / ".lucius" / "state_shift_10j_attempt_2.db"
    conn = sqlite3.connect(db_10j)
    conn.execute("CREATE TABLE durable_missions (id TEXT PRIMARY KEY, status TEXT, created_at TEXT, updated_at TEXT, mission_metadata TEXT)")
    conn.execute("INSERT INTO durable_missions VALUES ('LUCIUS_SHIFT_10J_FIRST_OVERNIGHT', 'ACTIVE', '2026-09-18T19:37:09.956275-06:00', '2026-09-19T02:00:00.000000-06:00', '{}')")
    conn.commit()
    conn.close()

    chk_10j = temp_smoke_env / ".lucius" / "shift_10j_attempt_2_health_checkpoints.jsonl"
    with open(chk_10j, "w") as f:
        f.write('{"elapsed_seconds": 23000.0, "elapsed_hours": 6.39}\n')

    wd_state = temp_smoke_env / ".lucius" / "shift_10l_handoff_watchdog_state.json"
    with open(wd_state, "w") as f:
        json.dump({"status": "HANDOFF_LAUNCHED", "launched_10l_pid": 888888}, f)

    db_10l = temp_smoke_env / ".lucius" / "state_shift_10l_travel_mode.db"
    conn = sqlite3.connect(db_10l)
    conn.execute("CREATE TABLE durable_missions (id TEXT PRIMARY KEY, status TEXT, created_at TEXT, updated_at TEXT, mission_metadata TEXT)")
    conn.execute("INSERT INTO durable_missions VALUES ('LUCIUS_SHIFT_10L_TRAVEL_MODE', 'ACTIVE', '2026-09-19T02:00:00-06:00', '2026-09-19T05:00:00-06:00', '{}')")
    conn.commit()
    conn.close()

    supervisor = RecoverySupervisor(repo_root=temp_smoke_env)
    decision = supervisor.evaluate_recovery_state(override_boot_id="SIMULATED_REBOOT_BOOT_UUID_2")

    if decision.action != RecoveryAction.RECOVERY_ALREADY_RUNNING:
        assert decision.action == RecoveryAction.RECOVER_10L
        assert decision.target_mission_id == "LUCIUS_SHIFT_10L_TRAVEL_MODE"


def test_recovery_smoke_storage_wait_and_appear(tmp_path):
    """Smoke test: Delayed storage mount transitions from WAITING_STORAGE to active recovery."""
    nonexistent_repo = tmp_path / "delayed_mount_vol" / "lucius-engineering"

    supervisor = RecoverySupervisor(repo_root=nonexistent_repo)
    decision1 = supervisor.evaluate_recovery_state()

    assert decision1.action == RecoveryAction.WAITING_STORAGE
    assert not decision1.storage_available

    # Storage appears
    nonexistent_repo.mkdir(parents=True, exist_ok=True)
    (nonexistent_repo / "src" / "lucius").mkdir(parents=True, exist_ok=True)

    decision2 = supervisor.evaluate_recovery_state(ignore_running_procs=True)
    assert decision2.storage_available
    assert decision2.action == RecoveryAction.NO_ACTIVE_MISSION
