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

One successful bounded engineering pilot normally supports another bounded
engineering pilot, not the multi-task tier. No outcome grants automatic merge,
push, deployment, destructive Git, credential changes, or unrestricted write
autonomy.
