# Phase 1.5: Project Registry & Task Contract

**Status:** Implemented
**Date:** 2026-08-29

## Implementation

Phase 1.5 connects repository reality to structured engineering intent.

Implemented:

- project registry service
- deterministic project slug handling
- explicit project lifecycle transitions
- repository attachment to projects
- Task persistence and controlled lifecycle
- TaskContract persistence and deterministic validation
- structured acceptance criteria records
- authority and allowed-action validation
- direct dependency validation
- structured blocker persistence
- TaskRun persistence with actor provenance
- repository snapshot binding for TaskRuns
- documentation completion gate
- documentation completion evidence records
- audit events for project, repository attachment, task, contract, run, and lifecycle actions
- deterministic unit and integration tests

## Architecture Impact

The canonical operational relationship is now:

```text
Project
  -> RepositoryRegistration(s)
  -> Task
     -> TaskContract
        -> TaskRun
           -> RepositorySnapshot
```

Project remains distinct from Repository. Repositories stay canonical outside
Lucius and are attached by reference. Task represents durable engineering
intent. TaskContract defines executable boundaries and acceptance criteria.
TaskRun records one execution attempt and preserves the snapshot used when
repository analysis is involved.

## Schema / Migration

Added Alembic revision:

- `0002_project_task_contracts`

Schema changes:

- extended `projects` with slug, organization, project type, workspace scope,
  documentation policy, and default authority level
- added `project_repository_attachments`
- added `tasks`
- added `task_contracts`
- added `task_runs`
- added `documentation_completions`

The migration upgrades from `0001_repository_core` to head and also supports
a clean base-to-head SQLite upgrade.

## Task Lifecycle

Implemented lifecycle:

```text
DRAFT
 -> READY
 -> RUNNING
 -> IMPLEMENTATION_COMPLETE
 -> DOCUMENTATION_PENDING
 -> COMPLETE
```

Alternative transitions:

- `RUNNING -> BLOCKED`
- `RUNNING -> FAILED`
- `DRAFT/READY/RUNNING/BLOCKED -> CANCELLED`
- `BLOCKED/FAILED -> READY` when the contract validates again

Callers cannot set Task status arbitrarily; transitions are exposed through
TaskService methods.

## Authority Validation

TaskContract authority cannot exceed Task authority.

Allowed actions are checked against a small explicit policy:

- L0 permits read-only and non-mutating local actions
- L1 permits controlled development-write contracts for future executors
- L2 gates commit/staging-class actions
- L3 gates production-class environments

Policy failures return structured blockers such as
`AUTHORITY_INSUFFICIENT`.

## Documentation Completion Gate

If `documentation_required = true`, a Task cannot move directly from
`IMPLEMENTATION_COMPLETE` to `COMPLETE`.

Required sequence:

```text
IMPLEMENTATION_COMPLETE
 -> DOCUMENTATION_PENDING
 -> COMPLETE
```

Completion requires persisted documentation evidence with completed targets
and evidence references. Missing proof returns `DOCUMENTATION_REQUIRED`.

## TaskRun Provenance

TaskRun records preserve:

- actor
- model provider and model name when applicable
- input and output summaries
- status and failure reason
- associated repository snapshot when applicable

Historical Codex work can be represented as `Actor.CODEX` without claiming
Lucius performed it.

## Tests / Results

Commands run:

```bash
.venv/bin/python -m pytest -q
LUCIUS_DATABASE_URL=sqlite:////private/tmp/lucius_phase15_final_clean_head.sqlite3 .venv/bin/alembic upgrade head
LUCIUS_DATABASE_URL=sqlite:////private/tmp/lucius_phase15_final_from_0001.sqlite3 .venv/bin/alembic upgrade 0001_repository_core
LUCIUS_DATABASE_URL=sqlite:////private/tmp/lucius_phase15_final_from_0001.sqlite3 .venv/bin/alembic upgrade head
```

Results:

- `50 passed in 10.83s`
- clean database upgraded through `0001_repository_core` and `0002_project_task_contracts`
- explicit `0001_repository_core -> 0002_project_task_contracts` upgrade succeeded

## Deviations

Acceptance criteria, contract list fields, repository IDs, dependency IDs,
allowed actions, allowed tools, documentation targets, and stop conditions
use JSON columns. This is intentional for SQLite Phase 1.5 and avoids early
over-normalization.

Project/repository attachment is represented by a small join table while
retaining the Phase 1.4B `repository_registrations.project_id` field for
compatibility.

## Limitations

No executor is implemented.

Allowed write actions in a TaskContract define future permission boundaries;
they do not grant current runtime write capability.

Dependency validation rejects missing dependencies, self-dependencies,
incomplete dependencies, and direct cycles only. A full execution graph is
out of scope.

Documentation completion records proof but does not generate documentation.

## Final Status

Phase 1.5 Project Registry & Task Contract is complete.

This does not begin retrieval, planning, autonomous execution, or repository
write operations.

## Next Steps

Review and commit the Phase 1.5 operational work-management layer before
starting the next approved Phase 1 package.
