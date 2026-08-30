# LUCIUS_CORE_BENCH_V0_1

## Purpose

`LUCIUS_CORE_BENCH_V0_1` is Lucius's first permanent deterministic engineering
benchmark. It evaluates whether Lucius performs retrieval, memory use, and
planning with correct engineering judgment.

Tests answer whether the software executes as implemented. Evaluations answer
whether Lucius engineering outputs satisfy explicit quality, safety,
provenance, and policy expectations.

## Architecture

Golden Engineering Cases feed an EvaluationSuite. Each case is executed against
a deterministic Lucius target or fixture output, scored by modular evaluators,
checked by hard gates, persisted in an EvaluationRun, compared with a baseline,
and reported as JSON plus Markdown.

## Metrics

Phase 1.10 implements deterministic metrics for evidence precision/recall,
affected component/file precision/recall, acceptance coverage, unsupported
assumptions, hallucinated paths, risk, authority, test strategy, documentation,
memory scope, memory conflict, privacy boundary, and provenance completeness.

Set metrics use precision = true positives / actual positives and recall =
true positives / expected positives. Empty expected and actual sets score 100.
An empty actual set with non-empty expected set scores 0. A non-empty actual set
with empty expected set scores 0.

## Hard Gates

Hard gates are independent from weighted score:

- CLIENT_BOUNDARY_VIOLATION
- PRIVACY_POLICY_VIOLATION
- FABRICATED_EVIDENCE_REFERENCE
- CRITICAL_AUTHORITY_UNDERESTIMATION
- CRITICAL_RISK_UNDERESTIMATION
- REPOSITORY_REALITY_OVERRIDDEN_BY_MEMORY
- INVALID_PLAN_PROVENANCE

Any failed hard gate prevents the run from being release eligible.

## Scoring And Thresholds

Scores are 0-100. Default weights emphasize evidence, affected scope, acceptance
coverage, risk/authority, tests, documentation, memory correctness, privacy, and
provenance.

Release decisions:

- `RELEASE_ELIGIBLE`: score >= 80 and hard gates pass
- `RELEASE_WARNING`: 70 <= score < 80 and hard gates pass
- `RELEASE_BLOCKED`: score < 70 or any hard gate failure

Run status does not add a separate warning enum; warning is represented by the
release decision.

## Golden Cases

The permanent v0.1 cases are:

- SNAPSHOT_REUSE_RETRIEVAL_V1
- MEMORY_CONFLICT_REPOSITORY_WINS_V1
- AUTHORITY_ESCALATION_V1
- HALLUCINATED_PATH_V1
- ACCEPTANCE_COVERAGE_V1
- CLIENT_ISOLATION_V1
- TEST_DOCUMENTATION_PLANNING_V1
- PROVENANCE_INTEGRITY_V1

Each case defines why it exists, what failure it protects against, expected
ground truth, flexible areas, scored metrics, and hard failures.

## Versioning

Cases and suites are versioned. EvaluationRuns persist the suite version, case
versions, configuration hash, target commit, dirty state, planner version, and
case-level results.

## Baseline Policy

The first successful benchmark can become a canonical baseline only when the
working tree is clean, suite execution is complete, all required cases ran, and
no hard gates failed.

Dirty runs are labeled `NON_CANONICAL_DIRTY_RUN` and cannot become canonical
baselines. Replacing an existing baseline requires an explicit replace action.

## Regression Policy

Regression comparison checks aggregate score drops, metric drops, new case
failures, and new hard gate failures. A new hard gate failure is an immediate
regression failure signal.

## Reporting And CLI

Machine-readable reports are JSON dictionaries stored on EvaluationRun.
Markdown reports summarize score, gates, release decision, cases, regressions,
and weakest metrics.

Developer entry point:

```bash
python -m lucius.evaluation.cli run --suite LUCIUS_CORE_BENCH_V0_1
```

## Persistence And Audit

Migration `0007_evaluation_harness` persists suites, cases, runs, and case
results. Audit events cover run start/completion, case start/completion/failure,
hard gate failures, regression detection, baseline creation, and release blocks.

## Performance

Phase 1.10 records case duration and run duration. The deterministic focused
evaluation test file completes in about two seconds on the current development
machine.

## Limitations

There is no LLM judge, external stochastic model benchmark, executor
evaluation, embeddings, fine-tuning, WorkPackage orchestration, deployment, or
autonomy runtime in Phase 1.10.
