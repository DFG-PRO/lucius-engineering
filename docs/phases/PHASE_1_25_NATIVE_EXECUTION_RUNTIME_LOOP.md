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
- Reconcile aggregate workflow/task lifecycle after durable queue completion.
  When every schedulable workflow backlog item is successfully `COMPLETED`,
  `completed_task_ids` exactly matches the backlog, no `pending_task_ids`
  remain, and no item is running, blocked, failed, or waiting to resume, the
  runtime moves the workflow to `COMPLETED_PENDING_INTEGRATION` and moves the
  bound parent task out of executable `READY`. If no task-level documentation
  evidence is required, the parent task becomes `COMPLETE`; if required
  documentation evidence is still missing or incomplete, it becomes
  `DOCUMENTATION_PENDING`.

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
- aggregate lifecycle reconciliation after successful workflow completion,
  including blocked, resumed, partial, idempotent, documentation-pending, and
  historical-row cases;
- CLI entrypoint execution.

## Phase 1.25 Darwin Pilot Closure Repair

The first Native Runtime Darwin Pilot exposed a canonical pre-mutation release
gap: `LTASK_000076` blocked on `MISSING_REPOSITORY` at `LAUDIT_002571` /
`LAUDIT_002572`, yet isolated target mutation began without a later successful
readiness transition. The repaired runtime treats that state as non-executable
and records `NATIVE_RUNTIME_PRE_MUTATION_RELEASE_BLOCKED` before blocking the
selected queue item. Provider dispatch is unreachable until the exact task is
explicitly re-readied and its current repository/plan/freeze evidence validates.

## Phase 1.25 Lifecycle Normalization Repair

The successful Native Runtime Darwin Pilot retry completed all runtime workflow
queue items, but `LTASK_000077` remained top-level `READY`. That was not a
reporting defect: the runtime used the queue as the execution lifecycle and did
not reconcile the bound aggregate `TaskORM` after the workflow backlog reached
successful terminal completion. The workflow row also remained `PLAN_READY`,
which kept a completed workflow inside an execution-eligible lifecycle class.

The repaired invariant is:

```text
A runtime workflow with a bound parent task must not remain execution-eligible
after all schedulable backlog items are successfully completed, no pending or
active work remains, and no unresolved blocker/retry/resume state exists.
```

The repair is deliberately narrow. Reconciliation runs only after
`NonBlockingQueueService.complete_item` has durably marked a queue item
`COMPLETED`. It does not hide blocked, partial, failed, malformed, or legacy
workflow state. Reconciliation is idempotent and records
`NATIVE_RUNTIME_WORKFLOW_PARENT_TASK_RECONCILED` when it changes canonical
state.

## Model Execution Router Readiness

Lucius is ready to implement the Model Execution Router as the next phase, but
the router must remain below runtime orchestration authority. The runtime owns
global/task selection, repository attachment checks, readiness/release,
plan/freeze authority, exact dispatch identity, verification, blocking,
resume, and completion. The router chooses an execution provider for an already
authorized `RuntimeExecutionContext`; it does not select tasks, mutate queue
state, alter frozen plans, or grant authority.

The minimum router should provide:

- a deterministic execution-provider registry with declared capabilities,
  supported task types, repository/project allowlists, privacy/isolation class,
  tool requirements, context limits, cost/latency classes, availability, and
  provider/model/version identity;
- a canonical `RuntimeExecutionRequest` derived from the frozen plan and
  pre-dispatch release context, carrying required capabilities, policy,
  budget, sandbox/isolation requirements, tool requirements, and immutable
  task/workflow/repository/plan/freeze identifiers;
- a canonical `RuntimeExecutionResult` compatible with the existing
  `ExecutionAdapterResult`, plus provider/model/version, routing decision,
  attempt number, fallback flag, usage/cost/latency, retryability, and
  sanitized error category;
- deterministic routing over capability fit, policy, repository/project scope,
  availability, cost/budget, latency, context limits, tool support, sandbox
  requirements, and reliability history;
- failover only for provider unavailability or retryable provider failure, not
  for Lucius-side release, validation, verification, or authority failures;
- audit events that preserve selected provider/model/profile, rejected
  candidates, fallback chain, request/result IDs, and sanitized metrics.

The router should reuse Phase 1.8 `ModelGateway` concepts where they fit, but
it must not simply rename the current scripted/Codex adapter. The structural
boundary must allow scripted, Codex, API model, local model, and future
specialized execution providers to satisfy the same runtime execution
contract.

Explicitly deferred from the router phase: multi-project dispatcher expansion,
multi-worker leases or heartbeats, provider marketplace discovery, credential
expansion, push/merge/deploy authority, live-money execution, autonomous
authority escalation, and providers directly writing Lucius canonical lifecycle
state.

## Limits

This phase does not add multi-project scheduling beyond the existing global
queue service, does not add multi-worker leases or heartbeats, does not grant
push/merge/deploy authority, and does not authorize live-money execution.
