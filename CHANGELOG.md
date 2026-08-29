# Changelog

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
