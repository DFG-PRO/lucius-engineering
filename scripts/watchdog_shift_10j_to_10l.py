#!/usr/bin/env python3
"""Shift 10J -> Shift 10L Automatic Post-10J Handoff Watchdog.

Monitors Shift 10J Attempt 2 execution until the 6.0-hour acceptance threshold is reached.
Evaluates 10J evidence in READ-ONLY mode once process ownership is cleanly released,
and launches Shift 10L Travel Mode if and only if all acceptance conditions are met.

Provides strict double-launch protection and idempotent restart safety.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any

PROJECT_ROOT = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering").resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lucius.persistence.orm import utc_now
from scripts.run_shift_10l_travel_mode import (
    DEFAULT_DB_PATH as DEFAULT_10L_DB_PATH,
    SHIFT_10J_CHECKPOINT_PATH,
    SHIFT_10J_DB_PATH,
    SHIFT_10J_T0_ISO,
    verify_10j_acceptance_evidence,
)

DEFAULT_WATCHDOG_STATE_PATH = ".lucius/shift_10l_handoff_watchdog_state.json"
DEFAULT_WATCHDOG_LOG_PATH = ".lucius/shift_10l_handoff_watchdog.log"
CANONICAL_10L_SHA = "1c3eeb60e96f43f51b4ca478b4636ff4c92b48f6"

logger = logging.getLogger("Shift10LHandoffWatchdog")


def _setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not logger.handlers:
        handler_file = logging.FileHandler(log_path)
        handler_stdout = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        handler_file.setFormatter(formatter)
        handler_stdout.setFormatter(formatter)
        logger.setLevel(logging.INFO)
        logger.addHandler(handler_file)
        logger.addHandler(handler_stdout)


def is_process_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
def is_10l_already_running(state_data: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Checks if a Shift 10L Travel Mode supervisor is already running to prevent double launch."""
    if state_data and state_data.get("status") == "HANDOFF_LAUNCHED":
        l_pid = state_data.get("launched_10l_pid")
        if is_process_alive(l_pid):
            return True, f"Shift 10L already launched by watchdog (PID {l_pid})"

    # Check system process table for run_shift_10l_travel_mode.py using ps
    try:
        out = subprocess.check_output(["ps", "-ax", "-o", "pid,command"], text=True)
        for line in out.splitlines():
            if "run_shift_10l_travel_mode.py" in line:
                parts = line.strip().split(maxsplit=1)
                if parts:
                    try:
                        proc_pid = int(parts[0])
                        if proc_pid != os.getpid():
                            return True, f"Shift 10L process found running in process table (PID {proc_pid})"
                    except ValueError:
                        pass
    except Exception as exc:
        logger.warning("Failed to query process table via ps: %s", exc)

    return False, "No active Shift 10L process detected."


def load_watchdog_state(state_path: Path) -> dict[str, Any]:
    if state_path.exists():
        try:
            with open(state_path, "r") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("Failed to load existing watchdog state from %s: %s", state_path, exc)
    return {}


