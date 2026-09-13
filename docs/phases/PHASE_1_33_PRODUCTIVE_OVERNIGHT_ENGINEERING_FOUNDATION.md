# Phase 1.33 Productive Overnight Engineering Foundation

Status: IMPLEMENTED IN LUCIUS; READY FOR REVIEW; NOT FORMALLY CLOSED

Phase 1.33 moves Night Shift from exact-output synthetic tasks toward bounded productive unattended engineering while preserving fail-closed authority boundaries.

## 1.33A Richer Deterministic Acceptance

Deterministic acceptance checks remain explicit, typed, and non-process-executing. The supported check types are:

- `exact_file_content`: `path` and string `expected_text` are required. The file must be an authorized mutation path and its UTF-8 content must exactly match `expected_text`.
- `file_exists`: `path` is required. `expected_text` is rejected. The target must be an authorized mutation path and exist as a regular file.
- `file_contains`: `path` and string `expected_text` are required. The target must be an authorized mutation path, readable as UTF-8 text, and contain `expected_text`.
- `file_not_contains`: `path` and string `expected_text` are required. The target must be an authorized mutation path, readable as UTF-8 text, and not contain `expected_text`.

Unknown types, malformed type-specific arguments, absolute paths, traversal paths, and paths outside the frozen authorized mutation scope fail closed. Multiple distinct checks on the same path are allowed. Exact duplicate checks are rejected deterministically.

Every layer that consumes deterministic acceptance now uses the same explicit validation semantics:

- planning schema and validation
- PlanFreeze semantic validation
- runtime router frozen mutation scope handoff
- Ollama deterministic mutation verification
- L1 canonical integration
- L2 controlled local commit

L1 canonical integration still captures candidate content after candidate verification and re-verifies the captured payload before canonical write. Changed paths must be covered by at least one frozen deterministic acceptance check; that check no longer has to be `exact_file_content`.

## 1.33B Contained Failure Continuation

Night Shift now takes a pre-cycle containment snapshot before each runtime attempt. A task-local failure may continue only when the supervisor can prove containment.

Containment requires:

- canonical HEAD did not advance from the frozen baseline;
- canonical tracked and staged state was clean before failure and remains clean after failure;
- pre-existing untracked canonical state captured before the attempt still matches by hash;
- no unauthorized canonical mutation remains;
- no L1 integration or L2 commit was accepted for the failed task;
- the candidate workspace is clean after failure, or the failed candidate is quarantined by blocking its workflow and recording a checkpoint;
- the failure does not require manual reconciliation or escalation.

If containment cannot be proven, Night Shift hard-stops. Manual reconciliation and integrity violations still hard-stop. Escalation behavior remains governed by the existing stop-on-escalation policy.

Night Shift remains single-worker and single-dispatcher. Subsequent independent tasks still receive fresh runtime cycles, fresh frozen baseline checks, isolated candidate workspaces, L1 integration, and L2 local commit validation. No push, deploy, merge, remote mutation, arbitrary shell execution, unrestricted subprocess execution, or multi-worker concurrency is authorized by this phase.

## Rationale

The Phase 1.32 endurance evidence showed that small, independent, deterministic tasks can run safely overnight. Phase 1.33 keeps that decomposition principle but removes the exact-output-only bottleneck by supporting richer deterministic file predicates and by making contained task-local failure continuation a native supervisor responsibility rather than an external wrapper behavior.
