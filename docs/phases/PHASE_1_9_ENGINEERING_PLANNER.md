# Phase 1.9: Engineering Planner v0.1

Phase 1.9 adds Lucius's first evidence-bound Engineering Planner. The planner
receives an authorized Task and TaskContract, binds explicit repository
snapshots, retrieves current evidence, retrieves relevant memory, builds a
bounded PlanningContext, invokes ModelGateway, validates structured model
output, and persists a versioned proposed EngineeringPlan.

The planner does not write repositories, execute code changes, open pull
requests, deploy, or approve critical work.

## Architecture

Planning is implemented in `src/lucius/planning/`:

- `schemas.py`: PlanningContext, structured model output, EngineeringPlan,
  blockers, steps, coverage, risk, docs, and replan schemas.
- `context.py`: deterministic PlanningContext construction from Task,
  TaskContract, RepositorySnapshots, EvidenceReferences, and MemoryEntries.
- `validation.py`: evidence/memory reference validation, acceptance coverage,
  support classification, affected-file classification, risk floors, and
  authority floors.
- `persistence.py`: immutable/versioned EngineeringPlan persistence.
- `service.py`: orchestration through ModelGateway.

The planner depends on ModelGateway contracts and never imports provider SDKs.

## PlanningContext

PlanningContext contains task intent, active TaskContract fields, snapshot
metadata, ranked current evidence, ranked memory, acceptance criteria,
constraints, authority level, environment, documentation requirements, warnings,
omitted counts, and context byte counts.

Context is bounded by `PlanningContextBudget`: `max_evidence_items`,
`max_memory_items`, `max_total_context_bytes`, and `max_per_source_bytes`.
When evidence or memory is omitted, the context records
`PLANNING_CONTEXT_BUDGET_REACHED` and audit emits
`PLANNING_CONTEXT_TRUNCATED`.

## Epistemic Distinctions

The planner preserves separate categories:

- Evidence: current repository/source reality.
- Memory: historical/scoped Lucius knowledge.
- Inference: model/planner-generated conclusions.
- Assumption: necessary but unverified planning premise.
- Unknown: information insufficient to determine.

ModelResponse is never converted into EvidenceReference or MemoryEntry.

## Memory And Evidence

Memory retrieval prefers validated project memory, then broader allowed DFG,
domain, and global memory. Superseded, contradicted, and deprecated memory is
excluded by default. Revalidation-required memory can appear, but is explicitly
warned.

When memory conflicts with current repository evidence, the context records
`MEMORY_CONFLICT_WITH_REPOSITORY`, repository evidence wins, and the memory is
marked for revalidation.

## ModelGateway Request

Planner requests require `PLANNING` and `STRUCTURED_OUTPUT`. Complex tasks may
also request `CODE_REASONING` and `HIGH_REASONING`.

Privacy derives from the project context. Client projects are treated as
confidential. Privacy is not downgraded for model availability.

The model receives a bounded JSON planning contract with task, contract,
snapshot metadata, current evidence, historical memory, warnings, and expected
structured fields.

## Validation

Planner output is validated with Pydantic and deterministic Lucius checks.
Validation rejects plans that reference missing evidence, unauthorized memory,
wrong-project evidence, malformed steps, verified assumptions, missing
acceptance coverage, or uncovered acceptance criteria.

Each step is classified as `SUPPORTED`, `PARTIALLY_SUPPORTED`, `INFERRED`, or
`BLOCKED_BY_UNKNOWN`.

Affected files are classified as `EXISTING_VERIFIED`, `LIKELY_EXISTING`,
`NEW_PROPOSED`, or `UNKNOWN`.

## Risk And Authority

Model risk is advisory. Lucius applies deterministic floors from task
complexity, affected scope, migrations, schema work, security/credential
signals, production impact, destructive/irreversible actions, and blocking
unknowns.

Authority uses L0-L3. If the plan requires authority above the TaskContract,
the plan may still be persisted as useful planning output, but it receives
`AUTHORITY_ESCALATION_REQUIRED` and remains only proposed.

## Tests And Documentation

Plans include test recommendations such as static, unit, integration,
regression, system, or real-world validation where relevant. Documentation
requirements are preserved from TaskContract policy and planner output.

## Versioning And Replanning

EngineeringPlans are versioned per task. Replanning creates a new plan and
marks the previous plan `SUPERSEDED` without overwriting history.

## Provenance And Audit

Every persisted plan stores Task, TaskContract ID/version, repository snapshot
IDs, EvidenceReference IDs, MemoryEntry IDs, ModelExecution IDs, planner
version, timestamp, creator, validation warnings, and blockers.

Audit events include planning start, context creation/truncation, planner model
request, plan creation/proposal, invalid plan, planning block, authority
escalation, plan supersession, plan rejection, and memory conflict.

Audit metadata avoids hidden chain-of-thought and raw prompt storage.

## Migration

Alembic migration `0006_engineering_planner` adds `engineering_plans` with JSON
columns for v0.1 structured plan internals and scalar columns for identity,
status, provenance, risk, authority, versioning, and timestamps.

## Benchmark Fixtures

The deterministic test suite includes snapshot reuse planning, memory conflict,
and authority-escalation fixtures.

## Test Results

Focused Phase 1.9 tests pass with `10 passed`. Full repository tests pass with
`109 passed`.

## Limitations

The planner uses deterministic lexical retrieval and JSON v0.1 persistence. It
does not include embeddings, a scoring/evaluation harness, normalized step
tables, real provider calls, repository writes, executor behavior, automatic
approval, or multi-agent orchestration.
