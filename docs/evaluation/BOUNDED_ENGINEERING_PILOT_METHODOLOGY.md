# Bounded Engineering Pilot Methodology

Phase 1.16 introduces bounded engineering pilots. A bounded engineering pilot is
larger than a limited write pilot, but still limited to one explicit engineering
objective, one target repository, one isolated implementation branch/worktree,
and a fixed repair budget.

## Decomposition Model

A bounded objective must decompose into a small task graph with real dependency
meaning. Each subtask records:

- objective
- dependencies
- expected components
- expected files
- test strategy
- risk
- authority level
- completion criteria

Subtasks must not be created only to satisfy a count target. A valid graph may
be linear, branched, or convergent, but task transitions must respect declared
dependencies.

## Dependency Graph Semantics

An edge means the downstream task relies on the upstream task's completed
contract. Lucius may move to a dependent task only after the upstream task's
completion criteria and targeted verification pass.

For Phase 1.16, the graph was:

```text
LTASK_000004
  -> LTASK_000005
      -> LTASK_000006
LTASK_000004, LTASK_000005, LTASK_000006
  -> LTASK_000007
```

## Repair Budget

The default bounded repair budget is:

- 3 repair cycles per task
- 6 total repair cycles for the objective

Each repair cycle must record failing behavior, diagnosis, fix, changed files,
and resulting tests. Lucius must stop when the budget is exhausted or when a
material assumption invalidates the frozen package.

## Scope Governance

Allowed changes are restricted to planned files and minor necessary support code
inside the objective boundary. Forbidden changes without escalation include
unrelated refactoring, opportunistic cleanup, unrelated migrations, dependency
upgrades, infrastructure changes, production operations, cross-project writes,
and destructive Git.

Zero-change expectations for migrations, dependencies, configuration, and
infrastructure should be evaluated as positive evidence when confirmed.

## Orchestration Evaluation

Bounded pilots require a separate orchestration evaluation in addition to normal
plan-vs-implementation scoring. Minimum dimensions are:

- objective decomposition quality
- dependency sequencing
- task transition discipline
- scope containment
- repair allocation
- stop-condition compliance
- cross-task consistency
- completion verification

## Autonomy Outcomes

Post-bounded-pilot outcomes are:

- `NOT_READY_FOR_BOUNDED_ENGINEERING`
- `READY_FOR_ANOTHER_BOUNDED_ENGINEERING_PILOT`
- `READY_FOR_BOUNDED_MULTI_TASK_ENGINEERING`

One successful bounded engineering pilot with missing human review normally
supports another bounded engineering pilot, not the multi-task tier.

`READY_FOR_BOUNDED_MULTI_TASK_ENGINEERING` requires all hard gates to pass plus
captured human review with 5/5 scores across every canonical persisted rubric
dimension. The current canonical rubric dimensions are repository
understanding, architectural correctness, completeness, usefulness,
implementation realism, risk awareness, provenance quality, and hallucination
control. Extra Daniel review dimensions may be documented as review context, but
must not be invented as persisted scores.

The multi-task tier permits Lucius to receive one bounded engineering objective,
decompose it into multiple related tasks, construct a dependency graph,
internally sequence tasks, freeze plans, implement in an isolated branch or
worktree, use bounded repair loops, run targeted/integration/full tests, update
documentation, capture evidence, and prepare a review package.

No outcome grants automatic merge, push, deployment, destructive Git, credential
changes, force push, unrestricted architecture rewrites, silent migration/schema
expansion, unlimited task spawning, unlimited repair cycles, broad cross-project
autonomy, or unrestricted write autonomy.

## Bounded Multi-Task Pilot Model

A bounded multi-task pilot is still one coherent engineering objective. The
child tasks must be natural pieces of the objective rather than arbitrary slices
created to inflate task count.

The package must record:

- objective-level novelty and subtask-level novelty;
- existing primitives to reuse;
- child tasks with inputs, outputs, acceptance criteria, risk, authority, and
  zero-change expectations;
- a dependency graph with cycle and missing-dependency checks;
- execution order derived from the graph;
- local decisions and deviation classifications;
- repair budget usage;
- task-level, integrated, and full-repository verification;
- plan-vs-implementation and orchestration evaluation;
- autonomy audit and human-review package.

Post-multi-task outcomes are:

- `NOT_READY_FOR_BOUNDED_MULTI_TASK_ENGINEERING`
- `READY_FOR_ANOTHER_BOUNDED_MULTI_TASK_ENGINEERING_PILOT`
- `READY_FOR_SUPERVISED_ENGINEERING_WORKFLOW`

`READY_FOR_SUPERVISED_ENGINEERING_WORKFLOW` requires captured human review and
does not imply merge, push, deploy, production, credential, destructive Git, or
unrestricted architecture authority.

## Supervised Engineering Workflow Semantics

After a successful bounded multi-task pilot and captured human review, a
supervised engineering workflow may carry one human-authorized objective across
multiple internal checkpoints instead of treating every small task as a separate
pilot. Lucius may understand the repository, decompose work, create and freeze
plans, implement in an isolated branch or worktree, make minor local decisions,
run bounded repair loops, test, document, capture evidence, and prepare human
review packages.

