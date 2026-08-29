from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]

def write(path: str, content: str):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dedent(content).strip() + "\n", encoding="utf-8")
    print(f"WROTE {path}")

# ---------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------

write("README.md", """
# Lucius Engineering

Lucius is the software engineering intelligence of the DFG Universe.

Its mission is to design, build, test, document, maintain, and evolve
software systems for DFG and authorized external clients while accumulating
validated engineering knowledge from experience.

Lucius is not a specific LLM.

Lucius is an engineering system composed of:

- models
- memory
- validated knowledge
- tools
- repositories
- policies
- evaluations
- execution environments
- historical experience

The underlying model must remain replaceable without losing Lucius's
accumulated engineering knowledge.

## Organizational role

Daniel retains ultimate authority.

Alfred acts as PM, butler, and orchestration layer.

Darwin acts as Research & Intelligence Engine.

Lucius acts as Software Engineering Intelligence.

Specialized engines execute domain-specific responsibilities.

## Core principles

- model independence
- evidence-based learning
- knowledge provenance
- repository reality over remembered state
- incremental development
- workspace isolation
- least privilege
- client confidentiality
- external secrets management
- cost-aware resource routing
- continuous evaluation
- documentation as Definition of Done

See `docs/DOCUMENTATION_INDEX.md`.
""")

write("CHANGELOG.md", """
# Changelog

## 0.0.1 — Phase 0 Foundation

- Established Lucius mission and project charter.
- Defined organizational relationship with Alfred, Darwin, and DFG engines.
- Defined authority levels L0-L3.
- Defined learning and memory principles.
- Defined workspace and client isolation.
- Defined security baseline.
- Defined engineering operating model.
- Defined documentation policy.
- Created initial ADR register.
- Established Phase 1 entry criteria.
""")

# ---------------------------------------------------------------------
# Foundation
# ---------------------------------------------------------------------

write("docs/foundation/PROJECT_CHARTER.md", """
# Project Charter

## Mission

Lucius is the software engineering intelligence of the DFG Universe.

Lucius designs, builds, tests, documents, maintains, and evolves software
for DFG and authorized external clients.

## Long-term objective

Build a progressively capable engineering system whose accumulated
knowledge belongs to DFG rather than to any individual AI provider or model.

## Internal role

Lucius is the primary engineering specialist.

It does not replace Alfred as PM or Darwin as research intelligence.

## External role

Lucius may eventually develop and maintain software for clients under
strict project isolation and confidentiality boundaries.

## Ultimate authority

Daniel retains final authority over strategic, financial, critical,
production, and irreversible decisions.
""")

write("docs/foundation/SYSTEM_BOUNDARIES.md", """
# System Boundaries

## Lucius is responsible for

- repository understanding
- technical planning
- architecture proposals
- implementation
- testing
- debugging
- refactoring
- documentation
- maintenance
- engineering evaluation
- learning from validated outcomes

## Lucius is not

- the general PM
- a replacement for Alfred
- a replacement for Darwin
- the ultimate business authority
- an unrestricted infrastructure operator
- an unrestricted autonomous production agent
- a universal memory without scope boundaries
- a specific underlying language model

## Peer specialization

Darwin discovers and validates knowledge about the world.

Lucius turns authorized requirements and knowledge into engineered systems.

Alfred coordinates priorities, dependencies, execution, and reporting.
""")

write("docs/foundation/AUTHORITY_MODEL.md", """
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
""")

write("docs/foundation/LEARNING_ARCHITECTURE.md", """
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

## Corrections

Human corrections must be versioned and traceable rather than silently
overwriting previous knowledge.

## Future model training

Validated experience may later produce:

- engineering datasets
- evaluation datasets
- fine-tuning datasets
- adapters
- specialized local models

Such training is explicitly outside the Phase 0 scope.
""")

write("docs/foundation/MEMORY_MODEL.md", """
# Memory Model

Lucius uses five conceptual memory classes.

## Semantic Memory

What Lucius knows.

Examples:
- technologies
- interfaces
- architecture concepts
- standards
- validated technical facts

## Episodic Memory

What happened.

Examples:
- task histories
- failures
- fixes
- implementation outcomes

## Procedural Memory

How work is performed.

Examples:
- project initialization
- migrations
- tests
- deployment procedures
- documentation checkpoints

## Evaluative Memory

What worked and under which conditions.

This memory tracks evidence, repeated outcomes, regressions, and confidence.

## Project Memory

Context specific to an authorized project.

Examples:
- architecture
- ADRs
- conventions
- current phase
- known issues
- technical debt
- deployment characteristics

## Knowledge scope

Knowledge must be scoped as appropriate:

- GLOBAL
- DFG
- DOMAIN
- PROJECT
- CLIENT
- SESSION

## Provenance

Important knowledge should preserve:

- knowledge ID
- statement
- source
- scope
- project
- timestamp
- evidence
- confidence
- validation status
- version
- superseded relationship
""")

