# ADR-011: Repository Reality Principle

**Status:** Accepted
**Phase:** 0

## Context

Lucius requires a durable architectural baseline before technical
implementation begins.

## Decision

Current repository state overrides Lucius memory.

## Consequences

Memory is advisory context; implementation decisions must inspect present repository reality.

## Review

This ADR may later be superseded by a new ADR, but historical
rationale must remain available.
