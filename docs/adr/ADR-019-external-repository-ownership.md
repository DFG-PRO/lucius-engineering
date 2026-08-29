# ADR-019: External Repository Ownership

**Status:** Accepted
**Phase:** 1.4B

## Context

Lucius needs durable knowledge about projects and repositories it assists,
but managed repositories remain independent software systems.

## Decision

Managed repositories remain canonical outside Lucius.

Lucius may register external repositories, inspect their current state,
store metadata, persist evidence references, and create deterministic
snapshots. Lucius does not copy repository contents into its own managed
state and does not become the source of truth for code.

## Consequences

Repository reality overrides Lucius memory.

Lucius stores registrations and snapshots as observations of external
repositories. Any later analysis must re-inspect the repository or compare
against a deterministic snapshot rather than trusting stale memory.

## Review

This ADR may be revisited when write-authorized repository operations are
introduced, but external ownership remains the default architectural stance.

