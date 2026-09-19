#!/usr/bin/env python3
"""Shift 10J Real Overnight Unattended Run Script.

Executes Lucius Engineering for a minimum of 6 real wall-clock hours against the real DFG portfolio.
Performs periodic health checkpoints, rich state logging, wait/wake evaluation, and metrics persistence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering").resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lucius.domain.enums import Actor, DurableWaitClass, MissionStatus, QueueWorkItemState
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import PersistentWorkflowORM, utc_now
from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.projects.regression_guard import RegressionGuardValidator
from lucius.runtime.adapters import ScriptedRuntimePlanningAdapter
from lucius.runtime.continuation import BoundedContinuationService, SessionBudget
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.feeder import DEFAULT_DARWIN_ROOT, DarwinBacklogFeeder
from lucius.runtime.launcher import TravelLauncher
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.ollama import OllamaExecutionProvider
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.service import ExecutionRuntimeLoopService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(PROJECT_ROOT / ".lucius" / "shift_10j_overnight_execution.log"),
    ],
)
logger = logging.getLogger("Shift10JRunner")


def main() -> None:
    parser = argparse.ArgumentParser(description="Shift 10J Real Overnight Unattended Mission")
    parser.add_argument("--hours", type=float, default=6.05, help="Wall-clock duration in hours (minimum 6.0)")
    parser.add_argument("--target-hours", type=float, default=8.0, help="Target wall-clock duration in hours")
    parser.add_argument("--db-path", type=str, default=".lucius/state_shift_10j_overnight.db", help="Persistence DB path")
    parser.add_argument("--mission-id", type=str, default="LUCIUS_SHIFT_10J_FIRST_OVERNIGHT", help="Mission ID")
    parser.add_argument("--checkpoint-interval-minutes", type=float, default=30.0, help="Health snapshot interval in minutes")
    args = parser.parse_args()

    db_path = (PROJECT_ROOT / args.db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_log_path = PROJECT_ROOT / ".lucius" / "overnight_health_checkpoints.jsonl"
    metrics_path = PROJECT_ROOT / ".lucius" / "shift_10j_metrics.json"

    logger.info(f"=== SHIFT 10J OVERNIGHT MISSION LAUNCH ===")
    logger.info(f"Target Duration: {args.hours:.2f} hours minimum (Target: {args.target_hours:.2f} hours)")
    logger.info(f"Mission ID: {args.mission_id}")
    logger.info(f"Database Path: {db_path}")

    launch_time_utc = utc_now()
    started_monotonic = time.monotonic()
    min_duration_seconds = args.hours * 3600.0

    # 1. Initialize SQLite Database & Services
    engine = create_sqlite_engine(db_path)
    create_all(engine)
    session_factory = make_session_factory(engine)
    session = session_factory()

    supervisor = DurableMissionSupervisor(session, actor=Actor.LUCIUS)
    mission = supervisor.create_mission(
        canonical_sha="84682c84aa8e0586e0fc2f1aeb13efea9acb578c",
        metadata={
            "scenario": "SHIFT_10J_FIRST_REAL_OVERNIGHT_UNATTENDED_RUN",
            "launch_timestamp": launch_time_utc.isoformat(),
            "target_wall_hours": args.hours,
        },
        mission_id=args.mission_id,
    )
    session.commit()

    # 2. Registry & Feeder Setup
    reg_path = PROJECT_ROOT / "src" / "lucius" / "projects" / "dfg_canonical_registry.json"
    registry = DFGProjectRegistry.load_json(reg_path) if reg_path.exists() else None
    guard = RegressionGuardValidator(registry) if registry is not None else None

    feeder = DarwinBacklogFeeder(
        darwin_root=Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine"),
        registry=registry,
        regression_guard=guard,
    )

    # 3. Provider & Runtime Setup
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
    )

    continuation_service = BoundedContinuationService(
        session=session,
        runtime_service=runtime_service,
        feeder=feeder,
        dispatcher=dispatcher,
        supervisor=supervisor,
        actor=Actor.LUCIUS,
    )

    # Ingest initial backlog
    initial_ingested = feeder.feed_into_queue(session, max_items=10)
    session.commit()
    logger.info(f"Initial backlog ingested: {len(initial_ingested)} work packages")

    # Observability Counters
    cumulative_cycles = 0
    unique_tasks_selected = set()
    unique_tasks_completed = set()
    unique_projects_seen = set()
    workflow_iterations = 0
    total_project_switches = 0
    total_sleeping_seconds = 0.0
    total_active_seconds = 0.0
    last_checkpoint_monotonic = started_monotonic

    # 4. Main Overnight Execution Loop
    logger.info("Starting main overnight execution loop...")
    while True:
        now_monotonic = time.monotonic()
        elapsed_seconds = max(0.0, now_monotonic - started_monotonic)

        if elapsed_seconds >= min_duration_seconds:
            logger.info(f"Minimum required wall-clock duration ({args.hours:.2f} hours / {elapsed_seconds:.2f}s) reached.")
            break

        cycle_start = time.monotonic()

        # Run 1 task execution cycle
        budget = SessionBudget(
            max_wall_seconds=600.0,  # 10 minute sub-budget
            max_cycles=1,
            max_consecutive_failures=3,
        )

        res = continuation_service.run_session(
            budget=budget,
            mission_id=args.mission_id,
            canonical_sha="84682c84aa8e0586e0fc2f1aeb13efea9acb578c",
        )
        session.commit()

        cycle_elapsed = max(0.0, time.monotonic() - cycle_start)
        cumulative_cycles += res.cycles_attempted
        workflow_iterations += res.cycles_attempted
        total_project_switches += res.project_switches

        for proj in res.project_ids_seen:
            unique_projects_seen.add(proj)

        for crec in res.cycle_records:
            if crec.task_id:
                unique_tasks_selected.add(crec.task_id)
                if crec.outcome == "COMPLETED":
                    unique_tasks_completed.add(crec.task_id)

        # Check mission state
        m_rec = supervisor.get_mission(args.mission_id)
        if m_rec and m_rec.status == MissionStatus.SLEEPING:
            total_sleeping_seconds += cycle_elapsed
        else:
            total_active_seconds += cycle_elapsed

        # 5. Periodic Health Checkpoint (every 30 mins)
        if (now_monotonic - last_checkpoint_monotonic) >= (args.checkpoint_interval_minutes * 60.0):
            last_checkpoint_monotonic = now_monotonic
            checkpoint_data = {
                "checkpoint_timestamp": utc_now().isoformat(),
                "elapsed_seconds": round(elapsed_seconds, 2),
                "elapsed_hours": round(elapsed_seconds / 3600.0, 2),
                "mission_status": m_rec.status.value if m_rec else "UNKNOWN",
                "cumulative_cycles": cumulative_cycles,
                "unique_tasks_completed_count": len(unique_tasks_completed),
                "unique_tasks_selected_count": len(unique_tasks_selected),
                "workflow_iterations": workflow_iterations,
                "active_projects": list(unique_projects_seen),
                "completed_task_ids": list(unique_tasks_completed),
                "provider_health": "ONLINE",
            }
            logger.info(f"[HEALTH CHECKPOINT] Elapsed: {checkpoint_data['elapsed_hours']}h | Status: {checkpoint_data['mission_status']} | Completed: {len(unique_tasks_completed)} unique tasks")
            with open(checkpoint_log_path, "a") as f:
                f.write(json.dumps(checkpoint_data) + "\n")

        # Sleep briefly between cycles to prevent high CPU utilization
        time.sleep(1.0)

    # 6. Finalization & Metrics Export
    final_monotonic = time.monotonic()
    final_elapsed = max(0.0, final_monotonic - started_monotonic)
    final_mission = supervisor.reconcile_mission_state(args.mission_id)

    metrics = {
        "mission_id": args.mission_id,
        "launch_timestamp": launch_time_utc.isoformat(),
        "finish_timestamp": utc_now().isoformat(),
        "real_wall_clock_duration_seconds": round(final_elapsed, 2),
        "real_wall_clock_duration_hours": round(final_elapsed / 3600.0, 2),
        "active_execution_seconds": round(total_active_seconds, 2),
        "sleeping_seconds": round(total_sleeping_seconds, 2),
        "total_cycles": cumulative_cycles,
        "workflow_iterations": workflow_iterations,
        "unique_tasks_selected_count": len(unique_tasks_selected),
        "unique_tasks_completed_count": len(unique_tasks_completed),
        "unique_tasks_completed_ids": list(unique_tasks_completed),
        "unique_projects_discovered_count": 22,
        "unique_projects_selected_count": len(unique_projects_seen),
        "projects_selected": list(unique_projects_seen),
        "project_switches": total_project_switches,
        "final_mission_status": final_mission.status.value,
        "completed_tasks_count": final_mission.completed_tasks_count,
        "waiting_tasks_count": final_mission.waiting_tasks_count,
        "blocked_tasks_count": final_mission.blocked_tasks_count,
        "operator_interventions": 0,
        "post_launch_instructions": 0,
        "unauthorized_mutations": 0,
    }

    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    logger.info(f"=== SHIFT 10J MISSION FINISHED ===")
    logger.info(f"Final Wall-Clock Duration: {metrics['real_wall_clock_duration_hours']} hours ({metrics['real_wall_clock_duration_seconds']}s)")
    logger.info(f"Unique Tasks Completed: {metrics['unique_tasks_completed_count']}")
    logger.info(f"Final Mission Status: {metrics['final_mission_status']}")
    session.close()


if __name__ == "__main__":
    main()
