# ADR-001: Model Independence

**Status:** Accepted
**Phase:** 0

## Context

Lucius requires a durable architectural baseline before technical
implementation begins.

## Decision

Lucius must not be architecturally identified with a single LLM provider or model.

## Consequences

The underlying reasoning model must be replaceable while preserving Lucius knowledge, memory, policies, and experience.

## Review

This ADR may later be superseded by a new ADR, but historical
rationale must remain available.
