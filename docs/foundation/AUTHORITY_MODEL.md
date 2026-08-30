# Authority Model

Lucius operates using four initial authority levels.

## L0 — Read

Autonomous.

Examples:
- inspect repositories
- read documentation
- analyze architecture
- diagnose problems

## L1 — Safe Write

Autonomous inside an authorized controlled workspace, with audit trail.

Examples:
- modify development code
- create tests
- update documentation
- create temporary artifacts
- create development commits

## L2 — Controlled Change

Requires explicit authorization.

Examples:
- significant architecture changes
- staging deployments
- major migrations
- critical dependency replacement
- consequential integration changes

## L3 — Critical

Requires explicit human approval.

Examples:
- production deployment
- destructive data operations
- billing changes
- credential changes
- critical security changes
- irreversible external operations

## Principle

High autonomy inside controlled environments.
Restricted authority outside them.

## Planning authority

Phase 1.9 EngineeringPlans estimate required authority before execution. If a
proposed plan requires authority above the TaskContract, Lucius surfaces
`AUTHORITY_ESCALATION_REQUIRED`. The plan may be useful, but it is not
executable or approved by default.
