# Phase 1.28 Model Capability Qualification And Safe Routing

Phase 1.28 converts recent local-model pilot evidence into durable Lucius
runtime behavior. Technical executability is not unattended authorization.

## Evidence Preserved

`qwen3-coder:30b` failed the bounded Tier 2 qualification battery in
`LWORK_000111` / `LTASK_000156` on Q1 by fabricating unrelated quantitative
evidence and canonical-looking evidence references such as
`LPLAN_000119_EVIDENCE_001`. The correct status is
`TIER_2_LOCAL_SUPERVISED_SCHEMA_CONSTRAINED`, not unattended.

`qwen3:8b` executed real local Ollama work during `LWORK_000114` /
`LTASK_000159` / `LPLAN_000122` / `LFREEZE_000122` through
`ollama-local-unattended-safe`. The isolated worktree
`/private/tmp/lucius-unattended-safe` remained clean after the run. Several
items timed out and moved fail-closed while independent queue work continued.
The correct status is `LOCAL_TIER_1` candidate for small, bounded, highly
verifiable local work only.

No historical workflow outcome is rewritten by this phase.

## Runtime Model

`RuntimeProviderModel` may now carry a `ModelCapabilityProfile` describing:

- provider and model identity;
- execution tier and local/external placement;
- supported task classes and capabilities;
- mutation support;
- evidence-sensitive suitability;
- schema-constraint and deterministic-verification requirements;
- supervision and unattended eligibility;
- task complexity and timeout ceilings;
- qualification status.

Statuses are `QUALIFIED`, `QUALIFIED_WITH_CONSTRAINTS`, `SUPERVISED_ONLY`,
`NOT_QUALIFIED`, and `DISABLED`.

## Initial Local Policy

`qwen3:8b` is encoded as `LOCAL_TIER_1` and
`QUALIFIED_WITH_CONSTRAINTS`. It is eligible for unattended routing only when
the task is small enough for its configured profile, isolated, bounded,
deterministically verifiable, and within timeout policy.

`qwen3-coder:30b` is encoded as
`TIER_2_LOCAL_SUPERVISED_SCHEMA_CONSTRAINED` and `SUPERVISED_ONLY`. It cannot
be selected for unattended execution under current evidence. `unattended=false`
is not supervised authorization; supervision must be explicit. Because its
profile requires schema-constrained execution, an explicit supervision state
does not bypass `schema_constrained_required`; supervised execution without a
schema constraint fails closed with `SCHEMA_CONSTRAINT_REQUIRED`.

Unprofiled local models are not qualified for unattended execution.

Codex remains a higher-capability engineering escalation path according to
Lucius architecture, not an assumption of unrestricted autonomy. Human review
remains the final approval/escalation authority where policy requires it.

## Unattended Eligibility

The router now rejects unattended candidates deterministically when policy
requires it. Explicit reason codes include:

- `MODEL_SUPERVISION_REQUIRED`;
- `MODEL_NOT_QUALIFIED_FOR_UNATTENDED`;
- `MODEL_LACKS_REQUIRED_CAPABILITY`;
- `TASK_COMPLEXITY_UNKNOWN`;
- `TASK_COMPLEXITY_EXCEEDS_MODEL_PROFILE`;
- `TASK_RISK_UNKNOWN`;
- `EVIDENCE_VALIDATION_REQUIRED`;
- `DETERMINISTIC_VERIFICATION_UNKNOWN`;
- `DETERMINISTIC_VERIFICATION_REQUIRED`;
- `SCHEMA_CONSTRAINT_REQUIRED`;
- `WORKSPACE_ISOLATION_UNKNOWN`;
- `WORKSPACE_ALLOWED_ROOT_UNKNOWN`;
- `WORKSPACE_ISOLATION_REQUIRED`;
- `EXTERNAL_SIDE_EFFECT_NOT_AUTHORIZED`;
- `FINANCIAL_ACTION_NOT_AUTHORIZED`;
- `TIMEOUT_BUDGET_EXCEEDS_MODEL_PROFILE`;
- `SEMANTIC_RETRY_NOT_AUTHORIZED`.

Read-only inspection/reasoning can request `inspection_reasoning` without
claiming mutation authority. Mutation work still requires a provider/model
that advertises mutation-capable capabilities. Read-only is now a runtime
execution constraint: providers must not apply file changes for read-only
requests, and a returned file-change payload fails closed with
`READ_ONLY_MUTATION_ATTEMPT`.

