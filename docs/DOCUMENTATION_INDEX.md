# Documentation Index

This file identifies the canonical Lucius documentation baseline.

## Foundation

- `foundation/PROJECT_CHARTER.md`
- `foundation/SYSTEM_BOUNDARIES.md`
- `foundation/AUTHORITY_MODEL.md`
- `foundation/LEARNING_ARCHITECTURE.md`
- `foundation/MEMORY_MODEL.md`
- `foundation/WORKSPACE_ISOLATION.md`
- `foundation/SECURITY_PRINCIPLES.md`
- `foundation/DEVELOPMENT_OPERATING_MODEL.md`
- `foundation/DOCUMENTATION_POLICY.md`

## Architecture

- `architecture/SYSTEM_OVERVIEW.md`
- `architecture/PILOT_EVALUATION_DATA_MODEL.md`

## Evaluation

- `evaluation/LUCIUS_CORE_BENCH_V0_1.md`
- `evaluation/CANONICAL_PILOT_METHODOLOGY.md`
- `evaluation/BOUNDED_ENGINEERING_PILOT_METHODOLOGY.md`

## Runtime / Operator Workflows

- `runtime/PILOT_OPERATOR_WORKFLOW.md`

## Decisions

- `decisions/PHASE_0_DECISION_REGISTER.md`
- `adr/ADR-001-model-independence.md`
- `adr/ADR-002-alfred-orchestration.md`
- `adr/ADR-003-darwin-lucius-specialization.md`
- `adr/ADR-004-externalized-learning.md`
- `adr/ADR-005-knowledge-provenance.md`
- `adr/ADR-006-learning-promotion-gate.md`
- `adr/ADR-007-failure-memory.md`
- `adr/ADR-008-workspace-isolation.md`
- `adr/ADR-009-client-knowledge-boundary.md`
- `adr/ADR-010-external-secrets-management.md`
- `adr/ADR-011-repository-reality-principle.md`
- `adr/ADR-012-incremental-development.md`
- `adr/ADR-013-documentation-definition-of-done.md`
- `adr/ADR-014-cost-aware-resource-routing.md`
- `adr/ADR-015-critical-action-authority.md`
- `adr/ADR-016-portable-knowledge.md`
- `adr/ADR-017-continuous-evaluation.md`
- `adr/ADR-018-versioned-human-corrections.md`
- `adr/ADR-019-external-repository-ownership.md`
- `adr/ADR-020-specification-driven-autonomous-development.md`
- `adr/ADR-021-project-documentation-stewardship.md`
- `adr/ADR-022-documentation-as-learning-source.md`
- `adr/ADR-023-evidence-bound-engineering-reasoning.md`
- `adr/ADR-024-repository-reality-overrides-engineering-memory.md`
- `adr/ADR-025-continuous-universe-improvement-loop.md`
- `adr/ADR-026-evaluation-before-engineering-autonomy.md`

## Phase reports

- `phases/PHASE_0_FOUNDATION.md`
- `phases/PHASE_1_4B_REPOSITORY_CORE_IMPLEMENTATION.md`
- `phases/PHASE_1_5_PROJECT_REGISTRY_TASK_CONTRACT.md`
- `phases/PHASE_1_6_EVIDENCE_RETRIEVAL.md`
- `phases/PHASE_1_7_MEMORY_V0_1.md`
- `phases/PHASE_1_8_MODEL_GATEWAY.md`
- `phases/PHASE_1_9_ENGINEERING_PLANNER.md`
- `phases/PHASE_1_10_EVALUATION_HARNESS.md`
- `phases/PHASE_1_11_DARWIN_REAL_REPOSITORY_PILOT.md`
- `phases/PHASE_1_12_EVALUATION_CANONICAL_PILOT_INFRASTRUCTURE.md`
- `phases/PHASE_1_13_CANONICAL_DARWIN_REAL_REPOSITORY_PILOT_2.md`
- `phases/PHASE_1_13A_PLANNING_EVALUATION_GATE_CORRECTION.md`
- `phases/PHASE_1_14_FIRST_LIMITED_WRITE_PILOT_ON_DARWIN.md`
- `phases/PHASE_1_14A_ZERO_CHANGE_EVIDENCE_CORRECTION.md`
- `phases/PHASE_1_15_SECOND_LIMITED_WRITE_PILOT.md`
- `phases/PHASE_1_15A_HUMAN_REVIEW_AND_BOUNDED_READINESS.md`
- `phases/PHASE_1_16_FIRST_BOUNDED_ENGINEERING_PILOT.md`
- `phases/PHASE_1_16A_HUMAN_REVIEW_AND_MULTI_TASK_READINESS.md`
- `phases/PHASE_1_17_FIRST_BOUNDED_MULTI_TASK_ENGINEERING_PILOT.md`
- `phases/PHASE_1_17A_HUMAN_REVIEW_AND_SUPERVISED_READINESS.md`
- `phases/PHASE_1_18_FIRST_SUPERVISED_ENGINEERING_WORKFLOW.md`
- `phases/PHASE_1_18A_HUMAN_REVIEW_AND_PERSISTENT_SUPERVISED_READINESS.md`
- `phases/PHASE_1_19_PERSISTENT_WORKFLOW_PAUSE_RESUME_PILOT.md`
- `phases/PHASE_1_20_NON_BLOCKING_PROJECT_QUEUE_PILOT.md`
- `phases/PHASE_1_21_CROSS_PROJECT_NON_BLOCKING_QUEUE_PILOT.md`

