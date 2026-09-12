from __future__ import annotations

from pathlib import PurePosixPath
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, AuthorityLevel
from lucius.persistence.orm import AuditEventORM, ModelExecutionORM, PlanFreezeORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.runtime.provider_quality import evidence_sensitive_provider_quality_issues
from lucius.runtime.schema_constraints import (
    SKELETON_METADATA_KEY,
    enforce_schema_constrained_output,
    schema_constrained_output_skeleton,
)
from lucius.runtime.schemas import (
    ExecutionAdapterResult,
    RuntimeExecutionContext,
    RuntimeExecutionOutcome,
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    ModelCapabilityProfile,
    ModelQualificationStatus,
    RuntimeExecutionSupervision,
    RuntimeProviderCandidateEvaluation,
    RuntimeProviderModel,
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
        default_execution_supervision: RuntimeExecutionSupervision = RuntimeExecutionSupervision.UNSUPERVISED,
    ):
        self.session = session
        self.registry = registry
        self.actor = actor
        self.max_provider_retries = max(0, max_provider_retries)
        self.max_failovers = max(0, max_failovers)
        self.default_execution_supervision = default_execution_supervision
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
        mutation_scope_error = request.metadata.get("mutation_scope_error")
        if not request.read_only and mutation_scope_error:
            message = (
                "Mutation authorization rejected before provider routing: "
                + str(mutation_scope_error)
            )
            self.audit.record(
                event_type="MODEL_EXECUTION_MUTATION_SCOPE_REJECTED",
                actor=self.actor.value,
                project_id=context.project_id,
                repository_id=context.repository_id,
                task_id=context.workflow_task_id,
                action="validate_frozen_mutation_scope",
                result="FAILED_CLOSED",
                authority_level=AuthorityLevel.L1,
                metadata={
                    "execution_id": request.execution_id,
                    "plan_id": request.plan_id,
                    "plan_freeze_id": request.plan_freeze_id,
                    "mutation_scope_error": mutation_scope_error,
                    "allowed_mutation_paths": request.allowed_mutation_paths,
                },
            )
            return ExecutionAdapterResult(
                outcome="FAILED",
                execution_id=request.execution_id,
                blocker_category="MUTATION_SCOPE_VIOLATION",
                failure_class="MUTATION_SCOPE_VIOLATION",
                blocking_reason=message,
                resume_condition=(
                    "Restore a valid frozen plan with explicit authorized mutation paths "
                    "and rerun canonical readiness/release."
                ),
                error=str(mutation_scope_error),
                retryability=RuntimeRetryability.NON_RETRYABLE,
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
                failure_class="NO_ELIGIBLE_PROVIDER",
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
                provider_result = _enforce_provider_result_contract(provider_result, request)
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
                self._record_model_execution_attempt(
                    context=context,
                    request=request,
                    decision=decision,
                    result=provider_result,
                    attempt_number=attempt_index + 1,
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
        context_limits = dict(queue_item.get("context_limits", {}))
        read_only = _queue_item_is_read_only(queue_item)
        unattended = bool(queue_item.get("unattended", queue_item.get("unattended_eligible", False)))
        (
            allowed_mutation_paths,
            deterministic_acceptance_checks,
            mutation_scope_error,
        ) = _frozen_mutation_scope(
            self.session,
            context,
            read_only=read_only,
            require_deterministic_acceptance=unattended and not read_only,
        )
        required_capabilities = queue_item.get("required_capabilities")
        if required_capabilities is None:
            required_capabilities = ["inspection_reasoning"] if read_only else ["code_modification"]
        metadata = {
            "queue_item_version": queue_item.get("version"),
            "queue_item_priority": queue_item.get("priority"),
            "read_only": read_only,
            "task_complexity_explicit": "task_complexity" in queue_item or "complexity" in queue_item,
            "task_risk_explicit": "task_risk" in queue_item or "risk" in queue_item,
            "isolation_mode_explicit": "isolation_mode" in queue_item,
            "deterministic_verification_explicit": "deterministic_verification" in context_limits,
            "mutation_scope_source": "plan_freeze",
            "deterministic_acceptance_source": "plan_freeze",
            "mutation_scope_error": mutation_scope_error,
        }
        skeleton = schema_constrained_output_skeleton(context_limits)
        if skeleton:
            metadata[SKELETON_METADATA_KEY] = skeleton
        raw_supervision = queue_item.get("execution_supervision", self.default_execution_supervision)
        execution_supervision = (
            raw_supervision
            if isinstance(raw_supervision, RuntimeExecutionSupervision)
            else RuntimeExecutionSupervision(str(raw_supervision))
        )
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
            allowed_mutation_paths=allowed_mutation_paths,
            deterministic_acceptance_checks=deterministic_acceptance_checks,
            required_capabilities=[str(item) for item in required_capabilities],
            task_type=str(queue_item.get("task_type", "engineering")),
            task_complexity=str(queue_item.get("task_complexity", queue_item.get("complexity", "T1"))),
            task_risk=str(queue_item.get("task_risk", queue_item.get("risk", "LOW"))),
            unattended=unattended,
            read_only=read_only,
            execution_supervision=execution_supervision,
            tool_requirements=[str(item) for item in queue_item.get("tool_requirements", [])],
            isolation_mode=str(queue_item.get("isolation_mode", "ISOLATED_WORKTREE")),
            context_limits=context_limits,
            timeout_seconds=queue_item.get("timeout_seconds"),
            budget_policy=dict(queue_item.get("budget_policy", {})),
            release_evidence_refs=_release_evidence_refs(self.session, context),
            metadata=metadata,
        )

    def _record_model_execution_attempt(
        self,
        *,
        context: RuntimeExecutionContext,
        request: RuntimeExecutionRequest,
        decision: RuntimeRoutingDecision,
        result: RuntimeExecutionResult,
        attempt_number: int,
    ) -> None:
        record_id = request.execution_id
        if attempt_number > 1 or self.session.get(ModelExecutionORM, record_id) is not None:
            record_id = next_id(self.session, "model_execution")
        self.session.add(
            ModelExecutionORM(
                id=record_id,
                request_id=request.execution_id,
                project_id=context.project_id,
                task_id=context.workflow_task_id,
                run_id=context.workflow_id,
                provider_id=result.provider_id,
                provider=result.provider_id,
                model_profile_id=None,
                model=result.model_id,
                status=result.status.value,
                capabilities=list(request.required_capabilities),
                privacy_class="INTERNAL",
                routing_decision={
                    **decision.model_dump(mode="json"),
                    "attempt_number": attempt_number,
                    "fallback_used": result.fallback_used,
                    "failover_from_provider_id": result.failover_from_provider_id,
                    "retryability": result.retryability.value,
                },
                latency_ms=result.latency_ms,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                total_tokens=result.total_tokens,
                estimated_cost=result.estimated_cost,
                cost_source="REPORTED" if result.estimated_cost is not None else "UNKNOWN",
                fallback_used=result.fallback_used,
                attempt_number=attempt_number,
                error_code=result.failure_class,
                created_at=utc_now(),
            )
        )
        self.session.flush()

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
    selected_model = _selected_model(registration, request)
    profile_reasons = _profile_ineligibility_reasons(request, registration, selected_model)
    if profile_reasons:
        return RuntimeProviderCandidateEvaluation(
            provider_id=registration.provider_id,
            eligible=False,
            reasons=profile_reasons,
            selected_model_id=selected_model.model_id if selected_model else None,
        )
    if request.unattended:
        unattended_reasons = _unattended_ineligibility_reasons(request, registration, selected_model)
        if unattended_reasons:
            return RuntimeProviderCandidateEvaluation(
                provider_id=registration.provider_id,
                eligible=False,
                reasons=unattended_reasons,
                selected_model_id=selected_model.model_id if selected_model else None,
            )
        timeout_seconds = _bounded_timeout_seconds(request, selected_model.capability_profile if selected_model else None)
        if timeout_seconds is not None and request.timeout_seconds is None:
            request.timeout_seconds = timeout_seconds
    reasons.append("eligible: capability, policy, repository, isolation, tool, and context requirements satisfied")
    return RuntimeProviderCandidateEvaluation(
        provider_id=registration.provider_id,
        eligible=True,
        reasons=reasons,
        selected_model_id=selected_model.model_id if selected_model else None,
    )


_RUNTIME_COST_RANK = {
    "LOCAL_FREE": 0,
    "FREE": 0,
    "LOW_COST": 1,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "PREMIUM": 4,
}


def _provider_selection_key(registration: RuntimeProviderRegistration) -> tuple[int, int, str]:
    cost_rank = _RUNTIME_COST_RANK.get(str(registration.cost_class or "").upper(), 99)
    return (cost_rank, -registration.reliability_score, registration.provider_id)


def _selected_model_id(registration: RuntimeProviderRegistration, request: RuntimeExecutionRequest) -> str | None:
    selected = _selected_model(registration, request)
    return selected.model_id if selected else None


def _selected_model(registration: RuntimeProviderRegistration, request: RuntimeExecutionRequest) -> RuntimeProviderModel | None:
    if not registration.models:
        return None
    capable = [
        model
        for model in registration.models
        if set(request.required_capabilities).issubset(set(model.capabilities) | set(registration.capabilities))
    ]
    return sorted(capable or registration.models, key=lambda model: model.model_id)[0]


def _profile_ineligibility_reasons(
    request: RuntimeExecutionRequest,
    registration: RuntimeProviderRegistration,
    selected_model: RuntimeProviderModel | None,
) -> list[str]:
    profile = selected_model.capability_profile if selected_model else None
    if profile is None:
        return []
    reasons: list[str] = []
    if profile.status == ModelQualificationStatus.DISABLED:
        reasons.append("MODEL_NOT_QUALIFIED")
    if profile.supervision_required and request.execution_supervision not in {
        RuntimeExecutionSupervision.SUPERVISED,
        RuntimeExecutionSupervision.HUMAN_APPROVED,
    }:
        reasons.append("MODEL_SUPERVISION_REQUIRED")
    if request.unattended and (profile.supervision_required or profile.status == ModelQualificationStatus.SUPERVISED_ONLY):
        reasons.append("MODEL_SUPERVISION_REQUIRED")
    if profile.schema_constrained_required and not request.context_limits.get("schema_constraint"):
        reasons.append("SCHEMA_CONSTRAINT_REQUIRED")
    if _requires_mutation(request) and not profile.supports_mutation:
        reasons.append("MODEL_LACKS_REQUIRED_CAPABILITY")
    return list(dict.fromkeys(reasons))


def _unattended_ineligibility_reasons(
    request: RuntimeExecutionRequest,
    registration: RuntimeProviderRegistration,
    selected_model: RuntimeProviderModel | None,
) -> list[str]:
    reasons: list[str] = []
    profile = selected_model.capability_profile if selected_model else None
    if profile is None:
        reasons.append("MODEL_NOT_QUALIFIED_FOR_UNATTENDED")
    elif profile.status == ModelQualificationStatus.DISABLED:
        reasons.append("MODEL_NOT_QUALIFIED_FOR_UNATTENDED")
    elif profile.status == ModelQualificationStatus.SUPERVISED_ONLY or profile.supervision_required:
        reasons.append("MODEL_SUPERVISION_REQUIRED")
    elif profile.status == ModelQualificationStatus.NOT_QUALIFIED or not profile.unattended_eligible:
        reasons.append("MODEL_NOT_QUALIFIED_FOR_UNATTENDED")
    if profile is not None:
        if not request.metadata.get("task_complexity_explicit"):
            reasons.append("TASK_COMPLEXITY_UNKNOWN")
        if not request.metadata.get("task_risk_explicit"):
            reasons.append("TASK_RISK_UNKNOWN")
        if not request.metadata.get("deterministic_verification_explicit"):
            reasons.append("DETERMINISTIC_VERIFICATION_UNKNOWN")
        if not request.metadata.get("isolation_mode_explicit"):
            reasons.append("WORKSPACE_ISOLATION_UNKNOWN")
        if profile.is_local and not registration.metadata.get("explicit_allowed_workspace_roots"):
            reasons.append("WORKSPACE_ALLOWED_ROOT_UNKNOWN")
        missing_model_capabilities = sorted(set(request.required_capabilities) - set(profile.supported_capabilities))
        if missing_model_capabilities:
            reasons.append("MODEL_LACKS_REQUIRED_CAPABILITY")
        if _requires_mutation(request) and not profile.supports_mutation:
            reasons.append("MODEL_LACKS_REQUIRED_CAPABILITY")
        if _complexity_rank(request.task_complexity) > _complexity_rank(profile.max_task_complexity):
            reasons.append("TASK_COMPLEXITY_EXCEEDS_MODEL_PROFILE")
        if str(request.task_complexity).upper() not in {"T0", "T1", "T2", "T3", "T4"}:
            reasons.append("TASK_COMPLEXITY_UNKNOWN")
        if str(request.task_risk).upper() != "LOW":
            reasons.append("TASK_RISK_EXCEEDS_UNATTENDED_POLICY")
        if profile.deterministic_verification_required and not request.context_limits.get("deterministic_verification"):
            reasons.append("DETERMINISTIC_VERIFICATION_REQUIRED")
        if _is_evidence_sensitive_request(request) and not request.context_limits.get("evidence_reference_validation_required"):
            reasons.append("EVIDENCE_VALIDATION_REQUIRED")
        timeout = request.timeout_seconds or profile.default_timeout_seconds
        if profile.max_timeout_seconds is not None and timeout is not None and timeout > profile.max_timeout_seconds:
            reasons.append("TIMEOUT_BUDGET_EXCEEDS_MODEL_PROFILE")
    if request.isolation_mode != "ISOLATED_WORKTREE":
        reasons.append("WORKSPACE_ISOLATION_REQUIRED")
    if request.context_limits.get("external_side_effects") is True or _external_side_effect_action(request):
        reasons.append("EXTERNAL_SIDE_EFFECT_NOT_AUTHORIZED")
    if _financial_or_live_action(request):
        reasons.append("FINANCIAL_ACTION_NOT_AUTHORIZED")
    if request.context_limits.get("semantic_retry_allowed") is True:
        reasons.append("SEMANTIC_RETRY_NOT_AUTHORIZED")
    return list(dict.fromkeys(reasons))


def _bounded_timeout_seconds(request: RuntimeExecutionRequest, profile: ModelCapabilityProfile | None) -> int | None:
    if profile is None:
        return request.timeout_seconds
    if request.timeout_seconds is not None:
        return request.timeout_seconds
    return profile.default_timeout_seconds


def _requires_mutation(request: RuntimeExecutionRequest) -> bool:
    mutation_capabilities = {"code_modification", "documentation_update"}
    return bool(set(request.required_capabilities) & mutation_capabilities)


def _complexity_rank(value: str | None) -> int:
    ranks = {"T0": 0, "T1": 1, "T2": 2, "T3": 3, "T4": 4}
    return ranks.get(str(value or "T1").upper(), 99)


def _is_evidence_sensitive_request(request: RuntimeExecutionRequest) -> bool:
    if request.context_limits.get("evidence_sensitive") is True:
        return True
    haystack = " ".join(
        [
            request.task_intent or "",
            request.logical_task_id or "",
            " ".join(request.required_capabilities or []),
        ]
    ).lower()
    return any(term in haystack for term in ("evidence", "profitability", "backtest", "oos", "capital", "strategy"))


def _financial_or_live_action(request: RuntimeExecutionRequest) -> bool:
    haystack = " ".join(
        [
            request.task_intent or "",
            request.task_type or "",
            " ".join(request.required_capabilities or []),
        ]
    ).lower()
    blocked_terms = ("live trading", "money movement", "place order", "execute trade")
    return any(term in haystack for term in blocked_terms)


def _external_side_effect_action(request: RuntimeExecutionRequest) -> bool:
    haystack = " ".join(
        [
            request.task_intent or "",
            request.task_type or "",
            " ".join(request.required_capabilities or []),
        ]
    ).lower()
    blocked_terms = ("deploy", "push", "merge")
    return any(term in haystack for term in blocked_terms)


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



def _frozen_mutation_scope(
    session: Session,
    context: RuntimeExecutionContext,
    *,
    read_only: bool,
    require_deterministic_acceptance: bool,
) -> tuple[list[str], list[dict[str, Any]], str | None]:
    if read_only:
        return [], [], None

    freeze = session.get(PlanFreezeORM, context.plan_freeze_id)

    if freeze is None:
        return [], [], "PLAN_FREEZE_NOT_FOUND"

    if freeze.plan_id != context.plan_id:
        return [], [], "PLAN_FREEZE_PLAN_MISMATCH"

    payload = freeze.plan_payload if isinstance(freeze.plan_payload, dict) else {}
    affected_files = payload.get("affected_files")

    if not isinstance(affected_files, list):
        return [], [], "INVALID_FROZEN_AFFECTED_FILES"

    paths: list[str] = []
    seen: set[str] = set()

    for item in affected_files:
        if not isinstance(item, dict):
            return [], [], "INVALID_FROZEN_AFFECTED_FILES"

        raw_path = item.get("path")
        normalized = _normalize_frozen_mutation_path(raw_path)

        if normalized is None:
            return [], [], "INVALID_FROZEN_AFFECTED_FILES"

        if normalized not in seen:
            seen.add(normalized)
            paths.append(normalized)

    if not paths:
        return [], [], "EMPTY_FROZEN_MUTATION_SCOPE"

    raw_checks = payload.get("deterministic_acceptance_checks", [])

    if not isinstance(raw_checks, list):
        return paths, [], "INVALID_FROZEN_DETERMINISTIC_ACCEPTANCE_CHECKS"

    checks: list[dict[str, Any]] = []
    checked_paths: set[str] = set()

    for item in raw_checks:
        if not isinstance(item, dict):
            return paths, [], "INVALID_FROZEN_DETERMINISTIC_ACCEPTANCE_CHECKS"

        check_type = item.get("type")
        raw_path = item.get("path")
        expected_text = item.get("expected_text")

        if check_type != "exact_file_content":
            return paths, [], "UNSUPPORTED_FROZEN_DETERMINISTIC_ACCEPTANCE_CHECK"

        normalized_path = _normalize_frozen_mutation_path(raw_path)
        if normalized_path is None:
            return paths, [], "INVALID_FROZEN_DETERMINISTIC_ACCEPTANCE_PATH"

        if normalized_path not in seen:
            return paths, [], "DETERMINISTIC_ACCEPTANCE_PATH_OUTSIDE_MUTATION_SCOPE"

        if normalized_path in checked_paths:
            return paths, [], "DUPLICATE_FROZEN_DETERMINISTIC_ACCEPTANCE_PATH"

        if not isinstance(expected_text, str):
            return paths, [], "INVALID_FROZEN_DETERMINISTIC_ACCEPTANCE_EXPECTED_TEXT"

        checked_paths.add(normalized_path)
        checks.append(
            {
                "type": "exact_file_content",
                "path": normalized_path,
                "expected_text": expected_text,
            }
        )

    if require_deterministic_acceptance and not checks:
        return paths, [], "MISSING_FROZEN_DETERMINISTIC_ACCEPTANCE_CHECK"

    return paths, checks, None


def _normalize_frozen_mutation_path(raw_path: object) -> str | None:
    if not isinstance(raw_path, str):
        return None

    candidate = raw_path.strip()
    if not candidate or "\\" in candidate:
        return None

    path = PurePosixPath(candidate)

    if path.is_absolute() or ".." in path.parts:
        return None

    normalized = path.as_posix()
    if normalized in {"", "."}:
        return None

    return normalized

def _queue_item_is_read_only(queue_item: dict) -> bool:
    if queue_item.get("read_only") is True or queue_item.get("mutation_allowed") is False:
        return True
    task_type = str(queue_item.get("task_type", "")).lower()
    if task_type in {"inspection", "reasoning", "analysis", "read_only_engineering"}:
        return True
    return False


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


def _enforce_provider_result_contract(
    result: RuntimeExecutionResult,
    request: RuntimeExecutionRequest,
) -> RuntimeExecutionResult:
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
    result = enforce_schema_constrained_output(request, result)
    if result.status.value != "COMPLETED":
        return result
    quality_issues = evidence_sensitive_provider_quality_issues(request, result)
    if result.status.value == "COMPLETED" and quality_issues:
        evidence_reference_failed = any(
            str(issue.get("reason", "")).startswith((
                "FABRICATED_OR_UNAUTHORIZED_EVIDENCE_REFERENCE",
                "MALFORMED_EVIDENCE_REFERENCE_FIELD",
                "MISSING_REQUIRED_EVIDENCE_REFERENCE",
                "FACT_OR_DERIVED_VALUE_MISSING_ALLOWED_EVIDENCE_REFERENCE",
            ))
            for issue in quality_issues
        )
        return result.model_copy(
            update={
                "status": RuntimeExecutionOutcome.FAILED,
                "provider_native_status": result.provider_native_status or "COMPLETED_WITH_UNSUPPORTED_CLAIMS",
                "retryability": RuntimeRetryability.NON_RETRYABLE,
                "failure_class": "EVIDENCE_REFERENCE_VALIDATION_FAILED"
                if evidence_reference_failed
                else "UNSUPPORTED_QUANTITATIVE_CLAIM",
                "mutation_summary": "Evidence-sensitive provider output contains fabricated, missing, untyped, or unsupported evidence/quantitative claims.",
                "provider_error_metadata": {
                    **result.provider_error_metadata,
                    "reason": "Evidence-sensitive claims require allowed evidence references and FACT, DERIVED_VALUE, ASSUMPTION, PROPOSED_PARAMETER, or UNKNOWN classification.",
                    "quality_issues": quality_issues,
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
