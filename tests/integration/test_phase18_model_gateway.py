from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from pydantic import BaseModel
from sqlalchemy import select

from lucius.audit.service import AuditService
from lucius.domain.enums import (
    CostClass,
    LatencyClass,
    ModelCapability,
    ModelCostSource,
    ModelProfileStatus,
    ModelResponseStatus,
    PrivacyClass,
    ProviderStatus,
    ProviderType,
)
from lucius.evidence.schemas import EvidenceReferenceCreate
from lucius.memory.schemas import MemoryMatch
from lucius.models.errors import ModelErrorCode
from lucius.models.gateway import ModelGateway
from lucius.models.providers.mock import MockProviderAdapter
from lucius.models.schemas import ModelMessage, ModelProfile, ModelProvider, ModelRequest, ModelResponse
from lucius.persistence.orm import AuditEventORM, ModelExecutionORM, ModelProfileORM, ModelProviderORM


class SummaryResult(BaseModel):
    result: str


def _request(
    request_id: str,
    *capabilities: ModelCapability,
    privacy: PrivacyClass = PrivacyClass.INTERNAL,
    max_cost: CostClass | None = None,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
    structured_schema=None,
    fallback_allowed: bool = True,
    fallback_model_ids: list[str] | None = None,
    max_attempts: int | None = None,
    minimum_quality: int = 0,
    metadata: dict | None = None,
) -> ModelRequest:
    return ModelRequest(
        request_id=request_id,
        purpose="deterministic test",
        required_capabilities=set(capabilities),
        privacy_class=privacy,
        max_cost_class=max_cost,
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        fallback_model_ids=fallback_model_ids or [],
        structured_output_schema=structured_schema,
        fallback_allowed=fallback_allowed,
        max_attempts=max_attempts,
        minimum_quality=minimum_quality,
        input_messages=[ModelMessage(role="user", content="Summarize repository behavior without secrets.")],
        metadata=metadata or {},
    )


def _register_provider(
    gateway: ModelGateway,
    *,
    name: str,
    provider_type: ProviderType = ProviderType.CUSTOM,
    status: ProviderStatus = ProviderStatus.ACTIVE,
    supports_local: bool = False,
    metadata: dict | None = None,
) -> ModelProviderORM:
    provider = gateway.register_provider(
        ModelProvider(
            name=name,
            provider_type=provider_type,
            status=status,
            supports_local=supports_local,
            default_timeout=30,
            secret_reference_name=f"{name}_SECRET_REF",
            metadata=metadata or {},
        )
    )
    gateway.register_provider_adapter(MockProviderAdapter(provider.id))
    return provider


def _register_profile(
    gateway: ModelGateway,
    provider_id: str,
    *,
    model_name: str,
    capabilities: set[ModelCapability],
    privacy: PrivacyClass = PrivacyClass.INTERNAL,
    cost: CostClass = CostClass.LOW,
    status: ModelProfileStatus = ModelProfileStatus.ACTIVE,
    quality: int = 20,
    latency: LatencyClass = LatencyClass.MEDIUM,
    max_context_tokens: int | None = 4096,
    input_cost: float | None = None,
    output_cost: float | None = None,
    metadata: dict | None = None,
) -> ModelProfileORM:
    return gateway.register_profile(
        ModelProfile(
            provider_id=provider_id,
            model_name=model_name,
            display_name=model_name.replace("-", " ").title(),
            status=status,
            capabilities=capabilities,
            privacy_class=privacy,
            cost_class=cost,
            latency_class=latency,
            quality_score=quality,
            supports_structured_output=ModelCapability.STRUCTURED_OUTPUT in capabilities,
            supports_tool_use=ModelCapability.TOOL_USE in capabilities,
            supports_large_context=ModelCapability.LONG_CONTEXT in capabilities,
            supports_code=ModelCapability.CODE_GENERATION in capabilities or ModelCapability.CODE_REASONING in capabilities,
            supports_reasoning=ModelCapability.HIGH_REASONING in capabilities or ModelCapability.CODE_REASONING in capabilities,
            max_context_tokens=max_context_tokens,
            max_output_tokens=2048,
            input_cost_per_million=input_cost,
            output_cost_per_million=output_cost,
            metadata=metadata or {},
        )
    )


