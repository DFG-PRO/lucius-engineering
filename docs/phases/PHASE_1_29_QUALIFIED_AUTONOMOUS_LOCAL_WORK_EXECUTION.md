# Phase 1.29 Qualified Autonomous Local Work Execution

Phase 1.29 moves Lucius from local-model qualification to bounded autonomous
execution of real queue work. The phase does not expand model capability;
instead, it proves that the runtime can select only qualified local work,
reject tasks outside the model profile before inference, preserve dependency
semantics, and continue independent eligible work without Codex or operator
correction.

## Scope

The phase targets qualified unattended local execution through the existing
native runtime architecture:

- `MultiProjectDispatcher` determines operational queue eligibility;
- `ModelExecutionRouter` determines provider/model eligibility;
- `ExecutionRuntimeLoopService` executes bounded work and preserves queue
  lifecycle;
- local Ollama execution remains isolated, fail-closed, and explicitly rooted.

No new scheduler, queue architecture, or model gateway was introduced.

## Architectural Boundary

Operational task eligibility and model capability eligibility remain separate.

The dispatcher answers:

> Can this queue item run operationally?

The router answers:

> Is there an authorized and qualified executor for this request?

An operationally valid task may therefore be selected by the dispatcher and
then rejected by the router before provider inference. This separation is
intentional and preserves provider-neutral scheduling.

## Task-Local No-Eligible-Provider Hardening

Before Phase 1.29, `NO_ELIGIBLE_PROVIDER` returned a failed adapter result but
did not carry a task-local failure class. The runtime therefore treated the
condition as loop-fatal, which could stop unrelated independent work.

Phase 1.29 classifies this condition explicitly as:

`failure_class="NO_ELIGIBLE_PROVIDER"`

and includes it in the runtime task-local failure set.

The resulting behavior is:

- the rejected queue item moves fail-closed to `WAITING_HUMAN`;
- the runtime does not invoke an unqualified provider;
- dependent work remains blocked by normal dependency semantics;
- independent eligible work may continue;
- the condition remains visible through canonical audit evidence.

No model capability knowledge was added to the dispatcher.

## Deterministic Validation

The Phase 1.29 hardening patch was validated against:

- model execution router integration tests;
- native execution runtime loop integration tests;
- the complete canonical test suite.

Focused validation:

`73 passed`

Canonical full-suite validation:

`401 passed`

`git diff --check` was clean.

A repeated stability soak then executed the complete 401-test canonical suite
60 times. The first run was manually interrupted and terminated with an
explicit `KeyboardInterrupt`; it is not classified as a Lucius test failure.

All remaining valid runs passed:

`59 / 59 valid soak runs PASS`

No regression was observed across the valid soak population.

## Real Autonomous Local Pilot

A real Phase 1.29 pilot executed against:

- a temporary SQLite database;
- an isolated temporary Git repository under `/private/tmp`;
- real local Ollama;
- `qwen3:8b`;
- `UNSUPERVISED` execution;
- explicit allowed workspace root;
- read-only tasks;
- deterministic verification;
- zero semantic retries;
- zero failovers;
- zero Codex usage;
- no push, merge, deploy, or canonical integration.

The pilot used workflow:

`LWORK_000001`

with repository:

`LREPO_PHASE129`

and five queue items.

### Pilot Topology

`P129-A`

- complexity: T0;
- independent;
- read-only;
- eligible for `qwen3:8b`;
- result: `COMPLETED`.

`P129-B`

- complexity: T1;
- independent;
- read-only;
- eligible for `qwen3:8b`;
- result: `COMPLETED`.

`P129-C`

- complexity: T2;
- unattended;
- operationally eligible for dispatch;
- outside the qualified unattended profile of `qwen3:8b`;
- rejected by the router before provider inference;
- result: `WAITING_HUMAN`.

`P129-D`

- depends on `P129-C`;
- not dispatched after the dependency failed closed;
- result: `READY` with unresolved dependency.

`P129-E`

- complexity: T1;
- independent;
- eligible for `qwen3:8b`;
- executed after `P129-C` was rejected;
- result: `COMPLETED`.

## Real Provider Evidence

Exactly three real local model executions were recorded:

- `LMEXEC_000001`
- `LMEXEC_000002`
- `LMEXEC_000004`

All three used:

- provider: `ollama-local-phase129`;
- model: `qwen3:8b`;
- status: `COMPLETED`;
- attempt number: `1`;
- fallback: `false`;
- error code: none.

The deliberately ineligible T2 item did not create a model execution attempt.

The runtime emitted exactly one
`MODEL_EXECUTION_NO_ELIGIBLE_PROVIDER` audit event for the rejected item.

The runtime continued after the rejection and later completed independent
eligible work. It stopped only when no eligible multi-project work remained:

`NO_ELIGIBLE_MULTI_PROJECT_WORK`

Runtime wall-clock duration for the pilot was approximately 23.6 seconds.

## Isolation And Mutation Safety

The pilot repository finished Git-clean:

`git status --short` returned no changes.

The pilot did not modify the Lucius canonical repository, did not integrate
temporary work, and did not exercise any live financial or external side
effect.

The Phase 1.29 source patch remained uncommitted during empirical validation so
that the real pilot could validate the exact working-tree behavior before
formal closure.

## Observability Note

Current `model_executions.task_id` records the parent Lucius task identity.
Multiple backlog items belonging to the same parent task therefore share the
same `task_id`.

Individual queue-item identity remains reconstructable from runtime task
records and audit events, but `model_executions` does not yet expose backlog
`item_id` directly.

This is an observability improvement opportunity, not a Phase 1.29 safety or
closure blocker.

## Capability Conclusion

Phase 1.29 demonstrates that Lucius can autonomously execute a bounded queue of
qualified local work while remaining fail-closed around unsupported work.

Under the tested constraints, Lucius can now:

- execute qualified T0/T1 local work through `qwen3:8b`;
- reject work exceeding the model's unattended profile before inference;
- preserve dependency blocking after rejection;
- continue independent work after task-local model ineligibility;
- record real model execution evidence;
- remain isolated and Git-clean;
- operate without Codex for work that does not require Codex capability.

This does not authorize general unattended engineering.

`qwen3:8b` remains restricted to small, bounded, low-risk, isolated, and
deterministically verifiable work.

`qwen3-coder:30b` remains supervised and schema-constrained and is not
authorized for unattended execution.

Higher-risk mutation, broad architectural work, evidence-sensitive work, and
other tasks outside qualified profiles continue to fail closed or require an
authorized higher-capability executor or human review.

## Phase Status

Phase 1.29 status:

`IMPLEMENTED + DETERMINISTICALLY VALIDATED + EMPIRICALLY VALIDATED`

Closure evidence:

- focused tests: `73 passed`;
- canonical suite: `401 passed`;
- stability soak: `59 / 59 valid runs PASS`;
- real qwen3:8b autonomous pilot: PASS;
- unsupported T2 unattended task rejected before inference: PASS;
- independent continuation after task-local rejection: PASS;
- dependency preservation: PASS;
- isolated repository Git-clean: PASS;
- Codex usage: zero;
- automatic integration: zero.
