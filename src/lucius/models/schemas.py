from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
from lucius.models.errors import ModelErrorCode
from lucius.persistence.orm import utc_now


class ModelProvider(BaseModel):
    id: str | None = None
    name: str
    provider_type: ProviderType
    status: ProviderStatus = ProviderStatus.ACTIVE
    supports_local: bool = False
    default_timeout: int | None = None
    secret_reference_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ModelProfile(BaseModel):
    id: str | None = None
    provider_id: str
    model_name: str
    display_name: str
    status: ModelProfileStatus = ModelProfileStatus.ACTIVE
    capabilities: set[ModelCapability] = Field(default_factory=set)
    privacy_class: PrivacyClass = PrivacyClass.PUBLIC
    cost_class: CostClass = CostClass.LOW
    latency_class: LatencyClass = LatencyClass.MEDIUM
    quality_score: int = 0
    supports_structured_output: bool = False
    supports_tool_use: bool = False
    supports_large_context: bool = False
    supports_code: bool = False
    supports_reasoning: bool = False
    max_context_tokens: int | None = None
    max_output_tokens: int | None = None
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("quality_score")
    @classmethod
    def validate_quality_score(cls, value: int) -> int:
        if not 0 <= value <= 100:
            raise ValueError("quality_score must be between 0 and 100")
        return value


class ModelMessage(BaseModel):
    role: str
    content: str


class ModelRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    request_id: str
    task_id: str | None = None
    run_id: str | None = None
    project_id: str | None = None
    purpose: str
    required_capabilities: set[ModelCapability] = Field(default_factory=set)
    privacy_class: PrivacyClass = PrivacyClass.INTERNAL
    minimum_quality: int = 0
    max_cost_class: CostClass | None = None
    max_estimated_cost: float | None = None
    max_attempts: int | None = None
    preferred_provider: str | None = None
    preferred_model: str | None = None
    fallback_model_ids: list[str] = Field(default_factory=list)
    input_messages: list[ModelMessage] = Field(default_factory=list)
    structured_output_schema: Any | None = None
    max_output_tokens: int | None = None
    timeout: int | None = None
    fallback_allowed: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("minimum_quality")
    @classmethod
    def validate_minimum_quality(cls, value: int) -> int:
        if not 0 <= value <= 100:
            raise ValueError("minimum_quality must be between 0 and 100")
        return value


class RoutingRejection(BaseModel):
    provider_id: str | None = None
    model_profile_id: str | None = None
    reason: ModelErrorCode
    message: str


class RoutingDecision(BaseModel):
    request_id: str
    selected_profile_id: str | None = None
    fallback_profile_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    rejected: list[RoutingRejection] = Field(default_factory=list)
    error_code: ModelErrorCode | None = None


class ModelResponse(BaseModel):
    request_id: str
    execution_id: str | None = None
    provider_id: str | None = None
    model_profile_id: str | None = None
    model_name: str | None = None
    status: ModelResponseStatus
    content: str | None = None
    structured_data: dict[str, Any] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    cost_source: ModelCostSource = ModelCostSource.UNKNOWN
    latency_ms: int | None = None
    finish_reason: str | None = None
    error_code: ModelErrorCode | None = None
    error_message: str | None = None
    fallback_used: bool = False
    attempt_number: int = 1
    routing_decision: RoutingDecision | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ModelInferenceProvenance(BaseModel):
    provider_id: str
    model_profile_id: str
    model_name: str
    request_id: str
    execution_id: str
    timestamp: datetime
    routing_decision: RoutingDecision
