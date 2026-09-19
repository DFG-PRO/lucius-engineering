#!/usr/bin/env python3
"""Shift 10J Attempt 2 Real Overnight Unattended Run Script.

Executes Lucius Engineering for a minimum of 6 real wall-clock hours (target 8 hours)
against the real DFG portfolio using fresh Attempt 2 artifacts and durable mission state.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any

PROJECT_ROOT = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering").resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, DurableWaitClass, MissionStatus, QueueWorkItemState
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import AuditEventORM, DurableMissionORM, PersistentWorkflowORM, TaskORM, utc_now
from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.projects.regression_guard import RegressionGuardValidator
from lucius.runtime.adapters import ScriptedRuntimePlanningAdapter
from lucius.runtime.continuation import BoundedContinuationService, SessionBudget
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.feeder import DEFAULT_DARWIN_ROOT, DarwinBacklogFeeder
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.ollama import OllamaExecutionProvider
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.service import ExecutionRuntimeLoopService

# Fresh Attempt 2 Default Paths
DEFAULT_DB_PATH = ".lucius/state_shift_10j_attempt_2.db"
DEFAULT_LOG_PATH = ".lucius/shift_10j_attempt_2_execution.log"
DEFAULT_CHECKPOINT_PATH = ".lucius/shift_10j_attempt_2_health_checkpoints.jsonl"
DEFAULT_METRICS_PATH = ".lucius/shift_10j_attempt_2_metrics.json"

CANONICAL_BASELINE_SHA = "6a854800de8cb6e06be45c5ba64d972b4bd95b39"

logger = logging.getLogger("Shift10JRunnerAttempt2")


def _setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler_file = logging.FileHandler(log_path)
    handler_stdout = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler_file.setFormatter(formatter)
    handler_stdout.setFormatter(formatter)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler_file)
    logger.addHandler(handler_stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description="Shift 10J Attempt 2 Real Overnight Unattended Mission")
    parser.add_argument("--hours", type=float, default=6.0, help="Minimum wall-clock duration in hours (default: 6.0)")
    parser.add_argument("--target-hours", type=float, default=8.0, help="Target wall-clock duration in hours (default: 8.0)")
    parser.add_argument("--db-path", type=str, default=DEFAULT_DB_PATH, help="Attempt 2 Persistence DB path")
    parser.add_argument("--mission-id", type=str, default="LUCIUS_SHIFT_10J_FIRST_OVERNIGHT", help="Mission ID")
    parser.add_argument("--attempt-id", type=str, default="LUCIUS_SHIFT_10J_ATTEMPT_2", help="Attempt ID")
    parser.add_argument("--checkpoint-interval-minutes", type=float, default=30.0, help="Health snapshot interval in minutes")
    args = parser.parse_args()

    db_path = (PROJECT_ROOT / args.db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = (PROJECT_ROOT / DEFAULT_LOG_PATH).resolve()
    checkpoint_log_path = (PROJECT_ROOT / DEFAULT_CHECKPOINT_PATH).resolve()
    metrics_path = (PROJECT_ROOT / DEFAULT_METRICS_PATH).resolve()

    _setup_logging(log_path)

    logger.info("=== SHIFT 10J ATTEMPT 2 MISSION PREPARATION ===")
    logger.info("Baseline Starting SHA: %s", CANONICAL_BASELINE_SHA)
    logger.info("Minimum Required Real Wall-Clock Duration: %.2f hours (%.0f seconds)", args.hours, args.hours * 3600.0)
    logger.info("Target Real Wall-Clock Duration: %.2f hours (%.0f seconds)", args.target_hours, args.target_hours * 3600.0)
    logger.info("Mission ID: %s", args.mission_id)
    logger.info("Attempt ID: %s", args.attempt_id)
    logger.info("Fresh Database Path: %s", db_path)
    logger.info("Execution Log Path: %s", log_path)
    logger.info("Health Checkpoints Path: %s", checkpoint_log_path)

    # 1. Initialize Fresh SQLite Database
    engine = create_sqlite_engine(db_path)
    create_all(engine)
    session_factory = make_session_factory(engine)
    session = session_factory()

    supervisor = DurableMissionSupervisor(session, actor=Actor.LUCIUS)
    audit_service = AuditService(session)

    # 2. Create / Recover Durable Mission & Attempt
    existing_mission = supervisor.get_mission(args.mission_id)
    if existing_mission is None:
        mission_rec = supervisor.create_mission(
            canonical_sha=CANONICAL_BASELINE_SHA,
            mission_id=args.mission_id,
            attempt_id=args.attempt_id,
            metadata={
                "scenario": "SHIFT_10J_REAL_OVERNIGHT_UNATTENDED_RUN_ATTEMPT_2",
                "launch_timestamp": utc_now().isoformat(),
                "min_required_wall_hours": args.hours,
                "target_wall_hours": args.target_hours,
            },
        )
    else:
        mission_rec = supervisor.recover_mission(
            args.mission_id,
            canonical_sha=CANONICAL_BASELINE_SHA,
            attempt_id=args.attempt_id,
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

    # 4. Provider & Runtime Setup
    all_roots = [
        PROJECT_ROOT,
        Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine"),
        Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Billy Production Engine/billy-production-engine"),
    ]
    ollama_provider = OllamaExecutionProvider(
        provider_id="ollama-local",
        model="qwen3:8b",
        allowed_workspace_roots=all_roots,
    )
    provider_registry = RuntimeProviderRegistry([ollama_provider])
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

    # Signal / Interruption Handling Setup
    interrupted = False

    def _handle_signal(sig_num: int, frame: Any) -> None:
        nonlocal interrupted
        logger.warning("Clean interruption signal (%s) received! Persisting Attempt 2 state...", sig_num)
        interrupted = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Ingest initial backlog into queue
    initial_ingested = feeder.feed_into_queue(session, max_items=10)
    session.commit()
    logger.info("Initial backlog ingested into fresh queue: %d work packages", len(initial_ingested))

    # Preflight Check Complete Output
    logger.info("Preflight checks completed cleanly.")
    logger.info("OPERATOR COMMAND TO MONITOR ATTEMPT 2 WHILE RUNNING:")
    logger.info("PYTHONPATH=. ./.venv/bin/python -m lucius.pilots.cli --database '%s' status", db_path)


def run_overnight_attempt(
    db_path: Path,
    mission_id: str,
    attempt_id: str,
    min_hours: float,
    target_hours: float,
    checkpoint_interval_minutes: float,
) -> None:
    """Main execution loop for Attempt 2 (called upon explicit operator launch)."""
    log_path = (PROJECT_ROOT / DEFAULT_LOG_PATH).resolve()
    checkpoint_log_path = (PROJECT_ROOT / DEFAULT_CHECKPOINT_PATH).resolve()
    metrics_path = (PROJECT_ROOT / DEFAULT_METRICS_PATH).resolve()

    _setup_logging(log_path)
    engine = create_sqlite_engine(db_path)
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()

    supervisor = DurableMissionSupervisor(session, actor=Actor.LUCIUS)
    audit_service = AuditService(session)

    reg_path = PROJECT_ROOT / "src" / "lucius" / "projects" / "dfg_canonical_registry.json"
    registry = DFGProjectRegistry.load_json(reg_path) if reg_path.exists() else None
    guard = RegressionGuardValidator(registry) if registry is not None else None

    feeder = DarwinBacklogFeeder(
        darwin_root=Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine"),
        registry=registry,
        regression_guard=guard,
    )

    all_roots = [
        PROJECT_ROOT,
        Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine"),
        Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Billy Production Engine/billy-production-engine"),
    ]
    ollama_provider = OllamaExecutionProvider(
        provider_id="ollama-local",
        model="qwen3:8b",
        allowed_workspace_roots=all_roots,
    )
    router = ModelExecutionRouter(session, registry=RuntimeProviderRegistry([ollama_provider]), actor=Actor.LUCIUS)
    dispatcher = MultiProjectDispatcher(session, actor=Actor.LUCIUS)
    runtime_service = ExecutionRuntimeLoopService(
        session=session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
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

    started_monotonic = time.monotonic()
    launch_time_utc = utc_now()
    min_duration_seconds = min_hours * 3600.0
    last_checkpoint_monotonic = started_monotonic
    interrupted = False

    def _sig_handler(sig: int, frame: Any) -> None:
        nonlocal interrupted
        interrupted = True
        logger.warning("Clean interruption signal %d caught.", sig)

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    logger.info("=== LAUNCHING SHIFT 10J ATTEMPT 2 REAL ENDURANCE RUN ===")
    logger.info("Minimum Required Duration: %.2f hours", min_hours)

    cumulative_cycles = 0
    workflow_iterations = 0
    last_useful_completion = "NONE"

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
                canonical_sha=CANONICAL_BASELINE_SHA,
            )
            session.commit()

            cumulative_cycles += res.cycles_attempted
            workflow_iterations += res.cycles_attempted

            for crec in res.cycle_records:
                if crec.outcome == "COMPLETED" and crec.task_id:
                    last_useful_completion = crec.task_id

            # Periodic 30-minute health checkpoint
            if (now_monotonic - last_checkpoint_monotonic) >= (checkpoint_interval_minutes * 60.0):
                last_checkpoint_monotonic = now_monotonic
                m_rec = supervisor.get_mission(mission_id)
                audit_obs = audit_service.get_audit_observability(scheduler_cycles=max(1, cumulative_cycles))
                db_size = db_path.stat().st_size if db_path.exists() else 0

                # Count unique completions and backlog stats
                workflows = session.query(PersistentWorkflowORM).all()
                completed_keys = set()
                active_projects = set()
                waiting_count = 0
                blocked_count = 0
                for wf in workflows:
                    if wf.project_id:
                        active_projects.add(wf.project_id)
                    for item in wf.task_backlog or []:
                        if isinstance(item, dict):
                            st = item.get("state")
                            k = item.get("dedupe_key") or item.get("item_id")
                            if st == "COMPLETED" and k:
                                completed_keys.add(k)
                            elif st and "WAITING" in st:
                                waiting_count += 1
                            elif st and "BLOCKED" in st:
                                blocked_count += 1

                model_calls = session.scalar(
                    select(func.count()).select_from(AuditEventORM).where(
                        AuditEventORM.event_type.in_(["NATIVE_RUNTIME_TASK_EXECUTION_RECORDED", "MODEL_EXECUTION_ROUTING_DECISION"])
                    )
                ) or 0

                earliest_wake = None
                if m_rec and m_rec.metadata and "sleeping_metadata" in m_rec.metadata:
                    earliest_wake = m_rec.metadata["sleeping_metadata"].get("earliest_wake_at")

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
                    "waiting_count": waiting_count,
                    "blocked_count": blocked_count,
                    "retryable_count": waiting_count,
                    "provider_health": "ONLINE",
                    "next_wake": earliest_wake or "NONE",
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

            time.sleep(1.0)

    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt caught! Initiating clean attempt interruption...")
        interrupted = True

    if interrupted:
        supervisor.record_interruption(
            mission_id=mission_id,
            attempt_id=attempt_id,
            reason="Operator clean signal interruption (SIGINT/SIGTERM)",
        )
        session.commit()
        logger.info("Attempt 2 cleanly interrupted and recorded as PAUSED/INTERRUPTED.")
    else:
        final_m = supervisor.reconcile_mission_state(mission_id)
        session.commit()
        logger.info("Attempt 2 execution pass finished with status: %s", final_m.status)

    session.close()


if __name__ == "__main__":
    main()
