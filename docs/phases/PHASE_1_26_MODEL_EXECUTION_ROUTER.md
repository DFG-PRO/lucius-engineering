# Phase 1.26 Model Execution Router

Phase 1.26 inserts a provider-neutral Model Execution Router between the
native runtime loop and execution providers.

The architectural boundary is:

```text
ExecutionRuntimeLoopService
  -> ModelExecutionRouter
  -> RuntimeExecutionProvider
  -> RuntimeExecutionResult
  -> ExecutionRuntimeLoopService verification/lifecycle handling
```

Lucius runtime remains the orchestration authority. It owns global/task
selection, repository attachment checks, task readiness, pre-mutation release,
frozen-plan authority, queue state, mutation authorization, blocking/resume,
verification, completion, and audit trail. The router owns deterministic
provider eligibility, provider/model selection, bounded provider retry and
failover, routing evidence, provider invocation, and provider result
normalization.

Providers are replaceable workers. They do not select global work, authorize
mutation, mutate frozen plans, mark Lucius tasks complete, unblock tasks, alter
global queue state, suppress verification, or write canonical lifecycle state.

## Canonical Contracts

`RuntimeExecutionRequest` is provider-neutral and is constructed only after the
runtime has already selected an executable queue item and passed canonical
pre-dispatch release validation. It contains immutable task, workflow, queue
item, project, repository, plan, freeze, isolated workspace, allowed mutation
scope, required capability, tool, isolation, context, timeout, budget, and
release-evidence references. It does not expose SQLAlchemy sessions or
provider-specific fields.

`RuntimeExecutionResult` normalizes provider output into a common structure:
execution ID, provider/model identity and versions, routing decision ID,
normalized status, provider-native status, output artifacts, mutation summary,
verification handoff metadata, timing, token usage, cost, retryability, failure
class, fallback/failover information, and sanitized provider error metadata.

The runtime converts the normalized provider result into
`ExecutionAdapterResult` for existing lifecycle handling. Provider success does
not directly complete a task; it only gives Lucius evidence to verify and use
while Lucius advances queue/task lifecycle. A provider result that claims
`COMPLETED` without verification handoff evidence is normalized to fail closed
with `MISSING_VERIFICATION_HANDOFF`.

## Registry

`RuntimeProviderRegistry` is deterministic and explicit. Providers declare a
`RuntimeProviderRegistration` with:

- provider ID and version;
- availability/status;
- capabilities and supported task classes;
- code-modification, workspace, isolation, and tool support;
- context limits;
- model IDs and versions where applicable;
- retry/failover eligibility;
- cost, latency, policy labels, project/repository allowlists, and reliability
  score.

This phase does not add dynamic marketplace discovery or arbitrary
self-registration.

## Routing Policy

`ModelExecutionRouter` evaluates every registered provider against the
authorized request. It filters providers by status, availability, required
capabilities, task type, project/repository policy, isolation mode, tool
requirements, and context limit. Eligible providers are selected
deterministically by cost class, reliability score, and provider ID, so local
free or low-cost capable providers are preferred before premium providers once
health and capability constraints have already passed.

The policy is intentionally simple. It is auditable and deterministic, not an
optimization marketplace.

## Local Ollama Provider

Lucius now includes a minimal `OllamaExecutionProvider` for real local
execution through an Ollama HTTP endpoint. The provider implements the existing
runtime provider contract and receives only a provider-neutral
`RuntimeExecutionRequest`; it does not receive global scheduling authority,
lifecycle authority, release-gate authority, frozen-plan authority,
repository-selection authority, or owner-authorization authority.

The provider requires `ISOLATED_WORKTREE`, validates that the workspace is
inside configured temporary roots, sends bounded task instructions to the
selected local model, captures the model response, applies only bounded
relative file changes returned as strict JSON, records provider/model identity,
latency, token counts exposed by Ollama, zero estimated cost, verification
handoff evidence, and fails closed on unavailable endpoints, malformed
responses, unsafe paths, missing workspaces, or missing file changes.

The canonical runtime CLI can select it with:

```text
python -m lucius.pilots.cli run-runtime-loop --execution-provider ollama --ollama-model qwen3:8b
```

`qwen3:8b` is the preferred local text/code model when available.

## Retry And Failover

Provider retry, provider failover, and Lucius deterministic correction are
separate concepts.

Provider retry repeats the same provider/model after a retryable provider
failure, bounded by `max_provider_retries`.

Provider failover selects a different eligible provider after a failoverable
failure, bounded by `max_failovers`.

Lucius deterministic correction remains outside the router. It reconstructs or
repairs orchestration context, readiness, release, plans, queues, or evidence
through canonical Lucius services. Router retry/failover cannot reopen release
authority, change frozen plans, or bypass pre-dispatch validation.

If no eligible provider remains, routing fails closed and returns a failed
runtime result with `NO_ELIGIBLE_PROVIDER`.

## Audit Events

The router records canonical audit evidence for:

- `MODEL_EXECUTION_ROUTING_REQUEST_ACCEPTED`;
- `MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED`;
- `MODEL_EXECUTION_ROUTING_DECISION`;
- `MODEL_EXECUTION_PROVIDER_INVOCATION_STARTED`;
- `MODEL_EXECUTION_PROVIDER_INVOCATION_COMPLETED`;
- `MODEL_EXECUTION_PROVIDER_RETRY_ATTEMPTED`;
- `MODEL_EXECUTION_PROVIDER_FAILOVER_ATTEMPTED`;
- `MODEL_EXECUTION_NO_ELIGIBLE_PROVIDER`;
- `MODEL_EXECUTION_PROVIDER_RESULT_NORMALIZED`;
- `MODEL_EXECUTION_ROUTING_COMPLETED`.

Audit metadata includes provider/model identity, routing decision ID, execution
ID, candidate reasons, fallback state, bounded retry/failover counts, timing,
token usage, cost, retryability, and sanitized error metadata. Secrets and raw
credentials are not persisted.

## CLI And Runtime Migration

The runtime no longer invokes the scripted execution provider directly. The
canonical CLI builds a registry containing the scripted provider and passes a
`ModelExecutionRouter` into `ExecutionRuntimeLoopService`. Existing scripted
execution behavior remains available through the router path.

## Validation

Focused tests cover single-provider routing, deterministic selection among
eligible providers, capability exclusion, unavailable-provider exclusion,
policy/tool/context exclusion, no-provider fail-closed behavior, bounded
retry, non-retryable failure, bounded failover, failover exhaustion, result
normalization, routing/provider/model audit evidence, provider-neutral request
shape, runtime release-gate containment, CLI routing evidence, missing
verification-handoff rejection, and provider replaceability.

## Deferred

This phase does not implement the Multi-Project Single Dispatcher, multi-worker
concurrency, leases, heartbeats, provider marketplace discovery, automatic
credential provisioning, push/merge/deploy authority, production/live-money
execution authority, provider-controlled lifecycle, provider-controlled global
scheduling, or autonomous spending outside existing policy.
