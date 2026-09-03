# Artifact Store Preservation

Phase 1.21P establishes the durable local artifact store for Lucius pilot and
evaluation records.

## Canonical Store

The canonical operational SQLite store is:

```text
data/lucius-pilots.sqlite
```

This matches the `lucius.pilots.cli` default and the operator workflow
documentation. Runtime databases are local operational artifacts and must not be
committed to Git. The tracked `data/.gitignore` keeps the directory available
while excluding database payloads and archive copies.

## Preservation History

Before Phase 1.21P, the authoritative historical store lived at:

```text
/private/tmp/lucius-phase113-pilot.sqlite
```

That path contained the authoritative sequence through Phase 1.21D-E, including
`LBENCH_000032`, `LBENCH_000033`, `LEVALPLAN_000025`, `LRUBRIC_000020`,
`LRUBRIC_000022`, and `LPILOT_000020`. Because `/private/tmp` is not durable
enough for canonical history, Phase 1.21P copied the complete source database
byte-for-byte to `data/lucius-pilots.sqlite`.

Source integrity before preservation:

```text
source: /private/tmp/lucius-phase113-pilot.sqlite
sha256: c99cc749df16b4e27fcd5e7a715dbd4dbff6b4b59881f4860bd3f2af5b6e907a
sqlite integrity_check: ok
size: 3891200 bytes
```

Canonical copy verification:

```text
target: data/lucius-pilots.sqlite
sha256: c99cc749df16b4e27fcd5e7a715dbd4dbff6b4b59881f4860bd3f2af5b6e907a
sqlite integrity_check: ok
```

## Source Archives

Phase 1.21P retained the original `/private/tmp` files and also copied source
archives into:

```text
data/artifact-store-archives/
```

The Phase 1.21D temporary store and earlier Phase 1.12 benchmark stores contain
colliding local IDs such as `LBENCH_000001`, `LERUN_000001`, and
`LEVALPLAN_000001`. They are therefore archived as supporting source evidence
and are not merged into the canonical namespace.

## Collision Policy

When preserving or merging artifact stores:

- Full-store copy is preferred when the target canonical store is absent.
- If a target already exists, compare overlapping IDs by table, payload,
  timestamp, evaluator, linked IDs, and content hash.
- `IDENTICAL_DUPLICATE` records may be ignored.
- `COMPATIBLE_DUPLICATE` records require explicit documented rationale.
- `CONFLICTING_RECORD` collisions must stop migration; do not overwrite or
  renumber historical IDs.

Historical IDs, timestamps, ownership, and evaluation outcomes must never be
rewritten. For example, `LBENCH_000032` remains `LBENCH_000032`.

## Phase 1.21P Inventory

Phase 1.21P inspected all accessible Lucius SQLite pilot/evaluation stores in
the repository and known runtime locations. It also found
`/private/tmp/phase118-overview-outcome.sqlite`, which uses Darwin research
tables rather than Lucius pilot/evaluation artifact tables and is not part of
the canonical Lucius artifact namespace.