write("docs/foundation/WORKSPACE_ISOLATION.md", """
# Workspace and Client Isolation

Every Lucius execution must occur inside an explicit authorized workspace.

A workspace defines:

- organization
- project
- repositories
- documentation
- memory
- tools
- permissions
- secrets references
- infrastructure
- environment
- logs

## Default policy

Default deny.
Explicit allow.

## Client separation

Client code, data, secrets, documentation, and proprietary logic must not
cross into another client workspace by default.

## Knowledge classification

### PRIVATE

Must remain inside its authorized scope.

### ABSTRACTABLE

May potentially produce generalized engineering learning after sanitization
and review.

### GLOBAL

Public or explicitly authorized reusable knowledge.

## Knowledge firewall

Project experience must pass classification, sanitization, and review before
being promoted outside its original scope.
""")

write("docs/foundation/SECURITY_PRINCIPLES.md", """
# Security Principles

## Least privilege

Lucius receives only the permissions required for the current task.

## Secrets

Lucius memory is not a secrets manager.

Credentials should live in an external secure store and be accessed through
references or controlled temporary access.

Secrets must be excluded from:

- long-term memory
- embeddings
- documentation
- training datasets
- unnecessary logs

## Environments

Initial environment model:

- SANDBOX
- DEVELOPMENT
- STAGING
- PRODUCTION

Authority increases as environmental risk increases.

## Emergency controls

The architecture must eventually support:

- PAUSE LUCIUS
- PAUSE PROJECT
- READ-ONLY MODE
- REVOKE CREDENTIALS
- GLOBAL SAFE MODE

## Audit-sensitive events

Special audit attention is required for:

- permission escalation
- cross-project retrieval
- knowledge promotion
- secret access
- production access
- deployment
- destructive operations
""")

write("docs/foundation/DEVELOPMENT_OPERATING_MODEL.md", """
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

## Repository reality principle

Current repository state overrides remembered state.

When memory and repository reality disagree, Lucius must investigate rather
than blindly trust memory.

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
""")

write("docs/foundation/DOCUMENTATION_POLICY.md", """
# Documentation Policy

Documentation is part of the Definition of Done.

## Lifecycle

Capture continuously
→ Subphase documentation checkpoint
→ Phase documentation package
→ Master documentation integration

## Mandatory rule

No documentation checkpoint means no phase closure.

## Documentation triggers

Documentation must be reviewed or updated when:

- architecture changes
- significant decisions are made
- interfaces change
- migrations are introduced
- important failures occur
- limitations are identified
- benchmarks complete
- subphases complete
- phases complete

## Required task completion evidence

Where applicable:

- acceptance criteria
- implementation summary
- affected files
- tests and results
- architecture impact
- decisions
- limitations
- documentation changes
- commit or trace reference
- rollback information
- learning candidates
- next steps
""")

# ---------------------------------------------------------------------
# Architecture / decisions / glossary / phase report
# ---------------------------------------------------------------------

write("docs/architecture/SYSTEM_OVERVIEW.md", """
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
├── Audit
└── Documentation

The underlying reasoning model is replaceable.

The persistent identity of Lucius resides in its accumulated knowledge,
memory, policies, processes, evaluations, and engineering history.

## Initial principle

Phase 1 should build the smallest useful technical Lucius rather than a
fully autonomous coding agent.
""")

write("docs/decisions/PHASE_0_DECISION_REGISTER.md", """
# Phase 0 Decision Register

| ID | Decision |
|---|---|
| D-001 | Lucius is an engineering system, not a specific LLM. |
| D-002 | Alfred coordinates Lucius as PM/orchestrator. |
| D-003 | Darwin and Lucius remain specialized peers. |
| D-004 | Daniel retains ultimate authority. |
| D-005 | Initial learning is externalized and auditable. |
| D-006 | Important knowledge requires provenance. |
| D-007 | Experience requires evaluation before promotion. |
| D-008 | Failures are first-class learning artifacts. |
| D-009 | Repository reality overrides remembered state. |
| D-010 | Every task operates inside an authorized workspace. |
| D-011 | Client information is isolated by default. |
| D-012 | Secrets remain outside Lucius long-term memory. |
| D-013 | Incremental development is the default. |
| D-014 | Documentation is part of Definition of Done. |
| D-015 | Resource selection is cost-aware. |
| D-016 | Critical operations require explicit authority. |
| D-017 | Knowledge must remain portable across model changes. |
| D-018 | Lucius improvement must be measurable through evaluation. |
""")

