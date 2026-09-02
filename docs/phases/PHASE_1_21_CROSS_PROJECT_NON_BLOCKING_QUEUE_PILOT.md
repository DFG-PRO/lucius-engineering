# Phase 1.21: Cross-Project Non-Blocking Queue Pilot

Phase 1.21 extends the Phase 1.20 queue from one persistent workflow to a
global, deterministic read model over multiple persistent workflows. The phase
proves multi-project scheduling with one logical execution capacity. It is not
proof of multi-worker concurrency.

Darwin is not modified during this phase.

## Baseline

- Lucius baseline HEAD: `ccb1b3f5fd1763ada4202f63deb3debfb35e52d1`
- Lucius baseline status: `## main...origin/main [ahead 16]`, clean
- Lucius baseline tests: `.venv/bin/python -m pytest` -> `193 passed in 98.63s`
- PRE benchmark: `LBENCH_000027`, `LUCIUS_CORE_BENCH_V0_1`, `8 passed`, aggregate `100.0`, hard gate `PASS`
- Darwin baseline HEAD: `3207aee087fa9018ee9ff2d526fe7a97443f6e21`
- Darwin baseline status: `## main...origin/main [ahead 6]`, clean

## Objective

Prove that Lucius can evaluate eligible work across distinct projects and
workflows without allowing a blocked project to stop unrelated work.

The required cross-project behavior is:

- Project A can block locally and persist a project-specific checkpoint.
- Project B remains schedulable while Project A is blocked.
- Project A can later transition to `READY_TO_RESUME`.
- Global scheduling re-evaluates Project A and Project B together.
- A higher-priority `READY` item in Project B beats a lower-priority
  `READY_TO_RESUME` item in Project A.
- Project identity, workflow identity, checkpoints, dependency graphs, repair
  counters, and resume conditions remain isolated.

## Design

The implementation reuses `PersistentWorkflow` records and
`NonBlockingQueueService`. Phase 1.21 adds global queue read models and service
methods that aggregate queue items across workflows without creating a new
table, dependency, broker, worker pool, or distributed scheduler.

Global queue inspection reports:

- active workflow ids;
- active project ids;
- running items;
- blocked items;
- ready items;
- ready-to-resume items;
- dependency-blocked items;
- completed items;
- failed items;
- next global selection and reason.

## Global Scheduling Rule

`select_global_next` is deterministic and fail-closed after the Phase 1.21B
repair:

1. gather workflows with non-empty queue backlogs;
2. classify workflow lifecycle eligibility;
3. exclude lifecycle-ineligible workflows from execution while keeping them
   inspectable;
4. require each schedulable item to carry an explicit queue `state`;
5. classify items without explicit queue state as
   `LEGACY_UNSCHEDULABLE_MISSING_QUEUE_STATE`;
6. validate duplicate work-item ids within each workflow;
7. validate duplicate running logical identities by project scope;
8. if any eligible workflow has a running item, return
   `GLOBAL_RUNNING_ITEM_ACTIVE_NO_PREEMPTION`;
9. exclude blocked, terminal, and dependency-incomplete queue-enabled items;
10. sort eligible items by priority rank, state class, creation order,
    project id, workflow id, and item id.

The globally schedulable workflow lifecycle states are `PLAN_READY`,
`IMPLEMENTING`, `VERIFYING`, `CHECKPOINT_REVIEW_REQUIRED`,
`RESUME_VALIDATION`, `BLOCKED`, and `APPROVED_TO_CONTINUE`.

`OBJECTIVE_ACCEPTED`, `PLANNING`, `PAUSED`,
`COMPLETED_PENDING_INTEGRATION`, and `CLOSED` are inspectable but excluded from
global execution. `COMPLETED_PENDING_INTEGRATION` means engineering execution is
finished and awaiting integration/review; residual backlog items in that state
must not become executable by accident.

Pre-Phase 1.20 backlog items may lack queue execution state. Missing state is
historical/ambiguous data, not readiness. Such items remain visible in
inspection output with `state_label: LEGACY_UNSCHEDULABLE`,
`queue_state_present: false`, `schedulable: false`, and an exclusion reason, but
they are never eligible for global scheduling.

The original Phase 1.21 rule before the repair was:

