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
