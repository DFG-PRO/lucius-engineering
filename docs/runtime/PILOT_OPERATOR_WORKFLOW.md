# Pilot Operator Workflow

Phase 1.12 exposes a minimal CLI for canonical pilot evidence capture.

## Inspect Repository State

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite inspect-repository-state /path/to/repo --strict-canonical
```

Without `--strict-canonical`, Lucius records dirty or exploratory state instead
of refusing the command.

## Run Formal Core Benchmark

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite run-core-benchmark --repo-root .
```

This persists an `LBENCH_*` record. Pytest output is not a substitute.

## Freeze A Plan

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite freeze-plan LPLAN_000001 --repository-state-id LRSTATE_000001
```

Use `--historical` when the plan was generated from
`HISTORICAL_STATE_PLANNING`.

## Evaluate A Frozen Plan

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite evaluate-plan LFREEZE_000001 implementation.json
```

The implementation artifact is JSON containing deterministic comparison fields
such as files, known paths, tests, documentation, migrations, dependencies,
risk, authority, architecture, and material work.

When the plan expects no migration, dependency, configuration, or infrastructure
change, the artifact should include verified absence evidence instead of leaving
the field empty:

```json
{
  "implementation_evidence_manifest": {
    "changed_files": ["src/example.py"],
    "verified_absences": {
      "migrations": {
        "state": "NO_CHANGE_CONFIRMED",
        "changed_files": [],
        "verified_paths": ["alembic/versions"]
      },
      "dependencies": {
        "state": "NO_CHANGE_CONFIRMED",
        "changed_files": [],
        "verified_paths": ["pyproject.toml"]
      }
    }
  }
}
```

Use `UNEXPECTED_CHANGE_DETECTED` when a no-change expectation is violated, and
`NOT_CAPTURED` only when the evidence is genuinely unavailable.

For a read/plan-only pilot, use `PLANNING_ONLY`. The artifact is optional but
should include captured planning-evaluation evidence such as known repository
paths, provenance quality, and novelty/leakage audit state when available:

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite evaluate-plan \
  LFREEZE_000001 planning-evidence.json \
  --mode PLANNING_ONLY \
  --supersedes-evaluation-id LEVALPLAN_000001 \
  --evaluator-version 1.13A.0
```

Implementation-relative metrics in `PLANNING_ONLY` are stored as
`NOT_APPLICABLE`; missing required planning evidence is stored as
`NOT_CAPTURED`.

## Record Human Rubric

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite record-human-rubric-not-captured --plan-freeze-id LFREEZE_000001
```

Captured rubric example:

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite capture-human-rubric \
  --plan-freeze-id LFREEZE_000001 \
  --evaluator daniel \
  --score repository_understanding=5 \
  --score architectural_correctness=4 \
  --score completeness=4 \
  --score usefulness=5 \
  --score implementation_realism=4 \
  --score risk_awareness=5 \
  --score provenance_quality=5 \
  --score hallucination_control=4
```

## Compute Release Gate

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite compute-release-gate \
  --repository-state-id LRSTATE_000001 \
  --deterministic-evaluation-id LEVALPLAN_000001 \
  --benchmark-before-id LBENCH_000001 \
  --benchmark-after-id LBENCH_000002 \
  --human-rubric-id LRUBRIC_000001
```

The command returns deterministic blockers, warnings, benchmark regression
status, and autonomy recommendation. After Phase 1.15A, a current successful
limited-write pilot with captured human review and at least one distinct prior
successful limited-write pilot can recommend
`READY_FOR_BOUNDED_ENGINEERING_PILOT`.

That recommendation still requires isolated implementation and human review. It
does not authorize automatic merge to main, automatic push, force push,
destructive Git, production deployment, credential changes, external production
operations, broad cross-project modification, silent migration/schema expansion,
or unlimited repair loops.

