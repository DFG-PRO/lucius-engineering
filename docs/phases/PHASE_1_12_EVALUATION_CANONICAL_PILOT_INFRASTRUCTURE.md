# Phase 1.12 Evaluation And Canonical Pilot Infrastructure

## Status

IMPLEMENTED.

## Objective

Phase 1.12 builds the repository-state, historical-planning, plan-freeze,
deterministic evaluation, human rubric, benchmark, learning-candidate, and
release-gate infrastructure required for reproducible canonical
real-repository pilots.

This phase does not grant write autonomy, does not rerun the Darwin pilot, and
does not mutate Darwin.

## Baseline

Phase 1.11 was closed first in commit `cfc54fe`:
`Close Phase 1.11 Darwin pilot`.

The Phase 1.11 closure preserves `NON_CANONICAL_DIRTY_RUN`, the Darwin snapshot
and plan ids, all `NOT_CAPTURED` fields, and
`NOT_READY_FOR_WRITE_AUTONOMY`.

## Repository State Gate

`RepositoryStateService` classifies real repositories before pilot execution:

- `CANONICAL_CLEAN`
- `NON_CANONICAL_DIRTY`
- `NON_CANONICAL_HEAD_CHANGED`
- `NON_CANONICAL_UNTRACKED_STATE`
- `NON_CANONICAL_EXTERNAL_MUTATION`

It captures repository path, branch, HEAD commit, remote, tracked
modifications, staged modifications, untracked files, timestamp, and manifest
hash. Strict mode records/audits the observation and refuses non-canonical
execution.

## Historical Planning

`WorkspaceContext` now carries `planning_mode` and `repository_ref`.
`LocalGitRepositoryAdapter` can list files, read files, and build snapshots from
the selected Git commit or tag instead of the current worktree.

`HistoricalPlanningContextService` resolves current-state contexts, historical
Git refs, and Lucius snapshot ids. Historical planning evidence cannot silently
consume future files.

## Plan Freeze

`PlanFreezeService` persists an effectively immutable planning checkpoint:
plan id, task, project, repository state, snapshot ids, evidence ids, commit
sha when available, planning mode, evaluation version, full plan payload,
timestamp, and actor.

## Deterministic Evaluation

`EngineeringPlanEvaluationService` compares a frozen plan with an implementation
artifact and persists individual dimensions, aggregate score, result, and
corrections. Dimensions include architecture alignment, component coverage,
file/path prediction, schema/migration awareness, testing strategy,
documentation strategy, dependency awareness, authority/risk classification,
unnecessary work, hallucinated files/paths, unsupported claims, and missed
material implementation work.

## Human Rubric

`HumanRubricService` persists either captured human rubric scores or explicit
`NOT_CAPTURED`. Required 1-5 dimensions are repository understanding,
architectural correctness, completeness, usefulness, implementation realism,
risk awareness, provenance quality, and hallucination control.

## Benchmark Runner

`BenchmarkRunnerService` wraps `LUCIUS_CORE_BENCH_V0_1`, stores a formal
`BenchmarkResult`, and compares pre/post benchmark records for
`NO_REGRESSION`, `REGRESSION`, `INCONCLUSIVE`, or `NOT_RUN`.

## Learning Candidates

`PilotLearningService` persists pilot learning candidates with statuses
`IDENTIFIED`, `ACCEPTED`, `REJECTED`, and `IMPLEMENTED`.

## Release Gate

`ReleaseGateService` consumes canonical evidence and returns deterministic
blockers, warnings, benchmark regression status, and autonomy recommendation.
Hard blockers include non-canonical repository state, missing deterministic
evaluation, failing/insufficient deterministic evaluation, critical corrections,
missing benchmarks, benchmark regression/inconclusive result, and target
repository integrity violation.

The only passing recommendation in Phase 1.12 is
`READY_FOR_LIMITED_WRITE_PILOT`. There is no path to unrestricted autonomy.

## CLI

`python -m lucius.pilots.cli` supports:

- inspect repository state
- run the formal core benchmark
- freeze a plan
- evaluate a frozen plan
- record missing human rubric
- capture human rubric scores
- list pilot evidence
- compute release gate

## Tests

Focused Phase 1.12 test result:

```text
12 passed in 5.21s
```

Coverage demonstrates:

- clean repository accepted as canonical
- dirty and untracked repository states classified non-canonical
- strict canonical refusal
- repository HEAD and worktree mutation detection
- historical commit/tag/snapshot evidence
- future-file look-ahead prevention
- plan freeze immutability
- deterministic evaluation persistence
- hallucinated-path correction
- explicit `NOT_CAPTURED` human assessment
- human rubric persistence
- benchmark result persistence and association
- benchmark regression detection
- learning candidate persistence
- release gate rejection for non-canonical, missing benchmark, and regression
- valid evidence reaches at most `READY_FOR_LIMITED_WRITE_PILOT`
- no path to unrestricted autonomy
- Alembic `0007` to `head` and clean database to `head`

## Final Verification

Full test suite:

```text
133 passed in 94.99s
```

`LUCIUS_CORE_BENCH_V0_1` formal pilot result:

```text
LBENCH_000001
suite_name: LUCIUS_CORE_BENCH_V0_1
suite_version: 1
benchmark_version: LUCIUS_CORE_BENCH_V0_1
git_head: cfc54fe460aa6e0cae9b685de506268a5ba3e3dc
target_dirty: true
status: PASSED
total_cases: 8
passed: 8
failed: 0
skipped: 0
aggregate_score: 100.0
hard_gate_status: PASS
release_decision: RELEASE_ELIGIBLE
artifact_result_id: LERUN_000001
```

This result demonstrates formal benchmark persistence through the Phase 1.12
pilot runner. It is not a canonical post-commit benchmark because it was run
before the Phase 1.12 implementation commit and correctly captured
`target_dirty: true`.

Final Lucius git status is reported in the operator close-out after the Phase
1.12 commit.

## Known Limitations

- The deterministic plan-vs-implementation evaluator is intentionally coarse.
  It produces structured review evidence, not a complete substitute for senior
  engineering judgment.
- Historical planning resolves Git commits, tags, and Lucius snapshot commits
  for local Git repositories only.
- Human rubric policy is visible as a warning when absent, but Phase 1.12 does
  not require human scores as a hard blocker.
- Phase 1.12 introduces infrastructure only; the canonical Darwin rerun is a
  future dedicated phase.

## Recommended Next Phase

Run a clean, canonical Darwin real-repository pilot using this infrastructure,
with pre/post `LUCIUS_CORE_BENCH_V0_1`, frozen plans, deterministic evaluation,
explicit human rubric capture, learning candidates, and a persisted pilot
evaluation record.