def _benchmark_fixture(session):
    gateway = ModelGateway(session)
    local = _register_provider(gateway, name="local", provider_type=ProviderType.LOCAL, supports_local=True)
    cloud = _register_provider(gateway, name="cloud", provider_type=ProviderType.OPENAI)
    frontier = _register_provider(gateway, name="frontier", provider_type=ProviderType.CUSTOM)
    local_small = _register_profile(
        gateway,
        local.id,
        model_name="LOCAL_SMALL",
        capabilities={ModelCapability.CLASSIFICATION, ModelCapability.SUMMARIZATION},
        privacy=PrivacyClass.RESTRICTED,
        cost=CostClass.FREE,
        quality=15,
        latency=LatencyClass.LOW,
    )
    cloud_general = _register_profile(
        gateway,
        cloud.id,
        model_name="CLOUD_GENERAL",
        capabilities={ModelCapability.SUMMARIZATION, ModelCapability.STRUCTURED_OUTPUT, ModelCapability.PLANNING},
        privacy=PrivacyClass.INTERNAL,
        cost=CostClass.LOW,
        quality=35,
    )
    frontier_engineering = _register_profile(
        gateway,
        frontier.id,
        model_name="FRONTIER_ENGINEERING",
        capabilities={
            ModelCapability.PLANNING,
            ModelCapability.CODE_REASONING,
            ModelCapability.HIGH_REASONING,
            ModelCapability.STRUCTURED_OUTPUT,
        },
        privacy=PrivacyClass.INTERNAL,
        cost=CostClass.PREMIUM,
        quality=95,
        input_cost=10.0,
        output_cost=20.0,
    )
    return gateway, local_small, cloud_general, frontier_engineering


def test_provider_and_profile_registration_keeps_secrets_external(session):
    gateway = ModelGateway(session)
    provider = _register_provider(
        gateway,
        name="secure-local",
        provider_type=ProviderType.LOCAL,
        supports_local=True,
        metadata={"api_key": "must-not-persist", "nested": {"token": "must-not-persist", "region": "local"}},
    )
    profile = _register_profile(
        gateway,
        provider.id,
        model_name="local-classifier",
        capabilities={ModelCapability.CLASSIFICATION},
        privacy=PrivacyClass.RESTRICTED,
        metadata={"secret": "nope", "owner": "lucius"},
    )
    provider_row = session.get(ModelProviderORM, provider.id)
    profile_row = session.get(ModelProfileORM, profile.id)
    assert provider_row.provider_type == ProviderType.LOCAL.value
    assert provider_row.supports_local is True
    assert provider_row.provider_metadata == {"nested": {"region": "local"}}
    assert profile_row.profile_metadata == {"owner": "lucius"}
    assert profile_row.capabilities == [ModelCapability.CLASSIFICATION.value]


def test_routing_benchmark_fixture_selects_expected_models(session):
    gateway, local_small, cloud_general, frontier_engineering = _benchmark_fixture(session)
    assert gateway.route_request(_request("REQ_1", ModelCapability.CLASSIFICATION)).selected_profile_id == local_small.id
    assert (
        gateway.route_request(
            _request("REQ_2", ModelCapability.SUMMARIZATION, structured_schema=SummaryResult)
        ).selected_profile_id
        == cloud_general.id
    )
    assert (
        gateway.route_request(
            _request("REQ_3", ModelCapability.PLANNING, ModelCapability.CODE_REASONING, ModelCapability.HIGH_REASONING)
        ).selected_profile_id
        == frontier_engineering.id
    )
    assert (
        gateway.route_request(
            _request("REQ_4", ModelCapability.CLASSIFICATION, privacy=PrivacyClass.RESTRICTED)
        ).selected_profile_id
        == local_small.id
    )
    blocked = gateway.route_request(
        _request("REQ_5", ModelCapability.PLANNING, privacy=PrivacyClass.RESTRICTED)
    )
    assert blocked.error_code == ModelErrorCode.PRIVACY_POLICY_BLOCK


