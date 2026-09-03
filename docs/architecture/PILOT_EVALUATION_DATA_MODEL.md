# Pilot Evaluation Data Model

Phase 1.12 adds persisted pilot-evaluation infrastructure around existing
Projects, RepositoryRegistrations, RepositorySnapshots, Tasks, TaskContracts,
EngineeringPlans, and EvaluationRuns.

## New Tables

- `repository_state_observations`: canonical/dirty repository state capture.
- `plan_freezes`: immutable checkpoint for an EngineeringPlan before comparison.
- `engineering_plan_evaluations`: deterministic planning-only or
  plan-vs-implementation metrics.
- `human_rubrics`: explicit human scores or `NOT_CAPTURED`.
- `benchmark_results`: formal persisted `LUCIUS_CORE_BENCH_V0_1` run summaries.
- `pilot_learning_candidates`: candidate lessons from pilot analysis.
- `pilot_evaluation_records`: stable artifact connecting all pilot evidence.

## Evidence Flow

```text
RepositoryStateObservation
  -> RepositorySnapshot / historical commit or tag
  -> EngineeringPlan
  -> PlanFreeze
  -> EngineeringPlanEvaluation
  -> HumanRubric
  -> BenchmarkResult(before) + BenchmarkResult(after)
  -> PilotLearningCandidate(s)
  -> PilotEvaluationRecord
  -> ReleaseGateResult
```

## Stable IDs

The public ID generator now includes Phase 1.12 prefixes:

- `LRSTATE_*`
- `LFREEZE_*`
- `LEVALPLAN_*`
- `LRUBRIC_*`
- `LBENCH_*`
- `LPLEARN_*`
- `LPILOT_*`
- `LQCHK_*`

## Repository State

Repository state observations store path, branch, HEAD commit, origin remote,
tracked modifications, staged modifications, untracked files, manifest hash,
timestamp, and classification. Strict canonical mode persists and audits the
state before refusing execution.

## Historical Planning Context

`WorkspaceContext` now includes `planning_mode` and `repository_ref`.
`LocalGitRepositoryAdapter` resolves historical evidence through Git object
commands, so file listing, file reads, snapshots, and manifest hashes are based
on the selected commit/tag instead of the current worktree.

`HistoricalPlanningContextService` creates current-state contexts, historical
Git-ref contexts, and historical snapshot-id contexts.

## Evaluation Records

`engineering_plan_evaluations` records `evaluation_mode` as either
`PLANNING_ONLY` or `PLAN_VS_IMPLEMENTATION`. It may also record
`supersedes_evaluation_id` when a later evaluation corrects the mode or
methodology applied to the same frozen plan. Superseding creates a new artifact;
it does not mutate the original evaluation.

Each evaluation dimension stores an applicability state:

- `CAPTURED`: the metric had enough evidence to score.
- `NOT_CAPTURED`: required evidence was absent and must not be treated as a
  pass.
- `NOT_APPLICABLE`: the metric does not apply to the selected evaluation mode
  and is excluded from the aggregate score.

`PLAN_VS_IMPLEMENTATION` artifacts may include an
`implementation_evidence_manifest` with changed files and verified absences.
Verified absence is represented with implementation change states:

- `CHANGE_CONFIRMED`
- `EXPECTED_CHANGE_MISSING`
- `NO_CHANGE_CONFIRMED`
- `UNEXPECTED_CHANGE_DETECTED`
- `NOT_CAPTURED`

These states let Lucius score zero-change requirements such as no migration or
no new dependency without confusing verified absence with missing evidence.

`PilotEvaluationRecord` connects the target repository, repository snapshot,
repository state, task, plan, frozen plan, deterministic evaluation, human
rubric, before/after benchmarks, learning candidates, corrections, repository
integrity result, canonical status, and autonomy recommendation.

The gate result is stored as machine-readable JSON so later reports can
reconstruct the exact blockers and warnings.

