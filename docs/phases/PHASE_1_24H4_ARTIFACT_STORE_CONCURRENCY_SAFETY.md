# Phase 1.24H4 Artifact Store Concurrency Safety

Phase 1.24H4 repairs `LPLEARN_000057`, identified during the final compact
extended controlled multi-project pilot attempt.

The pilot stopped before target mutation after concurrent canonical
repository-state capture commands independently allocated `LRSTATE_000058`.
SQLite rejected the duplicate inserts, so the store remained intact, but the
behavior was a HIGH blocker for longer low-supervision single-dispatcher
operation.

## Scope

This phase is Lucius-internal only. It does not start another operational
pilot, mutate external target repositories, push, merge, deploy, expand
authority, or authorize multi-worker implementation.

## Repair

Canonical public ID allocation now reserves IDs through an atomic SQLite
counter update. `next_id` still repairs sparse or manually drifted historical
IDs by scanning persisted public IDs first, then inserts a missing counter row
with `OR IGNORE` and advances `id_counters` with a single `UPDATE ... RETURNING`
statement. Concurrent writers therefore serialize at SQLite's write boundary
and receive distinct IDs.

SQLite engines now use a bounded busy timeout for lock contention. If another
writer temporarily holds the database write lock, canonical allocation waits
within that bound rather than racing a stale counter.

The pilot CLI now wraps artifact-store-writing entrypoints in an exclusive
artifact-store write lock. This enforces one-dispatcher serialization for
canonical operations that mutate `data/lucius-pilots.sqlite`, including
repository-state capture, benchmarks, freezes, evaluations, rubrics, release
gate computation, and queue-start operations.

## Verification

Adversarial process-level tests cover:

- simultaneous ID allocation;
- simultaneous canonical CLI repository-state captures;
- direct service repository-state captures from multiple processes;
- rollback during allocation;
- crash/restart equivalent before commit;
- stale counter plus concurrent writers;
- SQLite busy/lock contention;
- CLI single-dispatcher write-lock waiting;
- ordinary sequential ID allocation.

The tests verify unique public IDs, correct repository identity and manifests,
fresh-session readback, SQLite integrity, no FK debt in fresh stores, and no
regression in ordinary sequential operation.

## Result

`LPLEARN_000057` is resolved when focused concurrency tests, Phase 1.24H
regression tests, adjacent regression tests, full Lucius tests, and PRE/POST
`LUCIUS_CORE_BENCH_V0_1` comparison all pass with `NO_REGRESSION`.
