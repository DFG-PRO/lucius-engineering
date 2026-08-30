# ADR-026: Evaluation Before Engineering Autonomy

**Status:** Accepted
**Phase:** 1.10

## Context

Lucius now has repository evidence, memory, model routing, and evidence-bound
planning. Passing implementation tests proves the software behaves as coded,
but it does not prove Lucius performs engineering judgment correctly.

## Decision

Lucius may not gain materially greater autonomous engineering authority solely
because implementation tests pass.

Progression toward repository writes, WorkPackage execution, phase execution,
or long-running stewardship requires versioned engineering evaluations,
permanent regression benchmarks, and hard safety/integrity gates.

A benchmark regression involving client isolation, privacy, provenance,
critical authority, critical risk, or repository-reality integrity blocks
autonomy progression regardless of aggregate score.

## Consequences

EvaluationRuns become part of Lucius's engineering control surface. Release and
autonomy decisions must consider hard gates independently from weighted scores.

Deterministic benchmark cases are engineering assets and must remain readable,
versioned, and auditable.
