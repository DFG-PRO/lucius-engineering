# Controlled Mutation Provisioning

## Purpose

`provision-controlled-mutation` creates bounded L1 engineering workflows for
target-project mutation through the existing Lucius runtime.

It is the mutation-capable sibling of `provision-read-only-backlog`.

## Authority boundary

Provisioning does not authorize unrestricted writes.

Version 1 requires:

- exact frozen canonical repository HEAD;
- a pre-existing clean isolated Git worktree;
- an explicitly authorized workspace root;
- explicit mutation paths;
- deterministic acceptance coverage for every mutation path;
- `mutation_allowed=true`;
- `read_only=false`;
- supervised or human-approved execution;
- L1 task, contract, plan, freeze, and workflow authority.

Version 1 explicitly rejects unattended mutation.

The provisioned task contract does not grant `CREATE_COMMIT`.

Provisioning does not push, merge, deploy, trade, spend money, contact external
parties, or mutate the canonical target repository.

## Lifecycle

The intended lifecycle is:

1. provision controlled mutation;
2. runtime preflight;
3. execute in isolated workspace;
4. deterministic acceptance verification;
5. reach `COMPLETED_PENDING_INTEGRATION`;
6. perform controlled canonical integration under the existing integration
   authority boundary;
7. close the workflow;
8. perform controlled commit under L2 authority when separately authorized.

Canonical integration and controlled commit remain separate operations.

## Target-project documentation

When a retained mutation changes behavior, architecture, interfaces,
operations, or capabilities, the target project's canonical documentation must
be included in the authorized mutation scope and updated with the change.

Lucius documentation records orchestration and verification evidence but does
not replace target-project canonical documentation.

## Safety properties

The provisioner fails closed when:

- canonical HEAD differs from the frozen baseline;
- workspace HEAD differs from the frozen baseline;
- the workspace is dirty;
- workspace is outside its allowed root;
- canonical and candidate workspaces are the same;
- mutation paths are invalid or duplicated;
- deterministic acceptance does not cover every authorized path;
- execution supervision is unsupervised;
- unattended mutation is requested;
- deterministic verification is disabled.

## Initial use

The first intended productive use is bounded development of Darwin Research
Engine's Portfolio 01 / Research Backlog 01 from an exact canonical Darwin
baseline.
