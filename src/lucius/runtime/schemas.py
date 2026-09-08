from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class RuntimeExecutionOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class RuntimeLoopStatus(StrEnum):
    COMPLETED = "COMPLETED"
    IDLE = "IDLE"
    BLOCKED = "BLOCKED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class RuntimeLoopConfig(BaseModel):
    workflow_ids: list[str] | None = None
    max_tasks: int = 1
    dispatcher_count: int = 1
    stop_on_block: bool = False
    stop_on_escalation: bool = True

    @field_validator("max_tasks")
    @classmethod
    def max_tasks_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_tasks must be positive")
        return value

    @field_validator("dispatcher_count")
    @classmethod
    def exactly_one_dispatcher(cls, value: int) -> int:
        if value != 1:
            raise ValueError("native runtime loop supports exactly one logical dispatcher")
        return value


class RuntimePlanReference(BaseModel):
    workflow_id: str
    plan_id: str
    plan_freeze_id: str
    created_by_runtime: bool = False


class RuntimeExecutionContext(BaseModel):
    workflow_id: str
    item_id: str
    logical_task_id: str
    project_id: str | None = None
    repository_id: str | None = None
    workflow_task_id: str | None = None
    title: str | None = None
    workflow_objective: str
    worktree_path: str
    authority_tier: str
    plan_id: str | None = None
    plan_freeze_id: str | None = None
    queue_item: dict[str, Any] = Field(default_factory=dict)


class ExecutionAdapterResult(BaseModel):
    outcome: RuntimeExecutionOutcome
    completed_substeps: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    verification: list[dict[str, Any]] = Field(default_factory=list)
    documentation: list[dict[str, Any]] = Field(default_factory=list)
    active_execution_seconds: float = 0.0
    external_capacity_wait_seconds: float = 0.0
    human_wait_seconds: float = 0.0
    blocked_task_seconds: float = 0.0
    retries: int = 0
    escalations: int = 0
    blocking_reason: str | None = None
    blocker_category: str | None = None
    resume_condition: str | None = None
    error: str | None = None

    @field_validator(
        "active_execution_seconds",
        "external_capacity_wait_seconds",
        "human_wait_seconds",
        "blocked_task_seconds",
    )
    @classmethod
    def duration_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("runtime durations must be non-negative")
        return value


class RuntimeTaskExecutionRecord(BaseModel):
    workflow_id: str
    item_id: str
    project_id: str | None = None
    provider_id: str
    outcome: RuntimeExecutionOutcome
    completed_substeps: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    verification_count: int = 0
    documentation_count: int = 0


class ExecutionRuntimeLoopResult(BaseModel):
    status: RuntimeLoopStatus
    selected_tasks: int = 0
    completed_tasks: int = 0
    blocked_tasks: int = 0
    resumed_tasks: int = 0
    retries: int = 0
    escalations: int = 0
    provider_ids: list[str] = Field(default_factory=list)
    plan_references: list[RuntimePlanReference] = Field(default_factory=list)
    task_records: list[RuntimeTaskExecutionRecord] = Field(default_factory=list)
    wall_clock_duration_seconds: float = 0.0
    active_execution_time_seconds: float = 0.0
    external_capacity_wait_time_seconds: float = 0.0
    human_wait_time_seconds: float = 0.0
    blocked_task_time_seconds: float = 0.0
    idle_eligible_work_time_seconds: float = 0.0
    single_dispatcher_enforced: bool = True
    stopped_reason: str | None = None
