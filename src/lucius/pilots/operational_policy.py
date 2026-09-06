from __future__ import annotations


EXTENDED_OPERATIONAL_PILOT_STAGES = {
    "EXTENDED_MULTI_PROJECT_OPERATIONAL_PILOT",
    "CONTROLLED_MULTI_PROJECT_OPERATIONAL_QUEUE_PILOT",
}


def overall_orchestration_freeze_required(*, pilot_stage: str, mutation_started: bool) -> bool:
    return mutation_started and pilot_stage in EXTENDED_OPERATIONAL_PILOT_STAGES


def validate_overall_orchestration_freeze(
    *,
    pilot_stage: str,
    mutation_started: bool,
    overall_plan_freeze_id: str | None,
) -> list[dict[str, str]]:
    if overall_orchestration_freeze_required(pilot_stage=pilot_stage, mutation_started=mutation_started) and not overall_plan_freeze_id:
        return [
            {
                "code": "OVERALL_ORCHESTRATION_FREEZE_REQUIRED",
                "message": "Future extended operational pilots must freeze an overall orchestration plan before pilot mutation.",
            }
        ]
    return []
