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

## Consequences

Lucius may rank and package candidate sources, but any later reasoning must
cite captured EvidenceReferences when claiming repository-grounded facts.

Evidence is immutable historical provenance. If source content changes, new
evidence must be captured rather than rewriting old evidence.

## Review

This ADR should be reviewed when Engineering Planner and inference layers are
implemented.

