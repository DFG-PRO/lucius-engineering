# Phase 1.11: Darwin Real Repository Pilot

## Summary

Phase 1.11 validates Lucius v0.1 against Darwin as a real external repository,
not as a Lucius evaluation fixture. The pilot remained read-only with respect to
Darwin and did not add repository-write autonomy or an Engineering Executor.

Result: Lucius registered, inspected, snapshotted, retrieved evidence from, and
planned against Darwin using existing v0.1 APIs. The historical assisted Claim
construction plan was directionally useful and identified the main architecture,
files, migration, test, documentation, and safety boundaries. Limitations remain:
the model response in this pilot is deterministic/mock-backed, and current
repository evidence necessarily includes the already-implemented historical
solution.

## Pre-Flight

Lucius manual pre-flight before pilot edits:

- repository path: `/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering`
- branch: `main`
- HEAD: `7057ffa`
- working tree: clean before edits
- full tests: `/private/tmp/lucius-phase111-venv/bin/python -m pytest` -> `121 passed in 83.37s`

Darwin pre-flight:

- repository path: `/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine`
- branch: `main`
- HEAD: `8003624844e08d67a38406b433098aafd8ad5945`
- working tree before: `## main...origin/main
 M .env.example
 M src/darwin/cli/app.py
 M src/darwin/config/settings.py
 M src/darwin/db/__init__.py
 M src/darwin/db/models.py
 M src/darwin/orchestration/service.py
?? alembic/versions/0010_narrative_synthesis.py
?? src/darwin/narrative_synthesis/`
- working tree after: `## main...origin/main
 M .env.example
 M src/darwin/cli/app.py
 M src/darwin/config/settings.py
 M src/darwin/db/__init__.py
 M src/darwin/db/models.py
 M src/darwin/orchestration/service.py
?? alembic/versions/0010_narrative_synthesis.py
?? src/darwin/narrative_synthesis/`
- tests available: verified by `pyproject.toml` pytest config and tracked `tests/` files; Darwin tests were not executed to avoid cache/artifact writes in the external repo.
- documentation available: verified by tracked `README.md`, `docs/README.md`, architecture, method, runtime, data model, decision, benchmark, phase, and implementation-record files.
- canonicality: non-canonical pilot condition if status is dirty; the condition is recorded and Darwin is not modified.

## Safety Boundary

Darwin was accessed through `LocalGitRepositoryAdapter` with
`RepositoryAccessMode.READ_ONLY` and a workspace root limited to the Darwin
repository parent. The pilot used Git inspection, file listing, file reads,
Lucius snapshot persistence in an in-memory database, evidence retrieval,
planning context construction, and model-gateway execution. Darwin HEAD and working tree status are unchanged.

## Pilot A: Repository Understanding

Snapshot:

- Lucius repository id: `LREPO_000001`
- Lucius snapshot id: `LSNAP_000001`
- snapshot mode: `STANDARD`
- branch: `main`
- commit: `8003624844e08d67a38406b433098aafd8ad5945`
- dirty: `True`
- files: `161`
- documents: `54`
- tests: `20`
- config files: `3`
- manifest hash: `aeec9ac56ea6dbc43fcbe761140ad0b161b7b7a1294dc06c438542ddd033dcd9`

Verified architecture:

- Primary language is Python: `README.md:9`, `pyproject.toml:10`, and the `src/darwin/` layout.
- Package/build configuration is Hatchling with Python 3.12+, SQLAlchemy, Alembic, httpx, psycopg, pydantic-settings, Typer, and pytest dev dependency: `pyproject.toml:1-37`.
- Darwin is documented as a modular monolith with one supplied-material orchestrator and service modules for config, persistence, acquisition, content, construction, validation, synthesis, and CLI: `docs/architecture/v0.1-architecture.md:3`.
- PostgreSQL is the intended persistent store and SQLAlchemy/Alembic define the durable model: `docs/architecture/v0.1-architecture.md:7-8`, `README.md:60`, `README.md:211-237`.
- Current boundaries include controlled acquisition, content snapshots/segments, explicit Evidence extraction, caller-supplied Claim construction, structured synthesis, research planning proposals, assisted Evidence proposals, and assisted Claim proposals: `README.md:17-24`.
- Not-present boundaries include autonomous research, multi-agent architecture, semantic validation, recommendations, embeddings/vector DB/graph DB, browser automation, scheduled research, trading, and web UI: `docs/architecture/v0.1-architecture.md:17-19`.
- ADRs verify Python, PostgreSQL, modular monolith, no separate vector/graph DB, core data model, validation lineage, deterministic orchestration, provider-agnostic acquisition, source artifact strategy, and explicit claim construction decisions: `docs/decisions/ADR-INDEX.md:5-14`.