1. gather non-closed workflows with non-empty queue backlogs;
2. validate duplicate work-item ids within each workflow;
3. validate duplicate running logical identities by project scope;
4. if any workflow has a running item, return
   `GLOBAL_RUNNING_ITEM_ACTIVE_NO_PREEMPTION`;
5. exclude blocked, terminal, and dependency-incomplete items;
6. sort eligible items by priority rank, state class, creation order,
   project id, workflow id, and item id.

Priority rank is `CRITICAL`, `HIGH`, `NORMAL`, `LOW`. State class preserves the
Phase 1.20 rule: within equal priority, `READY_TO_RESUME` outranks `READY`.
Priority still wins across projects, so `HIGH READY` beats
`NORMAL READY_TO_RESUME`.

## Isolation

Dependencies are evaluated inside the owning workflow. A dependency named `B1`
in Project B does not satisfy or block work in Project A.

Duplicate running logical work is checked by project-scoped identity. Two
projects may have similarly named substeps, but the same project must not run
the same logical item twice.

A queue checkpoint belongs to one workflow and item. Resolving Project A with a
Project A checkpoint cannot mutate Project B because the resolver checks the
target workflow, item, current checkpoint id, state, and item version.

## Operator Workflow

Read-only global inspection commands:

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite queue-global-status LWORK_000001 LWORK_000002
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite queue-global-next LWORK_000001 LWORK_000002
```

When workflow ids are omitted, the service inspects all non-closed workflows
with queue backlog state. After Phase 1.21B, omitted workflow ids are safe by
default because lifecycle-ineligible workflows and legacy items without explicit
queue state remain inspectable but cannot enter the executable candidate set.
Supplying workflow ids is still preferred for pilot reconstruction because it
makes the evaluated scope explicit.

## Pilot Scenario

The canonical Phase 1.21 scenario uses two active project workflows plus an
optional blocked third workflow:

- Project A: `A1` starts, records completed substeps, then blocks as
  `WAITING_EXTERNAL`.
- Project B: `B1` is selected while `A1` is blocked and completes useful work.
- Project B: `B2` becomes eligible only after `B1` completes.
- Project A: `A1` resolves to `READY_TO_RESUME`.
- Global scheduler selects higher-priority Project B `B2` before Project A
  `A1`.
- Project B `B2` runs without being preempted by Project A.
- After Project B reaches a safe checkpoint, Project A `A1` resumes and
  completes without repeating checkpointed substeps.

## Evaluation

The deterministic Phase 1.21 evaluation covers:

- cross-project correctness;
- global scheduler correctness;
- project/workflow isolation;
- persistence;
- priority behavior;
- resume behavior;
- duplicate protection;
- stale-state protection;
- dependency handling;
- fresh-context recovery;
- observability;
- autonomy behavior;
- regression risk;
- documentation.

## Phase 1.21B Repair Audit Trail

The original Phase 1.21 scoped pilot passed, but independent closure review
artifact `LRUBRIC_000015` found a HIGH stale-state defect: unscoped
`queue-global-next` on the shared pilot database could surface historical
workflow work. The root cause was that the global workflow query admitted all
non-closed backlogs and global state interpretation treated missing queue state
as `READY`.

Phase 1.21B repaired the defect without rewriting historical rows:

- `LPLAN_000009` / `LFREEZE_000009` froze the bounded repair plan.
- `LBENCH_000029` captured the PRE benchmark from the Phase 1.21 HEAD.
- Global scheduling now uses explicit lifecycle eligibility and explicit queue
  state for execution.
- Unscoped `queue-global-next` on the shared pilot database returns
  `NO_GLOBAL_ELIGIBLE_WORK` for stale history instead of selecting old backlog.
- Exclusion reasons are machine-readable in `lifecycle_excluded`,
  `legacy_unschedulable`, and `excluded_items`.

No human rubric is invented. If no human scoring is captured, the rubric remains
`NOT_CAPTURED`.

## Limits

Phase 1.21 is multi-project scheduling with one logical execution capacity. It
does not authorize multi-worker concurrency, automatic merge, push, production
deployment, credential work, destructive Git, or unrestricted autonomy.

## Conclusion

Phase 1.21 can close only when targeted cross-project queue tests, the full
Lucius suite, the POST benchmark, repository-integrity checks, and the
deterministic pilot evaluation pass with no unacceptable regression.