For a bounded engineering pilot, create one parent objective, decompose it into
real dependent subtasks, freeze the package before implementation, execute in an
isolated worktree, and record both objective-level plan-vs-implementation and
orchestration evaluations. The first successful bounded engineering pilot should
normally recommend `READY_FOR_ANOTHER_BOUNDED_ENGINEERING_PILOT`.

After explicit human approval is captured for that bounded pilot, recompute the
gate with the captured rubric id. If all canonical rubric dimensions are scored
5/5 and the hard gates still pass, the result may advance to
`READY_FOR_BOUNDED_MULTI_TASK_ENGINEERING`:

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite compute-release-gate \
  --repository-state-id LRSTATE_000001 \
  --deterministic-evaluation-id LEVALPLAN_000001 \
  --benchmark-before-id LBENCH_000001 \
  --benchmark-after-id LBENCH_000002 \
  --human-rubric-id LRUBRIC_000002 \
  --repository-integrity-result UNCHANGED
```

This tier still authorizes only one coherent bounded objective at a time. It
does not authorize automatic merge to main, automatic push, production
deployment, credential changes, destructive Git, force push, broad cross-project
autonomy, unrestricted architecture rewrites, silent migration/schema expansion,
unlimited task spawning, unlimited repair cycles, or unrestricted autonomous
operation.

For a bounded multi-task pilot, create one parent objective, independently
derive child tasks, validate the graph, freeze the package, execute only in an
isolated branch or worktree, record local decisions/deviations/repairs, run
task-level and integrated verification continuously, run the full target
repository suite, commit only on the isolated branch, and prepare a human-review
package.

The normal passing result with missing human review is
`READY_FOR_ANOTHER_BOUNDED_MULTI_TASK_ENGINEERING_PILOT`. A later captured human
review may support `READY_FOR_SUPERVISED_ENGINEERING_WORKFLOW`, but that result
still does not authorize merge, push, deployment, production operations, or
unrestricted architecture changes.

For supervised engineering readiness, capture Daniel's human review against the
bounded multi-task pilot package, recompute the release gate with the captured
rubric, and persist a superseding pilot evaluation record. A promoted workflow
may maintain one bounded objective through repository understanding,
decomposition, planning, isolated implementation, bounded repair, verification,
documentation, evidence capture, and review packaging.

The operator must stop for human approval before merge to main, push when policy
requires it, production deployment, destructive operations, credential/security
changes, unplanned migration or schema expansion, material new dependencies,
architecture expansion outside the frozen objective, cross-project changes, or
material scope changes.

Suggested workflow states for a supervised run are `OBJECTIVE_ACCEPTED`,
`PLANNING`, `PLAN_READY`, `IMPLEMENTING`, `VERIFYING`,
`CHECKPOINT_REVIEW_REQUIRED`, `BLOCKED`, `APPROVED_TO_CONTINUE`,
`COMPLETED_PENDING_INTEGRATION`, and `CLOSED`.

For a completed supervised workflow with human review still `NOT_CAPTURED`, the
release gate should recommend
`READY_FOR_ANOTHER_SUPERVISED_ENGINEERING_WORKFLOW`. Promotion to
`READY_FOR_PERSISTENT_SUPERVISED_ENGINEERING` requires captured 5/5 human review
across all canonical dimensions in addition to passing deterministic,
benchmark, and repository-integrity gates.

For persistent supervised workflows, resume by validating the persisted workflow
identity, repository, branch or worktree, expected HEAD, canonical baseline,
task graph, checkpoint state, implementation commits, completed and pending
tests, and pending human authorization. If any material state differs, stop in
`CHECKPOINT_REVIEW_REQUIRED` or `BLOCKED` instead of reconstructing state from
conversation.

Persistent workflow artifacts should be treated as the source of truth during a
pause/resume pilot. Operators should record the `LWORK_*`, `LWCHK_*`, and
`LRESUME_*` ids, then verify repository HEADs, worktree cleanliness, task graph
readiness, repair counters, and pending human approvals before allowing the
workflow to leave `RESUME_VALIDATION`.

## Inspect Non-Blocking Queue State

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite queue-status LWORK_000001
```