def save_watchdog_state(state_path: Path, data: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    data["last_updated"] = utc_now().isoformat()
    temp_p = state_path.with_suffix(".tmp")
    with open(temp_p, "w") as f:
        json.dump(data, f, indent=2)
    temp_p.replace(state_path)


def run_watchdog_loop(
    t0_iso: str = SHIFT_10J_T0_ISO,
    min_duration_seconds: float = 21600.0,
    target_10j_pid: int = 9902,
    db_10j_path: Path | None = None,
    checkpoint_10j_path: Path | None = None,
    db_10l_path: Path | None = None,
    state_path: Path | None = None,
    log_path: Path | None = None,
    poll_interval_seconds: float = 30.0,
    l10_hours: float = 102.0,
    l10_target_hours: float = 108.0,
    force_eval: bool = False,
) -> None:
    db_10j_path = (db_10j_path or (PROJECT_ROOT / SHIFT_10J_DB_PATH)).resolve()
    checkpoint_10j_path = (checkpoint_10j_path or (PROJECT_ROOT / SHIFT_10J_CHECKPOINT_PATH)).resolve()
    db_10l_path = (db_10l_path or (PROJECT_ROOT / DEFAULT_10L_DB_PATH)).resolve()
    state_path = (state_path or (PROJECT_ROOT / DEFAULT_WATCHDOG_STATE_PATH)).resolve()
    log_path = (log_path or (PROJECT_ROOT / DEFAULT_WATCHDOG_LOG_PATH)).resolve()

    _setup_logging(log_path)

    logger.info("=== SHIFT 10L AUTOMATIC POST-10J HANDOFF WATCHDOG STARTED ===")
    logger.info("Watchdog PID: %d", os.getpid())
    logger.info("Target 10J T0: %s", t0_iso)
    logger.info("Target 10J PID: %d", target_10j_pid)
    logger.info("Minimum 10J Required Seconds: %.1fs (%.2fh)", min_duration_seconds, min_duration_seconds / 3600.0)
    logger.info("Watchdog State Path: %s", state_path)
    logger.info("Watchdog Log Path: %s", log_path)

    # 1. Double Launch & Idempotency Pre-Check
    state = load_watchdog_state(state_path)
    running, reason = is_10l_already_running(state)
    if running:
        logger.warning("[IDEMPOTENCY GUARANTEE] %s. Exiting watchdog cleanly.", reason)
        return

    if state.get("status") == "HANDOFF_BLOCKED" and not force_eval:
        logger.warning("[IDEMPOTENCY GUARANTEE] Watchdog state is already HANDOFF_BLOCKED (%s). Exiting.", state.get("blocked_reason"))
        return

    # Parse T0 timestamp & threshold
    try:
        t0_dt = datetime.fromisoformat(t0_iso)
    except Exception as exc:
        logger.error("Invalid T0 ISO format '%s': %s", t0_iso, exc)
        return

    threshold_dt = t0_dt + timedelta(seconds=min_duration_seconds)
    logger.info("10J Acceptance Threshold Timestamp: %s", threshold_dt.isoformat())

    # Initialize/update watchdog state file
    state.update({
        "watchdog_pid": os.getpid(),
        "started_at": state.get("started_at") or utc_now().isoformat(),
        "status": state.get("status") or "WAITING_FOR_10J",
        "target_10j_pid": target_10j_pid,
        "t0_timestamp": t0_iso,
        "threshold_timestamp": threshold_dt.isoformat(),
        "10l_hours": l10_hours,
        "10l_target_hours": l10_target_hours,
        "10l_canonical_sha": CANONICAL_10L_SHA,
    })
    save_watchdog_state(state_path, state)

    interrupted = False

    def _handle_signal(sig_num: int, frame: Any) -> None:
        nonlocal interrupted
        logger.warning("Signal (%s) received by watchdog. Cleaning up...", sig_num)
        interrupted = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # 2. Phase 1: Wait for Threshold Timestamp (T0 + 6h = 01:37 AM)
    logger.info("Phase 1: Waiting for 10J threshold timestamp (%s)...", threshold_dt.isoformat())

    while not interrupted and not force_eval:
        now_dt = utc_now()
        if now_dt >= threshold_dt:
            logger.info("10J threshold timestamp reached (%s >= %s).", now_dt.isoformat(), threshold_dt.isoformat())
            break

        remaining = (threshold_dt - now_dt).total_seconds()
        logger.info("[WATCHDOG POLL] 10J running (PID %d alive=%s). Threshold in %.2fh (%.0fs). Sleeping %ds...",
                    target_10j_pid, is_process_alive(target_10j_pid), remaining / 3600.0, remaining, poll_interval_seconds)

        sleep_step = min(poll_interval_seconds, max(1.0, remaining))
        time.sleep(sleep_step)

    if interrupted:
        logger.warning("Watchdog interrupted during Phase 1 sleep.")
        return

    # 3. Phase 2: Wait for Safe Ownership Release (10J Process Exit)
    logger.info("Phase 2: Verifying 10J process ownership release (PID %d)...", target_10j_pid)
    state["status"] = "WAITING_FOR_10J_PROCESS_EXIT"
    save_watchdog_state(state_path, state)

    while not interrupted and not force_eval:
        if not is_process_alive(target_10j_pid):
            logger.info("10J process (PID %d) has exited cleanly. Safe ownership released.", target_10j_pid)
            break

        logger.info("[OWNERSHIP WAIT] 10J threshold reached, but PID %d is still running. Waiting for process completion to avoid concurrent write lock...", target_10j_pid)
        time.sleep(poll_interval_seconds)

    if interrupted:
        logger.warning("Watchdog interrupted during Phase 2 process wait.")
        return

    # 4. Phase 3: Read-Only Handoff Gate Evaluation
    logger.info("Phase 3: Evaluating 10J evidence READ-ONLY...")
    state["status"] = "EVALUATING_GATE"
    save_watchdog_state(state_path, state)

    passed, reason = verify_10j_acceptance_evidence(
        db_path=db_10j_path,
        checkpoint_path=checkpoint_10j_path,
        min_required_seconds=min_duration_seconds,
    )

    logger.info("[HANDOFF GATE EVALUATION] Result: %s | Reason: %s", "PASS" if passed else "FAIL/BLOCKED", reason)

    # Re-check double-launch right before launching Popen
    running_final, reason_final = is_10l_already_running(state)
    if running_final:
        logger.warning("[IDEMPOTENCY GUARANTEE] %s. Aborting double launch.", reason_final)
        return

    if passed:
        logger.info("Phase 4: Gate PASSED. Launching Shift 10L Travel Mode Mission...")

        python_bin = PROJECT_ROOT / ".venv" / "bin" / "python"
        launcher_script = PROJECT_ROOT / "scripts" / "run_shift_10l_travel_mode.py"

        cmd = [
            str(python_bin),
            str(launcher_script),
            "--hours", str(l10_hours),
            "--target-hours", str(l10_target_hours),
            "--db-path", str(db_10l_path),
            "--mission-id", "LUCIUS_SHIFT_10L_TRAVEL_MODE",
            "--attempt-id", "LUCIUS_SHIFT_10L_ATTEMPT_1",
            "--handoff-from-10j",
            "--skip-handoff-verification",  # Already verified by watchdog
        ]

        # Launch detached 10L process
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT)

        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        state.update({
            "status": "HANDOFF_LAUNCHED",
            "gate_result": {"passed": True, "reason": reason},
            "launched_10l_pid": proc.pid,
            "launched_at": utc_now().isoformat(),
            "cmd": cmd,
        })
        save_watchdog_state(state_path, state)

        logger.info("[HANDOFF LAUNCH SUCCESS] Shift 10L Travel Mode launched successfully as PID %d!", proc.pid)
        logger.info("10L Command: %s", " ".join(cmd))
    else:
        logger.error("[HANDOFF BLOCKED] 10J acceptance gate FAILED: %s. Shift 10L will NOT be launched.", reason)
        state.update({
            "status": "HANDOFF_BLOCKED",
            "gate_result": {"passed": False, "reason": reason},
            "blocked_reason": reason,
            "blocked_at": utc_now().isoformat(),
        })
        save_watchdog_state(state_path, state)

    logger.info("Watchdog execution pass completed cleanly.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Shift 10L Automatic Post-10J Handoff Watchdog")
    parser.add_argument("--target-t0", type=str, default=SHIFT_10J_T0_ISO, help="10J T0 ISO timestamp")
    parser.add_argument("--min-duration-seconds", type=float, default=21600.0, help="Minimum 10J required seconds (default: 21600.0 = 6h)")
    parser.add_argument("--target-10j-pid", type=int, default=9902, help="Target 10J process PID (default: 9902)")
    parser.add_argument("--db-10j-path", type=str, default=SHIFT_10J_DB_PATH, help="10J SQLite DB path")
    parser.add_argument("--checkpoint-10j-path", type=str, default=SHIFT_10J_CHECKPOINT_PATH, help="10J Checkpoint log path")
    parser.add_argument("--db-10l-path", type=str, default=DEFAULT_10L_DB_PATH, help="10L SQLite DB path")
    parser.add_argument("--watchdog-state-path", type=str, default=DEFAULT_WATCHDOG_STATE_PATH, help="Watchdog JSON state path")
    parser.add_argument("--watchdog-log-path", type=str, default=DEFAULT_WATCHDOG_LOG_PATH, help="Watchdog log path")
    parser.add_argument("--poll-interval-seconds", type=float, default=30.0, help="Watchdog polling interval in seconds (default: 30.0)")
    parser.add_argument("--hours-10l", type=float, default=102.0, help="10L Travel Mode min hours (default: 102.0)")
    parser.add_argument("--target-hours-10l", type=float, default=108.0, help="10L Travel Mode target hours (default: 108.0)")
    parser.add_argument("--force-eval", action="store_true", help="Force immediate evaluation (for testing)")
    args = parser.parse_args()

    run_watchdog_loop(
        t0_iso=args.target_t0,
        min_duration_seconds=args.min_duration_seconds,
        target_10j_pid=args.target_10j_pid,
        db_10j_path=Path(args.db_10j_path),
        checkpoint_10j_path=Path(args.checkpoint_10j_path),
        db_10l_path=Path(args.db_10l_path),
        state_path=Path(args.watchdog_state_path),
        log_path=Path(args.watchdog_log_path),
        poll_interval_seconds=args.poll_interval_seconds,
        l10_hours=args.hours_10l,
        l10_target_hours=args.target_hours_10l,
        force_eval=args.force_eval,
    )


if __name__ == "__main__":
    main()
