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
