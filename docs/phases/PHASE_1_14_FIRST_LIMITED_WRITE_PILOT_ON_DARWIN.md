# Phase 1.14 First Limited Write Pilot on Darwin

## Baseline

Lucius baseline:

- HEAD: `748bdbc296ca58006d644aac8e38b6c97c29e98a`
- status: `## main...origin/main [ahead 4]`
- full tests: `149 passed in 87.73s`
- pre-write benchmark: `LBENCH_000004`, `PASSED`, 8 passed, 0 failed,
  aggregate `100.0`, hard gate `PASS`, target dirty `false`

Darwin main baseline:

- branch: `main`
- HEAD: `cf1dcdc0e88039a3598ed6be271e8a3d953e301b`
- status: `## main...origin/main`
- repository-state capture: `LRSTATE_000003`
- classification: `CANONICAL_CLEAN`
- manifest hash:
  `4f31c47265e6613f6d9c9b5aaedecf3d655019c82aa8b43a14f3ede150183410`

## Isolated Write Environment

- source HEAD: `cf1dcdc0e88039a3598ed6be271e8a3d953e301b`
- branch: `lucius/phase-1.14-loop-list-pilot`
- worktree: `/private/tmp/darwin-phase-1.14-loop-list-pilot`
- created: `2026-08-31T20:48:35Z`

No Darwin main implementation writes were performed.

## Frozen Plan

The pilot executed preserved Phase 1.13 artifacts:

- snapshot: `LSNAP_000001`
- plan: `LPLAN_000001`
- freeze: `LFREEZE_000001`
- planning evaluation: `LEVALPLAN_000002`
- gate record: `LPILOT_000002`

Task:

Add read-only `darwin research loop-list --research-run-id <id>`.

## Implementation Summary

The isolated worktree implements a read-only loop list path:

- adds `ResearchLoopExecutionSummary`;
- adds `ResearchLoopController.list_for_research_run`;
- exports the summary schema;
- adds CLI command `darwin research loop-list --research-run-id`;
- prints execution id, mode, state, stop reason, completion, iteration count,
  counters, linked synthesis/report ids, and latest event timestamp;
- supports ResearchRun UUID or public id through existing `ResearchService`;
- adds service and CLI coverage;
- updates runtime operator documentation.

Actual isolated Darwin files changed:

- `src/darwin/research_loop/schemas.py`
- `src/darwin/research_loop/service.py`
- `src/darwin/research_loop/__init__.py`
- `src/darwin/cli/app.py`
- `tests/test_research_loop.py`
- `tests/test_cli.py`
- `docs/runtime/research-loop.md`

Diff summary:

```text
7 files changed, 288 insertions(+), 1 deletion(-)
```

## Deviations

`tests/test_cli.py` was modified for CLI coverage although it was not listed as
an affected file in `LPLAN_000001`. It was explicitly named in the frozen
validation command, so this is task-relevant and not scope expansion.

No migration and no new dependency were added.

## Repair Cycles

Cycle 1:

- failure: targeted tests found SQLite returned a naive datetime for
  `max(created_at)` in the latest-event aggregate;
- diagnosis: aggregate timestamp from SQLite lost timezone info while model
  defaults are timezone-aware;
- files changed: `src/darwin/research_loop/service.py`;
- fix: normalize aggregate `latest_event_at` to UTC in the read mapper;
- result: targeted tests passed.

No further repair cycles were needed.

## Tests

Targeted tests:

```text
26 passed in 3.24s
```

Full Darwin tests:

```text
168 passed in 9.17s
```

No unexpected generated files were present in the isolated worktree.

## Plan-Vs-Implementation Evaluation

- evaluation id: `LEVALPLAN_000003`
- mode: `PLAN_VS_IMPLEMENTATION`
- supersedes: `LEVALPLAN_000002`
- evaluator version: `1.14.0`
- result: `INSUFFICIENT_EVIDENCE`
- aggregate score: `95.49`

Corrections:

- `MODERATE schema_migration_awareness`: required comparison evidence was not
  captured.
- `MODERATE dependency_awareness`: required comparison evidence was not
  captured.

The implementation did not add a migration or dependency. The blocker is an
evaluator representation gap for captured zero-change evidence, not a Darwin
test failure.

Learning candidate `LPLEARN_000004` was identified for this issue.

## Provenance

Implementation used direct evidence from the relevant frozen-plan paths:

- CLI source: `src/darwin/cli/app.py`
- service source: `src/darwin/research_loop/service.py`
- schemas: `src/darwin/research_loop/schemas.py`
- persistence models: `src/darwin/db/models.py`
- tests: `tests/test_research_loop.py`, `tests/test_cli.py`
- docs: `docs/runtime/research-loop.md`

Provenance quality improved in the implementation work, but the deterministic
plan-vs-implementation artifact remains blocked by the zero-change evaluator
gap above.

## Benchmark And Gate

Post-pilot Lucius benchmark:

- benchmark id: `LBENCH_000005`
- result: `PASSED`
- 8 passed, 0 failed
- aggregate: `100.0`
- hard gate: `PASS`
- target dirty: `false`

Benchmark comparison:

- before: `LBENCH_000004`
- after: `LBENCH_000005`
- result: `NO_REGRESSION`

Phase 1.14 pilot record:

- id: `LPILOT_000003`
- recommendation: `NOT_READY_FOR_WRITE_AUTONOMY`
- blocker: deterministic `PLAN_VS_IMPLEMENTATION` evaluation not passing:
  `INSUFFICIENT_EVIDENCE`
- warning: human rubric `NOT_CAPTURED`

## Human Review

Human rubric artifact:

- id: `LRUBRIC_000002`
- state: `NOT_CAPTURED`

Human approval is required before any merge. No Darwin merge or push was
performed.

## Integrity

Darwin main remained clean and unchanged:

- final status: `## main...origin/main`
- final HEAD: `cf1dcdc0e88039a3598ed6be271e8a3d953e301b`

Isolated worktree final status contains only task-relevant modified files on
`lucius/phase-1.14-loop-list-pilot`.

## Rollback

No merge was performed, so rollback is to discard the isolated pilot branch and
worktree after review:

```bash
git worktree remove --force /private/tmp/darwin-phase-1.14-loop-list-pilot
git branch -D lucius/phase-1.14-loop-list-pilot
```

Do not run these commands until the human review package is no longer needed.

## Conclusion

The bounded Darwin implementation succeeded in the isolated worktree and all
Darwin tests passed. The next autonomy gate is blocked by Lucius evaluation
evidence semantics for zero-change migration/dependency cases, so the exact
recommendation is `NOT_READY_FOR_WRITE_AUTONOMY`.
