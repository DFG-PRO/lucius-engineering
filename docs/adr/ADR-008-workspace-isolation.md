# ADR-008: Workspace Isolation

**Status:** Accepted
**Phase:** 0

## Context

Lucius requires a durable architectural baseline before technical
implementation begins.

## Decision

Every Lucius task must operate inside an explicit authorized workspace.

## Consequences

Repositories, tools, memory, permissions, and environment are bounded by workspace context.

## Review

This ADR may later be superseded by a new ADR, but historical
rationale must remain available.
