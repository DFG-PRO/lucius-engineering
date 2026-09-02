# Phase 1.18: First Supervised Engineering Workflow

Phase 1.18 ran the first supervised engineering workflow pilot against Darwin.
Lucius selected one bounded outcome, planned it from the frozen Darwin baseline,
implemented only in an isolated Darwin worktree, verified the result, and stopped
at human review. Darwin main was not modified, merged, pushed, cleaned, or
deployed.

## Baseline

- Lucius baseline HEAD: `8b670e7f33caa9bd2e8a1f7023f847bc232c4e29`
- Lucius baseline status: `## main...origin/main [ahead 12]`, clean
- Lucius baseline full tests: `168 passed in 153.39s`
- Darwin required baseline HEAD: `6df37914f0204e9ebcad28fbd435ed0f98c26524`
- Darwin main status: `## main...origin/main [ahead 5]`, clean
- Darwin repository-state observation: `LRSTATE_000015`
- Darwin classification: `CANONICAL_CLEAN`
- Darwin snapshot: `LSNAP_000005`
- Darwin manifest hash: `fc65335c1907ca8df3499f3345d03392322e8462b5e4d6dd5226ea30d874710e`
- Snapshot counts: 185 files, 66 docs, 24 tests, 3 config files
- Pre benchmark: `LBENCH_000019`, `PASSED`, 8 passed, 0 failed, aggregate 100.0,
  hard gate `PASS`, target dirty false

## Outcome Selection

Selected outcome: improve Darwin research-run observability for one run.

Exact task:

Add a read-only Darwin research-run overview capability that lets an operator
inspect one research run from a single CLI command, combining existing run
status, source/evidence/claim/conclusion counts, integrity health, and latest
research-loop execution state, without migrations, new dependencies, dashboard
infrastructure, or writes to Darwin main.

The novelty audit found no existing single overview command or observability
aggregation service in frozen Darwin HEAD. Existing capabilities were separate:
research record retrieval, run listing, export, integrity report, loop listing,
and loop event inspection.

## Plan

- Parent task: `LTASK_000014`
- Child tasks: `LTASK_000015` through `LTASK_000020`
- Plan: `LPLAN_000005`
- Freeze: `LFREEZE_000005`
- Planning context: `CURRENT_STATE_PLANNING`
- Planning evaluation: `LEVALPLAN_000011`, `PLANNING_ONLY`, `PASS`, aggregate
  98.9

The EngineeringPlan proposed a new `darwin.observability` read boundary with
Pydantic overview models, a read-only aggregation service, a
`darwin research overview` CLI command, targeted service and CLI tests, full
Darwin test verification, and runtime documentation.

## Implementation

Implementation occurred only in the isolated worktree
`/private/tmp/darwin-phase-1.18-research-run-overview` on branch
`lucius/phase-1.18-research-run-overview`.

Darwin isolated implementation commit:
`cee5c4fbace97bb00d41b5a789d282c0bafb7919`

Changed files:

- `src/darwin/observability/__init__.py`
- `src/darwin/observability/schemas.py`
- `src/darwin/observability/service.py`
- `src/darwin/cli/app.py`
- `tests/test_research_observability.py`
- `tests/test_cli.py`
- `docs/runtime/research-run-lifecycle.md`

No migrations or dependency manifest changes were made.

## Verification

- Observability tests: `2 passed in 0.67s`
- CLI overview/help tests: `4 passed, 11 deselected in 2.52s`
- Integrated targeted tests: `17 passed in 3.93s`
- Operator smoke: exit code 0; text overview reported run status, counts,
  integrity, loop summary, warnings, and next action
- Full Darwin suite in isolated worktree: `179 passed in 11.95s`
- `git diff --check`: clean

## Evaluation

Deterministic evaluations are preserved in order:

- `LEVALPLAN_000012`: `FAIL`; initial implementation artifact was too coarse for
  deterministic comparison.
- `LEVALPLAN_000013`: `FAIL`; exposed a Lucius evaluator bug where Pydantic
  `schemas.py` was treated as a database migration expectation.
- `LEVALPLAN_000014`: `PASS`; aggregate 98.57; no corrections; supersedes
  `LEVALPLAN_000013`.

Human review was explicitly `NOT_CAPTURED` at Phase 1.18 closure. Phase 1.18A
later captured Daniel's review in `LRUBRIC_000010` without erasing the
historical `LRUBRIC_000009` record.

Learning candidates:

- `LPLEARN_000006`: evaluator artifacts should match the deterministic contract
  shape directly.
- `LPLEARN_000007`: schema-migration detection must distinguish database
  migrations from Pydantic schema model files.
- `LPLEARN_000008`: supervised workflows stop at
  `COMPLETED_PENDING_INTEGRATION` when human review is not captured.

## Lucius Changes

Lucius Phase 1.18 adds:

- supervised-workflow autonomy recommendations:
  `NOT_READY_FOR_SUPERVISED_ENGINEERING_WORKFLOW`,
  `READY_FOR_ANOTHER_SUPERVISED_ENGINEERING_WORKFLOW`, and
  `READY_FOR_PERSISTENT_SUPERVISED_ENGINEERING`;
- release-gate handling for `SUPERVISED_ENGINEERING_WORKFLOW`;
- evaluator corrections for evidence-backed verified assumptions and Pydantic
  schema files;
- regression tests for those behaviors.

## Closure Rules

The workflow state is `COMPLETED_PENDING_INTEGRATION`. The isolated Darwin
implementation is ready for Daniel's review but is not integrated.

The final post benchmark must be captured after this Lucius documentation/code
checkpoint is committed, so it runs against a clean final Lucius HEAD. The exact
post benchmark ID and regression result are part of the Phase 1.18 pilot record
and closure report.

No Phase 1.19 work begins from this phase.
