from __future__ import annotations

from typing import Any

from lucius.domain.enums import ModelCostSource
from lucius.models.errors import ModelErrorCode, ModelProviderError
from lucius.models.providers.base import ProviderAdapter, ProviderGeneration
from lucius.models.schemas import ModelProfile, ModelRequest


class MockProviderAdapter(ProviderAdapter):
    def __init__(
        self,
        provider_id: str,
        *,
        available: bool = True,
        text: str = "mock-response",
        structured_data: dict[str, Any] | None = None,
        fail_with: ModelErrorCode | None = None,
        latency_ms: int = 7,
        input_tokens: int = 11,
        output_tokens: int = 13,
        estimated_cost: float | None = 0.0001,
        cost_source: ModelCostSource = ModelCostSource.ESTIMATED,
    ):
        super().__init__(provider_id)
        self.available = available
        self.text = text
        self.structured_data = structured_data
        self.fail_with = fail_with
        self.latency_ms = latency_ms
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.estimated_cost = estimated_cost
        self.cost_source = cost_source

    def validate_configuration(self) -> None:
        return None

    def is_available(self) -> bool:
        return self.available

    def generate(self, request: ModelRequest, profile: ModelProfile) -> ProviderGeneration:
        del profile
        if not self.available:
            raise ModelProviderError(ModelErrorCode.PROVIDER_UNAVAILABLE, "Mock provider unavailable.")
        if self.fail_with is not None:
            raise ModelProviderError(self.fail_with, f"Mock failure: {self.fail_with.value}.")
        structured_data = self.structured_data
        if request.structured_output_schema is not None and structured_data is None:
            structured_data = {"result": self.text}
        total_tokens = self.input_tokens + self.output_tokens
        return ProviderGeneration(
            content=self.text,
            structured_data=structured_data,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=total_tokens,
            estimated_cost=self.estimated_cost,
            cost_source=self.cost_source,
            latency_ms=self.latency_ms,
        )