## Reference

- `glossary/GLOSSARY.md`

## Documentation status

Phase 0 content: PERSISTED

Phase 0 closure: PENDING AUDIT + GIT BASELINE COMMIT

Phase 1.4B repository core: IMPLEMENTED

Phase 1.5 project registry and task contract: IMPLEMENTED

Phase 1.6 evidence and retrieval: IMPLEMENTED

Phase 1.7 memory v0.1: IMPLEMENTED

Phase 1.8 model gateway and capability routing: IMPLEMENTED

Phase 1.9 engineering planner v0.1: IMPLEMENTED

Phase 1.10 evaluation harness and Lucius benchmark v0.1: IMPLEMENTED

Phase 1.11 Darwin real repository pilot: IMPLEMENTED

Phase 1.12 evaluation and canonical pilot infrastructure: IMPLEMENTED

Phase 1.13 canonical Darwin real-repository pilot #2: COMPLETED READ/PLAN-ONLY; AUTONOMY GATE BLOCKED

Phase 1.13A planning evaluation gate correction: IMPLEMENTED; CORRECTED GATE READY_FOR_LIMITED_WRITE_PILOT

Phase 1.14 first limited write pilot on Darwin: IMPLEMENTED IN ISOLATED WORKTREE; AUTONOMY GATE BLOCKED

Phase 1.14A zero-change evidence correction: IMPLEMENTED; CORRECTED GATE READY_FOR_ANOTHER_LIMITED_WRITE_PILOT

Phase 1.15 second limited write pilot on Darwin: IMPLEMENTED IN ISOLATED WORKTREE; GATE READY_FOR_ANOTHER_LIMITED_WRITE_PILOT

Phase 1.15A human review and bounded readiness: IMPLEMENTED; GATE READY_FOR_BOUNDED_ENGINEERING_PILOT

Phase 1.16 first bounded engineering pilot: IMPLEMENTED IN ISOLATED WORKTREE; HUMAN REVIEW NOT_CAPTURED

Phase 1.16A human review and multi-task readiness: IMPLEMENTED; GATE READY_FOR_BOUNDED_MULTI_TASK_ENGINEERING

Phase 1.17 first bounded multi-task engineering pilot: IMPLEMENTED IN ISOLATED WORKTREE; HUMAN REVIEW NOT_CAPTURED

Phase 1.17A human review and supervised readiness: IMPLEMENTED; GATE READY_FOR_SUPERVISED_ENGINEERING_WORKFLOW

Phase 1.18 first supervised engineering workflow: IMPLEMENTED IN ISOLATED DARWIN WORKTREE; HUMAN REVIEW NOT_CAPTURED; INTEGRATION PENDING

Phase 1.18A human review and persistent supervised readiness: IMPLEMENTED; GATE READY_FOR_PERSISTENT_SUPERVISED_ENGINEERING

Phase 1.19 persistent workflow pause/resume pilot: IMPLEMENTED IN ISOLATED DARWIN WORKTREE; HUMAN REVIEW NOT_CAPTURED; INTEGRATION PENDING

Phase 1.20 non-blocking project queue pilot: IMPLEMENTED IN LUCIUS; HUMAN REVIEW NOT_CAPTURED

Phase 1.21 cross-project non-blocking queue pilot: IMPLEMENTED IN LUCIUS; HUMAN REVIEW NOT_CAPTURED; PHASE 1.21B STALE-STATE REPAIR APPLIED
