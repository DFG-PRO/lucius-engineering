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

Current locally observed Ollama models include:

- `qwen3:8b`;
- `qwen3-coder:30b`;
- `qwen2.5vl:7b`;
- `qwen2.5vl:3b`.

These model names are local environment evidence, not a universal Lucius
assumption. `qwen3:8b` remains the Tier 1 candidate/default for bounded,
simple, automatically verifiable local tasks. `qwen3-coder:30b` remains
supervised/schema-constrained only after later qualification evidence showed
fabricated evidence references; it is not qualified for unattended operation
or Night Shift use. `qwen2.5vl:7b`
is available for visual workloads and possible Production Engine use, but
Production Engine integration and qualification remain governed by that
project's own evidence and authority gates. Codex and other premium providers
remain escalation paths; they are not required for validated local Ollama
provider execution.

On the current Mac mini with Apple M2 and 24 GB unified memory,
`qwen3-coder:30b` should be treated as a heavy local workload. Avoid concurrent
heavy model workloads until resource scheduling and concurrency are explicitly
validated for this machine. This is operational guidance for the observed host,
not a Lucius architecture rule.

## Provider Quality Gate

Evidence-sensitive provider outputs are subject to deterministic quality
validation after provider execution and before Lucius accepts the normalized
provider result. The gate distinguishes:

- `FACT`;
- `DERIVED_VALUE`;
- `ASSUMPTION`;
- `PROPOSED_PARAMETER`;
- `UNKNOWN`;
- `REQUIRES_VALIDATION`;
- `INSUFFICIENT_EVIDENCE`.

Unsupported quantitative or factual claims in evidence-sensitive workflows
must fail closed. Proposed protocol values, thresholds, dates, costs, markets,
credentials, or performance values must not be represented as verified facts
without supplied evidence. `FACT` and `DERIVED_VALUE` quantitative claims must
carry evidence/source/provenance on the same claim line where the current
line-oriented gate can verify it.

Identifier and provenance tokens are not quantitative claims merely because
they contain numbers. Git SHAs, file/content hashes, UUIDs, Lucius task,
workflow, audit, plan, and freeze IDs, version identifiers, semantic asset IDs,
and repository identifiers may appear as provenance without being classified as
financial or research quantities. This exemption is intentionally narrow and
does not apply to genuine financial, trading, performance, threshold, window,
date, leverage, or research-metric claims.

## Schema-Constrained Provider Execution

For schema-sensitive work, Lucius can now use a provider-neutral
schema-constrained execution path:

```text
canonical schema
  -> deterministic output skeleton
  -> provider completion
  -> safe deterministic structural repair
  -> provider quality validation
  -> deterministic acceptance or fail closed
```

The runtime/router path derives a deterministic output skeleton from the
request's schema constraint and passes it to the provider in request metadata.
The Ollama provider includes that skeleton in the model prompt and instructs
the model to preserve the required structure. After provider execution, Lucius
checks required files, sections, labels, and explicitly forbidden semantic
patterns before running the evidence-sensitive quality gate.

Deterministic repair is limited to safe structural placeholders, such as
restoring a missing required empty category with
`DERIVED_VALUE: NOT_ESTABLISHED`. It must not invent semantic content,
evidence, conclusions, thresholds, performance, or readiness claims. Unsafe
semantic content, fabricated evidence, missing files, unsafe paths, unsupported
quantitative claims, or unrecoverable schema omissions fail closed.

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
verification-handoff rejection, provider replaceability, Ollama prompt skeleton
delivery, schema-constrained structural validation, safe missing-category
repair, unsupported quantitative-claim rejection, fabricated-evidence
rejection, conservative schema-valid output acceptance, provenance identifier
handling, and provider/model identity preservation.

Relevant validated local-provider commits:

- `89fe49d49922f986aeef5dc3e27368b3c1695492`: added the local Ollama runtime provider;
- `0b6b661c33a9128e03c3c97c848374d1269399be`: preserved the native runtime planning path repair;
- `f5a4069ab73733b99a7f523f99f69a0aaf4d925d`: hardened provider output quality checks;
- `d4707eb9db37a096480e46aa258a60457f32836d`: refined identifier/provenance handling;
- `a01494384f4285735306dadac3e493ad8ec46fd7`: added schema-constrained provider execution.

Post-commit benchmark `LBENCH_000109` passed
`LUCIUS_CORE_BENCH_V0_1` with aggregate 100 and hard gate PASS on the clean
`a01494384f4285735306dadac3e493ad8ec46fd7` tree. Later
`qwen3-coder:30b` qualification evidence supersedes any stronger reading of
local benchmark performance: the model is supervised/schema-constrained only,
not unattended.

## Deferred

This phase does not implement the Multi-Project Single Dispatcher, multi-worker
concurrency, leases, heartbeats, provider marketplace discovery, automatic
credential provisioning, push/merge/deploy authority, production/live-money
execution authority, provider-controlled lifecycle, provider-controlled global
scheduling, or autonomous spending outside existing policy.