Mandatory human checkpoints remain required before merge, push when policy
requires it, production deployment, destructive operations, credential/security
changes, unplanned migration or schema expansion, material new dependencies,
architecture expansion outside the frozen objective, cross-project changes, or
material scope changes.

The conceptual state model is:

- `OBJECTIVE_ACCEPTED`
- `PLANNING`
- `PLAN_READY`
- `IMPLEMENTING`
- `VERIFYING`
- `CHECKPOINT_REVIEW_REQUIRED`
- `BLOCKED`
- `APPROVED_TO_CONTINUE`
- `COMPLETED_PENDING_INTEGRATION`
- `CLOSED`

Phase 1.17A defined these semantics without adding new workflow-state
persistence.

## Persistent Supervised Engineering Gate

Phase 1.18 adds post-supervised-workflow recommendations:

- `NOT_READY_FOR_SUPERVISED_ENGINEERING_WORKFLOW`
- `READY_FOR_ANOTHER_SUPERVISED_ENGINEERING_WORKFLOW`
- `READY_FOR_PERSISTENT_SUPERVISED_ENGINEERING`

`READY_FOR_ANOTHER_SUPERVISED_ENGINEERING_WORKFLOW` is the normal passing result
when a supervised workflow reaches review with human rubric state
`NOT_CAPTURED`.

`READY_FOR_PERSISTENT_SUPERVISED_ENGINEERING` requires passing deterministic
plan-vs-implementation evaluation, no unacceptable benchmark regression,
unchanged target repository integrity, and captured 5/5 human review across all
canonical rubric dimensions. It does not authorize automatic merge, push,
deployment, credential changes, destructive Git, or unrestricted engineering
autonomy.

Persistent supervised readiness requires resumable workflow state. A resumed
workflow must validate the expected repository, branch or worktree, HEAD,
baseline compatibility, task graph, checkpoint state, and pending human
authorization before continuing. Material mismatch moves the workflow to
`CHECKPOINT_REVIEW_REQUIRED` or `BLOCKED`.

## Persistent Workflow Pause/Resume Pilot

Phase 1.19 adds durable persistent workflow artifacts:

- `LWORK_*`: current workflow objective, authority tier, task backlog,
  dependency graph, state, decisions, repairs, tests, checkpoint history, and
  pending approvals.
- `LWCHK_*`: immutable pause checkpoint with implementation HEAD, expected main
  HEAD, worktree, branch, completed/pending task ids, tests, repairs, warnings,
  and resume conditions.
- `LRESUME_*`: resume validation result with explicit Git/filesystem/state
  checks and the next eligible task id.

A persistent workflow may resume only after repository state, implementation
HEAD, checkpoint state, task graph, repair budget, and authorization checks pass
from durable state. Conversation memory is not sufficient evidence.

## Non-Blocking Project Queue Pilot

Phase 1.20 adds a queue semantics layer on top of persistent workflows. The
principle is to block the work item, not the whole system.

Blocked queue states are `WAITING_HUMAN`, `WAITING_EXTERNAL`,
`BLOCKED_DEPENDENCY`, and `RETRY_LATER`. Terminal states are `COMPLETED` and
`FAILED`. Eligible states are `READY` and `READY_TO_RESUME`, subject to complete
dependencies.

The deterministic scheduler first refuses unsafe mid-task preemption when any
item is already `RUNNING`. Otherwise it selects eligible work by priority rank,
state class, creation order, and id. Priority outranks resume status, so
`READY_TO_RESUME` does not automatically jump ahead of higher-priority ready
work.

A blocked item must persist a queue checkpoint containing the blocking reason,
blocker category, completed substeps, relevant artifacts, resume condition,
repair counters, approval requirements, next safe action, and stale validation
requirements. A resume transition must validate both checkpoint id and item
version before setting the item to `READY_TO_RESUME`.

The Phase 1.20 evaluation must separately check scheduler correctness,
persistence quality, non-blocking behavior, resume correctness, duplicate-work
prevention, dependency correctness, workflow isolation, fresh-context recovery,
operator observability, autonomy behavior, documentation quality, and regression
risk.

Post-queue outcomes are:

- `NOT_READY_FOR_NON_BLOCKING_PROJECT_QUEUE`
- `READY_FOR_ANOTHER_NON_BLOCKING_PROJECT_QUEUE_PILOT`
- `READY_FOR_MULTI_PROJECT_NON_BLOCKING_QUEUE`

`READY_FOR_MULTI_PROJECT_NON_BLOCKING_QUEUE` requires all hard gates to pass,
no unacceptable benchmark regression, unchanged target integrity, passing queue
evaluation, passing fresh-context reconstruction, passing duplicate protection,
passing project isolation, passing safe-interruption checks, and explicit
evidence that multi-project non-blocking selection was tested. It does not
authorize multi-worker concurrency, automatic merge, push, deployment,
credential work, destructive Git, or unrestricted autonomy.

## Cross-Project Non-Blocking Queue Pilot

