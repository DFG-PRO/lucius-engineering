from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, AuthorityLevel
from lucius.persistence.orm import AuditEventORM
from lucius.persistence.repositories import next_id
from lucius.runtime.schemas import (
    ExecutionAdapterResult,
    RuntimeExecutionContext,
    RuntimeExecutionOutcome,
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    RuntimeProviderCandidateEvaluation,
    RuntimeProviderRegistration,
    RuntimeProviderStatus,
    RuntimeRetryability,
    RuntimeRoutingDecision,
)


class RuntimeExecutionProvider(Protocol):
    registration: RuntimeProviderRegistration

    def is_available(self) -> bool:
        ...

    def invoke(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        ...


class RuntimeProviderRegistry:
    """Deterministic registry for runtime execution providers."""

    def __init__(self, providers: list[RuntimeExecutionProvider] | None = None):
        self._providers: dict[str, RuntimeExecutionProvider] = {}
        for provider in providers or []:
            self.register(provider)

    def register(self, provider: RuntimeExecutionProvider) -> None:
        provider_id = provider.registration.provider_id
        if provider_id in self._providers:
            raise ValueError(f"Duplicate runtime execution provider: {provider_id}")
        self._providers[provider_id] = provider

    def providers(self) -> list[RuntimeExecutionProvider]:
        return [self._providers[key] for key in sorted(self._providers)]


class ModelExecutionRouter:
    """Routes authorized runtime execution requests to provider workers."""

    router_id = "model-execution-router"

    def __init__(
        self,
        session: Session,
        *,
        registry: RuntimeProviderRegistry,
        actor: Actor = Actor.LUCIUS,
        max_provider_retries: int = 0,
        max_failovers: int = 0,
    ):
        self.session = session
        self.registry = registry
        self.actor = actor
        self.max_provider_retries = max(0, max_provider_retries)
        self.max_failovers = max(0, max_failovers)
        self.audit = AuditService(session)

    def execute(self, context: RuntimeExecutionContext) -> ExecutionAdapterResult:
        request = self._request_from_context(context)
        self.audit.record(
            event_type="MODEL_EXECUTION_ROUTING_REQUEST_ACCEPTED",
            actor=self.actor.value,
            project_id=context.project_id,
            repository_id=context.repository_id,
            task_id=context.workflow_task_id,
            action="accept_runtime_execution_request",
            result="ACCEPTED",
            authority_level=AuthorityLevel.L1,
            metadata=request.model_dump(mode="json"),
        )
        decision, provider_by_id = self._route(request, context)
        if decision.selected_provider_id is None:
            self.audit.record(
                event_type="MODEL_EXECUTION_NO_ELIGIBLE_PROVIDER",
                actor=self.actor.value,
                project_id=context.project_id,
                repository_id=context.repository_id,
                task_id=context.workflow_task_id,
                action="route_runtime_execution_request",
                result="FAILED_CLOSED",
                authority_level=AuthorityLevel.L1,
                metadata=decision.model_dump(mode="json"),
            )
            self.audit.record(
                event_type="MODEL_EXECUTION_ROUTING_COMPLETED",
                actor=self.actor.value,
                project_id=context.project_id,
                repository_id=context.repository_id,
                task_id=context.workflow_task_id,
                action="route_runtime_execution_request",
                result="FAILED",
                authority_level=AuthorityLevel.L1,
                metadata=decision.model_dump(mode="json"),
            )
            return ExecutionAdapterResult(
                outcome="FAILED",
                execution_id=request.execution_id,
                routing_decision_id=decision.routing_decision_id,
                blocker_category="NO_ELIGIBLE_PROVIDER",
                blocking_reason=decision.no_eligible_reason or "No eligible runtime execution provider.",
                resume_condition="Register or restore an eligible execution provider, then rerun canonical readiness/release.",
                error=decision.no_eligible_reason,
                retryability=RuntimeRetryability.NON_RETRYABLE,
            )

        remaining_provider_ids = [decision.selected_provider_id, *decision.fallback_provider_ids]
        failovers_used = 0
        previous_provider_id: str | None = None
        final_result: RuntimeExecutionResult | None = None
        final_retry_index = 0
        for provider_id in remaining_provider_ids:
            provider = provider_by_id[provider_id]
            request = request.model_copy(update={"routing_decision_id": decision.routing_decision_id})
            attempts = self.max_provider_retries + 1
            for attempt_index in range(attempts):
                if attempt_index:
                    self.audit.record(
                        event_type="MODEL_EXECUTION_PROVIDER_RETRY_ATTEMPTED",
                        actor=self.actor.value,
                        project_id=context.project_id,
                        repository_id=context.repository_id,
                        task_id=context.workflow_task_id,
                        action="retry_runtime_execution_provider",
                        result=provider_id,
                        authority_level=AuthorityLevel.L1,
                        metadata={
                            "execution_id": request.execution_id,
                            "routing_decision_id": decision.routing_decision_id,
                            "provider_id": provider_id,
                            "attempt_number": attempt_index + 1,
                        },
                    )
                self.audit.record(
                    event_type="MODEL_EXECUTION_PROVIDER_INVOCATION_STARTED",
                    actor=self.actor.value,
                    project_id=context.project_id,
                    repository_id=context.repository_id,
                    task_id=context.workflow_task_id,
                    action="invoke_runtime_execution_provider",
                    result=provider_id,
                    authority_level=AuthorityLevel.L1,
                    metadata={
                        "execution_id": request.execution_id,
                        "routing_decision_id": decision.routing_decision_id,
                        "provider_id": provider_id,
                        "attempt_number": attempt_index + 1,
                    },
                )
                provider_result = self._invoke_provider(provider, request)
                provider_result = provider_result.model_copy(
                    update={
                        "routing_decision_id": decision.routing_decision_id,
                        "fallback_used": previous_provider_id is not None,
                        "failover_from_provider_id": previous_provider_id,
                    }
                )
                provider_result = _enforce_provider_result_contract(provider_result)
                self.audit.record(
                    event_type="MODEL_EXECUTION_PROVIDER_INVOCATION_COMPLETED",
                    actor=self.actor.value,
                    project_id=context.project_id,
                    repository_id=context.repository_id,
                    task_id=context.workflow_task_id,
                    action="invoke_runtime_execution_provider",
                    result=provider_result.status.value,
                    authority_level=AuthorityLevel.L1,
                    metadata=_provider_result_audit_payload(provider_result, attempt_index + 1),
                )
                final_result = provider_result
                final_retry_index = attempt_index
                if provider_result.status.value != "FAILED":
                    return self._complete_routing(context, request, decision, provider_result, attempt_index, failovers_used)
                if attempt_index < self.max_provider_retries and _retryable(provider_result.retryability):
                    continue
                break

            if (
                final_result is not None
                and _failoverable(final_result.retryability)
                and failovers_used < self.max_failovers
            ):
                next_provider_id = _next_provider_id(remaining_provider_ids, provider_id)
                if next_provider_id is not None:
                    failovers_used += 1
                    previous_provider_id = provider_id
                    self.audit.record(
                        event_type="MODEL_EXECUTION_PROVIDER_FAILOVER_ATTEMPTED",
                        actor=self.actor.value,
                        project_id=context.project_id,
                        repository_id=context.repository_id,
                        task_id=context.workflow_task_id,
                        action="failover_runtime_execution_provider",
                        result=next_provider_id,
                        authority_level=AuthorityLevel.L1,
                        metadata={
                            "execution_id": request.execution_id,
                            "routing_decision_id": decision.routing_decision_id,
                            "from_provider_id": provider_id,
                            "to_provider_id": next_provider_id,
                            "failovers_used": failovers_used,
                        },
                    )
                    continue
            break

        if final_result is None:
            final_result = RuntimeExecutionResult(
                execution_id=request.execution_id,
                provider_id=decision.selected_provider_id,
                provider_version=provider_by_id[decision.selected_provider_id].registration.provider_version,
                routing_decision_id=decision.routing_decision_id,
                status="FAILED",
                retryability=RuntimeRetryability.NON_RETRYABLE,
                failure_class="ROUTER_NO_RESULT",
                provider_error_metadata={"reason": "Router completed without provider result."},
            )
        return self._complete_routing(context, request, decision, final_result, final_retry_index, failovers_used)

    def _request_from_context(self, context: RuntimeExecutionContext) -> RuntimeExecutionRequest:
        if not context.workflow_task_id:
            raise ValueError("Runtime execution context must include a bound task id.")
        if not context.plan_id or not context.plan_freeze_id:
            raise ValueError("Runtime execution context must include frozen plan references.")
        execution_id = next_id(self.session, "model_execution")
        queue_item = context.queue_item or {}
        return RuntimeExecutionRequest(
            execution_id=execution_id,
            task_id=context.workflow_task_id,
            workflow_id=context.workflow_id,
            item_id=context.item_id,
            logical_task_id=context.logical_task_id,
            plan_id=context.plan_id,
            plan_freeze_id=context.plan_freeze_id,
            project_id=context.project_id,
            repository_id=context.repository_id,
            isolated_workspace=context.worktree_path,
            task_intent=context.title or context.workflow_objective,
            allowed_mutation_scope=context.authority_tier,
            required_capabilities=[str(item) for item in queue_item.get("required_capabilities", ["code_modification"])],
            task_type=str(queue_item.get("task_type", "engineering")),
            tool_requirements=[str(item) for item in queue_item.get("tool_requirements", [])],
            isolation_mode=str(queue_item.get("isolation_mode", "ISOLATED_WORKTREE")),
            context_limits=dict(queue_item.get("context_limits", {})),
            timeout_seconds=queue_item.get("timeout_seconds"),
            budget_policy=dict(queue_item.get("budget_policy", {})),
            release_evidence_refs=_release_evidence_refs(self.session, context),
            metadata={
                "queue_item_version": queue_item.get("version"),
                "queue_item_priority": queue_item.get("priority"),
            },
        )

    def _route(
        self,
        request: RuntimeExecutionRequest,
        context: RuntimeExecutionContext,
    ) -> tuple[RuntimeRoutingDecision, dict[str, RuntimeExecutionProvider]]:
        provider_by_id = {provider.registration.provider_id: provider for provider in self.registry.providers()}
        evaluations = [
            _evaluate_provider(request, provider)
            for provider in self.registry.providers()
        ]
        self.audit.record(
            event_type="MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED",
            actor=self.actor.value,
            project_id=context.project_id,
            repository_id=context.repository_id,
            task_id=context.workflow_task_id,
            action="evaluate_runtime_execution_providers",
            result="SUCCESS",
            authority_level=AuthorityLevel.L1,
            metadata={
                "execution_id": request.execution_id,
                "candidates": [item.model_dump(mode="json") for item in evaluations],
            },
        )
        eligible = [item for item in evaluations if item.eligible]
        if not eligible:
            decision = RuntimeRoutingDecision(
                routing_decision_id="PENDING",
                execution_id=request.execution_id,
                candidates=evaluations,
                no_eligible_reason="No registered runtime execution provider satisfied the request.",
            )
        else:
            ordered = sorted(
                eligible,
                key=lambda item: _provider_selection_key(provider_by_id[item.provider_id].registration),
            )
            selected = ordered[0]
            decision = RuntimeRoutingDecision(
                routing_decision_id="PENDING",
                execution_id=request.execution_id,
                selected_provider_id=selected.provider_id,
                selected_model_id=selected.selected_model_id,
                fallback_provider_ids=[item.provider_id for item in ordered[1:]],
                candidates=evaluations,
                policy_reasons=selected.reasons,
            )
        audit = self.audit.record(
            event_type="MODEL_EXECUTION_ROUTING_DECISION",
            actor=self.actor.value,
            project_id=context.project_id,
            repository_id=context.repository_id,
            task_id=context.workflow_task_id,
            action="select_runtime_execution_provider",
            result=decision.selected_provider_id or "NO_ELIGIBLE_PROVIDER",
            authority_level=AuthorityLevel.L1,
            metadata=decision.model_dump(mode="json"),
        )
        return decision.model_copy(update={"routing_decision_id": audit.id}), provider_by_id

    def _invoke_provider(
        self,
        provider: RuntimeExecutionProvider,
        request: RuntimeExecutionRequest,
    ) -> RuntimeExecutionResult:
        try:
            return provider.invoke(request)
        except Exception as error:
            registration = provider.registration
            return RuntimeExecutionResult(
                execution_id=request.execution_id,
                provider_id=registration.provider_id,
                provider_version=registration.provider_version,
                model_id=_selected_model_id(registration, request),
                routing_decision_id=request.routing_decision_id,
                status="FAILED",
                provider_native_status="EXCEPTION",
                retryability=RuntimeRetryability.RETRYABLE_OR_FAILOVERABLE
                if registration.retry_eligible or registration.failover_eligible
                else RuntimeRetryability.NON_RETRYABLE,
                failure_class="PROVIDER_EXCEPTION",
                provider_error_metadata={"error_type": error.__class__.__name__, "message": str(error)},
            )

    def _complete_routing(
        self,
        context: RuntimeExecutionContext,
        request: RuntimeExecutionRequest,
        decision: RuntimeRoutingDecision,
        provider_result: RuntimeExecutionResult,
        retry_index: int,
        failovers_used: int,
    ) -> ExecutionAdapterResult:
        normalized = _adapter_result_from_provider(provider_result, retry_index, failovers_used)
        self.audit.record(
            event_type="MODEL_EXECUTION_PROVIDER_RESULT_NORMALIZED",
            actor=self.actor.value,
            project_id=context.project_id,
            repository_id=context.repository_id,
            task_id=context.workflow_task_id,
            action="normalize_runtime_execution_result",
            result=normalized.outcome.value,
            authority_level=AuthorityLevel.L1,
            metadata={
                "execution_id": request.execution_id,
                "routing_decision_id": decision.routing_decision_id,
                "provider_result": _provider_result_audit_payload(provider_result, retry_index + 1),
            },
        )
        self.audit.record(
            event_type="MODEL_EXECUTION_ROUTING_COMPLETED",
            actor=self.actor.value,
            project_id=context.project_id,
            repository_id=context.repository_id,
            task_id=context.workflow_task_id,
            action="route_runtime_execution_request",
            result=normalized.outcome.value,
            authority_level=AuthorityLevel.L1,
            metadata={
                "execution_id": request.execution_id,
                "routing_decision_id": decision.routing_decision_id,
                "selected_provider_id": provider_result.provider_id,
                "model_id": provider_result.model_id,
                "retries": normalized.retries,
                "failovers": failovers_used,
                "fallback_used": normalized.fallback_used,
            },
        )
        return normalized


def _evaluate_provider(
    request: RuntimeExecutionRequest,
    provider: RuntimeExecutionProvider,
) -> RuntimeProviderCandidateEvaluation:
    registration = provider.registration
    reasons: list[str] = []
    if registration.status != RuntimeProviderStatus.ACTIVE:
        return RuntimeProviderCandidateEvaluation(provider_id=registration.provider_id, eligible=False, reasons=[f"provider status {registration.status.value}"])
    if not registration.available or not provider.is_available():
        return RuntimeProviderCandidateEvaluation(provider_id=registration.provider_id, eligible=False, reasons=["provider unavailable"])
    missing_capabilities = sorted(set(request.required_capabilities) - set(registration.capabilities))
    if missing_capabilities:
        return RuntimeProviderCandidateEvaluation(provider_id=registration.provider_id, eligible=False, reasons=[f"missing capabilities: {missing_capabilities}"])
    if request.task_type and registration.supported_task_classes and request.task_type not in set(registration.supported_task_classes):
        return RuntimeProviderCandidateEvaluation(provider_id=registration.provider_id, eligible=False, reasons=[f"task type {request.task_type} unsupported"])
    if request.project_id and registration.allowed_project_ids and request.project_id not in set(registration.allowed_project_ids):
        return RuntimeProviderCandidateEvaluation(provider_id=registration.provider_id, eligible=False, reasons=["project not allowed"])
    if request.repository_id and registration.allowed_repository_ids and request.repository_id not in set(registration.allowed_repository_ids):
        return RuntimeProviderCandidateEvaluation(provider_id=registration.provider_id, eligible=False, reasons=["repository not allowed"])
    if request.isolation_mode and registration.supported_isolation_modes and request.isolation_mode not in set(registration.supported_isolation_modes):
        return RuntimeProviderCandidateEvaluation(provider_id=registration.provider_id, eligible=False, reasons=["isolation mode unsupported"])
    missing_tools = sorted(set(request.tool_requirements) - set(registration.supported_tools))
    if missing_tools:
        return RuntimeProviderCandidateEvaluation(provider_id=registration.provider_id, eligible=False, reasons=[f"missing tools: {missing_tools}"])
    requested_context = request.context_limits.get("max_context_tokens")
    if (
        isinstance(requested_context, int)
        and registration.max_context_tokens is not None
        and requested_context > registration.max_context_tokens
    ):
        return RuntimeProviderCandidateEvaluation(provider_id=registration.provider_id, eligible=False, reasons=["context limit exceeded"])
    reasons.append("eligible: capability, policy, repository, isolation, tool, and context requirements satisfied")
    return RuntimeProviderCandidateEvaluation(
        provider_id=registration.provider_id,
        eligible=True,
        reasons=reasons,
        selected_model_id=_selected_model_id(registration, request),
    )


def _provider_selection_key(registration: RuntimeProviderRegistration) -> tuple[int, str]:
    return (-registration.reliability_score, registration.provider_id)


def _selected_model_id(registration: RuntimeProviderRegistration, request: RuntimeExecutionRequest) -> str | None:
    if not registration.models:
        return None
    capable = [
        model
        for model in registration.models
        if set(request.required_capabilities).issubset(set(model.capabilities) | set(registration.capabilities))
    ]
    selected = sorted(capable or registration.models, key=lambda model: model.model_id)[0]
    return selected.model_id


def _release_evidence_refs(session: Session, context: RuntimeExecutionContext) -> list[str]:
    refs = [ref for ref in [context.plan_id, context.plan_freeze_id] if ref]
    if context.workflow_task_id:
        latest_ready = session.scalar(
            select(AuditEventORM)
            .where(AuditEventORM.task_id == context.workflow_task_id, AuditEventORM.event_type == "TASK_READY")
            .order_by(AuditEventORM.timestamp.desc(), AuditEventORM.id.desc())
            .limit(1)
        )
        if latest_ready is not None:
            refs.append(latest_ready.id)
    return refs


def _provider_result_audit_payload(result: RuntimeExecutionResult, attempt_number: int) -> dict:
    return {
        "execution_id": result.execution_id,
        "routing_decision_id": result.routing_decision_id,
        "provider_id": result.provider_id,
        "provider_version": result.provider_version,
        "model_id": result.model_id,
        "model_version": result.model_version,
        "status": result.status.value,
        "provider_native_status": result.provider_native_status,
        "latency_ms": result.latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "total_tokens": result.total_tokens,
        "estimated_cost": result.estimated_cost,
        "cost_currency": result.cost_currency,
        "retryability": result.retryability.value,
        "failure_class": result.failure_class,
        "provider_error_metadata": result.provider_error_metadata,
        "fallback_used": result.fallback_used,
        "failover_from_provider_id": result.failover_from_provider_id,
        "attempt_number": attempt_number,
    }


def _enforce_provider_result_contract(result: RuntimeExecutionResult) -> RuntimeExecutionResult:
    if result.status.value == "COMPLETED" and not result.verification:
        return result.model_copy(
            update={
                "status": RuntimeExecutionOutcome.FAILED,
                "provider_native_status": result.provider_native_status or "COMPLETED_WITHOUT_VERIFICATION",
                "retryability": RuntimeRetryability.NON_RETRYABLE,
                "failure_class": "MISSING_VERIFICATION_HANDOFF",
                "mutation_summary": "Provider reported completion without verification handoff evidence.",
                "provider_error_metadata": {
                    **result.provider_error_metadata,
                    "reason": "COMPLETED result requires verification handoff evidence.",
                },
            }
        )
    return result


def _adapter_result_from_provider(
    provider_result: RuntimeExecutionResult,
    retry_index: int,
    failovers_used: int,
) -> ExecutionAdapterResult:
    return ExecutionAdapterResult(
        outcome=provider_result.status,
        completed_substeps=provider_result.completed_substeps,
        evidence=provider_result.evidence or provider_result.output_artifact_refs,
        verification=provider_result.verification,
        documentation=provider_result.documentation,
        active_execution_seconds=provider_result.active_execution_seconds,
        external_capacity_wait_seconds=provider_result.external_capacity_wait_seconds,
        human_wait_seconds=provider_result.human_wait_seconds,
        blocked_task_seconds=provider_result.blocked_task_seconds,
        retries=retry_index,
        escalations=0,
        blocking_reason=provider_result.mutation_summary if provider_result.status.value != "COMPLETED" else None,
        blocker_category=provider_result.failure_class,
        resume_condition=provider_result.verification_handoff_metadata.get("resume_condition"),
        error=provider_result.provider_error_metadata.get("message"),
        execution_id=provider_result.execution_id,
        provider_id=provider_result.provider_id,
        provider_version=provider_result.provider_version,
        model_id=provider_result.model_id,
        model_version=provider_result.model_version,
        routing_decision_id=provider_result.routing_decision_id,
        provider_native_status=provider_result.provider_native_status,
        retryability=provider_result.retryability,
        failure_class=provider_result.failure_class,
        provider_error_metadata=provider_result.provider_error_metadata,
        fallback_used=failovers_used > 0 or provider_result.fallback_used,
        failover_from_provider_id=provider_result.failover_from_provider_id,
        latency_ms=provider_result.latency_ms,
        input_tokens=provider_result.input_tokens,
        output_tokens=provider_result.output_tokens,
        total_tokens=provider_result.total_tokens,
        estimated_cost=provider_result.estimated_cost,
        cost_currency=provider_result.cost_currency,
        started_at=provider_result.started_at,
        completed_at=provider_result.completed_at,
        verification_handoff_metadata=provider_result.verification_handoff_metadata,
    )


def _retryable(retryability: RuntimeRetryability) -> bool:
    return retryability in {RuntimeRetryability.RETRYABLE, RuntimeRetryability.RETRYABLE_OR_FAILOVERABLE}


def _failoverable(retryability: RuntimeRetryability) -> bool:
    return retryability in {RuntimeRetryability.FAILOVERABLE, RuntimeRetryability.RETRYABLE_OR_FAILOVERABLE}


def _next_provider_id(provider_ids: list[str], current_provider_id: str) -> str | None:
    try:
        index = provider_ids.index(current_provider_id)
    except ValueError:
        return None
    if index + 1 >= len(provider_ids):
        return None
    return provider_ids[index + 1]
