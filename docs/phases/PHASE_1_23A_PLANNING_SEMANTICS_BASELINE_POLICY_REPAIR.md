# Phase 1.23A Planning Semantics and Baseline Policy Repair

Phase 1.23A is a narrow repair for Phase 1.23 closure. It preserves the
completed Phase 1.23 artifacts, implementation branches, and failed historical
evaluations, then adds process controls and superseding evidence where the
original closure semantics were wrong.

## Scope

This phase does not start Phase 1.24, implement multi-worker concurrency, push,
merge, deploy, or redo target engineering work. It repairs Lucius planning and
evaluation policy for:

- unsupported verified planning claims;
- top-level dependency field misuse;
- dirty/non-canonical baseline handling;
- fixture-dependent full-suite interpretation;
- missing pre-frozen overall orchestration evaluation.

## Policy Repairs

Direct EngineeringPlan construction is now subject to the same semantic
verified-claim gate expected of normal planner output. Claims marked
`VERIFIED`, `CONFIRMED`, or equivalent require current evidence that supports
the exact claim before plan freeze.

EngineeringPlan dependencies now represent executable dependency relationships.
Validation prerequisites and local test-environment notes are not package or
runtime dependency expectations unless the implementation artifact explicitly
says so.

Fixture-dependent suite closure now separates suite health from change
regression. Missing canonical fixtures can degrade suite health while still
allowing `NO_NEW_REGRESSION` when baseline and target failures match.

Future extended operational pilots must freeze an overall orchestration plan
before any target mutation and evaluate that overall plan after implementation.
Post-hoc closure evaluation documents evidence but cannot replace a missing
pre-frozen orchestration plan.

## Verification

Phase 1.23A verification covers the new plan-freeze semantic gate, dependency
validation, fixture-aware baseline comparison, preservation of dirty/non-
canonical target baselines, operational freeze requirements, provenance
regression coverage, target branch validation, full Lucius tests, and formal
PRE/POST `LUCIUS_CORE_BENCH_V0_1` benchmarks.

## Closure

The final Phase 1.23A closure record is persisted in
`data/lucius-pilots.sqlite` and linked from the completion report. Historical
Phase 1.23 failures remain historical. Superseding evaluations and learning
candidates carry the corrected interpretation.
