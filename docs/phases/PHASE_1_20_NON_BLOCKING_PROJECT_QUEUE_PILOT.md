# Phase 1.20: Non-Blocking Project Queue Pilot

Phase 1.20 adds and evaluates a minimal persistent non-blocking project queue
for Lucius. The rule is: block the work item, not the system.

The pilot is Lucius-only. Darwin main remains a target-integrity reference and
is not modified, branched, cleaned, formatted, migrated, committed, merged, or
pushed.

## Baseline

- Lucius baseline HEAD: `70e40effedb698129db42d0d016fc19e85f13e4a`
- Lucius baseline status: `## main...origin/main [ahead 15]`, clean
- Lucius baseline tests: `.venv/bin/python -m pytest` -> `178 passed in 88.50s`
- PRE benchmark: `LBENCH_000025`, `LUCIUS_CORE_BENCH_V0_1`, `8 passed`, aggregate `100.0`, hard gate `PASS`
- Darwin baseline HEAD: `3207aee087fa9018ee9ff2d526fe7a97443f6e21`
- Darwin baseline status: `## main...origin/main [ahead 6]`, clean

## Objective

Implement the smallest durable queue service needed for Lucius to persist a
blocked work item, release capacity, schedule another eligible item, later mark
the blocked item `READY_TO_RESUME`, and resume it without duplicate work,
stale-state overwrite, or workflow contamination.

## Design

The implementation extends existing Phase 1.19 persistent workflow storage
rather than adding a migration. Queue work items live in
`PersistentWorkflow.task_backlog` as structured JSON. Queue block checkpoints
use stable `LQCHK_*` ids and are stored inside the blocked work item plus the
workflow checkpoint history.

The canonical queue states are:

- `READY`
- `RUNNING`
- `WAITING_HUMAN`
- `WAITING_EXTERNAL`
- `BLOCKED_DEPENDENCY`
- `RETRY_LATER`
- `READY_TO_RESUME`
- `COMPLETED`
- `FAILED`

Blocked states represent local blockage of a work item. They do not convert the
whole workflow into `WAITING_HUMAN`.

## Scheduling Rule

`NonBlockingQueueService.select_next` is deterministic:

1. validate duplicate queue ids and duplicate `RUNNING` logical task ids;
2. if any item is `RUNNING`, return `RUNNING_ITEM_ACTIVE_NO_PREEMPTION`;
3. exclude blocked, failed, completed, and dependency-incomplete work;
4. choose the first eligible item by priority rank, then state class, then
   creation order, then item id.

Priority rank is `CRITICAL`, `HIGH`, `NORMAL`, `LOW`. Within the same priority,
`READY_TO_RESUME` outranks `READY`; it does not automatically outrank a
higher-priority `READY` item.

## Block Checkpoint

When a running item blocks, Lucius records:

- workflow/project id and work-item id;
- prior state and blocking state;
- blocking reason and blocker category;
- timestamp;
- completed substeps;
- implementation HEAD where relevant;
- pending decision or dependency;
- resume condition;
- known risks and relevant artifacts;
- repair counters and approval requirements;
- next safe action;
- stale-state validation requirements.

Resolving a blocker requires the current checkpoint id and expected item
version. A stale checkpoint or stale item version fails closed.

## Pilot Scenario

The controlled scenario uses three work items:

- `A1`: high-priority implementation/service work; starts first, records
  progress, then blocks in `WAITING_EXTERNAL`.
- `B1`: normal-priority documentation/test work; selected after `A1` blocks and
  completed while `A1` remains blocked.
- `C1`: low-priority follow-up work depending on `B1`; ineligible until `B1`
  completes, then allowed to run.

After `A1` is resolved, it transitions to `READY_TO_RESUME`. If `C1` is already
running, `A1` waits until the next safe scheduler checkpoint. After `C1`
completes, `A1` resumes and completes without replaying the substeps already
recorded in its block checkpoint.

## Fresh Context

Fresh-context validation reopens the persisted SQLite database in a new Python
process and reconstructs queue status with `NonBlockingQueueService` only from
durable workflow state. The process must be able to identify running, blocked,
ready, ready-to-resume, completed, dependency-blocked, and next schedulable
work, including why the selected item wins.

## Observability

The pilot CLI exposes read-only JSON inspection:

- `python -m lucius.pilots.cli queue-status <workflow_id>`
- `python -m lucius.pilots.cli queue-next <workflow_id>`

`queue-status` reports running, blocked, ready, ready-to-resume,
dependency-blocked, completed, failed, and the next selection. `queue-next`
reports the deterministic scheduling decision without mutating state.

## Evaluation

The Phase 1.20 deterministic evaluation checks:

- scheduler correctness;
- persistence quality;
- non-blocking behavior;
- resume correctness;
- duplicate-work prevention;
- dependency correctness;
- workflow isolation;
- fresh-context recovery;
- operator observability;
- autonomy behavior;
- documentation quality;
- benchmark regression risk.

No human rubric is invented. If no human scoring is captured, the rubric remains
`NOT_CAPTURED`.

## Integrity

Lucius changes are local to the Lucius repository. Darwin is read-only for this
phase and remains unchanged. The implementation does not add migrations,
dependencies, external brokers, worker pools, cloud orchestration, agent swarms,
or arbitrary concurrency.

## Limitations

Phase 1.20 is not a distributed scheduler and does not provide true concurrent
workers. It supports deterministic single-runner queue decisions at safe
checkpoints. Multi-project readiness means Lucius can persist and choose among
multiple queued workflow items without blocking the whole system; it does not
mean unsupervised multi-worker write autonomy.

## Conclusion

The phase can close only if targeted queue tests, the full Lucius test suite,
the POST core benchmark, repository-integrity checks, and the Phase 1.20
deterministic evaluation pass with no unacceptable regression.
