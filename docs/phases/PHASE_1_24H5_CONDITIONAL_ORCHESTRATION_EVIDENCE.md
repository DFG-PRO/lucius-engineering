# Phase 1.24H5 Conditional Orchestration Evidence

Phase 1.24H5 dispositions `LLEARN_000001`, which was created after
`LPILOT_000036` completed four Darwin tasks but produced
`LEVALPLAN_000087 = INSUFFICIENT_EVIDENCE` because checkpoint/resume was not
naturally triggered.

## Repair

The orchestration evaluator now distinguishes conditional capability states:

- `NOT_TRIGGERED`
- `NOT_APPLICABLE_IN_THIS_RUN`
- `INHERITED_VALID_EVIDENCE`
- `FRESH_EVIDENCE_REQUIRED`
- `INSUFFICIENT_EVIDENCE`

Checkpoint/resume is not weakened globally. Fresh checkpoint/resume evidence is
still required when work blocks or when applicability is uncertain.

When no task genuinely blocks, the checkpoint/resume dimension may be
`NOT_APPLICABLE_IN_THIS_RUN` only if the artifact also proves:

- the trigger did not occur;
- the trigger was not artificially avoided;
- inherited canonical evidence exists;
- the inherited evidence is recent and runtime-applicable;
- no relevant implementation change invalidates it;
- the inherited evidence references a passing orchestration evaluation whose
  `checkpoint_resume_capacity` dimension passed.

Otherwise the evaluator returns `INSUFFICIENT_EVIDENCE`.

## Evidence

The current inherited evidence source is `LEVALPLAN_000082`, which passed
`ORCHESTRATION_CONTROL_PLANE` evaluation with a passing
`checkpoint_resume_capacity` dimension and `LQCHK_000013` checkpoint/resume
evidence.

## Verification

Focused adversarial tests cover:

- valid not-triggered checkpoint/resume with inherited evidence;
- triggered checkpoint/resume incorrectly claimed as not applicable;
- stale inherited evidence;
- missing inherited evaluation reference;
- implementation changes that invalidate inherited evidence.

`LEVALPLAN_000087` is preserved historically. A superseding evaluation may be
created against the same `LFREEZE_000048` evidence with explicit conditional
capability metadata.
