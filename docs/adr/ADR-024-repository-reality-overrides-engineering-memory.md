# ADR-024: Repository Reality Overrides Engineering Memory

**Status:** Accepted
**Phase:** 1.7

## Context

Phase 1.7 introduces persisted engineering memory and learning candidates.
Memory can preserve useful experience, but it is not the canonical source of
repository truth.

Lucius already captures repository-bound EvidenceReferences from explicit
RepositorySnapshots. Those records identify what source state was observed and
when it was observed. Memory may summarize or generalize from that evidence,
but source code, repository snapshots, and current evidence checks must retain
authority over remembered statements.

## Decision

Repository reality overrides engineering memory.

When a remembered engineering statement conflicts with current repository
state or stale/missing evidence, Lucius must prefer fresh repository evidence.
The memory entry must preserve history through revalidation flags,
supersession, contradiction, or deprecation rather than being silently
rewritten.

Future planning, code generation, and autonomous execution layers must treat
memory as context. Repository-grounded claims still require current or
explicitly accepted EvidenceReferences.

## Consequences

- Memory retrieval may rank useful context, but it cannot substitute for
  source inspection where repository truth matters.
- Evidence staleness can mark memory for revalidation without erasing the
  historical entry.
- Supersession preserves prior statements and records why a newer memory entry
  replaced them.
- Global promotion cannot launder project or client facts into reusable
  knowledge without provenance, classification, sanitization where required,
  validation, and explicit authority.

## Review

Review this ADR when the Engineering Planner begins using memory alongside
retrieved evidence.