def test_status_capability_cost_context_preference_and_tie_breaking(session):
    gateway = ModelGateway(session)
    paused_provider = _register_provider(gateway, name="paused", status=ProviderStatus.PAUSED)
    active_provider = _register_provider(gateway, name="active")
    _register_profile(
        gateway,
        paused_provider.id,
        model_name="paused-provider-model",
        capabilities={ModelCapability.CLASSIFICATION},
        cost=CostClass.FREE,
    )
    disabled = _register_profile(
        gateway,
        active_provider.id,
        model_name="disabled-model",
        capabilities={ModelCapability.CLASSIFICATION},
        status=ModelProfileStatus.DISABLED,
        cost=CostClass.FREE,
    )
    cheap = _register_profile(
        gateway,
        active_provider.id,
        model_name="cheap-model",
        capabilities={ModelCapability.CLASSIFICATION},
        cost=CostClass.LOW,
        max_context_tokens=100,
    )
    premium = _register_profile(
        gateway,
        active_provider.id,
        model_name="premium-model",
        capabilities={ModelCapability.CLASSIFICATION},
        cost=CostClass.PREMIUM,
        max_context_tokens=10_000,
    )
    assert gateway.route_request(_request("REQ_STATUS", ModelCapability.CLASSIFICATION)).selected_profile_id == cheap.id
    assert disabled.id not in gateway.route_request(_request("REQ_INACTIVE", ModelCapability.CLASSIFICATION)).fallback_profile_ids
    assert (
        gateway.route_request(_request("REQ_PREF", ModelCapability.CLASSIFICATION, preferred_model=premium.model_name)).selected_profile_id
        == premium.id
    )
    assert (
        gateway.route_request(_request("REQ_BAD_PREF", ModelCapability.CLASSIFICATION, preferred_model="missing")).selected_profile_id
        == cheap.id
    )
    assert (
        gateway.route_request(_request("REQ_COST", ModelCapability.CLASSIFICATION, max_cost=CostClass.LOW)).selected_profile_id
        == cheap.id
    )
    blocked_cost = gateway.route_request(_request("REQ_COST_BLOCK", ModelCapability.CLASSIFICATION, max_cost=CostClass.FREE))
    assert blocked_cost.error_code == ModelErrorCode.COST_POLICY_BLOCK
    blocked_context = gateway.route_request(
        _request("REQ_CONTEXT", ModelCapability.CLASSIFICATION, metadata={"context_tokens": 50_000})
    )
    assert blocked_context.error_code == ModelErrorCode.CONTEXT_LIMIT_EXCEEDED


def test_deterministic_tie_breaking_and_unavailable_capability(session):
    gateway = ModelGateway(session)
    provider = _register_provider(gateway, name="tie-provider")
    first = _register_profile(
        gateway,
        provider.id,
        model_name="aaa",
        capabilities={ModelCapability.CLASSIFICATION},
        cost=CostClass.LOW,
    )
    _register_profile(
        gateway,
        provider.id,
        model_name="bbb",
        capabilities={ModelCapability.CLASSIFICATION},
        cost=CostClass.LOW,
    )
    assert gateway.route_request(_request("REQ_TIE", ModelCapability.CLASSIFICATION)).selected_profile_id == first.id
    no_match = gateway.route_request(_request("REQ_NO_CAP", ModelCapability.TOOL_USE))
    assert no_match.error_code == ModelErrorCode.CAPABILITY_UNAVAILABLE


def test_gateway_execution_records_usage_latency_cost_and_audit(session):
    gateway = ModelGateway(session)
    provider = _register_provider(gateway, name="mock")
    profile = _register_profile(
        gateway,
        provider.id,
        model_name="mock-general",
        capabilities={ModelCapability.SUMMARIZATION},
    )
    gateway.register_provider_adapter(
        MockProviderAdapter(
            provider.id,
            text="stable summary",
            latency_ms=23,
            input_tokens=101,
            output_tokens=17,
            estimated_cost=0.002,
            cost_source=ModelCostSource.REPORTED,
        )
    )
    response = gateway.execute(_request("REQ_EXEC", ModelCapability.SUMMARIZATION))
    execution = session.get(ModelExecutionORM, response.execution_id)
    assert response.status == ModelResponseStatus.SUCCEEDED
    assert response.content == "stable summary"
    assert response.model_profile_id == profile.id
    assert execution.input_tokens == 101
    assert execution.output_tokens == 17
    assert execution.latency_ms == 23
    assert execution.estimated_cost == 0.002
    assert execution.cost_source == ModelCostSource.REPORTED.value
    events = session.scalars(select(AuditEventORM).where(AuditEventORM.event_type == "MODEL_EXECUTION_SUCCEEDED")).all()
    assert events[-1].event_metadata["execution_id"] == response.execution_id