For unattended execution, missing or unknown complexity, risk, deterministic
verification state, isolation mode, or explicit local workspace roots fails
closed. Missing metadata must not make a task more eligible.

## Evidence And Quantitative Validation

Evidence-sensitive provider output validates generated evidence reference
fields and canonical evidence-reference tokens against allowed evidence refs.
When `evidence_reference_validation_required` is explicit, evidence-reference
validation runs even if heuristic evidence-sensitive detection does not trigger.
Syntactically plausible but unauthorized references fail closed; Lucius does
not invent replacements or delete unsupported claims. Canonical
`evidence_reference` / `evidence_references` fields accept only a string or a
list of strings; malformed structures fail closed.

The quantitative gate ignores only explicit structural JSON metadata counters
such as `total_statements` and `categorized_count`, while preserving
fail-closed behavior for real unsupported semantic quantities such as win rate,
capital, thresholds, leverage, dates, trade counts, profit totals, and research
metrics.

When evidence-reference validation is required, `FACT` and `DERIVED_VALUE`
claims must cite an allowed evidence reference.

## Workspace Isolation

The Ollama provider still fails closed outside configured workspace roots. The
operator CLI now exposes explicit safe roots:

```bash
python -m lucius.pilots.cli run-runtime-loop LWORK_000114 \
  --execution-provider ollama \
  --ollama-model qwen3:8b \
  --ollama-allowed-workspace-root /private/tmp
```

`LUCIUS_OLLAMA_ALLOWED_WORKSPACE_ROOTS` may also provide roots separated by the
platform path separator. Isolated worktrees are supported as allowed roots, but
unattended local execution requires explicit allowed roots. No merge,
cherry-pick, push, or deployment is implicit.

## Timeout And Retry

Timeout budget is part of routing. A task known to exceed the selected model's
profile is rejected before spending inference time. Timeout failures remain
fail-closed and are recorded distinctly as `PROVIDER_TIMEOUT`. Semantic
rejection is non-retryable and remains distinct from provider infrastructure
retry/failover.

Runtime loop failure behavior distinguishes task-local semantic failures from
runtime-fatal failures. A task-local validation failure blocks that queue item
and may allow independent eligible work to continue; dependent work remains
blocked by dependency semantics. Runtime-fatal failures still stop the loop.

## Observability Contract

The previous empty `model_executions` observation occurred because the
`ModelGateway` path populated `model_executions`, while the newer native
runtime router only wrote audit events. Phase 1.28 keeps audit events as audit
evidence and also writes a canonical `model_executions` row for each real
runtime provider attempt.

Runtime model execution records now preserve workflow/task/run relationship,
provider/model identity, routing decision metadata, status, latency,
token/cost fields when known, fallback state, attempt number, error code, and
timestamp.

The legacy `ModelGateway` remains a planning/general model-request pathway that
owns its own `model_executions` persistence. Native runtime execution and safe
unattended routing go through `ModelExecutionRouter`; the Phase 1.28
eligibility policy is not duplicated into `ModelGateway`.

## Validation

Focused tests cover allowed and fabricated evidence references, missing
required evidence references, structural JSON counter handling, preservation of
semantic quantitative claim rejection, read-only capability routing, mutation
capability enforcement, Tier 1 unattended eligibility, Tier 1 complexity
rejection, supervised-only Tier 2 unattended rejection, supervised Tier 2
schema-constraint enforcement, unprofiled model rejection, CLI workspace-root
configuration, qwen3-coder supervised-only profile encoding, read-only mutation
rejection, timeout classification, unattended explicit workspace roots,
task-local semantic failure continuation, and runtime `model_executions`
persistence.

A bounded real `qwen3:8b` Phase 1.28 smoke executed through the native
`ModelExecutionRouter` in `/private/tmp/lucius-phase128-smoke`. It produced
exactly one canonical `model_executions` attempt (`LMEXEC_000060`), completed
with no fallback or error, preserved a Git-clean workspace, and emitted a
coherent routing/audit sequence from request acceptance through completion.

Final canonical validation after the supervised schema-constraint correction:
`400 passed`; `git diff --check` clean.
