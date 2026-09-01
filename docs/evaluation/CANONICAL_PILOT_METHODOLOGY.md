# Canonical Pilot Methodology

Phase 1.12 defines the evidence rules for reproducible real-repository pilots.
It does not grant write autonomy and it does not reinterpret Phase 1.11 as a
canonical run.

## Canonical vs Non-Canonical Pilot

A canonical pilot begins from an explicitly inspected repository state classified
as `CANONICAL_CLEAN`. Lucius records repository path, branch, HEAD commit,
remote, tracked modifications, staged modifications, untracked files, observed
timestamp, and manifest hash.

Non-canonical classifications are:

- `NON_CANONICAL_DIRTY`
- `NON_CANONICAL_HEAD_CHANGED`
- `NON_CANONICAL_UNTRACKED_STATE`
- `NON_CANONICAL_EXTERNAL_MUTATION`

Non-canonical pilots may be useful exploratory data, but they must not silently
promote autonomy readiness.

## Current-State vs Historical-State Planning

`CURRENT_STATE_PLANNING` means evidence is collected from the current repository
state.

`HISTORICAL_STATE_PLANNING` means evidence is collected from one selected
historical state only. The selected state may be a Git commit, Git tag, or
Lucius repository snapshot id resolved to its captured commit. Future/current
implementation files are not visible to planning unless a later evaluator uses
them after the plan is frozen.

## Plan Freeze

Before evaluation, an `EngineeringPlan` is frozen into a `PlanFreeze`. The
freeze stores the plan id, task, project, repository state, snapshot ids,
evidence ids, selected commit when available, planning mode, evaluation version,
complete plan payload, timestamp, and actor.

The freeze is effectively immutable: later plan edits do not mutate the frozen
payload used for evaluation.

## Deterministic Evaluation

`EngineeringPlanEvaluationService` supports two explicit modes:

- `PLANNING_ONLY`: evaluates a frozen plan before any implementation exists.
  Implementation-relative dimensions are recorded as `NOT_APPLICABLE` and do
  not reduce the aggregate score.
- `PLAN_VS_IMPLEMENTATION`: compares a frozen plan to an implementation
  artifact after the implementation exists. Missing implementation evidence is
  recorded as `NOT_CAPTURED`.

Post-implementation comparison also accepts explicit implementation change
evidence. This distinguishes:

- `CHANGE_CONFIRMED`
- `EXPECTED_CHANGE_MISSING`
- `NO_CHANGE_CONFIRMED`
- `UNEXPECTED_CHANGE_DETECTED`
- `NOT_CAPTURED`

Verified absence is positive engineering evidence. If a frozen plan expects no
schema migration or no new dependency, and deterministic diff evidence confirms
that absence, the evaluator records `NO_CHANGE_CONFIRMED` instead of
`NOT_CAPTURED`.

Unexpected changes are not automatic passes. If the plan expects no migration,
dependency, configuration, persistence, or infrastructure change and the
implementation introduces one, the relevant dimension records
`UNEXPECTED_CHANGE_DETECTED` and fails according to materiality.

Each dimension records applicability as `CAPTURED`, `NOT_CAPTURED`, or
`NOT_APPLICABLE`. Missing data is never reinterpreted as a pass. The service
persists per-dimension metrics and corrections, with aggregate result states:

- `PASS`
- `PASS_WITH_WARNINGS`
- `FAIL`
- `INSUFFICIENT_EVIDENCE`

Initial dimensions include architecture alignment, component coverage,
file/path prediction, schema/migration awareness, testing strategy,
documentation strategy, dependency awareness, authority/risk classification,
unnecessary work, hallucinated files/paths, unsupported claims, and missed
material implementation work.

Planning-only evaluations also capture repository understanding, planned path
validity, schema/migration reasoning, dependency reasoning, provenance quality,
novelty/leakage status, and explicit uncertainty handling.

Corrections are classified `MINOR`, `MODERATE`, `MAJOR`, or `CRITICAL`.

## Human Evaluation

Human rubric scores are persisted separately from deterministic evaluation.
Required 1-5 dimensions are repository understanding, architectural correctness,
completeness, usefulness, implementation realism, risk awareness, provenance
quality, and hallucination control.

