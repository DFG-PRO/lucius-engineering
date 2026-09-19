"""Shift 10M — Boot / Power-Loss Recovery & Durable Auto-Resume Module.

Provides machine-level boot recovery, single-owner lease management, process table
inspection, and deterministic recovery decision tree for Lucius Engineering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from typing import Any

from lucius.persistence.orm import utc_now

logger = logging.getLogger(__name__)

CANONICAL_RECOVERY_SUPERVISOR_SHA = "645cdec8e3f42330a1bf6f70bbbeaa3dbeecad41"


class RecoveryAction:
    NO_ACTIVE_MISSION = "NO_ACTIVE_MISSION"
    RECOVER_10J = "RECOVER_10J"
    RECOVER_10J_HANDOFF_WATCHDOG = "RECOVER_10J_HANDOFF_WATCHDOG"
    RECOVER_10L = "RECOVER_10L"
    HANDOFF_PENDING = "HANDOFF_PENDING"
    HANDOFF_BLOCKED = "HANDOFF_BLOCKED"
    MISSION_COMPLETED = "MISSION_COMPLETED"
    RECOVERY_ALREADY_RUNNING = "RECOVERY_ALREADY_RUNNING"
    RECOVERY_BLOCKED = "RECOVERY_BLOCKED"
    RECOVERY_CONFLICT = "RECOVERY_CONFLICT"
    WAITING_STORAGE = "WAITING_STORAGE"
    WAITING_RESOURCE = "WAITING_RESOURCE"


@dataclass
class DurableRuntimeLease:
    mission_id: str
    attempt_id: str
    owner_pid: int
    owner_boot_id: str
    canonical_sha: str
    acquired_at: datetime = field(default_factory=utc_now)
    heartbeat_at: datetime = field(default_factory=utc_now)
    lease_expires_at: datetime = field(default_factory=lambda: utc_now() + timedelta(seconds=120))
    state: str = "ACTIVE"

    def is_valid_owner(self, current_boot_id: str) -> bool:
        if self.owner_boot_id != current_boot_id:
            return False
        return is_process_alive(self.owner_pid)


@dataclass
class RecoveryDecision:
    action: str
    reason: str
    target_mission_id: str | None = None
    target_attempt_id: str | None = None
    storage_available: bool = True
    ollama_available: bool = True
    network_available: bool = True
    current_boot_id: str = ""
    owner_pid: int | None = None
    owner_stale: bool = False
    canonical_sha_match: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


def get_current_boot_id() -> str:
    """Resolves macOS machine boot UUID or boot time identifier."""
    try:
        out = subprocess.check_output(["sysctl", "-n", "kern.bootuuid"], text=True).strip()
        if out:
            return out
    except Exception:
        pass
    try:
        out = subprocess.check_output(["sysctl", "-n", "kern.boottime"], text=True).strip()
        if out:
            return out
    except Exception:
        pass
    return "UNKNOWN_BOOT_ID"


def is_process_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_storage_available(repo_path: Path) -> bool:
    """Verifies expected repository directory and volume exist."""
    try:
        repo_p = Path(repo_path).resolve()
        if not repo_p.exists():
            return False
        if not (repo_p / "src" / "lucius").exists():
            return False
        return True
    except Exception:
        return False


def is_ollama_available(host: str = "127.0.0.1", port: int = 11434, timeout_seconds: float = 1.0) -> bool:
    """Verifies local Ollama HTTP service readiness."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout_seconds)
        res = sock.connect_ex((host, port))
        sock.close()
        return res == 0
    except Exception:
        return False


