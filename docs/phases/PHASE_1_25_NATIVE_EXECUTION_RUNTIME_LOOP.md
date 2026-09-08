# Phase 1.25 Native Execution Runtime Loop

Phase 1.25 introduces the first native Lucius execution runtime loop. The
runtime is intentionally bounded: it supports one logical dispatcher, isolated
engineering work, canonical queue state, canonical plan freeze, pluggable
planning/execution adapters, and fail-closed task blocking or escalation.

## Scope

- Implement a runtime service that inspects persisted workflow queues, prepares
  missing workflow plans through `EngineeringPlanRepository`, freezes them
  through `PlanFreezeService`, and dispatches selected work through
  `NonBlockingQueueService`.
- Keep execution provider behavior behind adapter protocols so Codex, API
  model, local model, or deterministic scripted providers can share the same
  runtime lifecycle contract.
- Add a bounded CLI entrypoint:
  `python -m lucius.pilots.cli run-runtime-loop [workflow_ids...] --max-tasks N`.
- Preserve one-dispatcher semantics. The runtime rejects dispatcher counts
  other than one and the CLI command runs under the canonical artifact-store
  write lock.

## Runtime Observability

`ExecutionRuntimeLoopResult` records:

- `wall_clock_duration_seconds`
- `active_execution_time_seconds`
- `external_capacity_wait_time_seconds`
- `human_wait_time_seconds`
- `blocked_task_time_seconds`
- `idle_eligible_work_time_seconds`
- selected, completed, blocked, and resumed task counts
- provider IDs used for execution
- retries and escalations
- per-task provider/evidence/verification/documentation counts

## Validation

Focused runtime tests cover:

- canonical runtime planning and plan freeze before queue mutation;
- bounded multi-task continuation;
- block-and-switch behavior when alternative eligible work exists;
- resume counting without duplicate completed substeps;
- fresh-session readback of plan freeze and completed queue state;
- replaceable execution adapter behavior;
- CLI entrypoint execution.

## Limits

This phase does not add multi-project scheduling beyond the existing global
queue service, does not add multi-worker leases or heartbeats, does not grant
push/merge/deploy authority, and does not authorize live-money execution.