If no human rubric exists, Lucius records `NOT_CAPTURED`. Missing human scores
are never fabricated.

## Benchmark Regression

Canonical pilots require formal pre-pilot and post-pilot
`LUCIUS_CORE_BENCH_V0_1` benchmark results. A benchmark result stores suite
version, benchmark version, Git HEAD, dirty state, environment metadata, case
counts, duration, deterministic metrics, and artifact ids.

Regression statuses are:

- `NO_REGRESSION`
- `REGRESSION`
- `INCONCLUSIVE`
- `NOT_RUN`

Ordinary pytest output is not benchmark evidence.

## Autonomy Gates

The release gate consumes repository state, deterministic plan evaluation,
before/after benchmarks, target repository integrity, and optional human rubric.

Hard blockers include non-canonical repository state, missing deterministic
evaluation, failing or insufficient deterministic evaluation, critical
hallucination/provenance corrections, missing benchmarks, benchmark regression
or inconclusive comparison, and target repository integrity violation.

A canonical `PLANNING_ONLY` evaluation may support the first limited-write pilot
when canonicality, benchmark, leakage, and repository-integrity gates pass.
Post-implementation comparison remains required before any later autonomy
promotion beyond the bounded limited-write stage.

Missing human rubric input remains a warning for the first limited-write pilot
gate and must be treated as a hard blocker before promotion beyond bounded write
autonomy.

The only passing recommendation in Phase 1.12 is
`READY_FOR_LIMITED_WRITE_PILOT`. There is no path to unrestricted autonomy, and
non-canonical evidence remains blocked from promotion.

After a successful limited-write pilot with passing post-implementation
comparison, the next conservative recommendation is
`READY_FOR_ANOTHER_LIMITED_WRITE_PILOT` unless stronger staged evidence and
human review justify a higher bounded pilot. A single successful write pilot does
not grant broad write authority.

After two distinct successful limited-write pilot records, a current
`PLAN_VS_IMPLEMENTATION` pass with formal pre/post benchmark `NO_REGRESSION`,
unchanged target repository integrity, and a captured human rubric may support
`READY_FOR_BOUNDED_ENGINEERING_PILOT`. This recommendation grants authority for
one bounded engineering objective at a time in an isolated environment. It does
not grant unrestricted write autonomy.

## Staged Authority Matrix

`READY_FOR_LIMITED_WRITE_PILOT` permits one reviewed, bounded implementation
against an isolated branch or worktree after a frozen plan passes canonical
planning gates.

`READY_FOR_ANOTHER_LIMITED_WRITE_PILOT` permits another bounded isolated write
pilot after a previous write pilot succeeds, but human review or staged evidence
is still insufficient for the broader bounded-engineering tier.

`READY_FOR_BOUNDED_ENGINEERING_PILOT` permits Lucius to decompose one bounded
engineering objective into internal tasks, plan those tasks, implement in an
isolated environment, run targeted/integration/full tests, perform bounded
repair cycles, update documentation, capture evidence, and prepare a human
review package.

No staged recommendation permits automatic merge to main, automatic push, force
push, destructive Git, production deployment, credential changes, external
production operations, broad cross-project modification, silent migration/schema
expansion, or unlimited repair loops.

Phase 1.16 adds post-bounded-pilot recommendations:

- `NOT_READY_FOR_BOUNDED_ENGINEERING`
- `READY_FOR_ANOTHER_BOUNDED_ENGINEERING_PILOT`
- `READY_FOR_BOUNDED_MULTI_TASK_ENGINEERING`

The first successful bounded engineering pilot should normally recommend another
bounded engineering pilot. The multi-task tier requires stronger repeated
bounded-pilot evidence and must not be inferred from a single success.

## Phase 1.11 Lessons Preserved

Phase 1.11 remains a `NON_CANONICAL_DIRTY_RUN`. Its useful repository
understanding and planning observations informed this methodology, but its
`NOT_CAPTURED` deterministic metrics, human rubric scores, and pre/post
benchmark records remain `NOT_CAPTURED`.
