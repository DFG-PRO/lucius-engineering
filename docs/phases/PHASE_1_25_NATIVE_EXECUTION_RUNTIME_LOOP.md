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
- Enforce canonical pre-mutation release immediately before provider dispatch.
  The selected workflow item may only reach the execution adapter when the
  bound task is READY, its active contract is valid, its repository is
  registered and attached to the task project, its frozen plan matches the
  exact task/project/contract, and selection/mutation identity markers match.
- Preserve explicit re-readiness semantics after setup failure. A repository
  attachment made after a `MISSING_REPOSITORY` block does not implicitly
  authorize execution; the task must pass a later `TASK_READY` transition.
- Treat execution adapter exceptions as fail-closed task blocks. A provider
  that raises before returning a canonical result does not complete work or
  discard lifecycle state; the selected item is blocked with resume metadata.

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
- fail-closed blocking when an execution adapter raises;
- fail-closed pre-mutation blocking for missing repositories, stale readiness,
  and selected-task/mutation identity mismatches before the provider callback
  is reachable;
- blocked-interval accounting for resumed items when canonical queue
  timestamps include both `blocked_at` and `resolved_at`;
- CLI entrypoint execution.

## Phase 1.25 Darwin Pilot Closure Repair

The first Native Runtime Darwin Pilot exposed a canonical pre-mutation release
gap: `LTASK_000076` blocked on `MISSING_REPOSITORY` at `LAUDIT_002571` /
`LAUDIT_002572`, yet isolated target mutation began without a later successful
readiness transition. The repaired runtime treats that state as non-executable
and records `NATIVE_RUNTIME_PRE_MUTATION_RELEASE_BLOCKED` before blocking the
selected queue item. Provider dispatch is unreachable until the exact task is
explicitly re-readied and its current repository/plan/freeze evidence validates.

## Limits

This phase does not add multi-project scheduling beyond the existing global
queue service, does not add multi-worker leases or heartbeats, does not grant
push/merge/deploy authority, and does not authorize live-money execution.