| Store | Classification | SHA-256 | Integrity | Relevant counts |
| --- | --- | --- | --- | --- |
| `/private/tmp/lucius-phase113-pilot.sqlite` | `HISTORICAL_SOURCE` before preservation, superseded by canonical copy | `c99cc749df16b4e27fcd5e7a715dbd4dbff6b4b59881f4860bd3f2af5b6e907a` | `ok` | 33 benchmarks, 25 plan evaluations, 22 human rubrics, 22 pilot records, 24 pilot learning candidates, 5 workflows |
| `data/lucius-pilots.sqlite` | `CANONICAL` after preservation | `c99cc749df16b4e27fcd5e7a715dbd4dbff6b4b59881f4860bd3f2af5b6e907a` | `ok` | byte-identical to `/private/tmp/lucius-phase113-pilot.sqlite` |
| `/private/tmp/lucius_phase121d_artifacts.sqlite` | `TEMPORARY_OPERATIONAL` / `SOURCE_ARCHIVE` | `09364c75847e37f56b97da8a7886493454883ce03862f97714486ad45cb0706c` | `ok` | 1 benchmark, 1 plan evaluation, 1 plan, 1 freeze, 11 evidence refs, 1 pilot record |
| `/private/tmp/lucius_phase121d_pre.sqlite` | `TEMPORARY_OPERATIONAL` evaluation pre-check store | `8b65d4057250dd8dcf2fbf4457bb7d4b64bf7018c7ac9ce21e35556993acf015` | `ok` | 1 evaluation run, 8 case results, no benchmark result |
| `/private/tmp/lucius-phase112-benchmark-clean.sqlite` | `TEST_FIXTURE` / historical benchmark scratch store | `3d32e5e6ac203a05d4bdc1c5a23690adaf8aefcea0239d60a9cdef37618561d7` | `ok` | 1 benchmark, 1 evaluation run, 8 case results |
| `/private/tmp/lucius-phase112-benchmark.sqlite` | `TEST_FIXTURE` / historical dirty benchmark scratch store | `c84081544711b164bb6d889fd99a715410fedcb03389cf434f1b190fff713078` | `ok` | 1 benchmark, 1 evaluation run, 8 case results |

The canonical historical store has these key id ranges:

```text
LBENCH_000001..LBENCH_000033
LEVALPLAN_000001..LEVALPLAN_000025
LPLAN_000001..LPLAN_000011
LFREEZE_000001..LFREEZE_000011
LEVID_000001..LEVID_000079
LRUBRIC_000001..LRUBRIC_000022
LPILOT_000001..LPILOT_000022
LPLEARN_000001..LPLEARN_000024
LWORK_000001..LWORK_000005
LWCHK_000001
LRESUME_000001
```

The temporary Phase 1.21D DB and Phase 1.12 scratch DBs contain overlapping
local ids that conflict with the historical canonical sequence. Phase 1.21P
therefore archived those files and did not import them into
`data/lucius-pilots.sqlite`.

| Compared store | Collision result |
| --- | --- |
| `/private/tmp/lucius_phase121d_artifacts.sqlite` | 51 `CONFLICTING_RECORD`, 0 identical, 0 source-only |
| `/private/tmp/lucius_phase121d_pre.sqlite` | 9 `CONFLICTING_RECORD`, 0 identical, 0 source-only |
| `/private/tmp/lucius-phase112-benchmark-clean.sqlite` | 10 `CONFLICTING_RECORD`, 0 identical, 0 source-only |
| `/private/tmp/lucius-phase112-benchmark.sqlite` | 10 `CONFLICTING_RECORD`, 0 identical, 0 source-only |

## Next ID Allocation

Future artifact allocation must use the canonical store's `id_counters` table.
After Phase 1.21P preservation, the next benchmark allocation must follow the
historical sequence after `LBENCH_000033`; it must not restart at
`LBENCH_000001`.

Current next-number checkpoints after preservation:

```text
benchmark=34
evaluation_run=34
plan_evaluation=26
human_rubric=23
pilot_record=23
pilot_learning=25
engineering_plan=12
plan_freeze=12
evidence=80
persistent_workflow=6
queue_checkpoint=3
workflow_checkpoint=2
resume_validation=2
```

## Backup And Recovery

Before a pilot or benchmark run that will write closure-critical evidence:

1. Verify `data/lucius-pilots.sqlite` exists and `pragma integrity_check`
   returns `ok`.
2. Create a backup with SQLite's backup API or a copy taken while no writer is
   active.
3. Store backups under a durable local destination such as:

```text
data/artifact-store-backups/lucius-pilots.<UTC timestamp>.<sha256-prefix>.sqlite
```

After backup, verify the backup hash and `pragma integrity_check`. To restore,
stop writers, copy the chosen backup over `data/lucius-pilots.sqlite`, verify
integrity, and confirm key artifacts and `id_counters` before running new
pilot/evaluation commands.