Phase 1.21 extends the queue pilot to a global scheduler over multiple
persistent workflows. It still assumes one logical execution capacity and safe
checkpoint boundaries rather than worker concurrency.

The global scheduler gathers workflows with queue backlogs, classifies lifecycle
eligibility, validates per-workflow duplicate item ids, validates duplicate
running logical identities by project scope, refuses unsafe global preemption
when any eligible workflow has a running item, excludes
blocked/terminal/dependency-incomplete work, and selects by priority, state
class, creation order, project id, workflow id, then item id.

After Phase 1.21B, global read/select eligibility fails closed. Only `PLAN_READY`,
`IMPLEMENTING`, `VERIFYING`, `CHECKPOINT_REVIEW_REQUIRED`,
`RESUME_VALIDATION`, `BLOCKED`, and `APPROVED_TO_CONTINUE` workflows can
participate in active global scheduling. `OBJECTIVE_ACCEPTED`, `PLANNING`,
`PAUSED`, `COMPLETED_PENDING_INTEGRATION`, and `CLOSED` remain inspectable but
are lifecycle-excluded. Queue items without an explicit queue `state` are
classified as legacy unschedulable and are never treated as `READY` by the global
scheduler.

After Phase 1.21C, global execution also requires exact item validation. A
global selection is not execution authority until the selected project,
workflow, item id, item version, lifecycle state, dependencies, priority, and
ordering are revalidated immediately before mutation. Global dispatch must not
call scoped `start_next` or perform any second scheduling decision. If the
selected item becomes stale, blocked, completed, dependency-incomplete,
lifecycle-ineligible, malformed, or outranked before dispatch, execution rejects
and the caller must rerun global selection. Malformed persisted state or
priority remains inspectable with raw values and exclusion reasons, but is not
schedulable.

The cross-project priority rule is explicit: higher priority `READY` work in
one project beats lower-priority `READY_TO_RESUME` work in another project.
Within equal priority, `READY_TO_RESUME` may outrank `READY`.

Post-cross-project outcomes are:

- `NOT_READY_FOR_NON_BLOCKING_PROJECT_QUEUE`
- `READY_FOR_ANOTHER_CROSS_PROJECT_QUEUE_PILOT`
- `READY_FOR_MULTI_PROJECT_NON_BLOCKING_QUEUE`

Promotion to `READY_FOR_MULTI_PROJECT_NON_BLOCKING_QUEUE` requires passing
cross-project evaluation, global scheduler evaluation, fresh-context
reconstruction, duplicate protection, project isolation, safe interruption,
stale-history safety, lifecycle-scope safety, safe unscoped global
reconstruction, exact global dispatch, global selection equals mutation,
malformed persistence safety, stale-selection safety, formal benchmarks, and
target-integrity checks. The result does not authorize multi-worker
concurrency.

After Phase 1.21D, critical cross-project readiness claims require linked
evidence provenance. The gate requires an evidence artifact id, test/probe or
persisted-result id, expected invariant, and passing observed result for scoped
lifecycle execution safety, scoped malformed persistence safety, global/scoped
policy parity, exact global dispatch, selection-equals-mutation,
stale-selection safety, no-preemption, dependency scope isolation, stale-history
safety, lifecycle-scope safety, malformed persistence safety, and unscoped
global reconstruction. A free-standing `result: PASS` is not sufficient.

Formal deterministic warnings are release inputs. `PASS` remains eligible when
hard gates pass. `PASS_WITH_WARNINGS` is eligible only after corrections are
classified. MAJOR or CRITICAL corrections require explicit disposition linked to
evidence before readiness can pass. `FAIL` and `INSUFFICIENT_EVIDENCE` block
readiness.

## Phase 1.23A Planning Semantics Repair

Phase 1.23A preserves the Phase 1.23 multi-project pilot artifacts and failed
evaluations. The repair changes the process that allowed unsupported verified
planning claims, ambiguous dependency semantics, and missing overall
orchestration evaluation to reach closure.

Planning claims marked `VERIFIED`, `CONFIRMED`, or equivalent must be backed by
current semantic evidence before plan freeze. Expectations about future local
test execution are plan requirements or assumptions with
`TO_BE_VERIFIED`/`ASSUMPTION` status, not verified facts. Historical evaluations
that encoded unsupported verified claims remain immutable and are superseded
rather than rewritten.

Top-level EngineeringPlan dependencies represent executable task/package
dependencies. Validation prerequisites such as "local test environment only"
must be recorded as test prerequisites, risks, assumptions, or implementation
evidence. They must not be interpreted as package/runtime dependency changes
unless the implementation artifact explicitly records dependency-change
semantics.

Fixture-dependent suites must distinguish repository health from change
regression. A suite with missing canonical fixtures can be
`DEGRADED_FIXTURES_MISSING` while the change comparison is
`NO_NEW_REGRESSION` if the same failures exist at baseline and target. New
failures relative to baseline remain regressions.

Future extended operational pilots that mutate one or more target repositories
must freeze an overall orchestration plan before the first mutation and evaluate
that overall plan after implementation. Post-hoc closure evaluation may document
what happened, but it cannot substitute for a missing pre-frozen orchestration
plan.
