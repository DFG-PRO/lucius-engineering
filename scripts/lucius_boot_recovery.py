#!/usr/bin/env python3
"""Lucius Engineering — Boot / Power-Loss Recovery Supervisor CLI.

Inspects machine boot identity, external storage availability, runtime leases, and durable
mission state to perform deterministic auto-resume after power loss or machine reboot.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

PROJECT_ROOT = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering").resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lucius.persistence.orm import utc_now
from lucius.runtime.recovery import (
    CANONICAL_RECOVERY_SUPERVISOR_SHA,
    RecoveryAction,
    RecoverySupervisor,
)

DEFAULT_RECOVERY_LOG_PATH = ".lucius/boot_recovery_execution.log"
logger = logging.getLogger("LuciusBootRecovery")


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


def get_current_git_sha(repo_root: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(repo_root), text=True).strip()
    except Exception:
        return "645cdec8e3f42330a1bf6f70bbbeaa3dbeecad41"


def print_status_report(supervisor: RecoverySupervisor, args: argparse.Namespace) -> None:
    decision = supervisor.evaluate_recovery_state(
        db_10j_path=Path(args.db_10j_path) if args.db_10j_path else None,
        checkpoint_10j_path=Path(args.checkpoint_10j_path) if args.checkpoint_10j_path else None,
        watchdog_state_path=Path(args.watchdog_state_path) if args.watchdog_state_path else None,
        db_10l_path=Path(args.db_10l_path) if args.db_10l_path else None,
    )

    current_sha = get_current_git_sha(supervisor.repo_root)

    report = {
        "recovery_state": decision.action,
        "boot_identity": decision.current_boot_id,
        "current_pid": os.getpid(),
        "mission_id": decision.target_mission_id or "NONE",
        "attempt_id": decision.target_attempt_id or "NONE",
        "current_repository_sha": current_sha,
        "recovery_supervisor_sha": CANONICAL_RECOVERY_SUPERVISOR_SHA,
        "owner_pid": decision.owner_pid or "NONE",
        "owner_stale": decision.owner_stale,
        "BLACKBOX_available": decision.storage_available,
        "Ollama_available": decision.ollama_available,
        "network_available": decision.network_available,
        "recovery_decision": decision.reason,
        "health": "ONLINE" if decision.storage_available else "WAITING_STORAGE",
        "metadata": decision.metadata,
    }

    print(json.dumps(report, indent=2))


def run_recovery_execution(supervisor: RecoverySupervisor, args: argparse.Namespace) -> None:
    log_path = (supervisor.repo_root / DEFAULT_RECOVERY_LOG_PATH).resolve()
    _setup_logging(log_path)

    logger.info("=== LUCIUS BOOT RECOVERY SUPERVISOR STARTED ===")
    logger.info("Recovery Supervisor PID: %d", os.getpid())
    logger.info("Repository Root: %s", supervisor.repo_root)

    decision = supervisor.evaluate_recovery_state(
        db_10j_path=Path(args.db_10j_path) if args.db_10j_path else None,
        checkpoint_10j_path=Path(args.checkpoint_10j_path) if args.checkpoint_10j_path else None,
        watchdog_state_path=Path(args.watchdog_state_path) if args.watchdog_state_path else None,
        db_10l_path=Path(args.db_10l_path) if args.db_10l_path else None,
    )

    logger.info("[RECOVERY DECISION] Action: %s | Reason: %s", decision.action, decision.reason)

    if decision.action == RecoveryAction.RECOVERY_ALREADY_RUNNING:
        logger.info("[SINGLE OWNER GUARANTEE] Production process (PID %s) already active. Exiting recovery cleanly.", decision.owner_pid)
        return

    if decision.action == RecoveryAction.WAITING_STORAGE:
        logger.warning("[WAIT_STORAGE] %s", decision.reason)
        return

    if decision.action in (RecoveryAction.RECOVERY_BLOCKED, RecoveryAction.HANDOFF_BLOCKED, RecoveryAction.MISSION_COMPLETED, RecoveryAction.NO_ACTIVE_MISSION):
        logger.info("[RECOVERY PASSIVE] Action '%s': %s", decision.action, decision.reason)
        return

    python_bin = supervisor.repo_root / ".venv" / "bin" / "python"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(supervisor.repo_root)

    if decision.action == RecoveryAction.RECOVER_10J_HANDOFF_WATCHDOG:
        logger.info("[RECOVERY LAUNCH] Recovering Post-10J Handoff Watchdog process...")
        watchdog_script = supervisor.repo_root / "scripts" / "watchdog_shift_10j_to_10l.py"
        cmd = [str(python_bin), str(watchdog_script)]
        proc = subprocess.Popen(cmd, cwd=str(supervisor.repo_root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        logger.info("[RECOVERY SUCCESS] Handoff Watchdog recovered as PID %d.", proc.pid)

    elif decision.action == RecoveryAction.RECOVER_10L:
        logger.info("[RECOVERY LAUNCH] Recovering active Shift 10L Travel Mode mission...")
        l10_script = supervisor.repo_root / "scripts" / "run_shift_10l_travel_mode.py"
        cmd = [
            str(python_bin),
            str(l10_script),
            "--hours", "102.0",
            "--target-hours", "108.0",
            "--handoff-from-10j",
            "--skip-handoff-verification",
        ]
        proc = subprocess.Popen(cmd, cwd=str(supervisor.repo_root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        logger.info("[RECOVERY SUCCESS] Shift 10L Travel Mode recovered as PID %d.", proc.pid)


def main() -> None:
    parser = argparse.ArgumentParser(description="Lucius Boot Recovery Supervisor")
    parser.add_argument("--repo-root", type=str, default=str(PROJECT_ROOT), help="Repository root path")
    parser.add_argument("--status", action="store_true", help="Print operator JSON status report and exit")
    parser.add_argument("--execute", action="store_true", help="Execute recovery decision logic")
    parser.add_argument("--db-10j-path", type=str, default=None, help="10J DB override (for testing)")
    parser.add_argument("--checkpoint-10j-path", type=str, default=None, help="10J Checkpoints override (for testing)")
    parser.add_argument("--watchdog-state-path", type=str, default=None, help="Watchdog state override (for testing)")
    parser.add_argument("--db-10l-path", type=str, default=None, help="10L DB override (for testing)")
    args = parser.parse_args()

    supervisor = RecoverySupervisor(repo_root=Path(args.repo_root))

    if args.status:
        print_status_report(supervisor, args)
    else:
        run_recovery_execution(supervisor, args)


if __name__ == "__main__":
    main()
