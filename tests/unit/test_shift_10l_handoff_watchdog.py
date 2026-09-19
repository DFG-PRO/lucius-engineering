from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
import pytest

from lucius.persistence.orm import utc_now
from scripts.watchdog_shift_10j_to_10l import (
    is_10l_already_running,
    is_process_alive,
    load_watchdog_state,
    run_watchdog_loop,
    save_watchdog_state,
)


@pytest.fixture
def temp_env(tmp_path):
    env_dir = tmp_path / "watchdog_test_env"
    env_dir.mkdir(parents=True, exist_ok=True)
    yield env_dir


def test_watchdog_successful_gate_single_launch(temp_env):
    """Scenario 1: WAIT -> threshold -> read-only gate PASS -> ownership release -> exactly one 10L launch."""
    db_10j = temp_env / "state_shift_10j.db"
    conn = sqlite3.connect(db_10j)
    conn.execute("CREATE TABLE durable_missions (id TEXT PRIMARY KEY, status TEXT, created_at TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO durable_missions VALUES ('LUCIUS_SHIFT_10J_FIRST_OVERNIGHT', 'ACTIVE', '2026-09-18T19:37:09.956275-06:00', '2026-09-19T02:00:00.000000-06:00')")
    conn.commit()
    conn.close()

    chk_10j = temp_env / "checkpoints_10j.jsonl"
    with open(chk_10j, "w") as f:
        f.write('{"elapsed_seconds": 23000.0, "elapsed_hours": 6.39}\n')

    db_10l = temp_env / "state_shift_10l.db"
    state_p = temp_env / "watchdog_state.json"
    log_p = temp_env / "watchdog.log"

    # Use a non-existent PID (999999) to simulate process already exited (ownership released)
    # Use T0 in past and force_eval=True for deterministic test execution
    past_t0 = (utc_now() - timedelta(hours=7)).isoformat()

    run_watchdog_loop(
        t0_iso=past_t0,
        min_duration_seconds=21600.0,
        target_10j_pid=999999,
        db_10j_path=db_10j,
        checkpoint_10j_path=chk_10j,
        db_10l_path=db_10l,
        state_path=state_p,
        log_path=log_p,
        poll_interval_seconds=0.1,
        force_eval=True,
    )

    state = load_watchdog_state(state_p)
    assert state.get("status") == "HANDOFF_LAUNCHED"
    assert state.get("gate_result", {}).get("passed") is True
    assert state.get("launched_10l_pid") is not None

    # Clean up spawned test 10L process if alive
    l_pid = state.get("launched_10l_pid")
    if l_pid and is_process_alive(l_pid):
        try:
            import os
            os.kill(l_pid, 9)
        except OSError:
            pass


def test_watchdog_failed_gate_zero_launches(temp_env):
    """Scenario 2: WAIT -> threshold -> read-only gate FAIL -> HANDOFF_BLOCKED -> zero 10L launches."""
    db_10j = temp_env / "state_shift_10j_fail.db"
    conn = sqlite3.connect(db_10j)
    conn.execute("CREATE TABLE durable_missions (id TEXT PRIMARY KEY, status TEXT, created_at TEXT, updated_at TEXT)")
    conn.execute("INSERT INTO durable_missions VALUES ('LUCIUS_SHIFT_10J_FIRST_OVERNIGHT', 'ACTIVE', '2026-09-18T19:37:09.956275-06:00', '2026-09-18T20:37:09.956275-06:00')")
    conn.commit()
    conn.close()

    chk_10j = temp_env / "checkpoints_10j_fail.jsonl"
    with open(chk_10j, "w") as f:
        f.write('{"elapsed_seconds": 3600.0, "elapsed_hours": 1.0}\n')

    db_10l = temp_env / "state_shift_10l_fail.db"
    state_p = temp_env / "watchdog_state_fail.json"
    log_p = temp_env / "watchdog_fail.log"

    past_t0 = (utc_now() - timedelta(hours=7)).isoformat()

    run_watchdog_loop(
        t0_iso=past_t0,
        min_duration_seconds=21600.0,
        target_10j_pid=999999,
        db_10j_path=db_10j,
        checkpoint_10j_path=chk_10j,
        db_10l_path=db_10l,
        state_path=state_p,
        log_path=log_p,
        poll_interval_seconds=0.1,
        force_eval=True,
    )

    state = load_watchdog_state(state_p)
    assert state.get("status") == "HANDOFF_BLOCKED"
    assert state.get("gate_result", {}).get("passed") is False
    assert state.get("launched_10l_pid") is None


def test_watchdog_restart_idempotency_prevents_duplicate_launch(temp_env):
    """Scenario 3: Watchdog restart when 10L state is already HANDOFF_LAUNCHED does not spawn duplicate process."""
    state_p = temp_env / "watchdog_state_launched.json"
    log_p = temp_env / "watchdog_launched.log"

    # Pre-populate state file simulating an already launched mission
    my_pid = os.getpid()
    fake_state = {
        "watchdog_pid": 1234,
        "status": "HANDOFF_LAUNCHED",
        "launched_10l_pid": my_pid,  # Use current pid so is_process_alive returns True
        "gate_result": {"passed": True, "reason": "Already passed"},
    }
    save_watchdog_state(state_p, fake_state)

    run_watchdog_loop(
        t0_iso=(utc_now() - timedelta(hours=7)).isoformat(),
        min_duration_seconds=21600.0,
        target_10j_pid=999999,
        db_10j_path=temp_env / "dummy.db",
        checkpoint_10j_path=temp_env / "dummy.jsonl",
        db_10l_path=temp_env / "dummy_10l.db",
        state_path=state_p,
        log_path=log_p,
        poll_interval_seconds=0.1,
        force_eval=True,
    )

    # Verify state remains HANDOFF_LAUNCHED and wasn't overwritten or duplicated
    state_after = load_watchdog_state(state_p)
    assert state_after.get("status") == "HANDOFF_LAUNCHED"
    assert state_after.get("launched_10l_pid") == my_pid