Detected technologies from Lucius snapshot: Alembic (alembic.ini, alembic/env.py, alembic/script.py.mako, alembic/versions/.gitkeep, alembic/versions/0001_core_research_data_model.py, alembic/versions/0002_claim_validation_foundation.py, alembic/versions/0003_research_method_orchestration.py, alembic/versions/0004_external_research_acquisition.py, alembic/versions/0005_source_content_acquisition.py, alembic/versions/0006_claim_construction_synthesis.py, alembic/versions/0007_research_planning.py, alembic/versions/0008_assisted_evidence_extraction.py, alembic/versions/0009_assisted_claim_construction.py, alembic/versions/0010_narrative_synthesis.py, pyproject.toml), Pytest (pyproject.toml), Python (pyproject.toml), SQLAlchemy (pyproject.toml)

Detected source modules:

`__init__.py`, `acquisition`, `claim_assistance`, `cli`, `config`, `construction`, `content`, `db`, `extraction`, `logging.py`, `narrative_synthesis`, `orchestration`, `planning`, `research`, `synthesis`, `validation`

Configuration map:

- `.env.example` (ENV_EXAMPLE)
- `alembic.ini` (alembic.ini)
- `pyproject.toml` (pyproject.toml)

Documentation sample:

- `README.md` (README)
- `docs/README.md` (README)
- `docs/architecture/.gitkeep` (ARCHITECTURE)
- `docs/architecture/v0.1-architecture.md` (ARCHITECTURE)
- `docs/benchmarks/.gitkeep` (OTHER)
- `docs/benchmarks/phase-1.8i-gap-register.md` (PHASE_REPORT)
- `docs/benchmarks/phase-1.8i-research-mvp-benchmark.md` (PHASE_REPORT)
- `docs/data_model/.gitkeep` (OTHER)
- `docs/data_model/core-research-data-model.md` (OTHER)
- `docs/data_model/evidence-to-claim-synthesis.md` (OTHER)
- `docs/data_model/external-research-acquisition.md` (OTHER)
- `docs/data_model/source-content-acquisition.md` (OTHER)
- `docs/decisions/ADR-001-python-as-primary-language.md` (ADR)
- `docs/decisions/ADR-002-postgresql-as-primary-persistent-store.md` (ADR)
- `docs/decisions/ADR-003-modular-monolith-single-orchestrator-v01.md` (ADR)
- `docs/decisions/ADR-004-no-separate-vector-db-or-graph-db-v01.md` (ADR)
- `docs/decisions/ADR-005-core-research-data-model-foundation.md` (ADR)
- `docs/decisions/ADR-006-claim-validation-history-and-source-lineage.md` (ADR)

Test map sample:

- `pyproject.toml` (TEST_CONFIG)
- `tests/conftest.py` (TEST_SOURCE)
- `tests/fixtures/manual_research_complete.json` (TEST_SOURCE)
- `tests/test_acquisition_service.py` (TEST_SOURCE)
- `tests/test_assisted_claim_construction.py` (TEST_SOURCE)
- `tests/test_assisted_evidence_extraction.py` (TEST_SOURCE)
- `tests/test_claim_construction_service.py` (TEST_SOURCE)
- `tests/test_claim_validation_service.py` (TEST_SOURCE)
- `tests/test_cli.py` (TEST_SOURCE)
- `tests/test_config.py` (TEST_SOURCE)
- `tests/test_db.py` (TEST_SOURCE)
- `tests/test_imports.py` (TEST_SOURCE)
- `tests/test_migrations.py` (TEST_SOURCE)
- `tests/test_models.py` (TEST_SOURCE)
- `tests/test_phase_1_8i_benchmark.py` (TEST_SOURCE)
- `tests/test_research_orchestrator.py` (TEST_SOURCE)

