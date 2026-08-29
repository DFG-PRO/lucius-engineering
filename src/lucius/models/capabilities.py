from __future__ import annotations

from lucius.domain.enums import CostClass, LatencyClass, ModelCapability, PrivacyClass


PRIVACY_RANK = {
    PrivacyClass.PUBLIC: 0,
    PrivacyClass.INTERNAL: 1,
    PrivacyClass.CONFIDENTIAL: 2,
    PrivacyClass.RESTRICTED: 3,
}

COST_RANK = {
    CostClass.FREE: 0,
    CostClass.LOW: 1,
    CostClass.MEDIUM: 2,
    CostClass.HIGH: 3,
    CostClass.PREMIUM: 4,
}

LATENCY_RANK = {
    LatencyClass.LOW: 0,
    LatencyClass.MEDIUM: 1,
    LatencyClass.HIGH: 2,
}


def privacy_allows(model_privacy: PrivacyClass, request_privacy: PrivacyClass) -> bool:
    return PRIVACY_RANK[request_privacy] <= PRIVACY_RANK[model_privacy]


def cost_allows(model_cost: CostClass, max_cost: CostClass | None) -> bool:
    return max_cost is None or COST_RANK[model_cost] <= COST_RANK[max_cost]


def capabilities_satisfied(
    model_capabilities: set[ModelCapability],
    required_capabilities: set[ModelCapability],
    *,
    provider_supports_local: bool = False,
) -> bool:
    for capability in required_capabilities:
        if capability == ModelCapability.PRIVATE_INFERENCE and provider_supports_local:
            continue
        if capability not in model_capabilities:
            return False
    return True
