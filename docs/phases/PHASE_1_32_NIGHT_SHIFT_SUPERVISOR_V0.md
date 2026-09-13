# Phase 1.32 Night Shift Supervisor v0

Phase 1.32 adds a thin single-worker supervisor that composes existing Lucius
capabilities without duplicating their internals.

The v0 lifecycle is:

eligible work -> runtime executes exactly one task -> deterministic runtime
completion -> L1 controlled canonical integration -> L2 controlled local commit
when explicitly authorized -> auditable checkpoint -> next eligible task.

## Architecture

`NightShiftSupervisor` lives under `src/lucius/night_shift/`. It owns only the
continuation between existing services:

- `ExecutionRuntimeLoopService` selects and executes one eligible task per cycle;
- `CanonicalIntegrationService` performs L1 canonical integration;
- `ControlledCommitService` performs L2 local commit promotion.

Canonical integration and commit behavior remain outside the runtime loop. The
supervisor does not make `LocalGitRepositoryAdapter` writable and does not add a
new mutation path.

## Safety Bounds

Night Shift v0 is single-worker and single-dispatcher only. Each cycle invokes
the runtime with `max_tasks=1` and `dispatcher_count=1`.

The bounded configuration includes:

- `max_tasks`;
- `max_commits`;
- `max_wall_clock_seconds`;
- `stop_on_escalation`;
- `stop_on_manual_reconciliation`;
- allowed task complexity bounds, defaulting to `T0`/`T1`;
- allowed risk bounds, defaulting to `LOW`.

The supervisor does not broaden task authority. It resolves workflow, freeze,
candidate worktree, canonical repository, and baseline from persisted Lucius
state.

## Authority Boundaries

Runtime execution keeps using qualified local routing and existing runtime
policy. Night Shift v0 does not automatically invoke Codex.

L1 canonical integration is requested only after runtime completion. L2 commit
is requested only when existing task and task-contract state explicitly permits
`CREATE_COMMIT` with L2 authority. A task with only L1 authority may integrate
only through the L1 service; Night Shift will not manufacture commit authority.

## Stop Semantics

Clean termination:

- no eligible work / idle;
- `max_tasks` reached;
- `max_commits` reached;
- `max_wall_clock_seconds` reached.

Task-local non-completion may continue only when the runtime returns a concrete
non-escalated task record. The runtime remains responsible for queue mutation
and non-blocking semantics.

Global hard stop:

- runtime escalation;
- malformed or ambiguous runtime result;
- incomplete or inconsistent promotion state;
- baseline/freeze/path ambiguity;
- L1 integration failure;
- L1 rollback uncertainty;
- missing explicit `CREATE_COMMIT` authority after L1 integration;
- controlled commit failure;
- manual reconciliation required;
- any condition where continuing could compound repository damage.

## Checkpoints And Observability

Night Shift records audit events for start, every cycle checkpoint, hard stop,
and completion. Results expose:

- overall status and stop reason;
- cycles attempted;
- tasks selected/completed/blocked/escalated;
- workflows processed;
- providers used;
- L1 integration counts;
- L2 commit counts;
- resulting local commit SHAs;
- manual reconciliation flag;
- project switches;
- wall-clock duration;
- max-limit reason;
- remote action list, which remains empty in v0;
- integrity/hard-stop reason.

## Resource Policy

Night Shift v0 follows deterministic/local tooling first, then qualified local
model execution through existing runtime routing. Unsupported capability or Codex
requirements must surface as escalation or waiting conditions. The supervisor
itself does not invoke Codex.

## Not Autonomous In v0

Night Shift v0 does not push, deploy, merge, rebase, amend, modify remotes,
promote releases, run infrastructure mutation, or perform external side effects.
It does not add daemon, cron, launchd, or service deployment.

## Known Limitations

The supervisor is intentionally conservative. L1 integration failures hard-stop
instead of attempting to infer task-local safety. Later workflows rely on their
own frozen baseline state; Night Shift does not mutate old freezes to fit a new
commit.

## Empirical Validation And Closure Evidence

Phase 1.32 completed bounded empirical validation using temporary Git repositories only.

Validation included:

- real `CanonicalIntegrationService` execution;
- real `ControlledCommitService` execution;
- real frozen Git baseline SHAs;
- real candidate workspaces;
- real deterministic acceptance verification;
- real local L2 commits;
- explicit verification that no remote mutation occurred.

Pilot A exercised two independent workflows end to end:

`runtime completion -> L1 canonical integration -> L2 controlled local commit -> next cycle -> idle`

The happy-path pilot was repeated five times during the short soak, producing
10 successful real L1 integrations and 10 successful real L2 commits across
fresh temporary repositories.

Pilot B exercised a critical second-cycle post-commit verification failure that
required manual reconciliation. It verified that Night Shift hard-stopped and
did not consume the queued third runtime result. This scenario was repeated
three times during the short soak with the same fail-closed result.

Short-soak result:

- Pilot A: 5/5 PASS;
- Pilot B: 3/3 PASS;
- total soak executions: 8/8 PASS;
- unauthorized remote actions: none;
- baseline/freeze invariants were not weakened;
- no unattended Codex invocation occurred.

Prior validation before the soak:

- focused Night Shift suite: 19 PASS;
- canonical integration + controlled commit regression: 33 PASS;
- runtime/router/Ollama regression: 102 PASS;
- full suite: 472 PASS.

Phase 1.32 is therefore IMPLEMENTED, STATICALLY VALIDATED, and EMPIRICALLY
SOAK-VALIDATED. Formal local closure requires the final post-documentation full
suite, repository integrity checks, and canonical local commit.