Repository warnings: `[{'code': 'DIRTY_REPOSITORY', 'message': 'Snapshot includes uncommitted repository state.'}]`

Unknowns:

- Live provider behavior cannot be verified from repository evidence alone.
- Runtime database configuration and production deployment state are outside repository evidence.
- Some generated macOS `._*` files are tracked/visible in docs paths; Lucius records them as documentation-like files but they are likely metadata artifacts.
- Dirty/untracked Darwin files are included in the snapshot by Lucius v0.1, so understanding claims that depend on dirty paths are not canonical.

## Pilot B: Historical Planning

Historical target selected: Darwin Phase 1.9C assisted Claim construction.

Reason: The change is substantial, has implementation records, source modules,
migration, tests, CLI integration, runtime/method docs, and a reconstructable
actual implementation. The Task and TaskContract were written as an original
requirement and did not include the implementation-record file list as explicit
ground truth. Because the snapshot is current, the retrieved evidence can still
include already-implemented files; this is a pilot limitation, not a repository
mutation.

Lucius execution:

- Task: `LTASK_000001`
- TaskContract: `LCONTR_000001`
- PlanningContext evidence count: `14`
- PlanningContext memory count: `0`
- ModelGateway requests observed: `2`
- EngineeringPlan: `LPLAN_000001`
- Blockers: `[]`

Lucius plan summary:

Plan a controlled assisted Claim construction layer with strict schemas, provenance validation, explicit acceptance, migration, CLI, docs, and tests.

Plan affected components:

`claim_assistance`, `db models`, `alembic migrations`, `orchestration`, `CLI`, `tests`, `documentation`

Plan affected files:

- `alembic/versions/0009_assisted_claim_construction.py` (EXISTING_VERIFIED; evidence=['LEVID_000006'])
- `docs/method/assisted-claim-construction.md` (EXISTING_VERIFIED; evidence=['LEVID_000003'])
- `docs/runtime/assisted-claim-construction.md` (EXISTING_VERIFIED; evidence=['LEVID_000002'])
- `src/darwin/claim_assistance/providers.py` (EXISTING_VERIFIED; evidence=[])
- `src/darwin/claim_assistance/schemas.py` (EXISTING_VERIFIED; evidence=[])
- `src/darwin/claim_assistance/service.py` (EXISTING_VERIFIED; evidence=[])
- `src/darwin/cli/app.py` (EXISTING_VERIFIED; evidence=[])
- `src/darwin/config/settings.py` (EXISTING_VERIFIED; evidence=[])
- `src/darwin/construction/service.py` (EXISTING_VERIFIED; evidence=[])
- `src/darwin/db/models.py` (EXISTING_VERIFIED; evidence=[])
- `src/darwin/extraction/service.py` (EXISTING_VERIFIED; evidence=[])
- `src/darwin/orchestration/service.py` (EXISTING_VERIFIED; evidence=[])
- `src/darwin/research/service.py` (EXISTING_VERIFIED; evidence=[])
- `tests/test_assisted_claim_construction.py` (EXISTING_VERIFIED; evidence=['LEVID_000004'])

Plan tests:

- UNIT: Validate schemas, candidate validation, provider failures, provenance errors, acceptance, rejection, rollback, and side-effect boundaries.
- INTEGRATION: Exercise CLI and orchestrator helper paths around proposal, list, accept, and reject flows.
- REGRESSION: Run migration, model, claim validation, acquisition/content/synthesis/orchestration, and full pytest regressions.

Actual implementation reconstruction from repository evidence:

