from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
import logging
import time
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, DurableWaitClass, QueueWorkItemState, TaskStatus
from lucius.persistence.orm import PersistentWorkflowORM, utc_now
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.feeder import DarwinBacklogFeeder
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.schemas import (
    ExecutionRuntimeLoopResult,
    RuntimeLoopConfig,
    RuntimeLoopStatus,
    RuntimeTaskExecutionRecord,
)
from lucius.runtime.service import ExecutionRuntimeLoopService, TASK_LOCAL_FAILURE_CLASSES

logger = logging.getLogger(__name__)


class ContinuationStopReason(StrEnum):
    IDLE_NO_ELIGIBLE_WORK = "IDLE_NO_ELIGIBLE_WORK"
    IDLE_NO_WORK_EXISTS = "IDLE_NO_WORK_EXISTS"
    WAITING_NO_CURRENTLY_RUNNABLE_WORK = "WAITING_NO_CURRENTLY_RUNNABLE_WORK"
    SESSION_WALL_BUDGET_REACHED = "SESSION_WALL_BUDGET_REACHED"
    SESSION_CYCLE_BUDGET_REACHED = "SESSION_CYCLE_BUDGET_REACHED"
    FAILURE_BUDGET_REACHED = "FAILURE_BUDGET_REACHED"
    AUTHORITY_EXHAUSTED = "AUTHORITY_EXHAUSTED"
    ESCALATION_STOP = "ESCALATION_STOP"


class SessionBudget(BaseModel):
    max_wall_seconds: float = Field(default=14400.0, description="Max session duration (4 hours default)")
    max_cycles: int = Field(default=50, description="Max task execution cycles")
    max_consecutive_failures: int = Field(default=3, description="Max consecutive uncontained failures")
    max_total_failures: int = Field(default=5, description="Max total failures allowed in session")
    max_provider_calls: int = Field(default=100, description="Max total model provider calls")
    max_active_worktrees: int = Field(default=3, description="Max concurrent active worktrees")


@dataclass
class ContinuationCycleRecord:
    cycle_number: int
    task_id: str | None = None
    workflow_id: str | None = None
    project_id: str | None = None
    outcome: str | None = None
    stopped_reason: str | None = None
    duration_seconds: float = 0.0


@dataclass
class ContinuationSessionResult:
    status: str = "IDLE"
    stop_reason: str = ContinuationStopReason.IDLE_NO_ELIGIBLE_WORK.value
    cycles_attempted: int = 0
    tasks_selected: int = 0
    tasks_completed: int = 0
    tasks_blocked: int = 0
    tasks_failed: int = 0
    project_switches: int = 0
    feeder_invocations: int = 0
    tasks_fed: int = 0
    consecutive_failures: int = 0
    total_failures: int = 0
    provider_calls: int = 0
    wall_clock_duration_seconds: float = 0.0
    operator_corrections: int = 0
    post_launch_external_instructions: int = 0
    cycle_records: list[ContinuationCycleRecord] = field(default_factory=list)
    project_ids_seen: list[str] = field(default_factory=list)


