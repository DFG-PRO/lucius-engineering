# ADR-022: Documentation as Learning Source

**Status:** Accepted
**Phase:** 1.4B

## Context

Project documentation contains valuable engineering knowledge, decisions,
constraints, and operating context. That knowledge can help Lucius learn,
but client and project boundaries must remain intact.

## Decision

Project documentation is a learning source, but promotion requires
classification, provenance, evaluation, and client-safe sanitization.

Documentation may be observed as project evidence. It must not be promoted
into broader memory or reusable knowledge without preserving scope,
source, validation status, and confidentiality boundaries.

## Consequences

Lucius may discover documentation deterministically during repository
inspection. Later learning systems must treat documentation-derived
knowledge as scoped and provisional until evaluated.

## Review

This ADR should be reviewed when memory promotion, evaluation runs, and
learning workflows are implemented.

