from __future__ import annotations

from lucius.persistence.orm import ModelExecutionORM, utc_now
from lucius.persistence.repositories import next_id


def create_execution_id(session) -> str:
    return next_id(session, "model_execution")


def execution_from_response(*, execution_id: str, request, response, routing_decision) -> ModelExecutionORM:
    return ModelExecutionORM(
        id=execution_id,
        request_id=request.request_id,
        project_id=request.project_id,
        task_id=request.task_id,
        run_id=request.run_id,
        provider_id=response.provider_id,
        provider=response.provider_id,
        model_profile_id=response.model_profile_id,
        model=response.model_name,
        status=response.status.value,
        capabilities=[capability.value for capability in sorted(request.required_capabilities, key=lambda item: item.value)],
        privacy_class=request.privacy_class.value,
        routing_decision=routing_decision.model_dump(mode="json") if routing_decision is not None else {},
        latency_ms=response.latency_ms,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        total_tokens=response.total_tokens,
        estimated_cost=response.estimated_cost,
        cost_source=response.cost_source.value,
        fallback_used=response.fallback_used,
        attempt_number=response.attempt_number,
        error_code=response.error_code.value if response.error_code else None,
        created_at=utc_now(),
    )
