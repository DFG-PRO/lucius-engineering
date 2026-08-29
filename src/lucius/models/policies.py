from __future__ import annotations

from lucius.domain.enums import CostClass, ModelCapability, ModelProfileStatus, PrivacyClass, ProviderStatus
from lucius.models.capabilities import capabilities_satisfied, cost_allows, privacy_allows
from lucius.models.errors import ModelErrorCode
from lucius.models.schemas import ModelProfile, ModelProvider, ModelRequest, RoutingRejection


def estimate_request_tokens(request: ModelRequest) -> int:
    explicit = request.metadata.get("context_tokens")
    if isinstance(explicit, int):
        return explicit
    chars = sum(len(message.content) for message in request.input_messages)
    return max(1, chars // 4) if chars else 0


def estimate_profile_cost(profile: ModelProfile, *, input_tokens: int, output_tokens: int | None = None) -> float | None:
    output_tokens = output_tokens or 0
    if profile.input_cost_per_million is None and profile.output_cost_per_million is None:
        return None
    input_cost = (profile.input_cost_per_million or 0.0) * input_tokens / 1_000_000
    output_cost = (profile.output_cost_per_million or 0.0) * output_tokens / 1_000_000
    return input_cost + output_cost


def validate_profile_for_request(
    request: ModelRequest,
    provider: ModelProvider,
    profile: ModelProfile,
) -> list[RoutingRejection]:
    rejections: list[RoutingRejection] = []
    if provider.status != ProviderStatus.ACTIVE:
        rejections.append(
            RoutingRejection(
                provider_id=provider.id,
                model_profile_id=profile.id,
                reason=ModelErrorCode.PROVIDER_UNAVAILABLE,
                message=f"Provider {provider.id} is {provider.status.value}.",
            )
        )
    if profile.status != ModelProfileStatus.ACTIVE:
        rejections.append(
            RoutingRejection(
                provider_id=provider.id,
                model_profile_id=profile.id,
                reason=ModelErrorCode.MODEL_DISABLED,
                message=f"Model profile {profile.id} is {profile.status.value}.",
            )
        )
    required = set(request.required_capabilities)
    if request.structured_output_schema is not None:
        required.add(ModelCapability.STRUCTURED_OUTPUT)
    if not capabilities_satisfied(set(profile.capabilities), required, provider_supports_local=provider.supports_local):
        rejections.append(
            RoutingRejection(
                provider_id=provider.id,
                model_profile_id=profile.id,
                reason=ModelErrorCode.CAPABILITY_UNAVAILABLE,
                message="Required capabilities are not satisfied.",
            )
        )
    if not privacy_allows(profile.privacy_class, request.privacy_class):
        rejections.append(
            RoutingRejection(
                provider_id=provider.id,
                model_profile_id=profile.id,
                reason=ModelErrorCode.PRIVACY_POLICY_BLOCK,
                message=f"{profile.privacy_class.value} model cannot process {request.privacy_class.value} request.",
            )
        )
    if not cost_allows(profile.cost_class, request.max_cost_class):
        rejections.append(
            RoutingRejection(
                provider_id=provider.id,
                model_profile_id=profile.id,
                reason=ModelErrorCode.COST_POLICY_BLOCK,
                message=f"{profile.cost_class.value} exceeds max cost {request.max_cost_class.value}.",
            )
        )
    context_tokens = estimate_request_tokens(request)
    if profile.max_context_tokens is not None and context_tokens > profile.max_context_tokens:
        rejections.append(
            RoutingRejection(
                provider_id=provider.id,
                model_profile_id=profile.id,
                reason=ModelErrorCode.CONTEXT_LIMIT_EXCEEDED,
                message=f"Estimated context {context_tokens} exceeds limit {profile.max_context_tokens}.",
            )
        )
    if request.max_output_tokens is not None and profile.max_output_tokens is not None:
        if request.max_output_tokens > profile.max_output_tokens:
            rejections.append(
                RoutingRejection(
                    provider_id=provider.id,
                    model_profile_id=profile.id,
                    reason=ModelErrorCode.CONTEXT_LIMIT_EXCEEDED,
                    message=f"Requested output {request.max_output_tokens} exceeds limit {profile.max_output_tokens}.",
                )
            )
    if profile.quality_score < request.minimum_quality:
        rejections.append(
            RoutingRejection(
                provider_id=provider.id,
                model_profile_id=profile.id,
                reason=ModelErrorCode.CAPABILITY_UNAVAILABLE,
                message=f"Quality {profile.quality_score} is below minimum {request.minimum_quality}.",
            )
        )
    estimated_cost = estimate_profile_cost(profile, input_tokens=context_tokens, output_tokens=request.max_output_tokens)
    if request.max_estimated_cost is not None and estimated_cost is not None and estimated_cost > request.max_estimated_cost:
        rejections.append(
            RoutingRejection(
                provider_id=provider.id,
                model_profile_id=profile.id,
                reason=ModelErrorCode.BUDGET_EXCEEDED,
                message=f"Estimated cost {estimated_cost:.8f} exceeds request budget.",
            )
        )
    return rejections


def normalize_cost_class(value: CostClass | None) -> CostClass | None:
    return value


def normalize_privacy_class(value: PrivacyClass) -> PrivacyClass:
    return value