The command returns JSON for running, blocked, ready, ready-to-resume,
dependency-blocked, completed, failed, and next-selection groups. It is
read-only.

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite queue-next LWORK_000001
```

`queue-next` returns the deterministic scheduler decision without starting work.
The scheduler validates duplicate ids, rejects duplicate `RUNNING` logical
tasks, refuses unsafe mid-task preemption when a work item is already running,
excludes blocked/terminal/dependency-incomplete work, and selects by priority,
resume class, creation order, then id.

When a work item blocks, persist a queue checkpoint with its reason, category,
completed substeps, resume condition, approvals, repair counters, next safe
action, and stale validation rules. When the blocker resolves, require the
current checkpoint id and expected item version before moving the item to
`READY_TO_RESUME`.

For a non-blocking queue pilot, the release gate may recommend
`READY_FOR_MULTI_PROJECT_NON_BLOCKING_QUEUE` only when the deterministic
artifact reports passing queue evaluation, fresh-context reconstruction,
duplicate-work protection, workflow isolation, safe interruption, and explicit
multi-project non-blocking testing. This recommendation does not authorize
multi-worker concurrency or unrestricted write autonomy.

## Inspect Global Queue State

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite queue-global-status LWORK_000001 LWORK_000002
```

This command returns JSON across the supplied workflows: active projects,
running items, blocked items, ready items, ready-to-resume items,
dependency-blocked items, completed items, failed items, and the global next
selection. It also reports `observed_workflow_ids`,
`legacy_unschedulable`, and `lifecycle_excluded` so operators can distinguish
inspectable historical state from executable queue state.

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite queue-global-next LWORK_000001 LWORK_000002
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite queue-global-start LWORK_000001 LWORK_000002
```

`queue-global-next` returns the deterministic global scheduling decision
without starting work. The ordering rule is priority, resume class, creation
order, project id, workflow id, then item id. If any item is already running,
the command reports `GLOBAL_RUNNING_ITEM_ACTIVE_NO_PREEMPTION`.

`queue-global-start` uses the actual global dispatch path. It first computes the
global selection, then starts exactly that selected item only after validating
the selected project/workflow/item, item version, workflow lifecycle, item state,
priority, dependencies, no-preemption rule, and ordering. It must not fall back
to another item. If the selection is stale or malformed, the command fails and
the operator should rerun global selection after the underlying state is
resolved.

For Phase 1.21 pilot reconstruction, provide the workflow ids explicitly so the
evaluated global scope is unambiguous. Omitting workflow ids asks Lucius to
inspect every persistent workflow with backlog state, but Phase 1.21B makes that
safe by default: only lifecycle-eligible workflows with explicit queue item
state can become schedulable.

Global execution lifecycle eligibility is explicit. `PLAN_READY`,
`IMPLEMENTING`, `VERIFYING`, `CHECKPOINT_REVIEW_REQUIRED`,
`RESUME_VALIDATION`, `BLOCKED`, and `APPROVED_TO_CONTINUE` may participate in
global scheduling. `OBJECTIVE_ACCEPTED`, `PLANNING`, `PAUSED`,
`COMPLETED_PENDING_INTEGRATION`, and `CLOSED` are inspectable but excluded from
global execution.

Missing item `state` means legacy or ambiguous backlog data. It is reported with
`state_label: LEGACY_UNSCHEDULABLE`, `queue_state_present: false`,
`schedulable: false`, and an exclusion reason. It must not be treated as
`READY` for global execution.

Unknown or null item `state`, and unknown priority values, are malformed
unschedulable data. Operator output preserves raw state/priority where present
and reports reasons such as `MALFORMED_UNSCHEDULABLE:UNKNOWN_STATE:<value>`,
`MALFORMED_UNSCHEDULABLE:NULL_STATE`, or
`MALFORMED_UNSCHEDULABLE:UNKNOWN_PRIORITY:<value>`.

## List Pilot Evidence

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite list-pilot-evidence LPILOT_000001
```

This reconstructs the ids connected to a persisted pilot evaluation record.
