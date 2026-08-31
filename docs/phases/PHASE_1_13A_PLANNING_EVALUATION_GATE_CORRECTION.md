# Phase 1.13A Planning Evaluation Gate Correction

## Objective

Phase 1.13A corrected the evaluation deadlock discovered after the canonical
Darwin read/plan-only pilot. The original Phase 1.13 pilot froze a plan before
any Darwin implementation existed, but the deterministic evaluator treated
missing implementation evidence as insufficient plan evidence.

This phase does not rerun or rewrite the Phase 1.13 pilot. Darwin remains
read-only.

## Preserved Phase 1.13 Artifacts

- Darwin HEAD: `cf1dcdc0e88039a3598ed6be271e8a3d953e301b`
- repository state: `LRSTATE_000001`
- canonical snapshot: `LSNAP_000001`
- pre-pilot benchmark: `LBENCH_000001`
- plan: `LPLAN_000001`
- plan freeze: `LFREEZE_000001`
- original deterministic evaluation: `LEVALPLAN_000001`
- human rubric artifact: `LRUBRIC_000001`
- learning candidates: `LPLEARN_000001`, `LPLEARN_000002`
- final repository state: `LRSTATE_000002`
- post-pilot benchmark: `LBENCH_000002`

## Model Correction

`EngineeringPlanEvaluationService` now records explicit evaluation mode:

- `PLANNING_ONLY`
- `PLAN_VS_IMPLEMENTATION`

Each evaluation dimension records applicability:

- `CAPTURED`
- `NOT_CAPTURED`
- `NOT_APPLICABLE`

Planning-only evaluation excludes implementation-relative dimensions from the
aggregate score by marking them `NOT_APPLICABLE`. Missing planning evidence is
still recorded as `NOT_CAPTURED` and produces `INSUFFICIENT_EVIDENCE`.

## Gate Correction

The release gate now allows a canonical `PLANNING_ONLY` evaluation to support
the first limited-write pilot when the repository-state, benchmark,
repository-integrity, and leakage/novelty checks pass.

Human rubric `NOT_CAPTURED` remains a warning for the first limited-write pilot
gate. It must be treated as a hard blocker before promotion beyond bounded write
autonomy.

No gate path grants unrestricted autonomy.

## Re-Evaluation of Frozen Phase 1.13 Evidence

The frozen Phase 1.13 plan `LPLAN_000001` / `LFREEZE_000001` was re-evaluated
against snapshot `LSNAP_000001` in `PLANNING_ONLY` mode. The original
`LEVALPLAN_000001` artifact was preserved.

Corrected evaluation:

- evaluation id: `LEVALPLAN_000002`
- mode: `PLANNING_ONLY`
- supersedes: `LEVALPLAN_000001`
- evaluator version: `1.13A.0`
- result: `PASS_WITH_WARNINGS`
- aggregate score: `96.9`
- `NOT_CAPTURED` dimensions: none
- `NOT_APPLICABLE` dimensions: `file_path_prediction`, `unnecessary_work`,
  `missed_material_implementation_work`,
  `actual_schema_migration_implementation`, `actual_tests_added`,
  `actual_docs_added`
- correction: `MODERATE provenance_quality`, score `75.0`

## Learning Candidate

Phase 1.13A identified `LPLEARN_000003`:

Autonomy gates must evaluate planning-only pilots with explicit
`NOT_APPLICABLE` implementation-relative metrics rather than treating absent
implementation evidence as failure.

The candidate was identified only; it was not promoted.

## Corrected Pilot Record

Phase 1.13A created `LPILOT_000002` as a new record connecting the preserved
Phase 1.13 evidence to the corrected planning-only evaluation.

Gate result:

- recommendation: `READY_FOR_LIMITED_WRITE_PILOT`
- passed: `true`
- blockers: none
- warnings: human rubric `NOT_CAPTURED`; warning for first limited-write pilot,
  hard blocker before promotion beyond bounded write autonomy
- benchmark regression status: `NO_REGRESSION`

## Implementation Summary

Lucius changes:

- added evaluation modes and metric applicability enums;
- added persisted `evaluation_mode` and `supersedes_evaluation_id` fields;
- added Alembic migration `0009_planning_evaluation_modes`;
- split deterministic evaluation into planning-only and
  plan-vs-implementation paths;
- updated release-gate policy for canonical planning-only pilots;
- updated the pilot CLI to support optional implementation artifacts,
  `--mode`, and `--supersedes-evaluation-id`;
- added Phase 1.13A integration coverage.

## Verification

Focused integration tests passed:

```text
28 passed
```

Full test suite passed:

```text
149 passed
```

Formal post-implementation core benchmark:

- benchmark id: `LBENCH_000003`
- benchmark version: `LUCIUS_CORE_BENCH_V0_1`
- Lucius HEAD: `98ca6cff24622a9daf0c08d9ba949da617f82a30`
- target dirty: `true`
- result: `PASSED`
- cases: 8 passed, 0 failed, 0 skipped
- aggregate score: `100.0`
- hard gate: `PASS`
- release decision: `RELEASE_ELIGIBLE`

The verified behaviors include:

- planning-only evaluation does not require implementation artifacts;
- plan-vs-implementation still marks missing implementation evidence
  `NOT_CAPTURED`;
- implementation-relative dimensions are `NOT_APPLICABLE` in planning mode;
- missing planning evidence remains insufficient;
- unnecessary-work does not create planning-only corrections;
- hallucinated planned paths and unsupported claims remain critical;
- provenance weakness creates a correction without fabricating a clean pass;
- superseded evaluations preserve original artifacts;
- canonical planning-only evidence can reach `READY_FOR_LIMITED_WRITE_PILOT`;
- non-canonical state, benchmark regression, leakage failure, and insufficient
  evaluation block the gate;
- missing human rubric remains an explicit warning;
- no missing-evaluation path grants bounded write autonomy.

## Conclusion

Phase 1.13A corrects the planning-evaluation gate while preserving the original
Phase 1.13 artifacts. The corrected machine gate supports
`READY_FOR_LIMITED_WRITE_PILOT` as the maximum next autonomy step, subject to
independent review before Phase 1.14 begins.
