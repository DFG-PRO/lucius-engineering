# Bounded Continuation & Dynamic Task Feeder Architecture

**Document Version:** 1.0.0  
**Status:** CANONICAL  
**Last Updated:** 2026-09-18 (Long Engineering Shift 08)  

---

## 1. Overview & Context

To support multi-hour unattended Travel Mode operations across DFG Universe projects (`LUCIUS`, `DARWIN`, `BILLY`), Lucius requires a continuation runtime that does not halt whenever a single task completes, encounters a non-fatal blocker, or empties an active workflow queue.

This document defines the canonical architecture and contract implemented in:
- `src/lucius/runtime/continuation.py`: `BoundedContinuationService`, `SessionBudget`
- `src/lucius/runtime/feeder.py`: `DarwinBacklogFeeder`, `NormalizedTaskEnvelope`
- `src/lucius/repositories/worktree_hygiene.py`: `WorktreeHygieneService`
- `src/lucius/runtime/launcher.py`: `TravelLauncher`

---

## 2. Core Continuation Semantics

### A. Autonomous Reselection on Completion
When a task finishes execution and satisfies deterministic acceptance verification:
1. The task is marked `COMPLETED` in the database.
2. The runtime immediately executes a dispatch reselection pass across available project workflows.
3. The next eligible task is dispatched without waiting for external operator prompts.

### B. Non-Fatal Blocker Continuation
When a task cannot execute due to missing authority (e.g. Class C mutation attempted unattended) or missing external evidence:
1. The task is marked `BLOCKED`, and the exact blocking constraint is recorded in `TaskORM.blocking_reason`.
2. The runtime does NOT halt execution if `stop_on_block=False` (default for Travel Mode).
3. The dispatcher evaluates other tasks in the queue and advances to the next independent eligible task.

### C. Dynamic Feeder Consultation
When all tasks in active workflows are completed or blocked:
1. The dispatcher queries registered task feeders (such as `DarwinBacklogFeeder`).
2. The feeder evaluates eligible tasks (matching `READY` status, 0 blockers, allowed authority class).
3. Discovered tasks are normalized into `NormalizedTaskEnvelope` records and ingested idempotently into the target project workflow.
4. Execution resumes immediately on newly fed work.

### D. Safe IDLE Transition
When all active workflow queues are empty AND registered feeders return 0 new eligible tasks:
1. The session transitions cleanly to `IDLE` state.
2. Stop reason is set to `IDLE_NO_ELIGIBLE_WORK`.
3. Background processes and database sessions close gracefully without polling loops or CPU spin.

---

## 3. Dynamic Backlog Feeder (`DarwinBacklogFeeder`)

The feeder bridges canonical backlog sources into Lucius task workflows under strict safety guarantees:
1. **Zero Source Mutation**: Feeder operates in read-only mode against source repositories (e.g. Darwin `master_backlog.py`). It never alters backlog files or git states.
2. **Deterministic Priority Ordering**: Discovered items are sorted deterministically: `P0_NOW` (0) > `P0` (1) > `P1` (2) > `P2` (3).
3. **Fail-Closed Authority Filtering**: Any item requiring authority exceeding the session tier (e.g. Class C/D) is rejected from unattended feeding.
4. **Idempotency**: Ingested tasks receive a unique deduplication key (`darwin:<item_id>`). If a task already exists in any workflow state (`READY`, `IN_PROGRESS`, `COMPLETED`, `BLOCKED`), it is never re-ingested.

---

## 4. Session Budget & Safety Envelope

To prevent runaway execution loops, resource exhaustion, or cascade failures, every continuation session is governed by a strict `SessionBudget`:

- `max_cycles`: Hard cap on total task iterations executed (e.g. 25 cycles).
- `max_wall_seconds`: Hard timeout on total session execution time (e.g. 14,400s / 4.0 hours).
- `max_consecutive_failures`: Safety tripwire halting the session if unrecoverable errors occur repeatedly (e.g. 3 consecutive failures).

When any budget threshold is hit, the session halts immediately with a clear stop reason (`MAX_CYCLES_REACHED`, `WALL_CLOCK_EXCEEDED`, or `CONSECUTIVE_FAILURES_EXCEEDED`).

---

## 5. Ephemeral Worktree Hygiene

`WorktreeHygieneService` protects disk and git metadata from worktree sprawl:
- Classifies worktrees into `ACTIVE`, `DIRTY`, `REFERENCED_BY_OPEN_WORKFLOW`, `CLEAN_PRUNABLE`, and `UNKNOWN`.
- Verifies that main repository roots are never pruned.
- Detects orphaned `/private/tmp/` worktree paths whose directories have been removed by OS cleanup.
- Strictly guards against unattended deletion of dirty or open-workflow worktrees.

---

## 6. Travel Launcher UX

The `TravelLauncher` provides a single preflight-and-launch entry point for travel operations:

```bash
# Verify environment readiness without launching
python -m lucius.runtime.launcher --preflight-only

# Launch a 4-hour bounded session with up to 25 cycles
python -m lucius.runtime.launcher --hours 4.0 --max-cycles 25
```