- Implementation record objective: assisted Claim construction turns canonical Evidence for a ResearchPlanItem into auditable Claim candidates with explicit acceptance before canonical Claims/ClaimEvidence: `docs/implementation-record-phase-1.9c.md:3-6`.
- Scope included dedicated module, schemas, provider protocol, fake/OpenAI providers, persistence, provenance validation, accept/reject, duplicate prevention, orchestrator, CLI, migration, tests, and docs: `docs/implementation-record-phase-1.9c.md:20-38`.
- Migration added request, proposal, and candidate-evidence tables plus status/acceptance enums: `docs/implementation-record-phase-1.9c.md:69-80`, `alembic/versions/0009_assisted_claim_construction.py:61-220`.
- Service proposes candidates without canonical Claims, validates context and Evidence provenance, persists requests/candidates, and explicitly accepts/rejects: `src/darwin/claim_assistance/service.py:57-146`, `src/darwin/claim_assistance/service.py:161-273`.
- Candidate schema requires Evidence links and forbids extra fields: `src/darwin/claim_assistance/schemas.py:69-103`.
- Provider boundary includes protocol, deterministic fake provider, and OpenAI adapter with untrusted Evidence prompt policy: `src/darwin/claim_assistance/providers.py:27-90`, `src/darwin/claim_assistance/providers.py:93-188`.
- CLI exposes propose/list/accept/reject commands: `src/darwin/cli/app.py:604-717`.
- Orchestrator exposes helper methods without making the broader pipeline autonomous: `src/darwin/orchestration/service.py:136-169`.
- Tests cover candidate-only behavior, invalid contexts, provider failures, validation, acceptance, rejection, rollback, duplicate handling, and side-effect boundaries: `tests/test_assisted_claim_construction.py:146-360` and later tests in the same file.

Comparison:

- Relevant components identified: yes. Lucius named claim assistance, DB models, migrations, orchestration, CLI, tests, and docs.
- Relevant files identified: 11 of 13 reconstructed material files overlapped directly. Matched: `alembic/versions/0009_assisted_claim_construction.py`, `docs/method/assisted-claim-construction.md`, `docs/runtime/assisted-claim-construction.md`, `src/darwin/claim_assistance/providers.py`, `src/darwin/claim_assistance/schemas.py`, `src/darwin/claim_assistance/service.py`, `src/darwin/cli/app.py`, `src/darwin/config/settings.py`, `src/darwin/db/models.py`, `src/darwin/orchestration/service.py`, `tests/test_assisted_claim_construction.py`.
- Architectural direction: correct. Candidate proposal was separated from canonical Claim creation and explicit acceptance.
- Migration/schema awareness: correct. Lucius planned request/candidate/evidence-role persistence and a migration.
- Testing strategy: correct and broad. It included unit, integration, regression, migration, CLI, and side-effect boundary tests.
- Documentation requirements: correct. It included method, runtime, and implementation record targets.
- Authority/risk: correct. Schema/migration work was elevated to high risk and L2 authority.
- Dependencies: mostly correct. It depended on ResearchRun, ResearchPlanItem, Evidence, Claim, ClaimEvidence, and ResearchService boundaries.
- Missing work: Lucius did not explicitly name every modified README/docs index/test model file.
- Unnecessary work: none material.
- Unsupported assumptions: one non-blocking assumption about ResearchService remaining canonical; directionally valid but marked as assumption rather than verified fact.
- Hallucinated paths: none among planned existing/current paths; new proposed paths correspond to historical files in the current manifest after validation.

Answer to tested question: yes, this plan would have been useful and
directionally correct before implementation, especially for architecture,
provenance, migration, testing, docs, and safety boundaries. It was not an exact
implementation recipe and did not enumerate all incidental docs/index updates.

## Pilot C: Novel Planning

Legitimate current not-yet-implemented work was identified from the Phase 1.9C
deferred list: richer provider schema export. The current implementation record
lists richer provider schema export as deferred work, while current provider code
already has a strict candidate schema and OpenAI JSON schema helper.

Lucius execution:

- Task: `LTASK_000002`
- PlanningContext evidence count: `10`
- EngineeringPlan: `LPLAN_000002`
- Blockers: `[]`

Novel plan summary:

Plan a deterministic provider schema export for assisted Claim construction using the existing strict Pydantic candidate schema and provider prompt boundary.

Novel plan affected files:

- `docs/runtime/assisted-claim-construction.md` (EXISTING_VERIFIED; evidence=['LEVID_000029'])
- `src/darwin/claim_assistance/providers.py` (EXISTING_VERIFIED; evidence=['LEVID_000037'])
- `src/darwin/claim_assistance/schemas.py` (EXISTING_VERIFIED; evidence=[])
- `src/darwin/cli/app.py` (EXISTING_VERIFIED; evidence=[])
- `tests/test_assisted_claim_construction.py` (EXISTING_VERIFIED; evidence=['LEVID_000031'])

Novel plan assessment:

- The task is legitimate but should remain planning-only until explicitly authorized by Darwin ownership.
- The plan correctly derives schema export from existing Pydantic/provider boundaries rather than duplicating a second contract.
- The plan correctly calls out product/API uncertainty around CLI versus Python API exposure.
- No Darwin files were modified.

