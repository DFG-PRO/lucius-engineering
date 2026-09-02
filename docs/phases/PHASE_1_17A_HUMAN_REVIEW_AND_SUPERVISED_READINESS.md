# Phase 1.17A: Human Review And Supervised Readiness

Phase 1.17A captured Daniel's explicit human review of the successful Phase
1.17 bounded multi-task engineering pilot and recomputed the staged autonomy
gate. It did not modify Darwin main, did not modify the Phase 1.17 Darwin
implementation, did not merge, did not push, and did not begin Phase 1.18.

## Baseline

Lucius:

- repository: `/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering`
- baseline HEAD: `3e247f5ac94a80711803529fc2769581c84f8006`
- baseline status: `## main...origin/main [ahead 11]`
- baseline tests: `168 passed in 148.90s`
- Phase 1.17A pre benchmark: `LBENCH_000017`,
  `LUCIUS_CORE_BENCH_V0_1`, 8 passed, 0 failed, aggregate `100.0`,
  hard gate `PASS`, target dirty `false`

Darwin main:

- repository: `/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine`
- baseline HEAD: `e436a9792f0ca294c3810ddef259b812008d8635`
- baseline status: `## main...origin/main [ahead 4]`

## Preserved Phase 1.17 Evidence

Phase 1.17 evidence was preserved and not rewritten:

- snapshot: `LSNAP_000004`
- plan: `LPLAN_000004`
- freeze: `LFREEZE_000004`
- planning evaluation: `LEVALPLAN_000009`, `PLANNING_ONLY`, `PASS`,
  aggregate `98.57`
- plan-vs-implementation evaluation: `LEVALPLAN_000010`, `PASS`, aggregate
  `98.33`
- dependency graph audit: `LAUDIT_000372`, `PASS`
- orchestration audit: `LAUDIT_000376`, `PASS`, aggregate `100.0`
- autonomy audit: `LAUDIT_000377`, `PASS`
- benchmarks: `LBENCH_000015` and `LBENCH_000016`, both `PASS`, both target
  dirty `false`
- prior pilot record: `LPILOT_000010`
- historical missing rubric: `LRUBRIC_000007`, `NOT_CAPTURED`
- implementation commit:
  `de16ea2f23e74d3251c241b1af02a9c18d7572a3`

The preserved deterministic package records no implementation corrections,
zero material deviations, zero implementation repair cycles, no dependency or
migration changes, and unchanged Darwin main integrity.

## Human Review Capture

Captured rubric:

- id: `LRUBRIC_000008`
- state: `CAPTURED`
- reviewer type: `HUMAN`
- reviewer: Daniel
- source: explicit human approval in the Phase 1.17A request
- plan freeze: `LFREEZE_000004`
- pilot record: `LPILOT_000010`
- implementation commit:
  `de16ea2f23e74d3251c241b1af02a9c18d7572a3`
- planning evaluation: `LEVALPLAN_000009`
- plan-vs-implementation evaluation: `LEVALPLAN_000010`
- orchestration audit: `LAUDIT_000376`
- autonomy audit: `LAUDIT_000377`

Persisted canonical rubric scores:

- repository understanding: 5
- architectural correctness: 5
- completeness: 5
- usefulness: 5
- implementation realism: 5
- risk awareness: 5
- provenance quality: 5
- hallucination control: 5

The current data model does not persist a separate human rubric aggregate. The
simple mean of persisted scores is `5.0`, but the persisted aggregate field is
`NOT_CAPTURED`.

Daniel also supplied these explicit review metadata scores:

- decomposition quality: 5
- dependency-graph quality: 5
- sequencing discipline: 5
- scope containment: 5
- local engineering decision quality: 5
- orchestration quality: 5
- autonomy-discipline quality: 5

Those dimensions were recorded in rubric comments and phase documentation
because they are not canonical persisted human rubric score fields.

## Autonomy Gate

Gate artifact:

- id: `LPILOT_000011`
- target repository: `LREPO_000001`
- repository snapshot: `LSNAP_000004`
- repository state: `LRSTATE_000013`
- task: `LTASK_000008`
- plan: `LPLAN_000004`
- freeze: `LFREEZE_000004`
- deterministic evaluation: `LEVALPLAN_000010`
- human rubric: `LRUBRIC_000008`
- benchmark before: `LBENCH_000015`
- benchmark after: `LBENCH_000016`
- repository integrity: `UNCHANGED`
- canonical status: `CANONICAL_CLEAN`

Gate result:

- passed: `true`
- blockers: none
- warnings: none
- benchmark regression: `NO_REGRESSION`
- recommendation: `READY_FOR_SUPERVISED_ENGINEERING_WORKFLOW`

The recommendation is machine-computed from preserved Phase 1.17 evidence plus
the captured human rubric. It is not unrestricted autonomy and it does not
authorize integration of the Phase 1.17 Darwin implementation.

