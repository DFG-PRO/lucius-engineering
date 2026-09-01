# Phase 1.15: Second Limited Write Pilot on Darwin

Phase 1.15 ran an end-to-end planning, isolated implementation, deterministic
evaluation, and gate pass against Darwin. Darwin main was not used as an
implementation target. The implementation happened only in an isolated worktree.

## Baseline

Lucius baseline:

- repository: `/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering`
- HEAD: `bea1e3fd77c95284d7e2107df8d7ef0fbf130088`
- status: `## main...origin/main [ahead 6]`
- full tests: `160 passed in 96.86s`
- pre benchmark: `LBENCH_000007`, `LUCIUS_CORE_BENCH_V0_1`, 8 passed, 0 failed,
  aggregate `100.0`, hard gate `PASS`

Darwin baseline after Phase 1.14 integration:

- repository: `/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine`
- branch: `main`
- HEAD: `cf7a5933adcdb545b360f9e8b7ed86b9c0ecc1cf`
- status: `## main...origin/main [ahead 2]`
- repository-state observation: `LRSTATE_000004`
- classification: `CANONICAL_CLEAN`
- manifest hash: `32de99535690e5177444834ffbdd14703e57207a9e5fc503bd2bda7152723aaf`
- snapshot: `LSNAP_000002`
- snapshot counts: 185 files, 66 documents, 24 tests

## Task

Exact novel Darwin task:

Add read-only `darwin research list-runs` with optional status filtering and
bounded limit.

Novelty proof:

- frozen snapshot contained `create-run` and `get-run` research commands.
- frozen snapshot contained `loop-list`, but no research-run listing command.
- search for `list-runs`, `run-list`, `research.*list`, `list.*research`,
  research command list patterns, `ResearchRunSummary`, and `list_research`
  found no existing implementation.

## Repository Understanding

Supported findings:

- `ResearchRun` stores id, public id, title, status, created/updated/completed
  timestamps, method/version fields, and context.
- `ResearchService` owns create/get/status and record-read behavior for research
  persistence.
- `src/darwin/cli/app.py` uses Typer, `get_settings`, `session_scope`,
  `ResearchService`, and narrow command error handling.
- tests use in-memory or temporary SQLite databases with `Base.metadata.create_all`.
- no schema migration or dependency change was expected for a read-only summary
  over existing tables.

Evidence ids: `LEVID_000013` through `LEVID_000020`.

Provenance quality: `SUPPORTED`.

Unsupported claims found: none captured by deterministic evaluation.

## Plan Freeze

- task id: `LTASK_000002`
- plan id: `LPLAN_000002`
- freeze id: `LFREEZE_000002`
- planning context: `CURRENT_STATE_PLANNING`
- planning-only evaluation: `LEVALPLAN_000005`, `PASS`, aggregate `98.68`

EngineeringPlan summary:

Add a read-only ResearchRun listing path by extending research schemas/service,
Typer CLI, service/CLI tests, and lifecycle docs. Use existing
`ResearchRunStatus` and `ResearchService` conventions; no migration or new
dependency is expected.

## Implementation

Isolated Darwin worktree:

- path: `/private/tmp/darwin-phase-1.15-list-runs`
- branch: `lucius/phase-1.15-list-runs`
- source HEAD: `cf7a5933adcdb545b360f9e8b7ed86b9c0ecc1cf`
- implementation commit: `0c782caceeabd498bbe6d06e3ea8b74909518440`
- merge to Darwin main: not performed
- push: not performed

Changed files:

- `src/darwin/research/schemas.py`
- `src/darwin/research/service.py`
- `src/darwin/research/__init__.py`
- `src/darwin/cli/app.py`
- `tests/test_research_service.py`
- `tests/test_cli.py`
- `docs/runtime/research-run-lifecycle.md`

Actual implementation:

- added `ResearchRunSummaryRead`.
- added `ResearchService.list_research_runs`.
- added status parsing for enum values with case-insensitive hyphen support.
- added bounded limit validation from 1 to 200.
- used grouped aggregate subqueries for distinct sources, evidence, claims, and
  conclusions.
- added `darwin research list-runs --status --limit`.
- added service and CLI tests.
- updated runtime docs.

No migration, dependency, configuration, or infrastructure change was made.

## Evaluation

Targeted verification:

- initial run: `26 passed, 1 failed in 3.02s`
- failure: CLI fixture created both runs as `PENDING`, making `--limit 1`
  ambiguous.
- repair cycle: one fixture-only repair, classified `MINOR`
- final targeted run: `27 passed in 2.56s`

Full Darwin verification:

- command: `PYTHONPATH=src pytest`
- result: `171 passed in 8.28s`

Plan-vs-implementation evaluation:

- evaluation id: `LEVALPLAN_000006`
- mode: `PLAN_VS_IMPLEMENTATION`
- supersedes: `LEVALPLAN_000005`
- result: `PASS`
- aggregate: `98.46`
- corrections: none
- benchmark before: `LBENCH_000007`
- benchmark after: `LBENCH_000008`
- benchmark regression: `NO_REGRESSION`

Human rubric:

- id: `LRUBRIC_000003`
- status: `NOT_CAPTURED`
- all dimensions: `NOT_CAPTURED`

Learning candidate:

- id: `LPLEARN_000005`
- status: `IDENTIFIED`
- statement: limited write pilots should design list-command fixtures so filter
  and limit expectations are unambiguous before interpreting a targeted-test
  failure as an implementation defect.

## Integrity

Final Darwin main state:

- observation: `LRSTATE_000005`
- branch: `main`
- HEAD: `cf7a5933adcdb545b360f9e8b7ed86b9c0ecc1cf`
- classification: `CANONICAL_CLEAN`
- manifest hash: `32de99535690e5177444834ffbdd14703e57207a9e5fc503bd2bda7152723aaf`
- tracked modifications: none
- staged modifications: none
- untracked files: none

Darwin main remained unchanged throughout the Phase 1.15 write pilot after the
baseline freeze. Darwin writes were limited to the isolated Phase 1.15 worktree.

## Pilot Record

- pilot record: `LPILOT_000005`
- canonical status: `CANONICAL_CLEAN`
- repository integrity: `UNCHANGED`
- gate passed: `true`
- blockers: none
- warnings: human rubric `NOT_CAPTURED`
- autonomy recommendation: `READY_FOR_ANOTHER_LIMITED_WRITE_PILOT`

Phase 1.15 does not grant unrestricted write autonomy and does not begin
Phase 1.16.
