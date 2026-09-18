# DFG Regression Guard Specification

**Version:** 1.0.0  
**Status:** CANONICAL REPOSITORY SPECIFICATION  
**Module:** `src/lucius/projects/regression_guard.py`

---

## 1. Overview & Purpose

The **Regression Guard** is a deterministic gatekeeper that prevents Lucius, subagents, or task feeders from regressing project phases, reopening verified closed gates, or acting on stale SHA assumptions.

### Violations Detected
1. **`PHASE_REGRESSION`:** Task references an earlier development phase than the project's current phase in the canonical registry (e.g. attempting to execute Billy Phase 5 when Billy is in Phase 6.4B).
2. **`CLOSED_GATE_REOPEN`:** Task attempts to execute or reopen a gate that has already been recorded as closed (e.g. `PHASE_6_4B_LEDGER_RECEIPT_VERIFIED`).
3. **`STALE_SHA_ASSUMPTION`:** Task assumes an outdated repository SHA that does not match the canonical registry's `last_verified_sha`.
4. **`BRIEF_NOT_ENGINEERING_READY`:** Task attempts code mutation on a project whose brief status is not `BRIEF_ENGINEERING_READY`, `BRIEF_FROZEN_FOR_BUILD`, or `IN_DEVELOPMENT_DO_NOT_REBRIEF`.
5. **`SOURCE_OF_TRUTH_CONFLICT`:** Incoming instructions contradict canonical repository files or explicit documentation policies.

---

## 2. Integration with Runtime & Feeders

The Regression Guard is integrated directly into:
- **`DarwinBacklogFeeder`:** Filters candidate tasks during discovery to ensure no invalid or regressive tasks are ingested.
- **`TravelLauncher`:** Performs preflight validation against canonical project records.
- **`ExecutionRuntimeLoopService`:** Asserts authority and phase validity prior to plan freeze.