## Granted Authority

`READY_FOR_SUPERVISED_ENGINEERING_WORKFLOW` permits Lucius to maintain one
bounded engineering workflow across multiple internal checkpoints for one
human-authorized objective. Within that objective, Lucius may:

- understand the repository and relevant architecture;
- decompose work into internal tasks;
- sequence dependent tasks under an explicit graph;
- create and freeze execution packages;
- implement in isolated branches or worktrees;
- make minor local engineering decisions inside the frozen objective boundary;
- run bounded repair loops;
- run targeted, integration, and full tests;
- update project documentation;
- maintain evidence, task state, and review packages;
- pause at human checkpoint boundaries;
- resume after explicit approval where required.

## Mandatory Human Checkpoints

Human approval remains required before:

- merge to main;
- push when policy requires it;
- production deployment;
- destructive operations;
- credential or security changes;
- unplanned migration or schema expansion;
- new external dependency with material impact;
- architectural expansion outside the frozen objective;
- cross-project modifications;
- material scope changes.

Lucius must classify these boundaries and stop safely when a checkpoint is
reached.

## Authority Exclusions

The recommendation does not permit:

- unrestricted repository writes;
- automatic main merges;
- automatic production deployment;
- force push;
- destructive Git;
- unrestricted migrations;
- credential manipulation;
- unrestricted external-service operations;
- unlimited task spawning;
- unlimited repair cycles;
- broad autonomous cross-project work;
- self-granting additional authority;
- removal of human oversight boundaries.

## Supervised Workflow State Model

Phase 1.17A defines the following conceptual states for the next supervised
engineering pilot:

- `OBJECTIVE_ACCEPTED`: Daniel has authorized one bounded objective.
- `PLANNING`: Lucius is gathering evidence, decomposing work, and drafting the
  plan/package.
- `PLAN_READY`: the plan and task graph are ready for freeze or review.
- `IMPLEMENTING`: Lucius is executing within an isolated branch or worktree.
- `VERIFYING`: Lucius is running targeted, integrated, and full verification.
- `CHECKPOINT_REVIEW_REQUIRED`: a mandatory human checkpoint has been reached.
- `BLOCKED`: Lucius cannot proceed without new evidence, authority, or a human
  decision.
- `APPROVED_TO_CONTINUE`: Daniel has approved continuation past the checkpoint.
- `COMPLETED_PENDING_INTEGRATION`: implementation and verification are complete
  but merge/push/deploy authority has not been granted.
- `CLOSED`: the objective has been completed, rejected, or explicitly stopped.

These states are documented semantics for the next pilot. Phase 1.17A did not
add new persistence tables for supervised workflow state.

## Phase 1.17 Implementation Integration State

The reviewed Darwin implementation remains isolated:

- branch: `lucius/phase-1.17-research-integrity-report`
- worktree: `/private/tmp/darwin-phase-1.17-research-integrity-report`
- commit: `de16ea2f23e74d3251c241b1af02a9c18d7572a3`
- branch status: clean
- merge: not performed
- push: not performed

Changed files in the isolated branch remain:

- `docs/runtime/research-run-lifecycle.md`
- `src/darwin/cli/app.py`
- `src/darwin/research/__init__.py`
- `src/darwin/research/schemas.py`
- `src/darwin/research/service.py`
- `tests/test_cli.py`
- `tests/test_research_service.py`

Manual integration recommendation after separate Daniel authorization:

1. Inspect the isolated commit from the Darwin repository.
2. Merge or cherry-pick
   `de16ea2f23e74d3251c241b1af02a9c18d7572a3` into Darwin main.
3. Run Darwin targeted service and CLI tests.
4. Run the full Darwin test suite.
5. Push only after explicit authorization.

## Darwin Integrity

Final Darwin main observation before documentation commit:

- repository state: `LRSTATE_000013`
- branch: `main`
- HEAD: `e436a9792f0ca294c3810ddef259b812008d8635`
- status: `## main...origin/main [ahead 4]`
- classification: `CANONICAL_CLEAN`
- tracked modifications: none
- staged modifications: none
- untracked files: none
- manifest hash:
  `4636a1af8f48cb10b9062f0c8941f7bbc82725c14f6c8245647f64dbacd8be41`
- merge/rebase/cherry-pick state: none observed
- Phase 1.17 implementation applied to main: no

## Verification

Phase 1.17A final Lucius tests and post benchmark must be captured after the
Lucius documentation commit so the formal benchmark runs against a clean HEAD.
The completion report is authoritative for those final command results.

## Next Recommendation

Do not begin Phase 1.18 automatically. The recommended next phase is a
separately authorized supervised engineering workflow pilot, or a separate
manual integration review for the Phase 1.17 Darwin implementation.