def test_provider_failures_timeout_and_transient_fallback_preserve_provenance(session):
    gateway = ModelGateway(session)
    first_provider = _register_provider(gateway, name="failing")
    second_provider = _register_provider(gateway, name="fallback")
    first = _register_profile(
        gateway,
        first_provider.id,
        model_name="failing-summary",
        capabilities={ModelCapability.SUMMARIZATION},
        cost=CostClass.FREE,
    )
    second = _register_profile(
        gateway,
        second_provider.id,
        model_name="fallback-summary",
        capabilities={ModelCapability.SUMMARIZATION},
        cost=CostClass.LOW,
    )
    gateway.register_provider_adapter(MockProviderAdapter(first_provider.id, fail_with=ModelErrorCode.TIMEOUT))
    gateway.register_provider_adapter(MockProviderAdapter(second_provider.id, text="fallback ok"))
    response = gateway.execute(_request("REQ_FALLBACK", ModelCapability.SUMMARIZATION))
    executions = session.scalars(
        select(ModelExecutionORM).where(ModelExecutionORM.request_id == "REQ_FALLBACK").order_by(ModelExecutionORM.attempt_number)
    ).all()
    assert response.status == ModelResponseStatus.SUCCEEDED
    assert response.model_profile_id == second.id
    assert response.fallback_used is True
    assert executions[0].model_profile_id == first.id
    assert executions[0].error_code == ModelErrorCode.TIMEOUT.value
    assert executions[1].model_profile_id == second.id
    assert executions[1].fallback_used is True


def test_explicit_fallback_chain_is_honored_after_policy_filtering(session):
    gateway = ModelGateway(session)
    first_provider = _register_provider(gateway, name="explicit-a")
    second_provider = _register_provider(gateway, name="explicit-b")
    first = _register_profile(
        gateway,
        first_provider.id,
        model_name="explicit-a",
        capabilities={ModelCapability.SUMMARIZATION},
        cost=CostClass.LOW,
    )
    second = _register_profile(
        gateway,
        second_provider.id,
        model_name="explicit-b",
        capabilities={ModelCapability.SUMMARIZATION},
        cost=CostClass.PREMIUM,
    )
    gateway.register_provider_adapter(MockProviderAdapter(first_provider.id, fail_with=ModelErrorCode.TRANSIENT_ERROR))
    gateway.register_provider_adapter(MockProviderAdapter(second_provider.id, text="explicit fallback"))
    response = gateway.execute(
        _request(
            "REQ_EXPLICIT_CHAIN",
            ModelCapability.SUMMARIZATION,
            fallback_model_ids=[first.id, second.id],
        )
    )
    assert response.model_profile_id == second.id
    assert response.content == "explicit fallback"


def test_privacy_block_does_not_unsafe_fallback_and_policy_audit_has_no_prompt(session):
    gateway, _local_small, _cloud_general, _frontier = _benchmark_fixture(session)
    request = _request(
        "REQ_PRIVACY",
        ModelCapability.PLANNING,
        privacy=PrivacyClass.RESTRICTED,
        metadata={"note": "metadata ok"},
    )
    request.input_messages = [ModelMessage(role="user", content="client secret prompt must never be audited")]
    response = gateway.execute(request)
    executions = session.scalars(select(ModelExecutionORM).where(ModelExecutionORM.request_id == "REQ_PRIVACY")).all()
    audit_events = session.scalars(select(AuditEventORM)).all()
    audit_json = json.dumps([event.event_metadata for event in audit_events])
    assert response.status == ModelResponseStatus.FAILED
    assert response.error_code == ModelErrorCode.PRIVACY_POLICY_BLOCK
    assert len(executions) == 1
    assert "client secret prompt" not in audit_json
    assert any(event.event_type == "MODEL_POLICY_BLOCK" for event in audit_events)