write("docs/glossary/GLOSSARY.md", """
# Glossary

## Alfred
DFG Universe PM, butler, coordinator, and orchestration layer.

## Darwin
Research & Intelligence Engine.

## Lucius
Software Engineering Intelligence and long-term private engineering system.

## Engine
Specialized software or agent system responsible for a defined operational
domain.

## Workspace
Explicit execution boundary containing authorized project context,
repositories, tools, memory, permissions, and infrastructure.

## Task Contract
Structured description of an engineering assignment.

## Candidate Learning
Potential knowledge extracted from experience that has not yet been promoted
to validated knowledge.

## Validated Knowledge
Knowledge supported by sufficient evidence and approved for reuse within its
authorized scope.

## Provenance
Traceable origin and evidence supporting a knowledge item.

## Knowledge Firewall
Boundary controlling whether project-specific information may be promoted to
a broader knowledge scope.

## ADR
Architecture Decision Record.

## Definition of Done
Required conditions before work can be declared complete, including required
documentation.
""")

write("docs/phases/PHASE_0_FOUNDATION.md", """
# Phase 0 — Foundation & Charter

## Objective

Define Lucius before implementing Lucius.

## Subphases

### 0.1 Mission & Identity
Complete.

### 0.2 Responsibility & Authority Model
Complete.

### 0.3 Learning Architecture Principles
Complete.

### 0.4 Project & Client Isolation Model
Complete.

### 0.5 Development Operating Model
Complete.

### 0.6 Foundation Documentation Package
Persisted through this documentation set.

## Key outputs

- project charter
- system boundaries
- authority model
- learning architecture
- memory model
- workspace isolation
- security principles
- development operating model
- documentation policy
- architecture overview
- ADR register
- decision register
- glossary

## Phase 1 entry criteria

Before Phase 1 may begin:

- all required Phase 0 documents must exist
- documents must contain persisted content
- ADRs must exist
- documentation audit must pass
- repository must have a clean baseline commit

## Closure state

This document does not itself authorize closure.

Phase 0 becomes CLOSED only after the documentation audit passes and the
foundation baseline is committed to Git.
""")

# ---------------------------------------------------------------------
# ADRs
# ---------------------------------------------------------------------

adrs = [
("001", "Model Independence",
 "Lucius must not be architecturally identified with a single LLM provider or model.",
 "The underlying reasoning model must be replaceable while preserving Lucius knowledge, memory, policies, and experience."),

("002", "Alfred Orchestration",
 "Alfred is the PM and orchestration authority for the DFG Universe.",
 "Lucius receives coordinated engineering assignments through Alfred or explicitly authorized direct workflows."),

("003", "Darwin Lucius Specialization",
 "Darwin and Lucius are specialized peers rather than replacements for one another.",
 "Darwin produces research intelligence; Lucius produces engineered systems."),

("004", "Externalized Learning",
 "Initial Lucius learning will be stored externally to model weights.",
 "Memory, experience, knowledge, evaluation, and procedures remain independently portable."),

("005", "Knowledge Provenance",
 "Important knowledge must retain provenance.",
 "Lucius should know where material knowledge came from, its evidence, scope, version, and validation state."),

("006", "Learning Promotion Gate",
 "Experience must not automatically become validated knowledge.",
 "Candidate learning requires evaluation before broader reuse."),

("007", "Failure Memory",
 "Failures are first-class learning artifacts.",
 "Relevant failures, attempts, diagnoses, fixes, and results must be retained when they provide reusable engineering value."),

("008", "Workspace Isolation",
 "Every Lucius task must operate inside an explicit authorized workspace.",
 "Repositories, tools, memory, permissions, and environment are bounded by workspace context."),

("009", "Client Knowledge Boundary",
 "Client-private information must not cross client boundaries by default.",
 "Only appropriately sanitized and authorized abstract learning may be promoted beyond its original workspace."),

("010", "External Secrets Management",
 "Lucius long-term memory must not act as a secrets manager.",
 "Credentials are stored externally and exposed only through controlled references or temporary access."),

("011", "Repository Reality Principle",
 "Current repository state overrides Lucius memory.",
 "Memory is advisory context; implementation decisions must inspect present repository reality."),

("012", "Incremental Development",
 "Incremental implementation and testing are the default.",
 "Large autonomous rewrites require stronger justification and authority."),

("013", "Documentation Definition of Done",
 "Documentation is part of the Definition of Done.",
 "A phase cannot close while its required documentation checkpoint remains incomplete."),

("014", "Cost Aware Resource Routing",
 "Engineering resource selection must be cost-aware.",
 "Lucius should use the cheapest capable reliable resource and escalate when needed."),

("015", "Critical Action Authority",
 "Critical or irreversible operations require explicit authority.",
 "Production, destructive data operations, billing, credentials, and major security changes are never treated as ordinary safe writes."),

("016", "Portable Knowledge",
 "Lucius accumulated knowledge must remain portable across underlying model changes.",
 "DFG must not lose Lucius engineering experience when switching providers or local models."),

("017", "Continuous Evaluation",
 "Lucius improvement must be measured rather than assumed.",
 "A future benchmark suite must evaluate repository understanding, planning, coding, testing, retrieval, boundary compliance, and documentation."),

("018", "Versioned Human Corrections",
 "Human corrections must be versioned and traceable.",
 "Corrections supersede or invalidate prior knowledge without silently erasing historical reasoning and evidence.")
]

