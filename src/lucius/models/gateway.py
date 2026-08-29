from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import (
    AuthorityLevel,
    ModelCostSource,
    ModelProfileStatus,
    ModelResponseStatus,
    ProviderStatus,
)
from lucius.models.errors import ModelErrorCode, ModelGatewayError, ModelProviderError, NON_FALLBACK_MODEL_ERRORS, RETRYABLE_MODEL_ERRORS
from lucius.models.metrics import create_execution_id, execution_from_response
from lucius.models.providers.base import ProviderAdapter, ProviderGeneration
from lucius.models.router import CapabilityRouter
from lucius.models.schemas import ModelProfile, ModelProvider, ModelRequest, ModelResponse, RoutingDecision
from lucius.persistence.orm import ModelExecutionORM, ModelProfileORM, ModelProviderORM, utc_now
from lucius.persistence.repositories import next_id


SECRET_KEY_MARKERS = ("secret", "token", "api_key", "apikey", "password", "credential", "key")


class ModelGateway:
    def __init__(self, session: Session, *, router: CapabilityRouter | None = None):
        self.session = session
        self.router = router or CapabilityRouter()
        self.audit = AuditService(session)
        self._adapters: dict[str, ProviderAdapter] = {}

    def register_provider_adapter(self, adapter: ProviderAdapter) -> None:
        adapter.validate_configuration()
        self._adapters[adapter.provider_id] = adapter

    def register_provider(self, provider: ModelProvider, *, actor: str = "SYSTEM") -> ModelProviderORM:
        metadata = _sanitize_metadata(provider.metadata)
        if provider.id is None:
            provider_id = next_id(self.session, "model_provider")
        else:
            provider_id = provider.id
        row = self.session.get(ModelProviderORM, provider_id)
        if row is None:
            row = ModelProviderORM(
                id=provider_id,
                name=provider.name,
                provider_type=provider.provider_type.value,
                status=provider.status.value,
                supports_local=provider.supports_local,
                default_timeout=provider.default_timeout,
                secret_reference_name=provider.secret_reference_name,
                provider_metadata=metadata,
                created_at=provider.created_at,
                updated_at=utc_now(),
            )
            self.session.add(row)
        else:
            row.name = provider.name
            row.provider_type = provider.provider_type.value
            row.status = provider.status.value
            row.supports_local = provider.supports_local
            row.default_timeout = provider.default_timeout
            row.secret_reference_name = provider.secret_reference_name
            row.provider_metadata = metadata
            row.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="MODEL_PROVIDER_REGISTERED",
            actor=actor,
            action="register_model_provider",
            result="SUCCESS",
            metadata={"provider_id": row.id, "provider_type": row.provider_type, "status": row.status},
        )
        return row

    def register_profile(self, profile: ModelProfile, *, actor: str = "SYSTEM") -> ModelProfileORM:
        if self.session.get(ModelProviderORM, profile.provider_id) is None:
            raise ModelGatewayError(ModelErrorCode.PROVIDER_UNAVAILABLE, f"Unknown provider: {profile.provider_id}")
        metadata = _sanitize_metadata(profile.metadata)
        if profile.id is None:
            profile_id = next_id(self.session, "model_profile")
        else:
            profile_id = profile.id
        row = self.session.get(ModelProfileORM, profile_id)
        if row is None:
            row = ModelProfileORM(
                id=profile_id,
                provider_id=profile.provider_id,
                model_name=profile.model_name,
                display_name=profile.display_name,
                status=profile.status.value,
                capabilities=sorted(capability.value for capability in profile.capabilities),
                privacy_class=profile.privacy_class.value,
                cost_class=profile.cost_class.value,
                latency_class=profile.latency_class.value,
                quality_score=profile.quality_score,
                supports_structured_output=profile.supports_structured_output,
                supports_tool_use=profile.supports_tool_use,
                supports_large_context=profile.supports_large_context,
                supports_code=profile.supports_code,
                supports_reasoning=profile.supports_reasoning,
                max_context_tokens=profile.max_context_tokens,
                max_output_tokens=profile.max_output_tokens,
                input_cost_per_million=profile.input_cost_per_million,
                output_cost_per_million=profile.output_cost_per_million,
                profile_metadata=metadata,
                created_at=profile.created_at,
                updated_at=utc_now(),
            )
            self.session.add(row)
        else:
            row.provider_id = profile.provider_id
            row.model_name = profile.model_name
            row.display_name = profile.display_name
            row.status = profile.status.value
            row.capabilities = sorted(capability.value for capability in profile.capabilities)
            row.privacy_class = profile.privacy_class.value
            row.cost_class = profile.cost_class.value
            row.latency_class = profile.latency_class.value
            row.quality_score = profile.quality_score
            row.supports_structured_output = profile.supports_structured_output
            row.supports_tool_use = profile.supports_tool_use
            row.supports_large_context = profile.supports_large_context
            row.supports_code = profile.supports_code
            row.supports_reasoning = profile.supports_reasoning
            row.max_context_tokens = profile.max_context_tokens
            row.max_output_tokens = profile.max_output_tokens
            row.input_cost_per_million = profile.input_cost_per_million
            row.output_cost_per_million = profile.output_cost_per_million
            row.profile_metadata = metadata
            row.updated_at = utc_now()
        self.session.flush()
        self.audit.record(
            event_type="MODEL_PROFILE_REGISTERED",
            actor=actor,
            action="register_model_profile",
            result="SUCCESS",
            metadata={"profile_id": row.id, "provider_id": row.provider_id, "status": row.status},
        )
        return row

    def list_available_models(self) -> list[ModelProfile]:
        providers = {provider.id: provider for provider in self._load_providers() if provider.status == ProviderStatus.ACTIVE}
        return [
            profile
            for profile in self._load_profiles()
            if profile.status == ModelProfileStatus.ACTIVE and profile.provider_id in providers
        ]

    def route_request(self, request: ModelRequest) -> RoutingDecision:
        return self.router.route(request, providers=self._load_providers(), profiles=self._load_profiles())

    def execute(self, request: ModelRequest, *, actor: str = "SYSTEM") -> ModelResponse:
        return self.execute_with_fallback(request, actor=actor)

    def execute_with_fallback(self, request: ModelRequest, *, actor: str = "SYSTEM") -> ModelResponse:
        self._record_request_created(request, actor=actor)
        routing_decision = self.route_request(request)
        if routing_decision.error_code is not None or routing_decision.selected_profile_id is None:
            return self._record_policy_block(request, routing_decision, actor=actor)
        self.audit.record(
            event_type="MODEL_ROUTED",
            actor=actor,
            project_id=request.project_id,
            task_id=request.task_id,
            run_id=request.run_id,
            action="route_model_request",
            result="SUCCESS",
            metadata={
                "request_id": request.request_id,
                "selected_profile_id": routing_decision.selected_profile_id,
                "fallback_profile_ids": routing_decision.fallback_profile_ids,
                "reasons": routing_decision.reasons,
            },
        )
        profile_ids = [routing_decision.selected_profile_id]
        if request.fallback_allowed:
            profile_ids.extend(routing_decision.fallback_profile_ids)
        max_attempts = request.max_attempts or len(profile_ids)
        profile_ids = profile_ids[: max(1, max_attempts)]
        last_response: ModelResponse | None = None
        for attempt_number, profile_id in enumerate(profile_ids, start=1):
            profile = self._profile_by_id(profile_id)
            if profile is None:
                last_response = self._failure_response(
                    request,
                    routing_decision,
                    error_code=ModelErrorCode.MODEL_NOT_FOUND,
                    error_message=f"Model profile {profile_id} disappeared before execution.",
                    attempt_number=attempt_number,
                    fallback_used=attempt_number > 1,
                )
                self._persist_response(request, last_response, routing_decision, actor=actor)
                break
            provider = self._provider_by_id(profile.provider_id)
            adapter = self._adapters.get(profile.provider_id)
            if provider is None or adapter is None:
                last_response = self._failure_response(
                    request,
                    routing_decision,
                    error_code=ModelErrorCode.PROVIDER_UNAVAILABLE,
                    error_message=f"No adapter registered for provider {profile.provider_id}.",
                    profile=profile,
                    attempt_number=attempt_number,
                    fallback_used=attempt_number > 1,
                )
                self._persist_response(request, last_response, routing_decision, actor=actor)
            else:
                last_response = self._attempt_execution(
                    request,
                    routing_decision,
                    profile,
                    adapter,
                    attempt_number=attempt_number,
                    fallback_used=attempt_number > 1,
                    actor=actor,
                )
            if last_response.status == ModelResponseStatus.SUCCEEDED:
                return last_response
            if last_response.error_code in NON_FALLBACK_MODEL_ERRORS:
                return last_response
            if not request.fallback_allowed or last_response.error_code not in RETRYABLE_MODEL_ERRORS:
                return last_response
            if attempt_number < len(profile_ids):
                self.audit.record(
                    event_type="MODEL_FALLBACK_USED",
                    actor=actor,
                    project_id=request.project_id,
                    task_id=request.task_id,
                    run_id=request.run_id,
                    action="fallback_model_request",
                    result="SUCCESS",
                    metadata={
                        "request_id": request.request_id,
                        "from_profile_id": profile_id,
                        "next_profile_id": profile_ids[attempt_number],
                        "error_code": last_response.error_code.value if last_response.error_code else None,
                    },
                )
        if last_response is not None:
            return last_response
        return self._record_policy_block(
            request,
            routing_decision.model_copy(update={"error_code": ModelErrorCode.BUDGET_EXCEEDED}),
            actor=actor,
        )

    def _attempt_execution(
        self,
        request: ModelRequest,
        routing_decision: RoutingDecision,
        profile: ModelProfile,
        adapter: ProviderAdapter,
        *,
        attempt_number: int,
        fallback_used: bool,
        actor: str,
    ) -> ModelResponse:
        self.audit.record(
            event_type="MODEL_EXECUTION_STARTED",
            actor=actor,
            project_id=request.project_id,
            task_id=request.task_id,
            run_id=request.run_id,
            action="execute_model_request",
            result="STARTED",
            metadata={"request_id": request.request_id, "profile_id": profile.id, "attempt_number": attempt_number},
        )
        try:
            if not adapter.is_available():
                raise ModelProviderError(ModelErrorCode.PROVIDER_UNAVAILABLE, "Provider adapter is unavailable.")
            provider_response = adapter.generate(request, profile)
            structured_data = _validate_structured_response(request, provider_response)
            response = ModelResponse(
                request_id=request.request_id,
                provider_id=profile.provider_id,
                model_profile_id=profile.id,
                model_name=profile.model_name,
                status=ModelResponseStatus.SUCCEEDED,
                content=provider_response.content,
                structured_data=structured_data,
                input_tokens=provider_response.input_tokens,
                output_tokens=provider_response.output_tokens,
                total_tokens=provider_response.total_tokens,
                estimated_cost=provider_response.estimated_cost,
                cost_source=provider_response.cost_source,
                latency_ms=provider_response.latency_ms,
                finish_reason=provider_response.finish_reason,
                fallback_used=fallback_used,
                attempt_number=attempt_number,
                routing_decision=routing_decision,
            )
        except ModelProviderError as error:
            response = self._failure_response(
                request,
                routing_decision,
                error_code=error.code,
                error_message=error.message,
                profile=profile,
                attempt_number=attempt_number,
                fallback_used=fallback_used,
            )
        except ValidationError as error:
            response = self._failure_response(
                request,
                routing_decision,
                error_code=ModelErrorCode.STRUCTURED_OUTPUT_INVALID,
                error_message=str(error),
                profile=profile,
                attempt_number=attempt_number,
                fallback_used=fallback_used,
            )
        except ModelGatewayError as error:
            response = self._failure_response(
                request,
                routing_decision,
                error_code=error.code,
                error_message=error.message,
                profile=profile,
                attempt_number=attempt_number,
                fallback_used=fallback_used,
            )
        except Exception as error:
            response = self._failure_response(
                request,
                routing_decision,
                error_code=ModelErrorCode.PROVIDER_ERROR,
                error_message=str(error),
                profile=profile,
                attempt_number=attempt_number,
                fallback_used=fallback_used,
            )
        return self._persist_response(request, response, routing_decision, actor=actor)

    def _persist_response(
        self,
        request: ModelRequest,
        response: ModelResponse,
        routing_decision: RoutingDecision,
        *,
        actor: str,
    ) -> ModelResponse:
        execution_id = create_execution_id(self.session)
        response.execution_id = execution_id
        row = execution_from_response(
            execution_id=execution_id,
            request=request,
            response=response,
            routing_decision=routing_decision,
        )
        self.session.add(row)
        self.session.flush()
        if response.status == ModelResponseStatus.SUCCEEDED:
            event_type = "MODEL_EXECUTION_SUCCEEDED"
            result = "SUCCESS"
        else:
            event_type = "MODEL_EXECUTION_FAILED"
            result = "FAILURE"
            if response.error_code == ModelErrorCode.STRUCTURED_OUTPUT_INVALID:
                event_type = "MODEL_RESPONSE_INVALID"
        self.audit.record(
            event_type=event_type,
            actor=actor,
            project_id=request.project_id,
            task_id=request.task_id,
            run_id=request.run_id,
            action="execute_model_request",
            result=result,
            metadata={
                "request_id": request.request_id,
                "execution_id": execution_id,
                "provider_id": response.provider_id,
                "model_profile_id": response.model_profile_id,
                "model_name": response.model_name,
                "status": response.status.value,
                "latency_ms": response.latency_ms,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "estimated_cost": response.estimated_cost,
                "cost_source": response.cost_source.value,
                "fallback_used": response.fallback_used,
                "attempt_number": response.attempt_number,
                "error_code": response.error_code.value if response.error_code else None,
            },
        )
        return response

    def _record_request_created(self, request: ModelRequest, *, actor: str) -> None:
        self.audit.record(
            event_type="MODEL_REQUEST_CREATED",
            actor=actor,
            project_id=request.project_id,
            task_id=request.task_id,
            run_id=request.run_id,
            authority_level=AuthorityLevel.L0,
            action="create_model_request",
            result="SUCCESS",
            metadata={
                "request_id": request.request_id,
                "purpose": request.purpose,
                "required_capabilities": sorted(capability.value for capability in request.required_capabilities),
                "privacy_class": request.privacy_class.value,
                "max_cost_class": request.max_cost_class.value if request.max_cost_class else None,
                "fallback_allowed": request.fallback_allowed,
            },
        )

    def _record_policy_block(self, request: ModelRequest, routing_decision: RoutingDecision, *, actor: str) -> ModelResponse:
        error_code = routing_decision.error_code or ModelErrorCode.NO_ELIGIBLE_MODEL
        event_type = "MODEL_POLICY_BLOCK" if error_code in NON_FALLBACK_MODEL_ERRORS else "MODEL_EXECUTION_FAILED"
        self.audit.record(
            event_type=event_type,
            actor=actor,
            project_id=request.project_id,
            task_id=request.task_id,
            run_id=request.run_id,
            action="route_model_request",
            result="BLOCKED",
            metadata={
                "request_id": request.request_id,
                "error_code": error_code.value,
                "rejected": [item.model_dump(mode="json") for item in routing_decision.rejected],
            },
        )
        response = self._failure_response(
            request,
            routing_decision,
            error_code=error_code,
            error_message=routing_decision.reasons[0] if routing_decision.reasons else error_code.value,
        )
        return self._persist_response(request, response, routing_decision, actor=actor)

    def _failure_response(
        self,
        request: ModelRequest,
        routing_decision: RoutingDecision,
        *,
        error_code: ModelErrorCode,
        error_message: str,
        profile: ModelProfile | None = None,
        attempt_number: int = 1,
        fallback_used: bool = False,
    ) -> ModelResponse:
        return ModelResponse(
            request_id=request.request_id,
            provider_id=profile.provider_id if profile else None,
            model_profile_id=profile.id if profile else None,
            model_name=profile.model_name if profile else None,
            status=ModelResponseStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
            cost_source=ModelCostSource.UNKNOWN,
            fallback_used=fallback_used,
            attempt_number=attempt_number,
            routing_decision=routing_decision,
        )

    def _load_providers(self) -> list[ModelProvider]:
        rows = self.session.scalars(select(ModelProviderORM).order_by(ModelProviderORM.id)).all()
        return [_provider_from_row(row) for row in rows]

    def _load_profiles(self) -> list[ModelProfile]:
        rows = self.session.scalars(select(ModelProfileORM).order_by(ModelProfileORM.id)).all()
        return [_profile_from_row(row) for row in rows]

    def _provider_by_id(self, provider_id: str) -> ModelProvider | None:
        row = self.session.get(ModelProviderORM, provider_id)
        return _provider_from_row(row) if row else None

    def _profile_by_id(self, profile_id: str) -> ModelProfile | None:
        row = self.session.get(ModelProfileORM, profile_id)
        return _profile_from_row(row) if row else None


