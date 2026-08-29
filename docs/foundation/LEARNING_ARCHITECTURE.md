# Learning Architecture

Lucius must improve through accumulated, validated engineering experience.

Initial learning is externalized rather than implemented through automatic
model-weight modification.

## Learning pipeline

Event
→ Experience
→ Candidate Learning
→ Evaluation
→ Validated Knowledge

## Knowledge states

- OBSERVED
- CANDIDATE
- VALIDATED
- CONTRADICTED
- SUPERSEDED
- DEPRECATED

## Principle

Experience is not automatically truth.

A successful execution is evidence, but not sufficient proof that a pattern
should become a permanent engineering rule.

## EvidenceReferences

Phase 1.6 introduces persisted EvidenceReferences as repository-bound
provenance records. They may later support learning and evaluation, but they
are not automatically promoted into memory or validated knowledge.

## Phase 1.7 Learning Candidates

LearningCandidates are now persisted as explicit, reviewable records. They may
come from task experience, failure experience, documentation, existing memory,
or human correction. They preserve evidence IDs, source memory IDs, source
documentation references, confidence, proposed scope, source classification,
sanitization status, actor, and lifecycle state.

Candidate states are:

- PENDING
- NEEDS_MORE_EVIDENCE
- VALIDATED
- REJECTED

Validation still does not equal promotion. A validated candidate can become
memory only through an explicit promotion operation that passes the Knowledge
Firewall.

## Corrections

Human corrections must be versioned and traceable rather than silently
overwriting previous knowledge.

In Phase 1.7, human corrections create KNOWLEDGE_CORRECTION candidates. They
may reference prior memory IDs and are audited before any later validation or
promotion.

## Future model training

Validated experience may later produce:

- engineering datasets
- evaluation datasets
- fine-tuning datasets
- adapters
- specialized local models

Such training is explicitly outside the Phase 0 scope.
