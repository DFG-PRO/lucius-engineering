# Phase 1.30 Qualified Autonomous Local Mutation

Phase 1.30 extends Lucius from bounded autonomous read-only local work to bounded unattended local mutation under strict deterministic controls. It does not authorize general unattended engineering or automatic integration.

## Scope and authority

At Phase 1.30 closure, the qualified unattended mutation model was
`qwen3:8b`, limited to T0/T1, LOW-risk, isolated, explicitly rooted and
deterministically verifiable work.

Qualification update: later corrected-runtime evidence supersedes the original
model authorization. `LWORK_000147` / `LMEXEC_000196` / `LQCHK_000092` and
`LWORK_000148` / `LMEXEC_000197` / `LQCHK_000093` both failed deterministic
mutation verification after successful provider invocation because the model
produced invalid or incomplete Python. `qwen3:8b` remains useful for bounded
read-only or non-mutating local support work, but it is no longer qualified for
unattended code mutation under the current Lucius runtime contract.

Mutation authority is derived only from `PlanFreeze.plan_payload["affected_files"]`, not from model output, queue metadata or authority-tier labels:

`Plan -> Freeze -> Release Gate -> Allowed Mutation Paths -> Model -> Atomic Enforcement -> Deterministic Verification -> Completion`

Missing, mismatched, empty or malformed frozen mutation scope fails closed before provider inference as `MITATION_SCOPE_VIOLATION`. Read-only work does not require mutation authorization.

## Atomic mutation and Git verification

`OllamaExecutionProvider` validates the complete proposed file set before the first write. Unauthorized, duplicate, absolute, traversal, escaping or otherwise invalid paths are rejected atomically.

The workspace must be Git-clean before mutation. After mutation, Lucius observes unstaged, staged and untracked changes through Git. Actual changed paths must remain within the frozen scope, and provider-reported written paths must be observable in Git. No-op reported writes and unexpected side effects fail as `DETERMINISTIC_MUTATION_VERIFICATION_FAILED`.

## Deterministic semantic acceptance

Phase 1.30 introduces `deterministic_acceptance_checks` as canonical planning and freeze data. The first supported contract is `exact_file_content`: an authorized relative path plus exact expected UTF-8 text.

The lifecycle is:

`acceptance criteria -> EngineeringPlan -> deterministic_acceptance_checks -> PlanFreeze -> RuntimeExecutionRequest -> deterministic verifier`

For unattended mutation, a valid frozen deterministic acceptance check is required. Missing, malformed, unsupported, duplicate or out-of-scope checks fail closed before provider inference. Model verification claims are supplemental only and cannot override deterministic failure.

The field is persisted in `EngineeringPlanORM`, included in the frozen plan payload and migrated by `0012_deterministic_acceptance_checks`.

## Rollback and queue semantics

After crossing the write boundary, deterministic safety or acceptance failure restores the isolated workspace with Git reset/clean semantics and verifies cleanliness.

`MUTATION_SCOPE_VIOLATION` and `DETERMINISTIC_MUTATION_VERIFICATION_FAILED` are task-local failures. The failed item moves to `WAITING_HUMAN`; dependent work remains unresolved; independent eligible work may continue. Failed work is never automatically integrated.

## Real empirical evidence

A direct real `qwen3:8b` provider micro-smoke mutated only the authorized `pilot-proof.txt` and passed both Git-scope and exact-content verification.

The full native-runtime pilot used a temporary Git repository, in-memory Lucius DB, canonical project/task/contract/workflow/plan/freeze, real local Ollama and `qwen3:8b`.

Canonical pilot identities:

- workflow `L[ORK_000001`
- task `LTASK_000001`
- plan `LPLAN_000001`
- freeze `LFREEZE_000001`
- model execution `LMEXEC_000001`

Target: `docs/runtime.md`

Expected exact content: `phase 1.30b autonomous mutation passed\n`

Result:

- runtime `COMPLETED`
- selected 1 / completed 1 / blocked 0
- exactly one model execution
- provider `ollama-local`
- model `qwen3:8b`
- attempt 1, no fallback, no error
- retries 0, escalations 0
- wall time ~6.63 s
- workflow `COMPLETED_PENDING_INTEGRATION`
- repository HEAD unchanged from baseline
- only authorized target observed as changed
- no merge, push, deploy or canonical integration

An initial harness assertion was a false negative because default Git porcelain collapsed the untracked directory as `?? docs/`; `--untracked-files=all` confirmed the exact target path.

## Validation

Phase 1.30 coverage includes frozen-scope derivation and rejection, atomic unauthorized-path rejection, duplicate/traversal protection, Git-observed scope enforcement, unexpected side-effect detection, no-op rejection, exact semantic acceptance, rollback, task-local continuation, planning validation, persistence roundtrip, freeze preservation/rejection, and Alembic migration.

Closure validation:

- expanded focused suite: `156 passed`
- canonical full suite: `420 passed`
- planning/freeze regression suite: `51 passed`
- migration-specific test: `1 passed`
- real qwen3:8b provider mutation smoke: PASS
- real native-runtime unattended mutation pilot: PASS
- `git diff --check`: clean

## Capability boundary

Phase 1.30 proves bounded unattended local mutation only. It does not authorize automatic merge/cherry-pick, push, deployment, broad refactors, high-risk or architectural changes outside qualified capability, financial/external side effects, unattended `qwen3-coder:30b`, or bypass of required human/higher-capability escalation.

Canonical integration remains a separate future capability and safety boundary.

## Phase Status

`IMPLEMENTED + DETERMINISTICALLY VALIDATED + EMPIRICALLY VALIDATED`
Automatic canonical integration: zero. Push/merge/deploy: zero.
