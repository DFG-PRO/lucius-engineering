ENTITY_PREFIXES: dict[str, str] = {
    "project": "LPROJ",
    "repository": "LREPO",
    "snapshot": "LSNAP",
    "task": "LTASK",
    "task_contract": "LCONTR",
    "task_run": "LRUN",
    "context_package": "LCTX",
    "documentation_completion": "LDOC",
    "evidence": "LEVID",
    "engineering_plan": "LPLAN",
    "memory": "LMEM",
    "learning": "LLEARN",
    "evaluation": "LEVAL",
    "audit": "LAUDIT",
    "model_provider": "LMPROV",
    "model_profile": "LMPROF",
    "model_execution": "LMEXEC",
    "evaluation_suite": "LESUITE",
    "evaluation_case": "LCASE",
    "evaluation_run": "LERUN",
    "evaluation_case_result": "LERES",
    "repository_state": "LRSTATE",
    "plan_freeze": "LFREEZE",
    "plan_evaluation": "LEVALPLAN",
    "human_rubric": "LRUBRIC",
    "benchmark": "LBENCH",
    "pilot_learning": "LPLEARN",
    "pilot_record": "LPILOT",
    "persistent_workflow": "LWORK",
    "workflow_checkpoint": "LWCHK",
    "resume_validation": "LRESUME",
}


def format_public_id(entity: str, number: int) -> str:
    if entity not in ENTITY_PREFIXES:
        raise ValueError(f"Unknown Lucius entity type: {entity}")
    if number < 1:
        raise ValueError("Lucius public IDs start at 1")
    return f"{ENTITY_PREFIXES[entity]}_{number:06d}"
