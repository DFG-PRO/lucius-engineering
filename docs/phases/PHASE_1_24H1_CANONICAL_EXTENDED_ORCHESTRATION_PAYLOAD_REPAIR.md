# Phase 1.24H1 Canonical Extended Orchestration Payload Repair

Phase 1.24H1 repairs the canonical payload gap identified by `LPLEARN_000053`
and `LAUDIT_001657`.

## Objective

Make extended orchestration contracts and required adversarial probes durable
canonical planning data so a valid extended orchestration plan can freeze
through the normal `EngineeringPlanORM -> PlanFreezeService` path.

## Design

`EngineeringPlan` now persists:

- `orchestration_contract_required`
- `orchestration_contract`
- `adversarial_probes`

The planning schema, plan repository, ORM, migration, hardening payload adapter,
and freeze payload all expose the same fields. Non-extended plans retain empty
defaults and remain compatible with existing freezes.

## Validation

The Phase 1.24H adversarial regression band now verifies:

- a complete canonical extended contract freezes successfully;
- the previously failing planning repository to fresh-session freeze path
  preserves the contract and probes;
- missing or incomplete contracts block freeze;
- missing, incomplete, or malformed required probes block freeze;
- ordinary plans still freeze with backward-compatible defaults;
- unsupported verified claims still block before extended freeze success;
- existing initial-eligibility mismatch coverage remains active.

## Migration

Migration `0011_extended_orchestration_plan_payload` adds the three canonical
columns to `engineering_plans`.

The canonical store had an empty `alembic_version` row despite containing the
tables through `0010_persistent_workflows`; it was stamped to
`0010_persistent_workflows` before applying only revision
`0011_extended_orchestration_plan_payload`.

## Scope

This repair does not retry the extended controlled multi-project pilot and does
not modify Darwin, Billy, Trade Executor, or Trading Dashboard.
