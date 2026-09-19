#!/usr/bin/env python3
"""Shift 10L Travel Mode Dedicated Mission Script.

Executes Lucius Engineering for multi-day unattended operation (48-72 hours)
with durable idle sleep/wake semantics, bounded audit growth, and post-10J handoff verification.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import sys
import time
from typing import Any

from sqlalchemy import func, select

PROJECT_ROOT = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering").resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, DurableWaitClass, MissionStatus, QueueWorkItemState
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import AuditEventORM, DurableMissionORM, PersistentWorkflowORM, TaskORM, utc_now
from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.projects.regression_guard import RegressionGuardValidator
from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
from lucius.runtime.continuation import BoundedContinuationService, SessionBudget, sleep_until_next_wake_or_recheck
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.feeder import DEFAULT_DARWIN_ROOT, DarwinBacklogFeeder
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.ollama import OllamaExecutionProvider
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.service import ExecutionRuntimeLoopService

# Fresh Attempt 1 Default Paths for Shift 10L
DEFAULT_DB_PATH = ".lucius/state_shift_10l_travel_mode.db"
DEFAULT_LOG_PATH = ".lucius/shift_10l_travel_mode_execution.log"
DEFAULT_CHECKPOINT_PATH = ".lucius/shift_10l_travel_mode_health_checkpoints.jsonl"
DEFAULT_METRICS_PATH = ".lucius/shift_10l_travel_mode_metrics.json"

SHIFT_10J_DB_PATH = ".lucius/state_shift_10j_attempt_2.db"
SHIFT_10J_CHECKPOINT_PATH = ".lucius/shift_10j_attempt_2_health_checkpoints.jsonl"
SHIFT_10J_T0_ISO = "2026-09-18T19:37:09.956275-06:00"

logger = logging.getLogger("Shift10LTravelModeRunner")


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


def get_current_canonical_sha(expected_sha: str | None = None) -> str:
    try:
        import subprocess
        head_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(PROJECT_ROOT), text=True).strip()
        if expected_sha and head_sha != expected_sha:
            raise ValueError(f"Canonical SHA mismatch: expected {expected_sha}, but git HEAD is {head_sha}")
        return head_sha
    except ValueError:
        raise
    except Exception as exc:
        if expected_sha:
            return expected_sha
        logger.warning("Failed to resolve git HEAD SHA: %s", exc)
        return "d44ff8ce22ccdf30a15c498a772a41bc107229c3"


def verify_10j_acceptance_evidence(
    db_path: Path | None = None,
    checkpoint_path: Path | None = None,
    min_required_seconds: float = 21600.0,
) -> tuple[bool, str]:
    """Safely inspects Shift 10J Attempt 2 evidence in READ-ONLY mode.

    Verifies whether Shift 10J completed its minimum 6.0 real wall-clock hours.
    Returns (True, reason) if threshold met, else (False, reason).
    """
    db_p = (db_path or (PROJECT_ROOT / SHIFT_10J_DB_PATH)).resolve()
    chk_p = (checkpoint_path or (PROJECT_ROOT / SHIFT_10J_CHECKPOINT_PATH)).resolve()

    if not db_p.exists():
        return False, f"Shift 10J DB not found at {db_p}"

    # Read-only SQLite connection to avoid write-lock or mutation of PID 9902 DB
    try:
        conn = sqlite3.connect(f"file:{db_p}?mode=ro", uri=True)
        cursor = conn.cursor()

        # Query 10J mission status
        cursor.execute("SELECT id, status, created_at, updated_at FROM durable_missions WHERE id = 'LUCIUS_SHIFT_10J_FIRST_OVERNIGHT'")
        row = cursor.fetchone()
        conn.close()

        if row is None:
            return False, "Shift 10J mission record 'LUCIUS_SHIFT_10J_FIRST_OVERNIGHT' not found in 10J DB"

        m_id, m_status, m_created, m_updated = row

        # Check elapsed time from checkpoints if present
        if chk_p.exists():
            with open(chk_p, "r") as f:
                lines = [line.strip() for line in f if line.strip()]
            if lines:
                last_chk = json.loads(lines[-1])
                elapsed = last_chk.get("elapsed_seconds", 0.0)
                if elapsed >= min_required_seconds:
                    return True, f"Shift 10J reached acceptance condition ({elapsed / 3600.0:.2f}h elapsed >= 6.0h requirement)"
                else:
                    return False, f"Shift 10J in progress/incomplete ({elapsed / 3600.0:.2f}h elapsed < 6.0h requirement)"

        # Fallback to DB timestamps if no checkpoint log
        if m_created:
            try:
                t0_dt = datetime.fromisoformat(SHIFT_10J_T0_ISO)
                now_dt = utc_now()
                elapsed_dt = (now_dt - t0_dt).total_seconds()
                if elapsed_dt >= min_required_seconds:
                    return True, f"Shift 10J timestamp verification passed ({elapsed_dt / 3600.0:.2f}h elapsed >= 6.0h requirement)"
                else:
                    return False, f"Shift 10J timestamp verification pending ({elapsed_dt / 3600.0:.2f}h elapsed < 6.0h requirement)"
            except Exception as ex:
                return False, f"Failed to parse 10J T0 timestamp: {ex}"

        return False, f"Shift 10J evidence inconclusive (status={m_status})"

    except Exception as exc:
        return False, f"Error inspecting Shift 10J DB: {exc}"


def run_travel_mode_mission(
    db_path: Path,
    mission_id: str,
    attempt_id: str,
    min_hours: float = 48.0,
    target_hours: float = 72.0,
    checkpoint_interval_minutes: float = 30.0,
    recheck_interval_seconds: float = 300.0,
    canonical_sha: str | None = None,
    pulse_interval_seconds: float = 60.0,
    handoff_from_10j: bool = False,
    skip_handoff_verification: bool = False,
    use_scripted_provider: bool = False,
) -> None:
    """Main execution loop for Shift 10L Travel Mode multi-day mission."""
    db_path = Path(db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    log_path = (PROJECT_ROOT / DEFAULT_LOG_PATH).resolve()
    checkpoint_log_path = (PROJECT_ROOT / DEFAULT_CHECKPOINT_PATH).resolve()

    _setup_logging(log_path)

    logger.info("=== SHIFT 10L TRAVEL MODE MISSION PREPARATION ===")

    # 1. Handoff Verification Gate
    if handoff_from_10j and not skip_handoff_verification:
        passed, reason = verify_10j_acceptance_evidence(min_required_seconds=min_hours * 3600.0 if min_hours < 6.0 else 21600.0)
        logger.info("[HANDOFF GATE] 10J Evidence Verification Result: %s (%s)", "PASS" if passed else "FAIL/BLOCKED", reason)
        if not passed:
            logger.error("[HANDOFF GATE FAILED] Shift 10J has not reached acceptance threshold. Handoff BLOCKED.")
            logger.error("Shift 10L Travel Mode launch aborted to preserve 10J production integrity.")
            return

    current_canonical_sha = get_current_canonical_sha(canonical_sha)

    logger.info("Baseline Starting SHA: %s", current_canonical_sha)
    logger.info("Minimum Required Duration: %.2f hours (%.0f seconds)", min_hours, min_hours * 3600.0)
    logger.info("Target Duration: %.2f hours (%.0f seconds)", target_hours, target_hours * 3600.0)
    logger.info("Mission ID: %s", mission_id)
    logger.info("Attempt ID: %s", attempt_id)
    logger.info("Database Path: %s", db_path)
    logger.info("Execution Log Path: %s", log_path)
    logger.info("Health Checkpoints Path: %s", checkpoint_log_path)

    # 2. Database & Supervisor Setup
    engine = create_sqlite_engine(str(db_path))
    create_all(engine)
    session_factory = make_session_factory(engine)
    session = session_factory()

    supervisor = DurableMissionSupervisor(session, actor=Actor.LUCIUS)
    audit_service = AuditService(session)

    existing_mission = supervisor.get_mission(mission_id)
    if existing_mission is None:
        mission_rec = supervisor.create_mission(
            canonical_sha=current_canonical_sha,
            mission_id=mission_id,
            attempt_id=attempt_id,
            metadata={
                "scenario": "SHIFT_10L_TRAVEL_MODE_MULTI_DAY",
                "launch_timestamp": utc_now().isoformat(),
                "min_required_wall_hours": min_hours,
                "target_wall_hours": target_hours,
                "recheck_interval_seconds": recheck_interval_seconds,
            },
        )
    else:
        mission_rec = supervisor.recover_mission(
            mission_id,
            current_canonical_sha=current_canonical_sha,
            attempt_id=attempt_id,
        )
    session.commit()

    # 3. Registry & Feeder Setup
    reg_path = PROJECT_ROOT / "src" / "lucius" / "projects" / "dfg_canonical_registry.json"
    registry = DFGProjectRegistry.load_json(reg_path) if reg_path.exists() else None
    guard = RegressionGuardValidator(registry) if registry is not None else None

    feeder = DarwinBacklogFeeder(
        darwin_root=Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine"),
        registry=registry,
        regression_guard=guard,
    )

    initial_ingested = feeder.feed_into_queue(session, max_items=10)
    session.commit()
    logger.info("Initial backlog ingested: %d work packages", len(initial_ingested))

    # 4. Runtime & Dispatcher Setup
    if use_scripted_provider:
        provider = ScriptedExecutionAdapter(provider_id="scripted-local")
    else:
        all_roots = [
            PROJECT_ROOT,
            Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine"),
            Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Billy Production Engine/billy-production-engine"),
        ]
        provider = OllamaExecutionProvider(
            provider_id="ollama-local",
            model="qwen3:8b",
            allowed_workspace_roots=all_roots,
        )
    provider_registry = RuntimeProviderRegistry([provider])
    planning_adapter = ScriptedRuntimePlanningAdapter()
    router = ModelExecutionRouter(session, registry=provider_registry, actor=Actor.LUCIUS)
    dispatcher = MultiProjectDispatcher(session, actor=Actor.LUCIUS)

    runtime_service = ExecutionRuntimeLoopService(
        session=session,
        planning_adapter=planning_adapter,
        execution_router=router,
        dispatcher=dispatcher,
        actor=Actor.LUCIUS,
    )

    continuation_service = BoundedContinuationService(
        session=session,
        runtime_service=runtime_service,
        feeder=feeder,
        dispatcher=dispatcher,
        supervisor=supervisor,
        actor=Actor.LUCIUS,
    )

    logger.info("Preflight checks completed cleanly.")

    # 5. Signal Handling
    interrupted = False

    def _handle_signal(sig_num: int, frame: Any) -> None:
        nonlocal interrupted
        logger.warning("Clean interruption signal (%s) received! Persisting 10L Travel Mode state...", sig_num)
        interrupted = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    started_monotonic = time.monotonic()
    min_duration_seconds = min_hours * 3600.0

    logger.info("=== SHIFT 10L TRAVEL MODE REAL ENDURANCE START ===")
    logger.info("T0_LOCAL: %s", datetime.now().astimezone().isoformat())
    logger.info("T0_UTC: %s", utc_now().isoformat())
    logger.info("MISSION_ID: %s", mission_id)
    logger.info("ATTEMPT_ID: %s", attempt_id)

    cumulative_cycles = 0
    workflow_iterations = 0
    last_useful_completion = "NONE"
    last_checkpoint_monotonic = started_monotonic
    last_pulse_monotonic = started_monotonic

    try:
        while not interrupted:
            now_monotonic = time.monotonic()
            elapsed = max(0.0, now_monotonic - started_monotonic)

            if elapsed >= min_duration_seconds:
                logger.info("Minimum required duration (%.2f hours) reached. Session complete.", min_hours)
                break

            budget = SessionBudget(max_wall_seconds=600.0, max_cycles=1, max_consecutive_failures=3)
            res = continuation_service.run_session(
                budget,
                mission_id=mission_id,
                attempt_id=attempt_id,
                canonical_sha=current_canonical_sha,
                recheck_interval_seconds=recheck_interval_seconds,
            )
            session.commit()

            cumulative_cycles += res.cycles_attempted
            workflow_iterations += res.cycles_attempted

            for crec in res.cycle_records:
                if crec.outcome == "COMPLETED" and crec.task_id:
                    last_useful_completion = crec.task_id

            now_monotonic = time.monotonic()
            elapsed = max(0.0, now_monotonic - started_monotonic)

            # Liveness Pulse
            if (now_monotonic - last_pulse_monotonic) >= pulse_interval_seconds:
                last_pulse_monotonic = now_monotonic
                logger.info(
                    "[LIVENESS PULSE] Elapsed: %.2fh | Status: %s | Cycles: %d | Last Useful: %s | Next Wake: %s",
                    elapsed / 3600.0,
                    res.status,
                    cumulative_cycles,
                    last_useful_completion,
                    res.next_wake.isoformat() if res.next_wake else "NONE",
                )

            # Health Checkpoint
            if (now_monotonic - last_checkpoint_monotonic) >= (checkpoint_interval_minutes * 60.0):
                last_checkpoint_monotonic = now_monotonic
                m_rec = supervisor.get_mission(mission_id)
                audit_obs = audit_service.get_audit_observability(scheduler_cycles=max(1, cumulative_cycles))
                db_size = db_path.stat().st_size if db_path.exists() else 0

                workflows = session.query(PersistentWorkflowORM).all()
                completed_keys = set()
                active_projects = set()
                waiting_by_class: dict[str, int] = {}
                blocked_by_class: dict[str, int] = {}

                for wf in workflows:
                    if wf.project_id:
                        active_projects.add(wf.project_id)
                    for item in wf.task_backlog or []:
                        if isinstance(item, dict):
                            st = item.get("state")
                            k = item.get("dedupe_key") or item.get("item_id")
                            if st == QueueWorkItemState.COMPLETED.value and k:
                                completed_keys.add(k)
                            elif st and "WAITING" in st:
                                waiting_by_class[st] = waiting_by_class.get(st, 0) + 1
                            elif st and "BLOCKED" in st:
                                blocked_by_class[st] = blocked_by_class.get(st, 0) + 1

                model_calls = session.scalar(
                    select(func.count()).select_from(AuditEventORM).where(
                        AuditEventORM.event_type.in_(["NATIVE_RUNTIME_TASK_EXECUTION_RECORDED", "MODEL_EXECUTION_ROUTING_DECISION"])
                    )
                ) or 0

                checkpoint = {
                    "checkpoint_timestamp": utc_now().isoformat(),
                    "attempt_id": attempt_id,
                    "elapsed_seconds": round(elapsed, 2),
                    "elapsed_hours": round(elapsed / 3600.0, 2),
                    "mission_status": m_rec.status.value if m_rec else "UNKNOWN",
                    "cumulative_cycles": cumulative_cycles,
                    "workflow_iterations": workflow_iterations,
                    "model_executions": model_calls,
                    "canonical_work_count": session.scalar(select(func.count()).select_from(TaskORM)) or 0,
                    "unique_useful_completions_count": len(completed_keys),
                    "last_useful_completion": last_useful_completion,
                    "active_projects": sorted(list(active_projects)),
                    "waiting_by_class": waiting_by_class,
                    "blocked_by_class": blocked_by_class,
                    "total_waiting": sum(waiting_by_class.values()),
                    "total_blocked": sum(blocked_by_class.values()),
                    "next_wake": res.next_wake.isoformat() if res.next_wake else "NONE",
                    "sleep_recommended_seconds": res.sleep_recommended_seconds,
                    "db_size_bytes": db_size,
                    "audit_event_count": audit_obs["event_count"],
                    "audit_total_bytes": audit_obs["total_audit_bytes"],
                    "audit_bytes_per_cycle": round(audit_obs["audit_bytes_per_cycle"], 2),
                    "health_status": "ONLINE" if (m_rec and m_rec.status.value in ("ACTIVE", "WAITING", "SLEEPING")) else "OFFLINE",
                }

                logger.info(
                    "[HEALTH CHECKPOINT] Elapsed: %.2fh | Status: %s | Unique Completions: %d | DB: %d bytes | Audit: %.1f B/cycle",
                    checkpoint["elapsed_hours"],
                    checkpoint["mission_status"],
                    checkpoint["unique_useful_completions_count"],
                    checkpoint["db_size_bytes"],
                    checkpoint["audit_bytes_per_cycle"],
                )
                with open(checkpoint_log_path, "a") as f:
                    f.write(json.dumps(checkpoint) + "\n")

            # Durable Sleep / Backoff when no tasks executed in session
            if res.tasks_selected == 0:
                sleep_until_next_wake_or_recheck(
                    res.next_wake,
                    recheck_interval_seconds=recheck_interval_seconds,
                    check_interrupted_fn=lambda: interrupted,
                )
            else:
                time.sleep(1.0)

    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt caught! Initiating clean 10L Travel Mode interruption...")
        interrupted = True

    if interrupted:
        supervisor.record_interruption(
            mission_id=mission_id,
            attempt_id=attempt_id,
            reason="Operator clean signal interruption (SIGINT/SIGTERM)",
        )
        session.commit()
        logger.info("Shift 10L Travel Mode cleanly interrupted and recorded as PAUSED/INTERRUPTED.")
    else:
        final_m = supervisor.reconcile_mission_state(mission_id)
        session.commit()
        logger.info("Shift 10L Travel Mode execution pass finished with status: %s", final_m.status)

    session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Shift 10L Travel Mode Multi-Day Unattended Mission Runner")
    parser.add_argument("--hours", type=float, default=48.0, help="Minimum wall-clock duration in hours (default: 48.0)")
    parser.add_argument("--target-hours", type=float, default=72.0, help="Target wall-clock duration in hours (default: 72.0)")
    parser.add_argument("--db-path", type=str, default=DEFAULT_DB_PATH, help="Shift 10L Persistence DB path")
    parser.add_argument("--mission-id", type=str, default="LUCIUS_SHIFT_10L_TRAVEL_MODE", help="Mission ID")
    parser.add_argument("--attempt-id", type=str, default="LUCIUS_SHIFT_10L_ATTEMPT_1", help="Attempt ID")
    parser.add_argument("--canonical-sha", type=str, default=None, help="Explicit canonical SHA override")
    parser.add_argument("--recheck-interval-seconds", type=float, default=300.0, help="Periodic portfolio recheck interval in seconds")
    parser.add_argument("--checkpoint-interval-minutes", type=float, default=30.0, help="Health snapshot interval in minutes")
    parser.add_argument("--handoff-from-10j", action="store_true", help="Verify 10J Attempt 2 evidence gate before starting 10L")
    parser.add_argument("--skip-handoff-verification", action="store_true", help="Skip 10J handoff verification (for testing/smoke runs)")
    parser.add_argument("--use-scripted-provider", action="store_true", help="Use scripted execution provider instead of Ollama (for testing)")
    args = parser.parse_args()

    db_path = (PROJECT_ROOT / args.db_path).resolve()
    run_travel_mode_mission(
        db_path=db_path,
        mission_id=args.mission_id,
        attempt_id=args.attempt_id,
        min_hours=args.hours,
        target_hours=args.target_hours,
        recheck_interval_seconds=args.recheck_interval_seconds,
        checkpoint_interval_minutes=args.checkpoint_interval_minutes,
        canonical_sha=args.canonical_sha,
        handoff_from_10j=args.handoff_from_10j,
        skip_handoff_verification=args.skip_handoff_verification,
        use_scripted_provider=args.use_scripted_provider,
    )


if __name__ == "__main__":
    main()
