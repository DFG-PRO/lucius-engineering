# Phase 1.14A Zero-Change Evidence Correction

## Objective

Phase 1.14A corrected the post-implementation evaluator defect discovered after
the first limited Darwin write pilot. The Phase 1.14 implementation succeeded in
the isolated Darwin worktree, but `PLAN_VS_IMPLEMENTATION` returned
`INSUFFICIENT_EVIDENCE` because verified zero-change expectations were encoded
as missing evidence.

This phase did not regenerate `LPLAN_000001`, did not rewrite Phase 1.14
history, and did not modify Darwin main.

## Baseline

Lucius baseline:

- HEAD: `8b01da2092761caa9ec30239f8e0d1d5cdb7a493`
- status: `## main...origin/main [ahead 5]`
- baseline tests: `149 passed in 98.95s`

Preserved Darwin evidence:

- baseline state: `LRSTATE_000003`
- baseline HEAD: `cf1dcdc0e88039a3598ed6be271e8a3d953e301b`
- plan: `LPLAN_000001`
- freeze: `LFREEZE_000001`
- planning evaluation: `LEVALPLAN_000002`
- original post-implementation evaluation: `LEVALPLAN_000003`
- Phase 1.14 pilot record: `LPILOT_000003`
- benchmarks: `LBENCH_000004`, `LBENCH_000005`
- human rubric: `LRUBRIC_000002`

## Evidence Model

Lucius now records implementation change evidence states:

- `CHANGE_CONFIRMED`
- `EXPECTED_CHANGE_MISSING`
- `NO_CHANGE_CONFIRMED`
- `UNEXPECTED_CHANGE_DETECTED`
- `NOT_CAPTURED`

Verified absence is positive engineering evidence. If a plan expects no
migration or no new dependency, and deterministic diff evidence confirms that no
such files changed, the evaluator records `NO_CHANGE_CONFIRMED`.

Missing evidence remains different from verified absence. If no evidence is
provided for a required post-implementation dimension, Lucius still records
`NOT_CAPTURED` and may return `INSUFFICIENT_EVIDENCE`.

Unexpected changes are detected explicitly. If a plan expects no migration or no
dependency change but the implementation adds one, the relevant dimension fails.

## Corrected Phase 1.14 Evaluation

The existing Darwin implementation was evaluated again without changing the
frozen plan or implementation.

- new evaluation id: `LEVALPLAN_000004`
- mode: `PLAN_VS_IMPLEMENTATION`
- supersedes: `LEVALPLAN_000003`
- evaluator version: `1.14A.0`
- result: `PASS`
- aggregate score: `97.63`
- corrections: none

Zero-change evidence:

- schema migration: `NO_CHANGE_CONFIRMED`
- dependency change: `NO_CHANGE_CONFIRMED`
- configuration/infrastructure surfaces: verified unchanged in the implementation
  manifest

The implementation evidence manifest recorded changed files:

- `src/darwin/research_loop/schemas.py`
- `src/darwin/research_loop/service.py`
- `src/darwin/research_loop/__init__.py`
- `src/darwin/cli/app.py`
- `tests/test_research_loop.py`
- `tests/test_cli.py`
- `docs/runtime/research-loop.md`

Verified unchanged surfaces:

- `alembic/versions`
- dependency manifests including `pyproject.toml`
- unrelated configuration and infrastructure paths

## Implementation Freeze

The Phase 1.14 Darwin implementation was frozen by committing only on the
isolated pilot branch:

- branch: `lucius/phase-1.14-loop-list-pilot`
- worktree: `/private/tmp/darwin-phase-1.14-loop-list-pilot`
- commit: `8b0787e0eaf76e33ce19998d74f2e963eed607b3`

No merge and no push were performed.

## Gate

Recomputed Phase 1.14 pilot record:

- id: `LPILOT_000004`
- recommendation: `READY_FOR_ANOTHER_LIMITED_WRITE_PILOT`
- passed: `true`
- blockers: none
- warnings: human rubric `NOT_CAPTURED`; warning for another limited pilot and
  blocker before promotion beyond bounded write autonomy
- benchmark regression: `NO_REGRESSION`

Human rubric `LRUBRIC_000002` remains `NOT_CAPTURED`. Scores were not
fabricated.

## Verification

Focused tests:

```text
39 passed
```

Full Lucius test suite:

```text
160 passed
```

The added tests cover:

- no migration expected plus no migration implemented;
- no dependency expected plus no dependency added;
- unexpected migration/dependency changes;
- expected change missing;
- genuinely unavailable evidence remaining `NOT_CAPTURED`;
- zero-change contribution to scoring;
- `PLAN_VS_IMPLEMENTATION` passing with changed and confirmed-unchanged
  dimensions;
- corrected limited-write autonomy gate;
- no unrestricted autonomy path.

## Conclusion

Phase 1.14A fixes the evaluator semantics without altering the Darwin
implementation. The corrected evidence supports
`READY_FOR_ANOTHER_LIMITED_WRITE_PILOT`, not broader autonomy.
