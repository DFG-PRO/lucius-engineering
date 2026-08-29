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

## Memory safety

Long-term memory must not become a bypass around workspace isolation, evidence
freshness, or authority controls. Phase 1.7 therefore blocks autonomous global
promotion, requires provenance for validated memory, requires evidence IDs for
validated global memory, and marks memory for revalidation when source evidence
becomes stale or missing.
