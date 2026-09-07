# Phase 1.24H2 Learning Hardening Repair

Phase 1.24H2 repairs the learning candidates identified after the extended
controlled multi-project pilot retry:

- `LPLEARN_000054`: documentation planning needed to distinguish exact required
  documentation paths from flexible canonical documentation targets.
- `LPLEARN_000055`: no-file orchestration/control-plane plans needed
  deterministic evaluation semantics that do not depend on artificial
  implementation files.

Canonical repair plan:

- plan: `LPLAN_000035`
- freeze: `LFREEZE_000035`
- PRE benchmark: `LBENCH_000060`
- repository snapshot: `LSNAP_000029`

## Documentation Target Semantics

`DocumentationRequirement` now supports optional semantic fields:

- `target_type`
- `exact_path_required`
- `acceptable_paths`
- `acceptable_categories`
- `proposed_path`
- `canonical_target`

Exact-path documentation requirements remain enforceable. Flexible canonical
documentation requirements can be satisfied by architecture-correct canonical
documentation evidence, so an implementation that updates the correct target
project document is not treated as a false file-path deviation merely because
planning predicted a nearby filename.

## Orchestration Evaluation

`EngineeringPlanEvaluationMode` now includes
`ORCHESTRATION_CONTROL_PLANE`. The evaluator selects this mode automatically
when the frozen plan carries orchestration contract/probe semantics.

The orchestration evaluator checks persisted control-plane evidence for:

- participating workflows/projects;
- pre-mutation release;
- scheduler decisions;
- blocker/capacity release;
- checkpoints/resumes;
- priority;
- no-preemption;
- exact selection-to-mutation identity;
- dependency isolation;
- fresh-process reconstruction;
- target isolation;
- provenance refs;
- tests and documentation;
- authority compliance;
- declared closure claims.

Missing required orchestration evidence remains fail-closed as
`INSUFFICIENT_EVIDENCE`. False dispatch, participant, dependency, target
isolation, authority, hallucination, or unsupported-claim evidence remains a
critical failure.

## Verification

Focused tests cover:

- exact required documentation paths;
- flexible canonical documentation targets;
- unrelated documentation rejection;
- proposed new documentation paths;
- target-project canonical documentation requirements;
- valid no-file orchestration evaluation;
- missing scheduler, resume, and authority evidence;
- false exact dispatch;
- unrelated orchestration evidence;
- ordinary implementation-plan mode selection;
- mixed orchestration/file semantics.

Historical artifacts are preserved. `LEVALPLAN_000067` is not rewritten; an
equivalent no-file orchestration pattern is evaluated by the repaired
orchestration-aware path.
