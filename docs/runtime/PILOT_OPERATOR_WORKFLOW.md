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
status, and autonomy recommendation. Phase 1.12 can recommend at most
`READY_FOR_LIMITED_WRITE_PILOT`.

## List Pilot Evidence

```bash
python -m lucius.pilots.cli --database data/lucius-pilots.sqlite list-pilot-evidence LPILOT_000001
```

This reconstructs the ids connected to a persisted pilot evaluation record.