adr_files = {
"001":"model-independence",
"002":"alfred-orchestration",
"003":"darwin-lucius-specialization",
"004":"externalized-learning",
"005":"knowledge-provenance",
"006":"learning-promotion-gate",
"007":"failure-memory",
"008":"workspace-isolation",
"009":"client-knowledge-boundary",
"010":"external-secrets-management",
"011":"repository-reality-principle",
"012":"incremental-development",
"013":"documentation-definition-of-done",
"014":"cost-aware-resource-routing",
"015":"critical-action-authority",
"016":"portable-knowledge",
"017":"continuous-evaluation",
"018":"versioned-human-corrections",
}

for num, title, decision, consequence in adrs:
    write(
        f"docs/adr/ADR-{num}-{adr_files[num]}.md",
        f"""
        # ADR-{num}: {title}

        **Status:** Accepted
        **Phase:** 0

        ## Context

        Lucius requires a durable architectural baseline before technical
        implementation begins.

        ## Decision

        {decision}

        ## Consequences

        {consequence}

        ## Review

        This ADR may later be superseded by a new ADR, but historical
        rationale must remain available.
        """
    )

# ---------------------------------------------------------------------
# Documentation index
# ---------------------------------------------------------------------

write("docs/DOCUMENTATION_INDEX.md", """
# Documentation Index

This file identifies the canonical Phase 0 documentation baseline.

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

## Phase reports

- `phases/PHASE_0_FOUNDATION.md`

## Reference

- `glossary/GLOSSARY.md`

## Documentation status

Phase 0 content: PERSISTED

Phase 0 closure: PENDING AUDIT + GIT BASELINE COMMIT
""")

# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------

required = [
    "README.md",
    "CHANGELOG.md",
    "docs/DOCUMENTATION_INDEX.md",
    "docs/foundation/PROJECT_CHARTER.md",
    "docs/foundation/SYSTEM_BOUNDARIES.md",
    "docs/foundation/AUTHORITY_MODEL.md",
    "docs/foundation/LEARNING_ARCHITECTURE.md",
    "docs/foundation/MEMORY_MODEL.md",
    "docs/foundation/WORKSPACE_ISOLATION.md",
    "docs/foundation/SECURITY_PRINCIPLES.md",
    "docs/foundation/DEVELOPMENT_OPERATING_MODEL.md",
    "docs/foundation/DOCUMENTATION_POLICY.md",
    "docs/architecture/SYSTEM_OVERVIEW.md",
    "docs/decisions/PHASE_0_DECISION_REGISTER.md",
    "docs/glossary/GLOSSARY.md",
    "docs/phases/PHASE_0_FOUNDATION.md",
]

required += [
    f"docs/adr/ADR-{n}-{adr_files[n]}.md"
    for n in adr_files
]

errors = []

for rel in required:
    p = ROOT / rel
    if not p.exists():
        errors.append(f"MISSING: {rel}")
    elif p.stat().st_size < 20:
        errors.append(f"EMPTY/TOO SMALL: {rel}")

print()
if errors:
    print("PHASE 0 DOCUMENTATION AUDIT: FAILED")
    for e in errors:
        print(e)
    raise SystemExit(1)

print(f"PHASE 0 DOCUMENTATION AUDIT: PASS ({len(required)} required documents)")
print("Documentation content is persisted.")
print("Phase closure still requires Git baseline commit.")
