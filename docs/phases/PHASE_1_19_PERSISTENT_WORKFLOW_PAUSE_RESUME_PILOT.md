# Phase 1.19: Persistent Workflow Pause/Resume Pilot

Phase 1.19 tested whether Lucius can run a bounded supervised engineering
workflow, pause mid-execution, and resume from durable state rather than
conversation memory.

The Darwin target was read and frozen from main at
`3207aee087fa9018ee9ff2d526fe7a97443f6e21` (`Add research run overview
command`). Darwin main began clean, ahead of origin by 6, and remained the
authoritative baseline throughout the pilot.

## Baseline

- Lucius baseline HEAD: `dbd0f9e315341fd9c76feb76ba82c750d2ce7841`
- Lucius baseline status: `## main...origin/main [ahead 14]`, clean
- Lucius baseline tests: `.venv/bin/python -m pytest` -> `174 passed in 122.48s`
- PRE benchmark: `LBENCH_000023`, `LUCIUS_CORE_BENCH_V0_1`, `8 passed`, aggregate `100.0`, hard gate `PASS`
- Darwin baseline HEAD: `3207aee087fa9018ee9ff2d526fe7a97443f6e21`
- Darwin baseline status: `## main...origin/main [ahead 6]`, clean
- Repository-state observation: `LRSTATE_000018`, `CANONICAL_CLEAN`
- Snapshot: `LSNAP_000006`
- Manifest hash: `5274ee3cacacfd8629e54156026ff3933c407244dc2a860137f8364140720a82`
- Snapshot counts: 189 files, 66 documents, 25 tests, 3 config files

## Objective

Add a read-only Darwin research-run traceability matrix for one run, showing
sources, evidence, claims, claim-evidence links, conclusions, gaps, and warnings
in one inspectable read model and CLI command.

The selected solution extended the existing Darwin observability package. It did
not add migrations, dependencies, persistence tables, background work, external
credentials, or writes to Darwin main.

## Evidence

Evidence references `LEVID_000042` through `LEVID_000050` cite the frozen
Darwin snapshot `LSNAP_000006`.

Supported findings:

- `src/darwin/observability/schemas.py` already contained operator-facing
  overview read models.
- `src/darwin/observability/service.py` already composed read-only research,
  integrity, and loop services.
- `ResearchRecordRead` exposed sources, evidence, claims, claim-evidence links,
  and conclusions.
- `ResearchService.get_research_record` assembled the canonical read model.
- Existing CLI and tests established text/json command patterns.
- Runtime docs already described export, integrity, and overview behavior.

Uncertainty:

- Darwin did not expose a direct Conclusion-to-Claim relationship in
  `ResearchRecordRead`; the plan therefore treated conclusion linkage as
  unknown and structural rather than inferred.

Novelty proof:

- Frozen Darwin HEAD had `export-run`, `integrity-report`, and `overview`.
- Searches for `traceability` and `matrix` found no existing
  `traceability-matrix` command or matrix read model.
- No later Darwin implementation evidence was used before plan freeze.

## Plan Freeze

- Task: `LTASK_000021`
- Child tasks: `LTASK_000022` through `LTASK_000027`
- Contract: `LCONTR_000016`
- EngineeringPlan: `LPLAN_000006`
- Freeze: `LFREEZE_000006`
- Planning context: `CURRENT_STATE_PLANNING`
- Initial planning evaluation: `LEVALPLAN_000015`, `INSUFFICIENT_EVIDENCE`
- Superseding planning evaluation: `LEVALPLAN_000016`, `PASS`, aggregate `98.65`

`LEVALPLAN_000015` was superseded because the evaluation artifact used nested
keys for known paths and novelty/leakage that the deterministic evaluator does
not read. The frozen plan itself was not changed.

## Persistent Workflow

- Workflow artifact: `LWORK_000001`
- Initial state: `PLAN_READY`
- Isolated Darwin worktree:
  `/private/tmp/darwin-phase-1.19-traceability-matrix`
- Isolated branch: `lucius/phase-1.19-traceability-matrix`
- Expected Darwin main HEAD:
  `3207aee087fa9018ee9ff2d526fe7a97443f6e21`

