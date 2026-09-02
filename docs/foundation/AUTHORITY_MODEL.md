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

## Staged autonomy matrix

`READY_FOR_LIMITED_WRITE_PILOT` authorizes one reviewed, bounded implementation
in an isolated branch or worktree after canonical planning gates pass.

`READY_FOR_ANOTHER_LIMITED_WRITE_PILOT` authorizes another bounded isolated
write pilot, but not broader engineering autonomy.

`READY_FOR_BOUNDED_ENGINEERING_PILOT` authorizes one bounded engineering
objective at a time with internal task decomposition, isolated implementation,
bounded repair loops, tests, documentation, evidence capture, and review-package
preparation.

`READY_FOR_BOUNDED_MULTI_TASK_ENGINEERING` authorizes a larger one-objective
bounded engineering run where Lucius may create and sequence multiple related
tasks under a dependency graph. It still requires isolated implementation,
bounded repair limits, tests, documentation, and human review before integration.

`READY_FOR_ANOTHER_BOUNDED_MULTI_TASK_ENGINEERING_PILOT` authorizes another
one-objective bounded multi-task pilot after a successful run when human review
or supervised-workflow evidence is still incomplete.

`READY_FOR_SUPERVISED_ENGINEERING_WORKFLOW` authorizes supervised preparation
and execution of one bounded engineering objective with human review gates. It
may span multiple internal checkpoints for that objective, including repository
understanding, decomposition, planning, isolated implementation, bounded repair,
testing, documentation, evidence capture, and review-package preparation. It
does not authorize automatic integration or production operations.

Supervised workflow checkpoints require human approval before merge to main,
push when policy requires it, production deployment, destructive operations,
credential/security changes, unplanned migration or schema expansion, material
new external dependencies, architecture expansion outside the frozen objective,
cross-project modifications, or material scope changes.

No staged autonomy recommendation authorizes automatic merge to main, automatic
push, production deployment, credential changes, destructive Git, force push,
broad cross-project autonomy, unrestricted architecture rewrites, silent
migration/schema expansion, unlimited task spawning, unlimited repair cycles, or
unrestricted autonomous operation.