class BoundedContinuationService:
    """Manages multi-project continuation, feeder integration, and session budgets."""

    def __init__(
        self,
        session: Session,
        *,
        runtime_service: ExecutionRuntimeLoopService,
        feeder: DarwinBacklogFeeder | None = None,
        dispatcher: MultiProjectDispatcher | None = None,
        supervisor: DurableMissionSupervisor | None = None,
        actor: Actor = Actor.LUCIUS,
        clock=time.monotonic,
    ):
        self.session = session
        self.runtime_service = runtime_service
        self.feeder = feeder
        self.dispatcher = dispatcher or runtime_service.dispatcher
        self.supervisor = supervisor or DurableMissionSupervisor(session, actor=actor)
        self.actor = actor
        self.audit = AuditService(session)
        self.clock = clock

    def run_session(
        self,
        budget: SessionBudget,
        *,
        workflow_ids: list[str] | None = None,
        stop_on_block: bool = False,
        mission_id: str | None = None,
        canonical_sha: str | None = None,
    ) -> ContinuationSessionResult:
        started_at = self.clock()
        result = ContinuationSessionResult()
        active_workflows = list(workflow_ids) if workflow_ids is not None else None
        feeder_exhausted = False
        last_project_id: str | None = None

        if mission_id is not None:
            if canonical_sha:
                self.supervisor.recover_mission(mission_id, canonical_sha)
            else:
                self.supervisor.get_mission(mission_id)
        else:
            current_sha = canonical_sha or "HEAD"
            mission = self.supervisor.create_mission(canonical_sha=current_sha)
            mission_id = mission.mission_id

        self.audit.record(
            event_type="BOUNDED_CONTINUATION_SESSION_STARTED",
            actor=self.actor.value,
            action="run_session",
            result="STARTED",
            metadata={
                "budget": budget.model_dump(mode="json"),
                "workflow_ids": active_workflows,
                "mission_id": mission_id,
            },
        )

        while True:
            elapsed = max(0.0, self.clock() - started_at)
            result.wall_clock_duration_seconds = elapsed

            # Budget check: Wall Clock
            if elapsed >= budget.max_wall_seconds:
                result.stop_reason = ContinuationStopReason.SESSION_WALL_BUDGET_REACHED.value
                result.status = "BUDGET_EXHAUSTED"
                break

            # Budget check: Cycles
            if result.cycles_attempted >= budget.max_cycles:
                result.stop_reason = ContinuationStopReason.SESSION_CYCLE_BUDGET_REACHED.value
                result.status = "BUDGET_EXHAUSTED"
                break

            # Budget check: Failures
            if result.consecutive_failures >= budget.max_consecutive_failures or result.total_failures >= budget.max_total_failures:
                result.stop_reason = ContinuationStopReason.FAILURE_BUDGET_REACHED.value
                result.status = "FAILED"
                break

            # Select and run 1 task cycle
            cycle_start = self.clock()
            cycle_record = ContinuationCycleRecord(cycle_number=result.cycles_attempted + 1)

            loop_config = RuntimeLoopConfig(
                workflow_ids=active_workflows,
                max_tasks=1,
                dispatcher_count=1,
                stop_on_block=stop_on_block,
                stop_on_escalation=True,
            )

            runtime_res: ExecutionRuntimeLoopResult = self.runtime_service.run(loop_config)
            cycle_record.duration_seconds = max(0.0, self.clock() - cycle_start)
            result.cycles_attempted += 1

            if runtime_res.selected_tasks > 0:
                result.tasks_selected += runtime_res.selected_tasks
                result.tasks_completed += runtime_res.completed_tasks
                result.tasks_blocked += runtime_res.blocked_tasks
                if runtime_res.task_records:
                    rec = runtime_res.task_records[-1]
                    cycle_record.task_id = rec.item_id
                    cycle_record.workflow_id = rec.workflow_id
                    cycle_record.project_id = rec.project_id
                    cycle_record.outcome = rec.outcome.value

                    if rec.project_id:
                        if rec.project_id not in result.project_ids_seen:
                            result.project_ids_seen.append(rec.project_id)
                        if last_project_id is not None and rec.project_id != last_project_id:
                            result.project_switches += 1
                        last_project_id = rec.project_id

                    if rec.outcome.value == "COMPLETED":
                        result.consecutive_failures = 0
                    elif rec.outcome.value == "FAILED":
                        result.tasks_failed += 1
                        result.total_failures += 1
                        if rec.failure_class not in TASK_LOCAL_FAILURE_CLASSES:
                            result.consecutive_failures += 1
                        else:
                            result.consecutive_failures = 0
                    elif rec.outcome.value == "BLOCKED":
                        result.consecutive_failures = 0

                result.cycle_records.append(cycle_record)

                if runtime_res.status == RuntimeLoopStatus.ESCALATED:
                    result.stop_reason = ContinuationStopReason.ESCALATION_STOP.value
                    result.status = "ESCALATED"
                    break

                continue

            # If no task was selected:
            if runtime_res.status == RuntimeLoopStatus.FAILED:
                cycle_record.stopped_reason = runtime_res.stopped_reason
                result.cycle_records.append(cycle_record)
                result.stop_reason = runtime_res.stopped_reason or "DISPATCH_FAILED"
                result.status = "FAILED"
                break

            # Queue is empty / IDLE: consult task feeder if available
            if self.feeder is not None and not feeder_exhausted:
                result.feeder_invocations += 1
                newly_ingested = self.feeder.feed_into_queue(self.session, max_items=5)
                if newly_ingested:
                    result.tasks_fed += len(newly_ingested)
                    new_wf_ids = [item["workflow_id"] for item in newly_ingested]
                    if active_workflows is not None:
                        active_workflows.extend(new_wf_ids)
                    self.audit.record(
                        event_type="TASKS_FED_FROM_BACKLOG",
                        actor=self.actor.value,
                        action="feed_into_queue",
                        result="SUCCESS",
                        metadata={"count": len(newly_ingested), "workflows": new_wf_ids},
                    )
                    continue
                else:
                    feeder_exhausted = True

            # Check if any tasks remain in WAITING state in the queue
            waiting_count = 0
            for wf in self.session.query(PersistentWorkflowORM).all():
                for item in wf.task_backlog or []:
                    if isinstance(item, dict) and item.get("state") in (
                        QueueWorkItemState.WAITING_RESOURCE.value,
                        QueueWorkItemState.WAITING_DEPENDENCY.value,
                        QueueWorkItemState.WAITING_SCHEDULE.value,
                        QueueWorkItemState.WAITING_HUMAN.value,
                        QueueWorkItemState.WAITING_EXTERNAL.value,
                    ):
                        waiting_count += 1

            if mission_id:
                self.supervisor.reconcile_mission_state(mission_id)

            if waiting_count > 0:
                result.stop_reason = ContinuationStopReason.WAITING_NO_CURRENTLY_RUNNABLE_WORK.value
                result.status = "WAITING"
            else:
                result.stop_reason = ContinuationStopReason.IDLE_NO_ELIGIBLE_WORK.value
                result.status = "IDLE"
            break

        result.wall_clock_duration_seconds = max(0.0, self.clock() - started_at)
        self.audit.record(
            event_type="BOUNDED_CONTINUATION_SESSION_COMPLETED",
            actor=self.actor.value,
            action="run_session",
            result=result.status,
            metadata={
                "stop_reason": result.stop_reason,
                "cycles": result.cycles_attempted,
                "completed": result.tasks_completed,
                "blocked": result.tasks_blocked,
                "switches": result.project_switches,
                "duration": result.wall_clock_duration_seconds,
                "mission_id": mission_id,
            },
        )
        return result