## Evaluation Measurements

- Evidence relevance: high for Pilot B and medium-high for Pilot C. Retrieval surfaced code, migration, tests, docs, and configuration relevant to the tasks.
- Architecture understanding: high. Lucius identified modular monolith, PostgreSQL/SQLAlchemy/Alembic persistence, explicit candidate/canonical boundaries, provider abstraction, and no-autonomy constraints.
- Unsupported claims: low. Material statements in this report cite repository paths and line ranges; assumptions are labeled.
- Hallucinated paths: none material in the generated plans after Lucius validation/classification.
- Provenance completeness: high. Snapshot id, commit, manifest hash, evidence counts, task id, TaskContract id, plan id, blockers, and source evidence paths are preserved.

## Phase 1.11 Limitations

- This pilot does not add repository-write autonomy.
- This pilot does not implement an Engineering Executor.
- Darwin remains externally owned and unchanged.
- Historical planning cannot perfectly simulate a pre-implementation repository because Lucius v0.1 snapshots the current HEAD only.
- The model path used a deterministic structured adapter through `ModelGateway`, not a live external LLM.

## Closure Results

Canonical status: `NON_CANONICAL_DIRTY_RUN`.

Autonomy recommendation: `NOT_READY_FOR_WRITE_AUTONOMY`.

Captured durable pilot identifiers:

- Darwin snapshot id: `LSNAP_000001`
- Pilot B EngineeringPlan id: `LPLAN_000001`
- Pilot C EngineeringPlan id: `LPLAN_000002`
- Darwin manifest hash: `aeec9ac56ea6dbc43fcbe761140ad0b161b7b7a1294dc06c438542ddd033dcd9`
- Darwin HEAD: `8003624844e08d67a38406b433098aafd8ad5945`

Explicitly not captured:

- Pilot A numeric evidence/provenance quality metrics: `NOT_CAPTURED`
- Pilot A unsupported-claim count: `NOT_CAPTURED`
- Pilot B formal deterministic evaluation artifact: `NOT_CAPTURED`
- Pilot B human rubric scores: `NOT_CAPTURED`
- Pilot C formal deterministic evaluation artifact: `NOT_CAPTURED`
- Pilot C human usefulness score: `NOT_CAPTURED`
- Learning candidates persisted from the pilot: `NOT_CAPTURED`
- Pre-pilot `LUCIUS_CORE_BENCH_V0_1` formal result: `NOT_CAPTURED`
- Post-pilot `LUCIUS_CORE_BENCH_V0_1` formal result: `NOT_CAPTURED`
- Benchmark regression status: `NOT_CAPTURED`
- Benchmark hard-gate status and release decision: `NOT_CAPTURED`

Corrections required before any write-autonomy decision:

- Canonical clean target-repository pilot rerun required.
- Formal deterministic Pilot B and Pilot C evaluation metrics required.
- Explicit human rubric capture required.
- Historical planning must use pre-implementation repository states to prevent look-ahead contamination.
- Pre-pilot and post-pilot `LUCIUS_CORE_BENCH_V0_1` results must be captured as benchmark artifacts, not inferred from ordinary pytest output.
- Autonomy/release gate must be machine-computable from canonical evidence.

## Verification

- Lucius full tests: `121 passed in 83.37s`
- Darwin HEAD before: `8003624844e08d67a38406b433098aafd8ad5945`
- Darwin HEAD after: `8003624844e08d67a38406b433098aafd8ad5945`
- Darwin working tree before: `## main...origin/main
 M .env.example
 M src/darwin/cli/app.py
 M src/darwin/config/settings.py
 M src/darwin/db/__init__.py
 M src/darwin/db/models.py
 M src/darwin/orchestration/service.py
?? alembic/versions/0010_narrative_synthesis.py
?? src/darwin/narrative_synthesis/`
- Darwin working tree after: `## main...origin/main
 M .env.example
 M src/darwin/cli/app.py
 M src/darwin/config/settings.py
 M src/darwin/db/__init__.py
 M src/darwin/db/models.py
 M src/darwin/orchestration/service.py
?? alembic/versions/0010_narrative_synthesis.py
?? src/darwin/narrative_synthesis/`
