# ADR-010: External Secrets Management

**Status:** Accepted
**Phase:** 0

## Context

Lucius requires a durable architectural baseline before technical
implementation begins.

## Decision

Lucius long-term memory must not act as a secrets manager.

## Consequences

Credentials are stored externally and exposed only through controlled references or temporary access.

## Review

This ADR may later be superseded by a new ADR, but historical
rationale must remain available.
