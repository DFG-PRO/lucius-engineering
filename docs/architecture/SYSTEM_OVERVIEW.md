# System Overview

## Organizational architecture

Daniel
└── Alfred — PM / Butler / Orchestrator
    ├── Darwin — Research & Intelligence
    ├── Lucius — Software Engineering Intelligence
    └── Specialized Engines

## Lucius conceptual architecture

Lucius
├── Model Layer
├── Context / Retrieval
├── Memory
├── Knowledge
├── Evaluation
├── Policy / Authority
├── Tooling
├── Workspace Isolation
├── Project Registry
├── Task Contracts
├── Audit
└── Documentation

The underlying reasoning model is replaceable.

The persistent identity of Lucius resides in its accumulated knowledge,
memory, policies, processes, evaluations, and engineering history.

## Initial principle

Phase 1 should build the smallest useful technical Lucius rather than a
fully autonomous coding agent.

## Phase 1 operational core

Lucius now maintains a deterministic operational layer:

```text
Project
  -> RepositoryRegistration(s)
  -> Task
     -> TaskContract
        -> TaskRun
           -> RepositorySnapshot
              -> EvidenceReference(s)
              -> TechnicalContextPackage
```

Projects are work-management containers. Repositories remain external
canonical sources and are attached by reference. Tasks capture stable
engineering intent. TaskContracts define acceptance criteria, authority,
allowed actions, dependencies, repositories, and documentation requirements.
TaskRuns preserve execution provenance and may reference repository snapshots.

Phase 1.6 adds deterministic retrieval. Retrieval binds to explicit
RepositorySnapshots, ranks candidate sources with explainable lexical
signals, captures immutable EvidenceReferences, and packages bounded technical
context for future planning layers.

Phase 1.7 adds controlled engineering memory:

```text
EvidenceReference / TaskRun / Documentation / Human Correction / Failure
  -> MemoryEntry or LearningCandidate
  -> validation, revalidation, supersession, or explicit promotion
```

Memory is context, not repository authority. Repository snapshots and current
EvidenceReferences remain the basis for repository-grounded claims. Memory may
be ranked and reused only within its scope, validation status, provenance, and
Knowledge Firewall constraints.