`PersistentWorkflow` stores the durable state for one supervised workflow:
objective, expected main HEAD, isolated branch/worktree, authority tier,
backlog, dependency graph, active/completed/pending task ids, decisions,
deviations, repair counters, tests, checkpoint history, and pending human
approvals.

`PersistentWorkflowCheckpoint` stores an immutable pause point. It captures the
implementation HEAD, expected main HEAD, worktree, branch, task state,
dependency graph, decisions, deviations, repair counters, latest tests, known
warnings, authority tier, pending approvals, resume conditions, and recommended
next action.

`ResumeValidation` stores the result of a fresh resume audit. Its checks must
come from durable workflow/checkpoint rows plus repository and filesystem
verification, not conversational reconstruction.

## Non-Blocking Queue State

Phase 1.20 extends `PersistentWorkflow.task_backlog` to hold queue work items as
structured JSON. This avoids a migration while the queue model is still narrow
and pilot-scoped.

Each work item records an item id, logical task id, project/workflow identity,
state, priority, creation order, dependencies, completed substeps, and version.
The canonical states are `READY`, `RUNNING`, `WAITING_HUMAN`,
`WAITING_EXTERNAL`, `BLOCKED_DEPENDENCY`, `RETRY_LATER`, `READY_TO_RESUME`,
`COMPLETED`, and `FAILED`.

Queue block checkpoints use `LQCHK_*` ids. The checkpoint payload is persisted
inside the blocked work item and referenced from workflow checkpoint history.
It records the blocking reason, category, resume condition, completed work,
relevant artifacts, approvals, repair counters, next safe action, and stale
validation requirements.

The queue read model is computed from persisted workflow state and reports
running, blocked, ready, ready-to-resume, dependency-blocked, completed, failed,
excluded, and next-selection groups. Read-only queue inspection must not mutate
the workflow. After Phase 1.21D, scoped inspection exposes legacy or malformed
persisted rows in `excluded` so they remain visible without becoming execution
candidates.

Phase 1.21 adds global queue read models over multiple persistent workflows.
`GlobalQueueItem` includes the owning project, workflow, repository, workflow
task, work-item id, logical task id, queue state, derived state label, priority,
creation order, dependencies, current block checkpoint, original item payload,
and derived execution-eligibility fields.

`GlobalQueueSelection` records the selected project/workflow/item, selected item
version, mutation evidence when dispatch occurs, and eligible, blocked, running,
and excluded item groups. `GlobalQueueStatus` records observed workflows, active
schedulable workflows, active projects, state-grouped items,
legacy-unschedulable items, lifecycle-excluded workflows, and the next global
selection.

Phase 1.21B defines the fail-closed read model. Historical backlog entries
without explicit queue `state` are not normalized in storage and are not inferred
as `READY`; they are surfaced as legacy unschedulable. Workflows outside the
global execution lifecycle are surfaced under lifecycle exclusions with a reason
such as `WORKFLOW_LIFECYCLE_INELIGIBLE:COMPLETED_PENDING_INTEGRATION`.

Global queue state remains derived from existing `PersistentWorkflow` rows. No
new queue table is introduced in Phase 1.21.

Phase 1.21C extends the read model with raw state, raw priority, item version,
priority validity, and mutation identity evidence. Malformed state or priority
is represented as unschedulable inspection data rather than normalized storage.
Exact global dispatch validates the selected identity and version immediately
before mutation and records whether the mutated identity matched selection.

Phase 1.21D defines the shared execution safety policy. Scoped and global
execution use the same workflow lifecycle eligibility set and strict item
state/priority interpretation. Explicit workflow scope narrows identity but does
not authorize execution. Release-gate implementation artifacts for
cross-project readiness must link critical claims to evidence provenance:
evidence artifact id, test/probe or persisted-result id, expected invariant, and
passing observed result. MAJOR or CRITICAL `PASS_WITH_WARNINGS` corrections
must be disposed with similarly linked evidence before readiness can pass.
