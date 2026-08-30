# Changelog

## 0.7.0 — Phase 1.10 Evaluation Harness & Lucius Benchmark v0.1

- Added versioned EvaluationCase and EvaluationSuite models with permanent Golden Engineering Cases.
- Added persisted EvaluationRuns and EvaluationCaseResults with target commit, dirty state, config hash, scores, gates, regressions, and reports.
- Added deterministic evaluators for evidence, scope, acceptance coverage, assumptions, risk, authority, tests, documentation, memory, privacy, and provenance.
- Added weighted scoring, hard safety/integrity gates, release decisions, baseline policy, and regression comparison.
- Added `LUCIUS_CORE_BENCH_V0_1` with eight permanent golden cases.
- Added machine-readable and Markdown benchmark reporting plus `python -m lucius.evaluation.cli run --suite LUCIUS_CORE_BENCH_V0_1`.
- Added Alembic migration `0007_evaluation_harness`.
- Added ADR-026 and Phase 1.10 benchmark documentation.

## 0.6.0 — Phase 1.9 Engineering Planner v0.1

- Added evidence-bound PlanningContext construction over Tasks, TaskContracts, RepositorySnapshots, EvidenceReferences, and MemoryEntries.
- Added ModelGateway-backed EngineeringPlannerService with structured plan output validation.
- Added persisted, versioned EngineeringPlans with evidence, memory, model execution, task contract, snapshot, risk, authority, and audit provenance.
- Added deterministic validation for references, acceptance coverage, support status, affected-file classification, risk floors, authority floors, assumptions, and unknowns.
- Added explicit replanning, supersession, and rejection support without overwriting plan history.
- Added memory/evidence conflict surfacing where current repository evidence wins and stale memory is marked for revalidation.
- Added Alembic migration `0006_engineering_planner`.
- Added deterministic Phase 1.9 planner fixtures and tests.
- Added Phase 1.9 documentation and extended ADR-023 for persisted evidence-bound planning.

## 0.5.0 — Phase 1.8 Model Gateway & Capability Routing

- Added provider-independent model provider/profile registration.
- Added deterministic capability routing with privacy, cost, quality, context, provider status, model status, preference, and tie-breaking rules.
- Added provider adapter contracts and a deterministic MockProvider for offline tests.
- Added gateway execution with structured response validation, explicit fallback, provenance, usage, cost, latency, and failure classification.
- Added persisted model providers, profiles, and execution records through Alembic migration `0005_model_gateway`.
- Added model audit events that avoid raw prompt/response and secret persistence.
- Added model-inference documentation to keep responses separate from evidence and memory.
- Added ADR-025 and Phase 1.8 implementation documentation.

## 0.4.0 — Phase 1.7 Memory v0.1

- Added persisted MemoryEntries with type, scope, project, provenance,
  confidence, validation status, tags, revalidation, and supersession fields.
- Added persisted LearningCandidates with provenance, lifecycle state,
  proposed scope, source classification, and sanitization status.
- Added deterministic memory retrieval by scope, project, type, status, tags,
  and query terms.
- Added supersession, contradiction, deprecation, and revalidation handling
  without erasing memory history.
- Added documentation-derived learning candidates, failure memory capture, and
  human correction candidates.
- Added a baseline Knowledge Firewall for private, abstractable, and
  globally safe knowledge with explicit global promotion rules.
- Added memory and learning audit events.
- Added Alembic migration `0004_memory_learning`.
- Added ADR-024 and Phase 1.7 implementation documentation.

## 0.3.0 — Phase 1.6 Evidence & Retrieval v0.1

- Added deterministic retrieval request construction and query-term derivation.
- Added repository candidate discovery for code, documentation, tests, config, and Git state.
- Added explainable ranking with explicit lexical weights and deterministic tie-breaking.
- Added persisted EvidenceReferences with repository, snapshot, task, path, hash, snippet, score, and reasons.
- Added evidence staleness checks for current, changed, and missing sources.
- Added TechnicalContextPackage runtime objects with context budgets and coverage summaries.
- Added security exclusions for sensitive, private-key, binary, oversized, traversal, and symlink-escape cases.
- Added retrieval/evidence audit events.
- Added Alembic migration `0003_evidence_retrieval`.
- Added retrieval benchmark fixture for snapshot reuse understanding.
- Added ADR-023 and Phase 1.6 implementation documentation.

## 0.2.0 — Phase 1.5 Project Registry & Task Contract

- Added Project Registry service with lifecycle transitions and deterministic slugs.
- Added explicit repository attachment records for projects.
- Added Task, TaskContract, TaskRun, and documentation completion persistence.
- Added deterministic TaskContract validation for repositories, authority, dependencies, and documentation targets.
- Added structured blockers and controlled Task lifecycle transitions.
- Added TaskRun snapshot binding and actor provenance.
- Added documentation completion gate before Task completion.
- Added Alembic migration `0002_project_task_contracts`.
- Added deterministic tests for Phase 1.5 behavior while preserving Phase 1.4B tests.
- Added Phase 1.5 implementation documentation and updated architecture/foundation docs.

## 0.1.0 — Phase 1.4B Repository Core Implementation #1

- Added Python package structure for domain, persistence, repositories, and audit.
- Added deterministic Lucius public ID generation.
- Added SQLAlchemy 2.x SQLite persistence and Alembic baseline migration.
- Added project and external local Git repository registration.
- Added read-only Local Git repository adapter with workspace and path safety.
- Added deterministic repository inspection, manifests, hashing, snapshots, and snapshot reuse.
- Added sensitive-file, binary-file, and oversized-file protections.
- Added audit events for project, repository, inspection, snapshot creation, and snapshot reuse.
- Added deterministic unit and integration tests for repository core invariants.
- Added ADR-019 through ADR-022 and Phase 1.4B implementation report.

## 0.0.1 — Phase 0 Foundation

- Established Lucius mission and project charter.
- Defined organizational relationship with Alfred, Darwin, and DFG engines.
- Defined authority levels L0-L3.
- Defined learning and memory principles.
- Defined workspace and client isolation.
- Defined security baseline.
- Defined engineering operating model.
- Defined documentation policy.
- Created initial ADR register.
- Established Phase 1 entry criteria.
