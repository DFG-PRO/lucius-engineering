from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from lucius.domain.enums import ModelCostSource
from lucius.models.schemas import ModelProfile, ModelRequest, ModelResponse


@dataclass(frozen=True)
class ProviderGeneration:
    content: str | None = None
    structured_data: dict[str, Any] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    cost_source: ModelCostSource = ModelCostSource.UNKNOWN
    latency_ms: int | None = None
    finish_reason: str | None = "stop"
    metadata: dict[str, Any] = field(default_factory=dict)


class ProviderAdapter(ABC):
    provider_id: str

    def __init__(self, provider_id: str):
        self.provider_id = provider_id

    @abstractmethod
    def validate_configuration(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def generate(self, request: ModelRequest, profile: ModelProfile) -> ProviderGeneration:
        raise NotImplementedError

    def supports(self, profile: ModelProfile) -> bool:
        return profile.provider_id == self.provider_id

    def estimate_cost(self, request: ModelRequest, profile: ModelProfile) -> float | None:
        del request
        if profile.input_cost_per_million is None and profile.output_cost_per_million is None:
            return None
        return 0.0

    def normalize_response(self, response: ModelResponse) -> ModelResponse:
        return response
