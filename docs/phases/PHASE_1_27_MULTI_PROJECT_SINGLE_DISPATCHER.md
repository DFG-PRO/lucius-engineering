# Phase 1.27 Multi-Project Single Dispatcher

Phase 1.27 adds a canonical `MultiProjectDispatcher` for one Lucius runtime to
select eligible work across multiple registered projects while preserving the
native runtime and model-router authority boundaries.

The control boundary is:

```text
Project registry / persistent workflows / queue items
  -> MultiProjectDispatcher
  -> selected DispatchCandidate
  -> ExecutionRuntimeLoopService
  -> canonical release, frozen-plan, repository, and task checks
  -> ModelExecutionRouter
  -> replaceable execution provider
  -> runtime verification and lifecycle reconciliation
  -> dispatcher reevaluates eligible portfolio work
```

## Authority

The dispatcher may inspect registered project/workflow queues, evaluate
eligibility, compare candidates, select one next work item, record scheduling
evidence, and hand the selected candidate to the runtime.

The dispatcher must not authorize repository mutation, modify frozen plans,
invoke providers, mark tasks complete, verify work, alter provider routing
policy, mutate unrelated project state, or execute more than one job at a time.

The runtime remains the lifecycle and mutation authority. The
`ModelExecutionRouter` remains the provider/model routing authority. Execution
providers remain replaceable workers without global scheduling or lifecycle
authority.

## Project Context

Dispatchable work is reconstructed from canonical project, repository,
attachment, task, contract, persistent workflow, plan, and freeze rows. The
dispatcher records project identity, project status, project scheduling
priority from `ProjectORM.documentation_policy`, repository identity and
status, workflow state, task identity, queue item identity, and item count.

No secrets or provider credentials are persisted in dispatch audit payloads.
Project and repository identity must match the selected workflow and task.

## Dispatch Candidate

`DispatchCandidate` is provider-neutral and project-neutral. It captures:

- project, workflow, task, repository, queue item, and logical task IDs;
- task and project priority;
- queue state, dependency state, blocker state, retry state, and resume state;
- created order and ready timestamp;
- required generic capabilities;
- plan and freeze references;
- item version;
- scheduling metadata and deterministic tie-break fields.

The candidate intentionally does not include provider-specific model selection.

## Eligibility

The dispatcher excludes candidates when the project is missing or disabled, the
workflow is not execution-eligible, the task is missing, mismatched, or not
ready, dependencies are unresolved, blockers remain unresolved, retry/backoff or
resume prerequisites are not satisfied, repository registration or attachment is
missing, project/repository identity mismatches, frozen plan references are
missing, plan/freeze rows do not authorize the workflow project/task, queue
state is malformed, or the queue item is terminal or already running.

Malformed state is not silently repaired. When only closure-critical unsafe work
exists, the runtime records a fail-closed pre-mutation block and the provider is
not invoked.

## Scheduling Policy

The initial policy is deterministic and auditable:

1. task priority;
2. fairness deferral, except for `CRITICAL` work;
3. project priority;
4. resume preference;
5. queue created order;
6. project ID;
7. workflow ID;
8. item ID.

Two identical canonical states produce the same selected candidate.

## Fairness

The dispatcher prevents simple starvation by deferring a project after two
consecutive dispatch handoffs when another eligible project exists. Fairness
does not override safety, blockers, unresolved dependencies, or `CRITICAL`
task priority.

## Blocking And Resume

Blocked candidates are excluded with explicit reasons. If Project A is blocked
and Project B has eligible work, the dispatcher selects Project B without
manual project switching. If Project A later becomes `READY_TO_RESUME` with a
valid checkpoint reference, it reenters eligibility and is ordered by the same
deterministic policy, with resume preferred within equal priority.

## Isolation

The dispatcher fails closed on cross-project repository mismatch, task/project
mismatch, workflow/project mismatch, wrong-project plan or freeze references,
and queue mutation identity mismatch. Runtime pre-dispatch validation still
revalidates the selected candidate before provider routing, so dispatch
selection cannot authorize mutation by itself.

Provider results are reconciled only by the runtime against the selected
workflow/item context. Providers cannot complete unrelated project work.

## Audit Model

The dispatcher records:

- `MULTI_PROJECT_DISPATCH_CYCLE_STARTED`;
- `MULTI_PROJECT_DISPATCH_PROJECTS_INSPECTED`;
- `MULTI_PROJECT_DISPATCH_CANDIDATE_ELIGIBILITY_EVALUATED`;
- `MULTI_PROJECT_DISPATCH_ELIGIBLE_CANDIDATE_SET`;
- `MULTI_PROJECT_DISPATCH_SCHEDULING_POLICY_APPLIED`;
- `MULTI_PROJECT_DISPATCH_CANDIDATE_SELECTED`;
- `MULTI_PROJECT_DISPATCH_NO_ELIGIBLE_WORK`;
- `MULTI_PROJECT_DISPATCH_CANDIDATE_HANDED_TO_RUNTIME`.

These events reconstruct available work, exclusion reasons, eligible sets,
scheduling policy, selected work, project identity, fairness effects, resumed
versus new work, and handoff to runtime.

## Runtime Metrics

`ExecutionRuntimeLoopResult` now includes selected project IDs, project switch
count, and serialized scheduler decisions in addition to existing wall-clock,
active execution, wait, blocked, retry, escalation, provider, task, and plan
metrics.

## Deferred

This phase does not implement multi-worker concurrency, parallel task
execution, distributed scheduling, leases, heartbeats, remote worker ownership,
speculative execution, arbitrary project discovery, self-created projects,
provider marketplace, autonomous spending, push/merge/deploy authority,
live-money authority, or global optimization scheduling.