def _provider_from_row(row: ModelProviderORM) -> ModelProvider:
    return ModelProvider(
        id=row.id,
        name=row.name,
        provider_type=row.provider_type,
        status=row.status,
        supports_local=row.supports_local,
        default_timeout=row.default_timeout,
        secret_reference_name=row.secret_reference_name,
        metadata=row.provider_metadata,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _profile_from_row(row: ModelProfileORM) -> ModelProfile:
    return ModelProfile(
        id=row.id,
        provider_id=row.provider_id,
        model_name=row.model_name,
        display_name=row.display_name,
        status=row.status,
        capabilities=set(row.capabilities),
        privacy_class=row.privacy_class,
        cost_class=row.cost_class,
        latency_class=row.latency_class,
        quality_score=row.quality_score,
        supports_structured_output=row.supports_structured_output,
        supports_tool_use=row.supports_tool_use,
        supports_large_context=row.supports_large_context,
        supports_code=row.supports_code,
        supports_reasoning=row.supports_reasoning,
        max_context_tokens=row.max_context_tokens,
        max_output_tokens=row.max_output_tokens,
        input_cost_per_million=row.input_cost_per_million,
        output_cost_per_million=row.output_cost_per_million,
        metadata=row.profile_metadata,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_structured_response(request: ModelRequest, provider_response: ProviderGeneration) -> dict[str, Any] | None:
    if request.structured_output_schema is None:
        return provider_response.structured_data
    if provider_response.structured_data is None:
        raise ModelGatewayError(ModelErrorCode.STRUCTURED_OUTPUT_INVALID, "Provider returned no structured data.")
    schema = request.structured_output_schema
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        validated = schema.model_validate(provider_response.structured_data)
        return validated.model_dump(mode="json")
    return provider_response.structured_data


def _sanitize_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in SECRET_KEY_MARKERS):
                continue
            clean[key] = _sanitize_metadata(item)
        return clean
    if isinstance(value, list):
        return [_sanitize_metadata(item) for item in value]
    return value
