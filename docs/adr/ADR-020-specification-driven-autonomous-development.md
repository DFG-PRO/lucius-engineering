# ADR-020: Specification-Driven Autonomous Development

**Status:** Accepted
**Phase:** 1.4B

## Context

Lucius is designed to become capable of increasingly autonomous engineering
work, but autonomous execution must remain controlled by specifications,
authority levels, and explicit gates.

## Decision

Lucius is designed toward approved-specification-driven autonomous
development with execution gates.

The current phase implements only deterministic repository registration,
inspection, persistence, snapshots, and audit events. It does not implement
autonomous coding, commits, pull requests, deployment, or LLM planning.

## Consequences

Future autonomy must be grounded in approved specifications and observable
repository state. Execution gates must be explicit before Lucius performs
mutating work.

## Review

This ADR should guide later Project Specification, Development Plan, Work
Package, Task, and TaskRun implementation.

