# Phase 1.24H Learning Hardening for Unattended Safety

Phase 1.24H is a Lucius-internal hardening phase. It does not reopen Phase 1.24, does not begin Phase 1.25, and does not run another extended multi-project pilot.

## Objective

Remove the HIGH unattended-readiness risks identified by the Phase 1.24 final independent review (`LRUBRIC_000034`) before Lucius attempts another extended operational step.

## Baseline

- Expected Lucius baseline HEAD: `c01e48611e0d07d8ff887d4c2575c72c41a7e604`
- Expected baseline status: `main...origin/main [ahead 27]` with a clean tracked tree
- Canonical artifact store: `data/lucius-pilots.sqlite`
- Artifact-store integrity: SQLite `integrity_check` returned `ok`
- Foreign-key check: legacy Darwin rows remain as known historical integrity debt, not a new Phase 1.24H defect
- Darwin and Billy scope: no writes; not needed for implementation

## Learning Inputs

The phase addresses the following priority learning candidates without rewriting their original historical records:

- `LPLEARN_000048`: pre-mutation semantic validation must reject affected files marked `EXISTING_VERIFIED` when the frozen snapshot proves the path absent.
- `LPLEARN_000049`: overall workflow setup must validate initial item eligibility and priority state before global queue selection or target worktree creation.
- `LPLEARN_000050`: direct artifact persistence must not leave public ID counters behind.
- `LPLEARN_000051`: queue/dependency JSON updates need explicit mutable persistence helpers.
- `LPLEARN_000052`: deterministic dependency evaluation must distinguish orchestration artifact dependencies from package/runtime dependency changes.

## Plan Freeze

- Phase plan: `LPLAN_000029`
- Phase plan freeze: `LFREEZE_000029`
- Snapshot: `LSNAP_000025`
- Planning context: `CURRENT_STATE_PLANNING`
- Authority: Lucius internal hardening only

## Repairs

- ID allocation now scans persisted public IDs before issuing a new ID, preventing direct artifact inserts from desynchronizing `id_counters`.
- JSON persistence now uses explicit helper functions that clone values and mark SQLAlchemy JSON fields modified.
- Queue and persistent workflow transitions now use explicit JSON field assignment for backlog, checkpoint history, completed/pending task IDs, decisions, deviations, repair counters, test evidence, and approval state.
- Plan freeze now blocks file-state claims that contradict frozen snapshot manifests or lack same-snapshot evidence for `EXISTING_VERIFIED`.
- Dependency evaluation now honors typed dependency semantics, so orchestration/evidence/plan-freeze artifacts do not imply package dependency changes.
- Hardening validators now cover initial eligibility simulation, uncertainty-field invariants, orchestration contract completeness, and persisted adversarial probe policy.

## Adversarial Coverage

The Phase 1.24H regression band verifies:

- direct high-ID artifact inserts, sparse IDs, malformed IDs, rollback, restart, and sequential allocation;
- durable JSON round trips for nested workflow metadata and queue block/resume/complete state;
- initial queue eligibility mismatch and malformed ready-to-resume state detection;
- frozen manifest file-state rejection for false existing claims, invalid paths, and directory/file mismatches;
- typed dependency semantics for orchestration artifacts versus package dependencies;
- uncertainty requirements for extended or degraded contexts;
- orchestration contract and adversarial probe policy enforcement.

## Verification

- PRE formal benchmark: `LBENCH_000050`, `LUCIUS_CORE_BENCH_V0_1`, PASS, aggregate `100.0`, hard gate PASS.
- Focused Phase 1.24H tests: `13 passed`.
- Adjacent regression band: `107 passed`.
- Full Lucius test suite: `267 passed`.
- Static source compile: passed for real Python sources; ignored macOS AppleDouble sidecars are excluded because they are binary metadata files with null bytes.
- POST formal benchmark is captured after the tracked Lucius implementation commit so the benchmark target is clean.

## Limitations

- Existing historical foreign-key debt remains present in the artifact store and is classified as legacy known integrity debt.
- The new hardening gates do not automatically promote learning candidates.
- Extended orchestration contract and adversarial probe enforcement is opt-in through explicit orchestration metadata/warnings, so historical non-orchestration plans are not retroactively invalidated.
- Human operational usefulness remains an independent review input.

## Decision

Phase 1.24H is designed to eliminate the specific HIGH unattended-readiness risks from Phase 1.24 before any Phase 1.25 or extended operational pilot begins. Final readiness depends on full-suite verification, clean POST benchmark, regression comparison, and independent review of the hardening artifacts.
