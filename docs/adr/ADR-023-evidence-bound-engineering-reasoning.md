# ADR-023: Evidence-Bound Engineering Reasoning

**Status:** Accepted
**Phase:** 1.6

## Context

Lucius will eventually produce engineering reasoning, plans, and actions.
Those outputs must be reproducible and traceable to specific repository
states rather than vague remembered context.

## Decision

Engineering reasoning produced by Lucius must be traceable to explicit,
version-bound evidence.

Retrieval candidates alone are not authoritative. EvidenceReferences
preserve repository, snapshot, path, source state, line range where available,
and relevance provenance.

Future Engineering Planner work must distinguish:

- retrieved candidate
- captured evidence
- derived inference
- historical memory
- explicit assumptions
- explicit unknowns

Phase 1.9 implements this decision for persisted EngineeringPlans. Every plan
must preserve version-bound provenance to its Task, TaskContract,
RepositorySnapshots, EvidenceReferences, MemoryEntries, and ModelExecution.

Repository evidence overrides conflicting memory. Inference must never
masquerade as evidence, and memory must never be represented as current
repository reality.

## Consequences

Lucius may rank and package candidate sources, but any later reasoning must
cite captured EvidenceReferences when claiming repository-grounded facts.

Evidence is immutable historical provenance. If source content changes, new
evidence must be captured rather than rewriting old evidence.

## Review

This ADR was reviewed and extended during Phase 1.9 when the first Engineering
Planner and inference persistence layer were implemented.
