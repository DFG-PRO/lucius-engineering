# Development Operating Model

## Standard lifecycle

Request
→ Alfred
→ Task Contract
→ Lucius
→ Understand
→ Retrieve
→ Inspect
→ Plan
→ Risk Check
→ Implement
→ Test
→ Evaluate
→ Document
→ Deliver
→ Observe
→ Learn
→ Alfred

## Task Contract

A significant task should define:

- task ID
- project
- objective
- acceptance criteria
- constraints
- priority
- authority level
- allowed tools
- repositories
- environment
- dependencies
- documentation requirements

Phase 1.5 persists TaskContracts separately from Tasks. A Task captures
stable intent; a TaskContract captures executable boundaries, acceptance
criteria, allowed actions, authority, dependencies, repositories, and
documentation targets.

## Task lifecycle

Current operational lifecycle:

Draft
→ Ready
→ Running
→ Implementation Complete
→ Documentation Pending
→ Complete

Task status changes must go through explicit lifecycle operations. Terminal
Phase 1.5 states are Complete and Cancelled.

## Repository reality principle

Current repository state overrides remembered state.

When memory and repository reality disagree, Lucius must investigate rather
than blindly trust memory.

## Evidence-bound retrieval

Before future planning layers reason about repository facts, Lucius must bind
retrieval to explicit repository snapshots and capture EvidenceReferences.

Retrieval candidates are not yet conclusions. EvidenceReferences preserve
repository, snapshot, path, content hash, line range, bounded snippet, and
ranking reasons.

## Complexity classes

- T0 — trivial
- T1 — standard
- T2 — complex
- T3 — architectural
- T4 — critical

Planning and authorization depth should scale with complexity and risk.

## Incremental development

Default behavior:

small change
→ test
→ small change
→ test
→ integrate

Large autonomous rewrites are not the default.

## Test hierarchy

Static
→ Unit
→ Integration
→ Regression
→ System
→ Real-world validation

Passing tests are evidence, not proof of real-world correctness.

## Failure loop

Failure
→ Capture
→ Diagnose
→ Hypothesis
→ Change
→ Retest

Repeated failure must eventually trigger re-planning or escalation.

## Cost-aware routing

Lucius should use the cheapest resource capable of performing a task reliably
and escalate only when necessary.

Potential resource classes:

- local model
- general cloud model
- advanced coding model
- human
