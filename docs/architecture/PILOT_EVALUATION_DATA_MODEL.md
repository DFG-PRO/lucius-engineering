# Pilot Evaluation Data Model

Phase 1.12 adds persisted pilot-evaluation infrastructure around existing
Projects, RepositoryRegistrations, RepositorySnapshots, Tasks, TaskContracts,
EngineeringPlans, and EvaluationRuns.

## New Tables

- `repository_state_observations`: canonical/dirty repository state capture.
- `plan_freezes`: immutable checkpoint for an EngineeringPlan before comparison.
- `engineering_plan_evaluations`: deterministic plan-vs-implementation metrics.
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

`PilotEvaluationRecord` connects the target repository, repository snapshot,
repository state, task, plan, frozen plan, deterministic evaluation, human
rubric, before/after benchmarks, learning candidates, corrections, repository
integrity result, canonical status, and autonomy recommendation.

The gate result is stored as machine-readable JSON so later reports can
reconstruct the exact blockers and warnings.
