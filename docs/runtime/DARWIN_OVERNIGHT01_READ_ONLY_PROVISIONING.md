# Darwin Overnight01 Read-Only Provisioning

This capability provisions a new, bounded Darwin inspection backlog through
the canonical Lucius services. It does not launch execution and does not
modify Darwin.

## Safety Envelope

`provision-read-only-backlog` requires the target repository and persistent
isolated Git workspace to exist, be clean, and exactly match the declared
frozen commit. Every declared context path must be a relative UTF-8 regular
file inside that workspace and must remain within the existing Ollama context
limits of 200,000 bytes per file and 40,000 bytes per task.

Every task is fixed to `read_only=true`, `mutation_allowed=false`,
`unattended=true`, `T1`, `LOW`, `ISOLATED_WORKTREE`, `UNSUPERVISED`,
`inspection_reasoning`, deterministic verification, and evidence-reference
validation. The generated task contract permits only repository and
documentation reads. No source, analysis, output, commit, merge, push,
deployment, trading, messaging, or credential authority is granted.

The operation is atomic. Validation happens before durable provisioning, and a
savepoint rolls back all project, repository, task, plan, freeze, and workflow
rows if any task fails. A stable `provisioning_id` prevents duplicate
provisioning. Historical workflows `LWORK_000153`, `LWORK_000154`, and
`LWORK_000155` are never accepted as inputs or reused.

## Provision

The declarative specification for the frozen Darwin Overnight01 backlog is
[darwin-overnight01-read-only.json](darwin-overnight01-read-only.json). Its
context paths were selected from tracked files present at
`29db80f7ade0b30ff0b934c4511b7e344acd1213`.

The persistent workspace must be provisioned separately at:

```text
/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/.lucius-workspaces/overnight01-readonly-29db80f7
```

After that workspace has been created and verified at the frozen commit, the
operator command is:

```bash
python -m lucius.pilots.cli \
  --database data/lucius-pilots.sqlite \
  provision-read-only-backlog \
  --spec docs/runtime/darwin-overnight01-read-only.json
```

This command uses the canonical project, repository, state, snapshot, task,
contract, plan, freeze, workflow, and queue services. It does not write to
SQLite directly. This command is intentionally documented but must not be run
as part of this implementation change.

## Run Separately

Provisioning produces explicit new workflow IDs. Inspect them before any
runtime launch:

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite \
  queue-global-status LWORK_NEW_1 LWORK_NEW_2
```

The real launch remains a separate operator action through
`run-runtime-loop`, with explicit newly returned workflow IDs, `ollama-local`,
`qwen3:8b`, `UNSUPERVISED`, the persistent workspace allowed root, and no
`--stop-on-block`. Provisioning does not authorize launch, and launch does not
authorize mutation.

Repairing a blocked queue item changes persisted configuration or checkpoint
state; it does not authorize execution. Authorization is established only by
the canonical task contract, frozen plan, workflow lifecycle, queue state, and
runtime release checks. Resuming a blocked item requires the current
checkpoint and item version through `NonBlockingQueueService.resolve_blocker()`;
it is not a retry shortcut.

## Post-Run Audit

Use `queue-status` or `queue-global-status` for canonical queue results, then
inspect the persisted audit and execution records through the Lucius read APIs
or a read-only reporting query. Relevant evidence includes
`NATIVE_RUNTIME_LOOP_COMPLETED`, `MODEL_EXECUTION_ROUTING_REQUEST_ACCEPTED`,
`MODEL_EXECUTION_PROVIDER_RESULT_NORMALIZED`, `QUEUE_WORK_ITEM_COMPLETED`,
and `QUEUE_WORK_ITEM_BLOCKED`, together with the related workflow, task,
plan-freeze, and model-execution IDs. Do not repair, resume, or mutate rows by
editing SQLite directly.