Dependency graph:

- `LTASK_000022`: no dependencies
- `LTASK_000023`: depends on `LTASK_000022`
- `LTASK_000024`: depends on `LTASK_000023`
- `LTASK_000025`: depends on `LTASK_000023`
- `LTASK_000026`: depends on `LTASK_000025`
- `LTASK_000027`: depends on `LTASK_000024` and `LTASK_000026`

## Pause

Lucius intentionally paused after completing 3 of 6 tasks:

- `LTASK_000022`: read models
- `LTASK_000023`: read-only service
- `LTASK_000024`: service tests

Pre-pause test:

- `tests/test_research_observability.py` -> `4 passed in 0.62s`

Checkpoint:

- `LWCHK_000001`
- state: `PAUSED`
- implementation HEAD: `f4c463ed4734474e072d0e04083861714c98572c`
- completed tasks: `LTASK_000022`, `LTASK_000023`, `LTASK_000024`
- pending tasks: `LTASK_000025`, `LTASK_000026`, `LTASK_000027`
- repair counter: 1 minor test-assertion repair

## Resume

Session-loss simulation used a separate Python process that reconstructed state
only from SQLite workflow/checkpoint rows plus Git/filesystem checks.

Resume validation:

- artifact: `LRESUME_000001`
- result: `SAFE_TO_RESUME`
- checks: 13/13 passed
- next eligible task: `LTASK_000025`

Checked conditions included workflow existence, checkpoint existence, worktree
existence, isolated branch, implementation HEAD, worktree cleanliness, Darwin
main HEAD, Darwin main cleanliness, dependency readiness, repair counters,
human-approval state, paused workflow state, and attached plan/freeze.

## Implementation Result

Final isolated commits:

- `f4c463ed4734474e072d0e04083861714c98572c`:
  `Add research traceability matrix service`
- `9607fcb153401e8101f50a16369b81112236d6f6`:
  `Add research traceability matrix command`

Changed files in the isolated Darwin branch:

- `src/darwin/observability/schemas.py`
- `src/darwin/observability/service.py`
- `src/darwin/observability/__init__.py`
- `src/darwin/cli/app.py`
- `tests/test_research_observability.py`
- `tests/test_cli.py`
- `docs/runtime/research-run-lifecycle.md`

Final tests from the isolated worktree:

- focused post-resume tests: `7 passed, 13 deselected in 2.66s`
- broader targeted tests: `20 passed in 2.89s`
- full Darwin tests: `182 passed in 10.17s`

## Evaluation

- Plan-vs-implementation evaluation: `LEVALPLAN_000017`
- Result: `PASS`
- Aggregate: `98.42`
- Human rubric: `LRUBRIC_000011`, `NOT_CAPTURED`

Pause/resume evaluation result: `PASS`.

Persistent workflow evaluation result: `PASS`.

Autonomy audit result: `PASS`.

Corrections required:

- `MODERATE`: supersede malformed planning-only evaluation artifact
  `LEVALPLAN_000015`.
- `MINOR`: fix service test assertion that sorted dictionaries directly.
- `MINOR`: make linked evidence UUID assertion order-independent.

Learning candidates:

- `LPLEARN_000009`: durable workflow/checkpoint/resume rows are required before
  autonomy promotion beyond single-session supervised work.
- `LPLEARN_000010`: planning-only deterministic artifacts must use the
  evaluator's expected key shape.

## Integrity

Darwin main was not merged, pushed, rebased, reset, cleaned, formatted, migrated,
or edited. All implementation writes occurred in the isolated worktree.

Final Darwin main verification:

- HEAD remained `3207aee087fa9018ee9ff2d526fe7a97443f6e21`
- status remained `## main...origin/main [ahead 6]`
- working tree remained clean

## Conclusion

Phase 1.19 demonstrated durable pause/resume for one bounded supervised
engineering workflow. The final workflow state is
`COMPLETED_PENDING_INTEGRATION`.

Human review remains `NOT_CAPTURED`, so the result does not authorize merge,
push, deployment, destructive Git, credential work, unrestricted autonomy, or
Phase 1.20 execution.
