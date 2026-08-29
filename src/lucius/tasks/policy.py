from __future__ import annotations

from lucius.domain.enums import AllowedAction, AuthorityLevel, BlockerCode, Environment
from lucius.tasks.schemas import StructuredBlocker

AUTHORITY_RANK = {
    AuthorityLevel.L0: 0,
    AuthorityLevel.L1: 1,
    AuthorityLevel.L2: 2,
    AuthorityLevel.L3: 3,
}

ACTION_AUTHORITY = {
    AllowedAction.READ_REPOSITORY: AuthorityLevel.L0,
    AllowedAction.READ_DOCUMENTATION: AuthorityLevel.L0,
    AllowedAction.RUN_TESTS: AuthorityLevel.L0,
    AllowedAction.CREATE_TEMP_FILES: AuthorityLevel.L0,
    AllowedAction.WRITE_SOURCE: AuthorityLevel.L1,
    AllowedAction.WRITE_TESTS: AuthorityLevel.L1,
    AllowedAction.WRITE_DOCUMENTATION: AuthorityLevel.L1,
    AllowedAction.CREATE_MIGRATION: AuthorityLevel.L1,
    AllowedAction.CREATE_COMMIT: AuthorityLevel.L2,
}

ENVIRONMENT_AUTHORITY = {
    Environment.SANDBOX: AuthorityLevel.L0,
    Environment.DEVELOPMENT: AuthorityLevel.L0,
    Environment.STAGING: AuthorityLevel.L2,
    Environment.PRODUCTION: AuthorityLevel.L3,
}


def authority_exceeds(candidate: AuthorityLevel, maximum: AuthorityLevel) -> bool:
    return AUTHORITY_RANK[candidate] > AUTHORITY_RANK[maximum]


def validate_authority(
    *,
    task_authority: AuthorityLevel,
    contract_authority: AuthorityLevel,
    actions: list[AllowedAction],
    environment: Environment,
) -> list[StructuredBlocker]:
    blockers: list[StructuredBlocker] = []
    if authority_exceeds(contract_authority, task_authority):
        blockers.append(
            StructuredBlocker(
                code=BlockerCode.AUTHORITY_INSUFFICIENT,
                message="TaskContract authority cannot exceed Task authority.",
                metadata={"task_authority": task_authority.value, "contract_authority": contract_authority.value},
            )
        )
    for action in actions:
        required = ACTION_AUTHORITY[action]
        if authority_exceeds(required, contract_authority) or authority_exceeds(required, task_authority):
            blockers.append(
                StructuredBlocker(
                    code=BlockerCode.AUTHORITY_INSUFFICIENT,
                    message=f"Action {action.value} requires authority {required.value}.",
                    metadata={"action": action.value, "required_authority": required.value},
                )
            )
    env_required = ENVIRONMENT_AUTHORITY[environment]
    if authority_exceeds(env_required, contract_authority) or authority_exceeds(env_required, task_authority):
        blockers.append(
            StructuredBlocker(
                code=BlockerCode.AUTHORITY_INSUFFICIENT,
                message=f"Environment {environment.value} requires authority {env_required.value}.",
                metadata={"environment": environment.value, "required_authority": env_required.value},
            )
        )
    return blockers

