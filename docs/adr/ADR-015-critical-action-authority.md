# ADR-015: Critical Action Authority

**Status:** Accepted
**Phase:** 0

## Context

Lucius requires a durable architectural baseline before technical
implementation begins.

## Decision

Critical or irreversible operations require explicit authority.

## Consequences

Production, destructive data operations, billing, credentials, and major security changes are never treated as ordinary safe writes.

## Review

This ADR may later be superseded by a new ADR, but historical
rationale must remain available.
