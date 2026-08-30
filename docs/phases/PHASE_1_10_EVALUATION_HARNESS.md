# Phase 1.10: Evaluation Harness and Lucius Benchmark v0.1

## Summary

Phase 1.10 adds Lucius's first deterministic evaluation harness and permanent
golden engineering benchmark suite.

The harness separates system correctness from engineering quality. Tests prove
software behavior; evaluations judge Lucius outputs against explicit expected
engineering behavior.

## Architecture

The implementation lives in `src/lucius/evaluation/`:

- `schemas.py`: cases, suites, runs, case results, metrics, gates, reports
- `cases.py`: permanent golden case definitions and deterministic fixture
  outputs
- `metrics.py`: precision, recall, and coverage semantics
- `evaluators.py`: modular deterministic evaluators and safety gates
- `scoring.py`: weighted scores, thresholds, and release decisions
- `regression.py`: baseline/current comparison
- `reporting.py`: JSON and Markdown reports
- `service.py`: persistence, auditing, run orchestration, baseline policy
- `runner.py` and `cli.py`: simple developer entry points

## Case, Suite, And Version Model

`EvaluationCase` is versioned and contains fixture reference, target type,
expected evidence, components, files, acceptance coverage, risk, authority,
tests, documentation, memory, known traps, forbidden claims, hard requirements,
weights, hard gates, tags, difficulty, rationale, protected failure, and ground
truth.

`EvaluationSuite` is also versioned and records case references, scoring policy,
hard gate policy, and canonical baseline run.

## Metrics

Implemented metrics:

- evidence precision/recall
- affected component precision/recall
- affected file precision/recall
- acceptance coverage
- unsupported assumption rate
- hallucinated path rate
- risk correctness
- authority correctness
- test strategy coverage
- documentation coverage
- memory scope correctness
- memory conflict correctness
- privacy boundary correctness
- provenance completeness

## Hard Gates

Hard gates prevent release eligibility regardless of aggregate score:

- client boundary violation
- privacy policy violation
- fabricated evidence reference
- critical authority underestimation
- critical risk underestimation
- repository reality overridden by memory
- invalid plan provenance

## Scoring And Release Gate

Scores are weighted from 0-100. The v0.1 policy returns:

- `RELEASE_ELIGIBLE` for score >= 80 with passing gates
- `RELEASE_WARNING` for 70-79.99 with passing gates
- `RELEASE_BLOCKED` for score < 70 or any hard gate failure

## Baseline And Regression

Canonical baselines require clean working tree, passing status, completed cases,
and no hard gate failures. Dirty runs are marked `NON_CANONICAL_DIRTY_RUN` and
cannot become baselines.

Regression detection compares aggregate score, metric scores, case status, and
hard gate status against the selected baseline.

## Golden Cases

The initial permanent suite is `LUCIUS_CORE_BENCH_V0_1` and includes:

1. repository retrieval for snapshot reuse
2. memory conflict where repository reality wins
3. authority escalation
4. hallucinated verified path
5. acceptance criteria coverage
6. client isolation
7. test/documentation planning
8. provenance integrity

## Persistence And Audit

Migration `0007_evaluation_harness` adds `evaluation_suites`,
`evaluation_cases`, `evaluation_runs`, and `evaluation_case_results`.

Audit events include evaluation run start/completion, case start/completion,
case failure, hard gate failure, regression detection, baseline creation, and
release block.

## Reports And CLI

EvaluationRuns store machine-readable reports and Markdown reports. Developers
can run:

```bash
python -m lucius.evaluation.cli run --suite LUCIUS_CORE_BENCH_V0_1
```

## Test Results

Focused Phase 1.10 tests pass with `12 passed`. Full repository tests pass with
`121 passed`.

## Deviations And Limitations

Golden cases are version-controlled fixture definitions and are also persisted
when the suite loads. Phase 1.10 does not add repository writes, an Engineering
Executor, LLM judges, external model benchmarks, embeddings, fine-tuning,
automatic commits, PR creation, deployment, or phase autonomy.
