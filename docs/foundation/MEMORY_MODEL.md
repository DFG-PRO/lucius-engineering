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
