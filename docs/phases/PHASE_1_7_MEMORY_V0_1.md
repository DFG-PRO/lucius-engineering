# Phase 1.7: Memory v0.1

**Status:** Implemented
**Date:** 2026-08-29

## Scope

Phase 1.7 adds Lucius's first controlled engineering memory layer.

Implemented flow:

```text
EvidenceReference / TaskRun / Documentation / Human Correction / Failure
  -> MemoryEntry or LearningCandidate
  -> validation, retrieval, revalidation, supersession, or explicit promotion
```

This phase does not add LLM calls, embeddings, vector search, model training,
repository writes, the Model Gateway, the Engineering Planner, or the Project
Compiler.

## MemoryEntry Model

MemoryEntries persist:

- memory type: SEMANTIC, EPISODIC, PROCEDURAL, EVALUATIVE, PROJECT
- knowledge scope: GLOBAL, DFG, DOMAIN, PROJECT, CLIENT, SESSION
- project, organization, and workspace fields
- statement, confidence, validation status, and tags
- source type, source reference, source project/task/run IDs, and evidence IDs
- timestamps, actor, version, revalidation state, and supersession links

Validated memories require provenance. Project and client scoped memory
requires a project ID. Validated global memory requires source evidence IDs.

## Retrieval

Memory retrieval is deterministic. Callers may filter by:

- scope
- project
- memory type
- validation status
- context tags
- technology tags
- query terms

Ranking favors project-specific memory for the active project, broader reusable
scopes when allowed, validated status, non-superseded entries, provenance, and
query/tag matches. Deprecated, superseded, and contradicted memories are
excluded from default retrieval.

Memory retrieval returns memory matches only. It does not return
EvidenceReferences and does not replace Phase 1.6 repository evidence retrieval.

## Supersession and Revalidation

Memory updates preserve history:

- supersession links older and newer memory entries
- deprecated and contradicted states remain queryable only when requested
- stale or missing source evidence marks memory as requiring revalidation
- validation status is preserved during revalidation marking
- revalidation can be cleared only when provenance remains present

This implements ADR-024: repository reality overrides engineering memory.

## Learning Candidates

LearningCandidates persist candidate type, statement, evidence IDs, source
memory IDs, source documentation references, confidence, status, proposed
scope, sanitization status, source classification, actor, and validation notes.

Supported candidate types:

- PATTERN
- FAILURE_PATTERN
- PROCEDURE
- KNOWLEDGE_CORRECTION
- OPTIMIZATION

Candidate lifecycle:

```text
PENDING -> NEEDS_MORE_EVIDENCE -> VALIDATED
PENDING -> REJECTED
```

Validation does not automatically promote candidates into memory.

## Documentation-Derived Learning

Documentation can produce LearningCandidates only through explicit calls.
Documentation-derived candidates record their source document references and
remain pending until reviewed. They do not auto-promote into memory.

## Failure Memory

Failure experiences create project-scoped episodic memory and a project-scoped
failure-pattern LearningCandidate. The captured structure includes what failed,
context, error summary, attempted approaches, root cause, final fix, test
result, regression result, task/run provenance, and evidence IDs.

Failure memory remains scoped to the originating project unless later reviewed
through the Knowledge Firewall.

## Human Corrections

Human corrections create traceable KNOWLEDGE_CORRECTION candidates. They may
reference prior memory IDs and are audited separately from normal candidate
creation.

## Knowledge Firewall

The baseline Knowledge Firewall classifies project sources as:

- PRIVATE
- ABSTRACTABLE
- GLOBAL_SAFE

Client project knowledge and client-scoped memory are private by default.
Private knowledge cannot be promoted globally. Global promotion requires:

- HUMAN or SYSTEM actor
- VALIDATED candidate status
- provenance
- non-private source classification
- sanitization not required or completed
- an explicit promotion call

Autonomous global promotion is blocked and audited.

## Audit Behavior

Added audit events:

