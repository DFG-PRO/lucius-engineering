from __future__ import annotations

from lucius.domain.enums import ModelCapability
from lucius.models.capabilities import COST_RANK, LATENCY_RANK
from lucius.models.errors import ModelErrorCode
from lucius.models.policies import validate_profile_for_request
from lucius.models.schemas import ModelProfile, ModelProvider, ModelRequest, RoutingDecision, RoutingRejection


class CapabilityRouter:
    def route(
        self,
        request: ModelRequest,
        *,
        providers: list[ModelProvider],
        profiles: list[ModelProfile],
    ) -> RoutingDecision:
        provider_by_id = {provider.id: provider for provider in providers if provider.id is not None}
        eligible: list[tuple[ModelProfile, ModelProvider]] = []
        rejected: list[RoutingRejection] = []
        for profile in profiles:
            provider = provider_by_id.get(profile.provider_id)
            if provider is None:
                rejected.append(
                    RoutingRejection(
                        provider_id=profile.provider_id,
                        model_profile_id=profile.id,
                        reason=ModelErrorCode.PROVIDER_UNAVAILABLE,
                        message=f"Provider {profile.provider_id} is not registered.",
                    )
                )
                continue
            blockers = validate_profile_for_request(request, provider, profile)
            if blockers:
                rejected.extend(blockers)
            else:
                eligible.append((profile, provider))
        if not eligible:
            error_code = _dominant_error(rejected)
            return RoutingDecision(
                request_id=request.request_id,
                rejected=rejected,
                error_code=error_code,
                reasons=[f"No eligible model: {error_code.value}."],
            )
        eligible = _order_eligible(request, eligible)
        selected, _provider = eligible[0]
        fallback = [profile.id for profile, _ in eligible[1:] if profile.id is not None]
        reasons = [
            f"Selected {selected.id} for {','.join(sorted(c.value for c in request.required_capabilities)) or 'general'} request.",
            f"Cost class {selected.cost_class.value}; privacy class {selected.privacy_class.value}.",
        ]
        if request.preferred_model == selected.model_name or request.preferred_model == selected.id:
            reasons.append("Preferred model honored.")
        elif request.preferred_model:
            reasons.append("Preferred model unavailable or disallowed; routed to eligible alternative.")
        return RoutingDecision(
            request_id=request.request_id,
            selected_profile_id=selected.id,
            fallback_profile_ids=fallback,
            reasons=reasons,
            rejected=rejected,
        )


def _selection_key(request: ModelRequest, profile: ModelProfile, provider: ModelProvider) -> tuple[int, int, int, int, str, str]:
    preferred_model = int(not (request.preferred_model in {profile.id, profile.model_name}))
    preferred_provider = int(not (request.preferred_provider in {provider.id, provider.name, provider.provider_type.value}))
    reasoning_bonus = 0
    if ModelCapability.HIGH_REASONING in request.required_capabilities or request.minimum_quality:
        reasoning_bonus = -profile.quality_score
    return (
        preferred_model,
        preferred_provider,
        reasoning_bonus,
        COST_RANK[profile.cost_class],
        LATENCY_RANK[profile.latency_class],
        profile.id or profile.model_name,
    )


def _order_eligible(
    request: ModelRequest,
    eligible: list[tuple[ModelProfile, ModelProvider]],
) -> list[tuple[ModelProfile, ModelProvider]]:
    sorted_eligible = sorted(eligible, key=lambda pair: _selection_key(request, pair[0], pair[1]))
    if not request.fallback_model_ids:
        return sorted_eligible
    explicit: list[tuple[ModelProfile, ModelProvider]] = []
    remaining = list(sorted_eligible)
    for requested_id in request.fallback_model_ids:
        match = next(
            (
                pair
                for pair in remaining
                if pair[0].id == requested_id or pair[0].model_name == requested_id
            ),
            None,
        )
        if match is not None:
            explicit.append(match)
            remaining.remove(match)
    return explicit + remaining if explicit else sorted_eligible


def _dominant_error(rejected: list[RoutingRejection]) -> ModelErrorCode:
    if not rejected:
        return ModelErrorCode.NO_ELIGIBLE_MODEL
    priority = [
        ModelErrorCode.PRIVACY_POLICY_BLOCK,
        ModelErrorCode.COST_POLICY_BLOCK,
        ModelErrorCode.BUDGET_EXCEEDED,
        ModelErrorCode.CONTEXT_LIMIT_EXCEEDED,
        ModelErrorCode.CAPABILITY_UNAVAILABLE,
        ModelErrorCode.MODEL_DISABLED,
        ModelErrorCode.PROVIDER_UNAVAILABLE,
    ]
    reasons = {item.reason for item in rejected}
    for code in priority:
        if code in reasons:
            return code
    return ModelErrorCode.NO_ELIGIBLE_MODEL
