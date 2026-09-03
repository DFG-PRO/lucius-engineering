# Phase 1.22 Controlled Multi-Project Operational Queue Pilot

Phase 1.22 ran a controlled multi-project operational queue pilot using the canonical Lucius artifact store at `data/lucius-pilots.sqlite`.

The pilot used one logical execution capacity across three workflows:

- `LWORK_000006`: Darwin isolated branch `lucius/phase-1.22-list-runs-json`.
- `LWORK_000007`: Lucius isolated branch `lucius/phase-1.22-queue-summary`.
- `LWORK_000008` and `LWORK_000009`: control workflows for dependency, malformed-state, and lifecycle exclusion behavior.

## Real Engineering Artifacts

Project A added a read-only JSON output mode to Darwin `darwin research list-runs`.

- Plan: `LPLAN_000012`.
- Freeze: `LFREEZE_000012`.
- Original evaluation: `LEVALPLAN_000026`, FAIL.
- Phase 1.22B superseding evaluation: `LEVALPLAN_000033`, PASS.
- Final isolated branch head: `fdb13e17e44fa7e4fa2d418c1d96fe86cbde3a85`.
- Verification: Darwin target suite passed with `179 passed`.

Project B added a read-only Lucius `queue-global-summary` command for operator diagnostics.

- Plan: `LPLAN_000013`.
- Freeze: `LFREEZE_000013`.
- Original evaluation chain includes `LEVALPLAN_000032`, FAIL.
- Phase 1.22B superseding evaluation: `LEVALPLAN_000034`, PASS_WITH_WARNINGS.
- Final isolated branch head: `e4ebaf0d5919ab438ccd2395421e083c5b1e6224`.
- Verification: Lucius operational branch suite passed with `228 passed`.

Project C was a control-plane workflow for dependency, malformed-state, and lifecycle behavior.

- Plan: `LPLAN_000014`.
- Freeze: `LFREEZE_000014`.
- Original evaluation: `LEVALPLAN_000028`, INSUFFICIENT_EVIDENCE.
- Phase 1.22B superseding evaluation: `LEVALPLAN_000035`, PASS_WITH_WARNINGS.
- Authority correction: Project C is `L0` control-plane verification, not broad implementation authority.

## Queue Findings

The global queue preserved one logical execution capacity. While Project A was blocked at `LQCHK_000003`, Project B ran. When Project B was running, global selection returned `GLOBAL_RUNNING_ITEM_ACTIVE_NO_PREEMPTION`. After B completed, A resumed from persisted state without repeating completed substeps.

Control workflow `LWORK_000008` showed that `C2` remained dependency-blocked on `shared.validate` even after `C1` completed with a matching logical substep name. This demonstrates item-scoped dependency resolution rather than leakage through shared substep names.

`C3` remained malformed-unschedulable with raw state `UNKNOWN_READY`, and closed workflow `LWORK_000009` remained lifecycle-excluded.

## Original Closure Failure

Phase 1.22 did not initially pass closure.

- CRITICAL: frozen A/B/C EngineeringPlans marked an operational constraint assumption as verified without evidence IDs.
- MAJOR: `complete_item` could not complete a valid running item when a malformed sibling state existed in the same backlog.
- MODERATE: Phase 1.22 readiness labels were not represented in release-gate vocabulary.
- MODERATE: full file-byte repository snapshot creation stalled before records persisted.
- MINOR: control EngineeringPlan top-level authority was more conservative than its `L0` step/contract authority.

PRE benchmark `LBENCH_000034` and POST benchmark `LBENCH_000035` both passed `LUCIUS_CORE_BENCH_V0_1` with 8/8 cases and aggregate score `100.0`; comparison result was `NO_REGRESSION`.

Pilot record `LPILOT_000023` returned `NOT_READY_FOR_NON_BLOCKING_PROJECT_QUEUE`.

## Repair Chronology

Phase 1.22B repaired exact completion, snapshot bounding, gate vocabulary, and additive correction artifacts. Independent audit `LRUBRIC_000025` then found the Phase 1.22B provenance repair was still shape-only: nonexistent or malformed evidence reference strings could satisfy closure-critical checks.

Phase 1.22C repairs that remaining evidence-reference integrity issue. Phase 1.22 should only be considered closed after Phase 1.22C semantic provenance validation and closure artifacts pass.
