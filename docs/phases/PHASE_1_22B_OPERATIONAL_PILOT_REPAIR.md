# Phase 1.22B Operational Pilot Evidence and Completion Safety Repair

Phase 1.22B is a narrow repair subphase for the Phase 1.22 controlled multi-project operational queue pilot. It preserves the Phase 1.22 failed findings and adds superseding evidence rather than rewriting the original history.

## Baseline

- Official Lucius baseline HEAD: `7890cc5854e07358a0c9714a39c2ebb3de8adc1d`
- Official Lucius starting status: `## main...origin/main [ahead 21]`, no tracked or staged changes; ignored runtime artifacts under `data/`
- Official Darwin expected main HEAD: `3207aee087fa9018ee9ff2d526fe7a97443f6e21`
- Darwin Phase 1.22 target branch HEAD: `fdb13e17e44fa7e4fa2d418c1d96fe86cbde3a85`
- Lucius Phase 1.22 operational branch HEAD: `e4ebaf0d5919ab438ccd2395421e083c5b1e6224`
- Canonical artifact store: `data/lucius-pilots.sqlite`
- Artifact-store integrity before repair: `ok`
- PRE benchmark: `LBENCH_000036`, `LUCIUS_CORE_BENCH_V0_1`, PASS, 8 passed, 0 failed, aggregate `100.0`, hard gate PASS, target_dirty `false`

## Preserved Phase 1.22 Findings

- CRITICAL: unsupported verified assumptions were recorded without evidence IDs.
- MAJOR: `complete_item` could fail valid exact completion when a sibling queue item contained malformed persisted state.
- MODERATE: Phase 1.22 readiness/gate vocabulary reused older non-blocking queue semantics.
- MODERATE: initial full snapshot scan stalled.
- MINOR: Project C used top-level `L2` authority where control-plane verification should use minimum `L0` authority.

The original formal evaluations remain preserved:

- `LEVALPLAN_000026`: Project A, FAIL, aggregate `90.0`
- `LEVALPLAN_000032`: Project B, FAIL, aggregate `86.0`
- `LEVALPLAN_000028`: Project C, INSUFFICIENT_EVIDENCE
- `LPILOT_000023`: gate result `NOT_READY_FOR_NON_BLOCKING_PROJECT_QUEUE`

## Repair Plan

The Phase 1.22B repair plan was persisted before code repair:

- Project: `LPROJ_000012`
- Repository state: `LRSTATE_000029`
- Snapshot: `LSNAP_000014`
- Task: `LTASK_000043`
- Contract: `LCONTR_000032`
- Evidence: `LEVID_000085` through `LEVID_000091`
- EngineeringPlan: `LPLAN_000015`
- Plan freeze: `LFREEZE_000015`
- Planning context: `CURRENT_STATE_PLANNING`

The plan covered verified-claim evidence policy, exact completion safety, gate vocabulary, linked evidence requirements, superseding evaluations, Project C authority correction, snapshot-bounds repair, regression tests, target branch integrity, benchmarks, and documentation.

## Evidence Policy

Phase 1.22B defines the canonical rule:

A claim may be marked `VERIFIED`, `CONFIRMED`, or `PROVEN` only when it carries traceable evidence, such as an evidence artifact ID, persisted benchmark ID, test/probe result ID, repository snapshot/state ID, or canonical target artifact.

Claims without traceable evidence must remain `ASSUMPTION`, `UNVERIFIED`, `SUPPLIED`, or `INFERRED`. They must not be silently promoted.

The reusable helper in `src/lucius/pilots/provenance.py` detects verified claims without evidence and validates linked disposition evidence for superseding evaluations and gates.

## Exact Completion Safety

`complete_item` now follows an exact-item completion rule:

- validate the workflow lifecycle;
- identify the requested target item by exact workflow and item ID;
- strictly validate only the target's completion state;
- reject malformed, null-state, missing-state, or non-running targets;
- complete the target when it is `RUNNING` or `READY_TO_RESUME`;
- preserve malformed siblings without normalization or mutation;
- recompute completed and pending IDs through tolerant sibling parsing;
- record audit metadata proving requested identity equals mutated identity.

Malformed siblings may remain excluded from scheduling and inspectable as malformed state, but they do not block legitimate exact completion.

## Gate Vocabulary and Evidence

Phase 1.22B adds operational readiness vocabulary:

- `PHASE_1_22_READY_TO_CLOSE`
- `PHASE_1_22_REPAIR_REQUIRED`
- `PHASE_1_22_BLOCKED`
- `PHASE_1_22B_READY_TO_CLOSE`
- `PHASE_1_22B_REPAIR_REQUIRED`
- `PHASE_1_22B_BLOCKED`
- `READY_FOR_EXPANDED_MULTI_PROJECT_OPERATIONAL_PILOT`
- `READY_FOR_ANOTHER_CONTROLLED_MULTI_PROJECT_OPERATIONAL_PILOT`
- `NOT_READY_FOR_MULTI_PROJECT_OPERATIONAL_USE`

The release gate now recognizes `CONTROLLED_MULTI_PROJECT_OPERATIONAL_QUEUE_PILOT` and requires linked probe evidence for all Phase 1.22 operational claims. Bare booleans do not satisfy the gate.

## Project C Authority Correction

Project C is a control workstream. Its corrected classification is minimum authority `L0` for queue-state/control verification, not broad implementation authority. Phase 1.22B records this as a superseding correction artifact rather than editing the historical Project C plan.

## Snapshot Bounds

The initial snapshot stall was caused by repeated git-state collection while building per-file records. `LocalGitRepositoryAdapter.build_snapshot` now captures git state once and passes it through per-file record construction. `RepositoryStateService.inspect` uses `SnapshotMode.FAST` for manifest hashing and records explicit bounded-manifest provenance.

Bounded/fast snapshots are not represented as full complete content snapshots.

## Verification

Focused verification added:

- verified-claim evidence validation;
- unsupported verified-claim disposition from implementation/evidence artifacts;
- Phase 1.22 gate rejection of bare booleans and acceptance of linked evidence;
- valid exact completion with valid, missing-state, null-state, unknown-state, and malformed-priority siblings;
- sibling preservation;
- malformed target rejection;
- non-running target rejection;
- lifecycle-ineligible target rejection;
- audit identity evidence;
- fresh-process exact completion;
- scheduler ordering preservation;
- controlled operational replay with a malformed sibling;
- bounded snapshot/git-state behavior.

Targeted repair tests:

- `tests/integration/test_phase122b_operational_repair.py`
- `tests/integration/test_phase121_cross_project_queue.py`
- `tests/integration/test_phase121d_scoped_execution_safety.py`

Result: 28 passed.

## Final Closure Artifacts

Final POST benchmark, superseding evaluation, pilot record, regression comparison, rubric, and final release-gate IDs are recorded in the canonical artifact store and in the Phase 1.22B completion report.

## Limitations

Phase 1.22B does not add multi-worker concurrency, merging, deployment, or unrestricted operational execution. The repaired readiness recommendation remains limited to a subsequent controlled multi-project operational pilot unless separately promoted by a future phase and gate.
