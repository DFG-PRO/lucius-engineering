# Phase 1.4B: Repository Core Implementation #1

**Status:** Implemented
**Date:** 2026-08-29

## Implementation

Phase 1.4B adds the first deterministic technical core for Lucius.

Implemented:

- domain enums and Pydantic contracts
- deterministic public ID generation
- SQLite persistence through SQLAlchemy 2.x
- Alembic baseline migration
- project registration
- external local Git repository registration
- read-only Local Git repository adapter
- workspace boundary enforcement
- safe file reads with traversal, size, binary, and sensitive-file guards
- deterministic documentation, test, configuration, and technology discovery
- repository manifest and SHA-256 manifest hashing
- repository snapshots with reuse when no material change exists
- audit events for registration, inspection, snapshot creation, and reuse
- deterministic unit and integration tests

## Architecture Impact

Lucius now stores observations about external repositories without owning or
copying repository content. Repository registrations point to canonical
external paths, and snapshots persist structured metadata sufficient for
later comparison.

The code is organized as a modular monolith:

- `domain` for enums, IDs, and external contracts
- `persistence` for SQLAlchemy models, sessions, and service repositories
- `repositories` for adapter interfaces, local Git inspection, hashing, and schemas
- `audit` for audit-event recording

## Migrations

Initial Alembic revision:

- `0001_repository_core`

Tables:

- `id_counters`
- `projects`
- `repository_registrations`
- `repository_snapshots`
- `audit_events`

## Tests

The test suite covers:

- project registration
- canonical ID generation
- repository registration
- duplicate repository registration behavior
- valid local Git detection
- non-Git path rejection
- outside-workspace rejection
- path traversal rejection
- safe text file reading
- oversized file rejection
- binary file rejection
- sensitive file content protection
- branch and commit SHA detection
- dirty, modified, and untracked state detection
- document, test, and technology discovery
- manifest hashing determinism
- snapshot creation
- unchanged snapshot reuse
- changed repository snapshot creation
- audit event creation
- read-only adapter surface checks

## Verification

Commands run:

```bash
.venv/bin/python -m pytest -q
LUCIUS_DATABASE_URL=sqlite:////private/tmp/lucius_phase14b_test6.sqlite3 .venv/bin/alembic upgrade head
```

Results:

- `22 passed in 9.63s`
- Alembic upgraded a clean SQLite database to `0001_repository_core`

## Deviations

The brief allowed unused Phase 1 domain objects to be represented without
full persistence. This implementation defines MVP Pydantic contracts for
Task, TaskRun, EvidenceReference, EngineeringPlan, MemoryEntry,
LearningCandidate, EvaluationRun, and AuditEvent, but persists only the
mandatory Phase 1.4B tables.

`SnapshotMode.DEEP` currently resolves to STANDARD inspection behavior. The
contract exists, but advanced deep analysis is intentionally excluded.

## Limitations

The Local Git adapter is read-only.

Snapshot identity is based on commit SHA, branch, dirty summary, Git status,
file sizes, selected content hashes, and file metadata. It does not parse
source-code semantics.

Technology discovery is deterministic and manifest-based. It does not use
LLMs or semantic retrieval.

## Final Status

Phase 1.4B Repository Core Implementation #1 is complete.

This does not mark all of Phase 1 complete.

## Next Steps

Proceed to the next approved Phase 1 work package after review of the
repository core, migration, and test coverage.