def test_max_attempts_enforced(session):
    gateway = ModelGateway(session)
    provider_a = _register_provider(gateway, name="attempt-a")
    provider_b = _register_provider(gateway, name="attempt-b")
    _register_profile(gateway, provider_a.id, model_name="attempt-a", capabilities={ModelCapability.SUMMARIZATION}, cost=CostClass.FREE)
    _register_profile(gateway, provider_b.id, model_name="attempt-b", capabilities={ModelCapability.SUMMARIZATION}, cost=CostClass.LOW)
    gateway.register_provider_adapter(MockProviderAdapter(provider_a.id, fail_with=ModelErrorCode.PROVIDER_UNAVAILABLE))
    gateway.register_provider_adapter(MockProviderAdapter(provider_b.id, text="should not run"))
    response = gateway.execute(_request("REQ_ATTEMPTS", ModelCapability.SUMMARIZATION, max_attempts=1))
    executions = session.scalars(select(ModelExecutionORM).where(ModelExecutionORM.request_id == "REQ_ATTEMPTS")).all()
    assert response.error_code == ModelErrorCode.PROVIDER_UNAVAILABLE
    assert len(executions) == 1


def test_structured_output_valid_invalid_and_fallback(session):
    gateway = ModelGateway(session)
    bad_provider = _register_provider(gateway, name="bad-structured")
    good_provider = _register_provider(gateway, name="good-structured")
    _register_profile(
        gateway,
        bad_provider.id,
        model_name="bad-structured",
        capabilities={ModelCapability.SUMMARIZATION, ModelCapability.STRUCTURED_OUTPUT},
        cost=CostClass.FREE,
    )
    good = _register_profile(
        gateway,
        good_provider.id,
        model_name="good-structured",
        capabilities={ModelCapability.SUMMARIZATION, ModelCapability.STRUCTURED_OUTPUT},
        cost=CostClass.LOW,
    )
    gateway.register_provider_adapter(MockProviderAdapter(bad_provider.id, structured_data={"result": 123}))
    gateway.register_provider_adapter(MockProviderAdapter(good_provider.id, structured_data={"result": "ok"}))
    response = gateway.execute(_request("REQ_STRUCT", ModelCapability.SUMMARIZATION, structured_schema=SummaryResult))
    assert response.status == ModelResponseStatus.SUCCEEDED
    assert response.model_profile_id == good.id
    assert response.structured_data == {"result": "ok"}
    invalid = session.scalars(
        select(AuditEventORM).where(AuditEventORM.event_type == "MODEL_RESPONSE_INVALID")
    ).all()
    assert invalid[-1].event_metadata["error_code"] == ModelErrorCode.STRUCTURED_OUTPUT_INVALID.value


def test_model_response_epistemology_is_separate_from_evidence_and_memory(session):
    assert not issubclass(ModelResponse, EvidenceReferenceCreate)
    assert not issubclass(ModelResponse, MemoryMatch)
    response_fields = set(ModelResponse.model_fields)
    assert "content_hash" not in response_fields
    assert "validation_status" not in response_fields


def test_multiple_provider_adapters_and_provider_independent_api(session):
    gateway = ModelGateway(session)
    local = _register_provider(gateway, name="multi-local", provider_type=ProviderType.LOCAL, supports_local=True)
    cloud = _register_provider(gateway, name="multi-cloud", provider_type=ProviderType.GOOGLE)
    _register_profile(gateway, local.id, model_name="local", capabilities={ModelCapability.CLASSIFICATION}, privacy=PrivacyClass.RESTRICTED)
    _register_profile(gateway, cloud.id, model_name="cloud", capabilities={ModelCapability.SUMMARIZATION}, privacy=PrivacyClass.INTERNAL)
    gateway.register_provider_adapter(MockProviderAdapter(local.id, text="local"))
    gateway.register_provider_adapter(MockProviderAdapter(cloud.id, text="cloud"))
    local_response = gateway.execute(_request("REQ_MULTI_1", ModelCapability.CLASSIFICATION, privacy=PrivacyClass.RESTRICTED))
    cloud_response = gateway.execute(_request("REQ_MULTI_2", ModelCapability.SUMMARIZATION))
    assert local_response.content == "local"
    assert cloud_response.content == "cloud"
    assert not hasattr(cloud_response, "openai_response")
    assert not hasattr(cloud_response, "google_response")


def test_alembic_0004_to_head_and_clean_db_to_head(tmp_path):
    root = Path(__file__).resolve().parents[2]

    def config_for(path: Path) -> Config:
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path}")
        return config

    upgrade_db = tmp_path / "from_0004.sqlite"
    command.upgrade(config_for(upgrade_db), "0004_memory_learning")
    command.upgrade(config_for(upgrade_db), "head")

    clean_db = tmp_path / "clean.sqlite"
    command.upgrade(config_for(clean_db), "head")
