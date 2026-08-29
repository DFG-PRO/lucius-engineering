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

Phase 1.7 enforces this at the memory boundary. Client project knowledge and
client-scoped memory are PRIVATE by default. Private knowledge cannot be
promoted globally. Reusable global knowledge requires provenance, validation,
non-private classification, completed sanitization where required, and an
explicit HUMAN or SYSTEM promotion actor.