def is_network_available(host: str = "1.1.1.1", port: int = 53, timeout_seconds: float = 1.0) -> bool:
    """Verifies outbound network connectivity."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout_seconds)
        res = sock.connect_ex((host, port))
        sock.close()
        return res == 0
    except Exception:
        return False


class RecoverySupervisor:
    """Manages boot recovery decisions, single-owner guarantees, and auto-resume transitions."""

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root).resolve()

    def evaluate_recovery_state(
        self,
        db_10j_path: Path | None = None,
        checkpoint_10j_path: Path | None = None,
        watchdog_state_path: Path | None = None,
        db_10l_path: Path | None = None,
        override_boot_id: str | None = None,
        ignore_running_procs: bool = False,
    ) -> RecoveryDecision:
        current_boot_id = override_boot_id or get_current_boot_id()
        storage_ok = is_storage_available(self.repo_root)
        ollama_ok = is_ollama_available()
        network_ok = is_network_available()

        if not storage_ok:
            return RecoveryDecision(
                action=RecoveryAction.WAITING_STORAGE,
                reason=f"Storage or repository path '{self.repo_root}' is absent or unmounted. Waiting for storage volume.",
                storage_available=False,
                ollama_available=ollama_ok,
                network_available=network_ok,
                current_boot_id=current_boot_id,
            )

        db_10j = (db_10j_path or (self.repo_root / ".lucius" / "state_shift_10j_attempt_2.db")).resolve()
        chk_10j = (checkpoint_10j_path or (self.repo_root / ".lucius" / "shift_10j_attempt_2_health_checkpoints.jsonl")).resolve()
        wd_state_p = (watchdog_state_path or (self.repo_root / ".lucius" / "shift_10l_handoff_watchdog_state.json")).resolve()
        db_10l = (db_10l_path or (self.repo_root / ".lucius" / "state_shift_10l_travel_mode.db")).resolve()

        # 1. Process Table Inspection for Active Production Processes
        running_procs: list[dict[str, Any]] = []
        if not ignore_running_procs:
            try:
                out = subprocess.check_output(["ps", "-ax", "-o", "pid,command"], text=True)
                for line in out.splitlines():
                    if "run_shift_10j_attempt_2.py" in line or "watchdog_shift_10j_to_10l.py" in line or "run_shift_10l_travel_mode.py" in line:
                        parts = line.strip().split(maxsplit=1)
                        if parts:
                            try:
                                p_pid = int(parts[0])
                                if p_pid != os.getpid():
                                    running_procs.append({"pid": p_pid, "cmd": parts[1]})
                            except ValueError:
                                pass
            except Exception as exc:
                logger.warning("Failed to inspect process table via ps: %s", exc)

        if running_procs:
            active_pids = [p["pid"] for p in running_procs]
            return RecoveryDecision(
                action=RecoveryAction.RECOVERY_ALREADY_RUNNING,
                reason=f"Active production supervisor or watchdog process is already running (PIDs: {active_pids}). Single-owner guarantee enforced.",
                storage_available=True,
                ollama_available=ollama_ok,
                network_available=network_ok,
                current_boot_id=current_boot_id,
                owner_pid=active_pids[0],
                owner_stale=False,
                metadata={"running_processes": running_procs},
            )

        # 2. Check 10J DB Evidence & Watchdog State
        if not db_10j.exists():
            return RecoveryDecision(
                action=RecoveryAction.NO_ACTIVE_MISSION,
                reason="No active or historical Shift 10J mission DB found.",
                storage_available=True,
                ollama_available=ollama_ok,
                network_available=network_ok,
                current_boot_id=current_boot_id,
            )

        # Query 10J evidence READ-ONLY
        elapsed_10j = 0.0
        mission_10j_status = "UNKNOWN"
        t0_10j_iso = None

        if chk_10j.exists():
            try:
                with open(chk_10j, "r") as f:
                    lines = [line.strip() for line in f if line.strip()]
                if lines:
                    last_chk = json.loads(lines[-1])
                    elapsed_10j = last_chk.get("elapsed_seconds", 0.0)
            except Exception as exc:
                logger.warning("Failed to parse 10J checkpoints: %s", exc)

        try:
            conn = sqlite3.connect(f"file:{db_10j}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute("SELECT status, created_at, mission_metadata FROM durable_missions WHERE id = 'LUCIUS_SHIFT_10J_FIRST_OVERNIGHT'")
            row = cursor.fetchone()
            conn.close()
            if row:
                mission_10j_status = row[0]
                t0_10j_iso = row[1]
                if elapsed_10j == 0.0 and t0_10j_iso:
                    t0_dt = datetime.fromisoformat(t0_10j_iso)
                    elapsed_10j = (utc_now() - t0_dt).total_seconds()
        except Exception as exc:
            return RecoveryDecision(
                action=RecoveryAction.RECOVERY_BLOCKED,
                reason=f"Failed to query 10J DB in read-only mode: {exc}",
                storage_available=True,
                ollama_available=ollama_ok,
                network_available=network_ok,
                current_boot_id=current_boot_id,
            )

        # 3. Decision Tree Evaluation
        min_10j_required = 21600.0  # 6.0 hours

        # CASE 2: 10J was interrupted by reboot BEFORE reaching 6.0h threshold
        if elapsed_10j < min_10j_required:
            return RecoveryDecision(
                action=RecoveryAction.RECOVERY_BLOCKED,
                reason=f"Shift 10J process terminated or machine rebooted prior to 6.0h acceptance threshold ({elapsed_10j / 3600.0:.2f}h elapsed < 6.0h requirement). Wall-clock endurance time cannot be credited across downtime.",
                storage_available=True,
                ollama_available=ollama_ok,
                network_available=network_ok,
                current_boot_id=current_boot_id,
                owner_stale=True,
                metadata={"elapsed_10j_seconds": elapsed_10j, "10j_status": mission_10j_status},
            )

        # Read watchdog state if 10J reached threshold (>= 6.0h)
        wd_state = {}
        if wd_state_p.exists():
            try:
                with open(wd_state_p, "r") as f:
                    wd_state = json.load(f)
            except Exception as exc:
                logger.warning("Failed to load watchdog state: %s", exc)

        wd_status = wd_state.get("status")

        # CASE 6: Watchdog is explicitly HANDOFF_BLOCKED
        if wd_status == "HANDOFF_BLOCKED":
            return RecoveryDecision(
                action=RecoveryAction.HANDOFF_BLOCKED,
                reason=f"Post-10J handoff is explicitly HANDOFF_BLOCKED ({wd_state.get('blocked_reason')}).",
                storage_available=True,
                ollama_available=ollama_ok,
                network_available=network_ok,
                current_boot_id=current_boot_id,
                metadata={"watchdog_state": wd_state},
            )

        # CASE 4 / 5: 10L was already launched
        if wd_status == "HANDOFF_LAUNCHED" or db_10l.exists():
            m_10l_status = "UNKNOWN"
            if db_10l.exists():
                try:
                    conn = sqlite3.connect(f"file:{db_10l}?mode=ro", uri=True)
                    cursor = conn.cursor()
                    cursor.execute("SELECT status FROM durable_missions WHERE id = 'LUCIUS_SHIFT_10L_TRAVEL_MODE'")
                    r = cursor.fetchone()
                    conn.close()
                    if r:
                        m_10l_status = r[0]
                except Exception:
                    pass

            if m_10l_status == "COMPLETED":
                return RecoveryDecision(
                    action=RecoveryAction.MISSION_COMPLETED,
                    reason="Shift 10L Travel Mode mission has already completed successfully.",
                    storage_available=True,
                    ollama_available=ollama_ok,
                    network_available=network_ok,
                    current_boot_id=current_boot_id,
                    metadata={"10l_status": m_10l_status},
                )

            # Recover existing 10L mission
            return RecoveryDecision(
                action=RecoveryAction.RECOVER_10L,
                reason="Reboot detected while Shift 10L Travel Mode was active. Resuming existing 10L durable mission.",
                target_mission_id="LUCIUS_SHIFT_10L_TRAVEL_MODE",
                target_attempt_id="LUCIUS_SHIFT_10L_ATTEMPT_1",
                storage_available=True,
                ollama_available=ollama_ok,
                network_available=network_ok,
                current_boot_id=current_boot_id,
                owner_stale=True,
                metadata={"10l_status": m_10l_status, "watchdog_state": wd_state},
            )

        # CASE 3: 10J reached acceptance threshold and handoff watchdog needs recovery
        return RecoveryDecision(
            action=RecoveryAction.RECOVER_10J_HANDOFF_WATCHDOG,
            reason="Shift 10J completed 6.0h acceptance threshold. Recovering handoff watchdog process.",
            target_mission_id="LUCIUS_SHIFT_10J_FIRST_OVERNIGHT",
            storage_available=True,
            ollama_available=ollama_ok,
            network_available=network_ok,
            current_boot_id=current_boot_id,
            owner_stale=True,
            metadata={"elapsed_10j_seconds": elapsed_10j, "watchdog_state": wd_state},
        )
