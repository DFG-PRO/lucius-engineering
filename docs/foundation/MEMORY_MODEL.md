# Memory Model

Lucius uses five conceptual memory classes.

## Semantic Memory

What Lucius knows.

Examples:
- technologies
- interfaces
- architecture concepts
- standards
- validated technical facts

## Episodic Memory

What happened.

Examples:
- task histories
- failures
- fixes
- implementation outcomes

## Procedural Memory

How work is performed.

Examples:
- project initialization
- migrations
- tests
- deployment procedures
- documentation checkpoints

## Evaluative Memory

What worked and under which conditions.

This memory tracks evidence, repeated outcomes, regressions, and confidence.

## Project Memory

Context specific to an authorized project.

Examples:
- architecture
- ADRs
- conventions
- current phase
- known issues
- technical debt
- deployment characteristics

## Knowledge scope

Knowledge must be scoped as appropriate:

- GLOBAL
- DFG
- DOMAIN
- PROJECT
- CLIENT
- SESSION

## Provenance

Important knowledge should preserve:

- knowledge ID
- statement
- source
- scope
- project
- timestamp
- evidence
- confidence
- validation status
- version
- superseded relationship

## Phase 1.7 Persistence

MemoryEntries are persisted records, not model weights. Each entry captures a
memory type, scope, optional project, statement, source type/reference,
evidence IDs, task/run provenance, confidence, validation status, tags,
creation actor, timestamps, version, revalidation state, and supersession
links.

Validated memory requires provenance. Project and client memory require a
project ID. Validated global memory requires evidence IDs.

## Retrieval

Memory retrieval is deterministic and filterable by project, scope, memory
type, validation status, context tags, technology tags, and query terms.
Retrieval ranks memory by scope relevance, validation status, provenance, and
lexical matches. Superseded, deprecated, and contradicted memory is excluded by
default.

Memory retrieval does not replace evidence retrieval. When repository reality
matters, Lucius must retrieve or validate current EvidenceReferences.

## History

Memory is not silently overwritten. Older entries are preserved through:

- supersession
- contradiction
- deprecation
- revalidation flags

Stale or missing source evidence marks memory for revalidation while keeping
the historical record intact.