- MEMORY_CREATED
- MEMORY_VALIDATED
- MEMORY_CONTRADICTED
- MEMORY_SUPERSEDED
- MEMORY_DEPRECATED
- MEMORY_REVALIDATION_REQUIRED
- MEMORY_REVALIDATION_CLEARED
- FAILURE_MEMORY_CAPTURED
- LEARNING_CANDIDATE_CREATED
- LEARNING_CANDIDATE_VALIDATED
- LEARNING_CANDIDATE_REJECTED
- LEARNING_CANDIDATE_NEEDS_MORE_EVIDENCE
- HUMAN_CORRECTION_CAPTURED
- KNOWLEDGE_PROMOTION_BLOCKED
- KNOWLEDGE_PROMOTED

Audit payloads preserve IDs, scopes, actors, and reasons without treating
memory statements as repository truth.

## Migration

Added Alembic revision:

- `0004_memory_learning`

New tables:

- `memory_entries`
- `learning_candidates`

Migration checks run:

```bash
LUCIUS_DATABASE_URL=sqlite:////private/tmp/lucius_phase17_clean_head_20260829.sqlite3 .venv/bin/alembic upgrade head
LUCIUS_DATABASE_URL=sqlite:////private/tmp/lucius_phase17_from_0003_20260829.sqlite3 .venv/bin/alembic upgrade 0003_evidence_retrieval
LUCIUS_DATABASE_URL=sqlite:////private/tmp/lucius_phase17_from_0003_20260829.sqlite3 .venv/bin/alembic upgrade head
```

Results:

- clean database upgraded through `0004_memory_learning`
- explicit `0003_evidence_retrieval -> 0004_memory_learning` upgrade succeeded

## Test Coverage

Added grouped integration coverage for the requested Phase 1.7 behaviors:

1. MemoryEntry persistence
2. LearningCandidate persistence
3. memory type coverage
4. scope coverage
5. project scope requirements
6. confidence validation
7. validated-memory provenance
8. global-memory evidence requirement
9. observed-memory allowance
10. deterministic retrieval by project
11. deterministic retrieval by scope
12. deterministic retrieval by type
13. deterministic retrieval by status
14. deterministic retrieval by context tags
15. deterministic retrieval by technology tags
16. deterministic retrieval by query
17. retrieval ranking
18. default exclusion of superseded memory
19. default exclusion of deprecated memory
20. default exclusion of contradicted memory
21. supersession link preservation
22. circular supersession prevention
23. manual revalidation marking
24. revalidation clearing
25. stale evidence propagation
26. missing evidence detection
27. partial stale evidence handling
28. validation status preservation during revalidation
29. candidate provenance requirement
30. candidate pending status
31. needs-more-evidence transition
32. candidate validation transition
33. candidate rejection transition
34. documentation-derived candidate creation
35. no documentation auto-promotion
36. task experience capture
37. no-learning-candidate outcome
38. failure memory capture
39. failure-pattern candidate capture
40. failure memory project scoping
41. human correction capture
42. human correction audit
43. client global memory block
44. private source classification
45. required sanitization classification
46. unsanitized global promotion block
47. autonomous global promotion block
48. authorized explicit global promotion
49. promotion provenance transfer
50. memory retrieval separate from evidence retrieval
51. memory and learning audit events
52. internal learning fixture remains project-scoped

Commands run:

```bash
.venv/bin/python -m pytest tests/integration/test_phase17_memory_learning.py -q
.venv/bin/python -m pytest -q
```

Results:

- `19 passed in 8.90s`
- `86 passed in 54.05s`

## Internal Fixture

The internal Phase 1.7 fixture records the snapshot-reuse learning:

```text
Repository snapshots use deterministic SHA-256 manifest identity and reuse
unchanged state.
```

It is persisted as project-scoped candidate memory and as a project-scoped
LearningCandidate. It is not globally promoted.

## Deviations

No known deviations from the Phase 1.7 brief.

## Limitations

Memory retrieval is lexical and deterministic. It does not perform semantic
search, embeddings, LLM summarization, vector indexing, or repository writes.

LearningCandidate promotion is explicit and local to the persistence layer.
Future planner work must still retrieve current repository evidence before
acting on memory.

## Final Status

Phase 1.7 Memory v0.1 is complete.

This does not begin the Model Gateway, Engineering Planner, Project Compiler,
embeddings, vector database work, fine-tuning, or autonomous code execution.
