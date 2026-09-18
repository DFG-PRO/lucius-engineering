# DFG Canonical Project Registry Standard

**Version:** 1.0.0  
**Status:** CANONICAL REPOSITORY SPECIFICATION  
**Scope:** DFG Universe Orchestration (Lucius, Alfred, Darwin, Billy, Trading, Finance)

---

## 1. Executive Summary & Purpose

In multi-agent and long-running autonomous workflows, historical conversation context, stale user prompts, or outdated task descriptions can accidentally cause an agent to regress an engineering phase, reopen closed gates, or attempt to rebuild systems that are already operational.

The **DFG Canonical Project Registry Standard** establishes a versioned, machine-readable system of record defining the true current status, closed gates, active work, repository locations, and development readiness for every project across the DFG Universe.

### Core Governance Principles
1. **Repository Reality Outranks Prompts:** Canonical git state and repository documentation strictly supersede conversational claims or historical instructions.
2. **Phase Invariance:** A closed phase or gate can never be reopened automatically by an incoming prompt.
3. **No Retroactive Re-briefing:** Mature projects already in active development or operational status are protected from generic foundational re-briefing.
4. **Code-Independent Existence:** Projects in research, validation, or conceptual design can exist durably in the registry without requiring an active git repository.

---

## 2. Canonical Schema Specification

Every registered project record conforms to `src/lucius/projects/registry_schema.py` and is maintained in `src/lucius/projects/dfg_canonical_registry.json`.

```json
{
  "project_id": "slugified-unique-id",
  "display_name": "Human Readable Project Name",
  "project_type": "DFG_INTERNAL | CLIENT | R_AND_D | COMMERCIAL",
  "canonical_repo": "/absolute/path/to/repo or null",
  "canonical_docs": ["docs/path1.md", "docs/path2.md"],
  "owner_engine": "Lucius | Darwin | Billy | Trading | Andy | Alfred",
  "status": "CONCEPT | RESEARCHING | VALIDATING | DESIGNING | ENGINEERING_READY | IN_DEVELOPMENT | VALIDATING_BUILD | OPERATIONAL | MAINTENANCE | PARKED | BLOCKED",
  "current_phase": "CURRENT_NAMED_PHASE",
  "current_subphase": "CURRENT_SUBPHASE",
  "last_closed_gate": "LAST_VERIFIED_CLOSED_GATE",
  "active_work": "Summary of active bounded development",
  "next_canonical_gate": "NEXT_EXPECTED_GATE",
  "dependencies": ["project-id-1"],
  "priority": "LOW | NORMAL | HIGH | CRITICAL",
  "brief_status": "BRIEF_MISSING | BRIEF_PARTIAL | BRIEF_RESEARCH_REQUIRED | BRIEF_DECISION_REQUIRED | BRIEF_ENGINEERING_READY | BRIEF_FROZEN_FOR_BUILD | BRIEF_SUPERSEDED | IN_DEVELOPMENT_DO_NOT_REBRIEF | OPERATIONAL_FEATURE_BRIEFS_ONLY | BUSINESS_VALIDATION_BEFORE_SOFTWARE | RESEARCH_BEFORE_BRIEF",
  "development_status": "HIGH_LEVEL_STATUS_SUMMARY",
  "authority_profile": ["CLASS_A", "CLASS_B", ...],
  "monetization_role": "ROLE_DESCRIPTION",
  "last_verified_sha": "40_CHARACTER_HEX_SHA or null",
  "last_verified_at": "ISO_8601_TIMESTAMP or null",
  "source_of_truth": "git:main@SHA or docs/path",
  "notes": "Contextual notes"
}
```

---

## 3. Status Vocabulary

The registry distinguishes between non-software lifecycle stages and software development stages:

1. **`CONCEPT`:** High-level idea or proposal; not yet actively researched.
2. **`RESEARCHING`:** Under active market, technical, or intelligence investigation (owned by Darwin).
3. **`VALIDATING`:** Commercial or business model validation prior to software investment (e.g. rate card confirmation, partner negotiations).
4. **`DESIGNING`:** System architecture, API, or data schema design before engineering handoff.
5. **`ENGINEERING_READY`:** Development brief frozen, technical specifications complete, ready for Lucius/Codex implementation.
6. **`IN_DEVELOPMENT`:** Active supervised or autonomous code development underway.
7. **`VALIDATING_BUILD`:** Code complete; undergoing integration testing, paper validation, or pilot verification.
8. **`OPERATIONAL`:** Actively running in production or producing ongoing intelligence.
9. **`MAINTENANCE`:** Mature operational baseline receiving bugfixes or minor updates.
10. **`PARKED`:** Intentionally deferred or deprioritized.
11. **`BLOCKED`:** Halted by external legal, regulatory, or capital constraints.

---

## 4. Deterministic Validation Invariants

A valid project registry must satisfy the following deterministic rules:
- **ID Uniqueness:** `project_id` must be unique and alphanumeric-slug formatted.
- **Valid Repo Paths:** If `canonical_repo` is defined, it must be an absolute filesystem path.
- **Full 40-Character SHAs:** If `last_verified_sha` is defined, it must match `^[0-9a-fA-F]{40}$`. Truncated hashes or symbolic placeholders (`HEAD`, `main`) are rejected.
- **Explicit Source of Truth:** `source_of_truth` cannot be empty.
- **Fail-Closed on Regression:** Proposed tasks referencing earlier phases or attempting to reopen closed gates fail closed via the Regression Guard.
