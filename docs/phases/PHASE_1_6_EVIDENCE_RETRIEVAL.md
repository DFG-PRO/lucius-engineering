# Phase 1.6: Evidence & Retrieval v0.1

**Status:** Implemented
**Date:** 2026-08-29

## Retrieval Architecture

Phase 1.6 adds Lucius's first deterministic technical-context retrieval
layer.

Implemented flow:

```text
Task
  -> TaskContract
  -> RepositorySnapshot
  -> RetrievalRequest
  -> Candidate Sources
  -> Deterministic Ranking
  -> EvidenceReference(s)
  -> TechnicalContextPackage
```

The retrieval layer uses the existing repository registration, snapshot,
workspace safety, TaskContract, TaskRun, SQLAlchemy, Alembic, and audit
architecture. It does not call an LLM, create embeddings, use a vector
database, or modify repositories.

## Query Derivation

RetrievalRequest query terms are derived deterministically from:

- Task title
- Task objective
- active TaskContract objective
- acceptance criteria statements
- explicit technical terms supplied by the caller

Normalization uses lowercase comparison, punctuation-aware tokenization,
duplicate removal, stop-word suppression, and preservation of useful
technical identifiers such as filenames, extensions, snake_case identifiers,
and long CamelCase terms.

## Candidate Discovery

Candidates are discovered only from authorized RepositorySnapshots. The
stored snapshot manifest defines the candidate set. Current repository
content is read only through the Phase 1.4B Local Git adapter, so workspace
and repository path safety remain in force.

Supported source types:

- CODE
- DOCUMENTATION
- TEST
- CONFIG
- GIT

MEMORY and EXTERNAL exist only as future-compatible enum values and do not
participate in Phase 1.6 retrieval.

## Ranking Signals

Ranking is deterministic and explainable. Scores are ranking scores, not
confidence probabilities.

Weights:

- exact path/name match: 70
- basename match: 48
- technical identifier match: 24
- path token match: 14
- documentation relationship: 5
- test relationship: 8
- configuration relationship: 6
- generic content match: 2

Source type base weights:

- CODE: 6
- TEST: 5
- DOCUMENTATION: 4
- CONFIG: 4
- GIT: 3

Tie-breaking is deterministic by score, path, repository ID, then snapshot ID.

## EvidenceReference Model

EvidenceReferences are persisted with:

- project ID
- repository ID
- snapshot ID
- task ID
- optional task run ID
- source type
- path
- line range where available
- content hash
- bounded snippet
- optional claim
- relevance score
- match reasons
- capture timestamp

Evidence records answer where the evidence came from, which repository and
snapshot it belongs to, what source state it describes, and why it was
considered relevant.

## Snapshot Provenance

Retrieval binds to explicit RepositorySnapshot IDs. Before evidence capture,
the current repository state is compared against the bound snapshot.

If commit SHA, branch, dirty state, or manifest hash differ, retrieval records
`SNAPSHOT_STALE` and does not silently capture evidence against the stale
snapshot.

## Stale Evidence Behavior

Evidence is historical and immutable. Evidence status checks return:

- CURRENT when the current source hash still matches
- STALE when the source exists but content hash changed
- MISSING when the source path no longer exists or is unsafe

Changed repository state requires new evidence rather than rewriting old
EvidenceReferences.

## Context Budgeting

Retrieval supports:

- `max_results`
- `max_total_bytes`
- `max_snippet_bytes`

Ranking occurs before truncation. Highest-ranked evidence is selected until
the budget is reached. Snippets are bounded per source, and package warnings
include `CONTEXT_BUDGET_REACHED` when limits are hit.

## Coverage Semantics

TechnicalContextPackage coverage is deterministic and limited.

Acceptance criteria use:

- EVIDENCE_FOUND
- NO_EVIDENCE_FOUND

Query terms report match counts. Coverage does not mean Lucius understands,
proves, or solves the task.

## Security Exclusions

Retrieval inherits repository safety rules:

- no path traversal
- no symlink escape
- no binary content evidence
- no oversized content evidence
- no sensitive file content evidence
- no generated/excluded tree ingestion

Sensitive file presence may be observed through repository metadata, but
content is not read or persisted as evidence.

## Audit Behavior

Added audit events:

- RETRIEVAL_STARTED
- RETRIEVAL_COMPLETED
- RETRIEVAL_EMPTY
- EVIDENCE_CAPTURED
- CONTEXT_PACKAGE_CREATED
- SNAPSHOT_STALE_DETECTED

Audit events preserve project, task, run, repository, snapshot, counts, and
warnings without logging sensitive content.

## Migration

Added Alembic revision:

- `0003_evidence_retrieval`

New table:

- `evidence_references`

TechnicalContextPackage remains a runtime object in v0.1 because persisted
EvidenceReferences are the durable provenance layer.

## Tests / Results

Commands run:

```bash
.venv/bin/python -m pytest -q
LUCIUS_DATABASE_URL=sqlite:////private/tmp/lucius_phase16_final_clean_head.sqlite3 .venv/bin/alembic upgrade head
LUCIUS_DATABASE_URL=sqlite:////private/tmp/lucius_phase16_final_from_0002.sqlite3 .venv/bin/alembic upgrade 0002_project_task_contracts
LUCIUS_DATABASE_URL=sqlite:////private/tmp/lucius_phase16_final_from_0002.sqlite3 .venv/bin/alembic upgrade head
```

Results:

- `67 passed in 61.46s (0:01:01)`
- clean database upgraded through `0001_repository_core`,
  `0002_project_task_contracts`, and `0003_evidence_retrieval`
- explicit `0002_project_task_contracts -> 0003_evidence_retrieval`
  upgrade succeeded

## Benchmark Fixture

Added the first internal retrieval benchmark fixture for:

Title:

```text
Change snapshot reuse behavior
```

Objective:

```text
Modify repository snapshot reuse logic while preserving deterministic
manifest identity.
```

The fixture verifies that general ranking heuristics rank repository
hashing/snapshot implementation and snapshot tests above unrelated files.
This is an early precursor to LUC-BENCH-001 Repository Understanding.

## Deviations

TechnicalContextPackage is not persisted in v0.1. EvidenceReferences are
persisted because they are the durable provenance layer.

Search is implemented with safe Python scanning over bounded candidate files
rather than `git grep`, which keeps all repository safety checks centralized
in the existing Local Git adapter.

## Limitations

No semantic search, embeddings, AST parsing, LSP integration, or memory
retrieval is implemented.

Ranking is lexical and heuristic. It is useful for deterministic source
selection, but it does not claim semantic completeness.

## Final Status

Phase 1.6 Evidence & Retrieval v0.1 is complete.

This does not begin Memory, Model Gateway, Engineering Planner, Project
Compiler, or autonomous execution work.

## Next Steps

Review and commit the evidence-bound retrieval layer before starting the next
approved Phase 1 package.
