from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.domain.enums import (
    AcceptanceCoverageStatus,
    Actor,
    AffectedFileStatus,
    AuthorityLevel,
    Environment,
    PlanRiskLevel,
    PlanningEvidenceMode,
    TestStrategyKind,
)
from lucius.persistence.orm import PersistentWorkflowORM, TaskContractORM, TaskORM
from lucius.pilots.freeze import PlanFreezeService
from lucius.pilots.workflows import PersistentWorkflowService
from lucius.planning.persistence import EngineeringPlanRepository
from lucius.planning.schemas import (
    AcceptanceCoverage,
    AffectedFilePlan,
    ModelEngineeringPlanOutput,
    PlanningContext,
    PlanStep,
    TestRecommendation,
)
from lucius.runtime.schemas import (
    ExecutionAdapterResult,
    RuntimeExecutionContext,
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    RuntimePlanReference,
    RuntimeProviderModel,
    RuntimeProviderRegistration,
)


class RuntimePlanningAdapter(Protocol):
    provider_id: str

    def prepare_workflow_plan(self, session: Session, workflow_id: str) -> RuntimePlanReference:
        ...


class ExecutionAdapter(Protocol):
    provider_id: str

    def execute(self, context: RuntimeExecutionContext) -> ExecutionAdapterResult:
        ...


class RuntimeExecutionProvider(Protocol):
    registration: RuntimeProviderRegistration

    def is_available(self) -> bool:
        ...

    def invoke(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        ...


class ScriptedRuntimePlanningAdapter:
    """Canonical planning adapter for deterministic runtime tests and dry pilots."""

    provider_id = "scripted-planning-adapter"

    def __init__(self, plans_by_workflow_id: Mapping[str, ModelEngineeringPlanOutput] | None = None):
        self._plans_by_workflow_id = dict(plans_by_workflow_id or {})

    def prepare_workflow_plan(self, session: Session, workflow_id: str) -> RuntimePlanReference:
        workflow = session.get(PersistentWorkflowORM, workflow_id)
        if workflow is None:
            raise ValueError(f"Unknown persistent workflow: {workflow_id}")
        if workflow.plan_id and workflow.plan_freeze_id:
            return RuntimePlanReference(
                workflow_id=workflow.id,
                plan_id=workflow.plan_id,
                plan_freeze_id=workflow.plan_freeze_id,
                created_by_runtime=False,
            )

        model_plan = self._plans_by_workflow_id.get(workflow_id) or _default_model_plan(workflow)
        context = _planning_context_from_workflow(session, workflow)
        plan = EngineeringPlanRepository(session).create(
            context=context,
            model_plan=model_plan,
            model_execution_ids=[],
            blockers=[],
            validation_warnings=[],
            planner_version="native-runtime-loop-v1",
            created_by=Actor.LUCIUS.value,
        )
        freeze = PlanFreezeService(session).freeze(
            plan_id=plan.id,
            planning_mode=PlanningEvidenceMode.CURRENT_STATE_PLANNING,
            evaluation_version="native-runtime-loop-v1",
            actor=Actor.LUCIUS,
        )
        PersistentWorkflowService(session).attach_plan(
            workflow.id,
            plan_id=plan.id,
            plan_freeze_id=freeze.id,
            actor=Actor.LUCIUS,
        )
        return RuntimePlanReference(
            workflow_id=workflow.id,
            plan_id=plan.id,
            plan_freeze_id=freeze.id,
            created_by_runtime=True,
        )


class ScriptedExecutionAdapter:
    def __init__(
        self,
        outcomes_by_item_id: Mapping[str, ExecutionAdapterResult | RuntimeExecutionResult] | None = None,
        *,
        provider_id: str = "scripted-execution-adapter",
        provider_version: str = "1",
        available: bool = True,
        capabilities: list[str] | None = None,
        supported_tools: list[str] | None = None,
        reliability_score: int = 50,
        model_id: str | None = "scripted-runtime-model",
    ):
        self.provider_id = provider_id
        self.provider_version = provider_version
        self.available = available
        self._outcomes_by_item_id = dict(outcomes_by_item_id or {})
        models = [
            RuntimeProviderModel(
                model_id=model_id,
                model_version=provider_version,
                capabilities=capabilities or ["code_modification"],
            )
        ] if model_id else []
        self.registration = RuntimeProviderRegistration(
            provider_id=provider_id,
            provider_version=provider_version,
            capabilities=capabilities or ["code_modification"],
            supported_task_classes=["engineering"],
            supports_code_modification=True,
            supported_workspace_kinds=["local_git_worktree"],
            supported_isolation_modes=["ISOLATED_WORKTREE"],
            supported_tools=supported_tools or [],
            models=models,
            available=available,
            retry_eligible=True,
            failover_eligible=True,
            cost_class="FREE",
            latency_class="LOW",
            policy_labels=["deterministic-test-provider"],
            reliability_score=reliability_score,
        )

    def is_available(self) -> bool:
        return self.available

    def invoke(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        started_at = datetime.now(timezone.utc)
        result = self._outcomes_by_item_id.get(request.item_id)
        if isinstance(result, RuntimeExecutionResult):
            return result.model_copy(
                update={
                    "execution_id": request.execution_id,
                    "provider_id": self.provider_id,
                    "provider_version": self.provider_version,
                    "model_id": result.model_id or _default_model_id(self.registration),
                    "started_at": result.started_at or started_at,
                    "completed_at": result.completed_at or datetime.now(timezone.utc),
                }
            )
        adapter_result = result or ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=[f"{request.item_id}: executed by scripted adapter"],
            evidence=[
                {
                    "type": "scripted_runtime_execution",
                    "workflow_id": request.workflow_id,
                    "item_id": request.item_id,
                    "provider_id": self.provider_id,
                }
            ],
            verification=[{"type": "scripted_verification", "result": "PASS"}],
            active_execution_seconds=0.001,
        )
        return _runtime_result_from_adapter(
            adapter_result,
            request=request,
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            model_id=_default_model_id(self.registration),
            started_at=started_at,
        )

    def execute(self, context: RuntimeExecutionContext) -> ExecutionAdapterResult:
        result = self._outcomes_by_item_id.get(context.item_id)
        if isinstance(result, ExecutionAdapterResult):
            return result
        if isinstance(result, RuntimeExecutionResult):
            return ExecutionAdapterResult(outcome=result.status, completed_substeps=result.completed_substeps)
        return ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=[f"{context.item_id}: executed by scripted adapter"],
            evidence=[
                {
                    "type": "scripted_runtime_execution",
                    "workflow_id": context.workflow_id,
                    "item_id": context.item_id,
                    "provider_id": self.provider_id,
                }
            ],
            verification=[{"type": "scripted_verification", "result": "PASS"}],
            active_execution_seconds=0.001,
        )


def _default_model_id(registration: RuntimeProviderRegistration) -> str | None:
    return registration.models[0].model_id if registration.models else None


def _runtime_result_from_adapter(
    adapter_result: ExecutionAdapterResult,
    *,
    request: RuntimeExecutionRequest,
    provider_id: str,
    provider_version: str,
    model_id: str | None,
    started_at: datetime,
) -> RuntimeExecutionResult:
    return RuntimeExecutionResult(
        execution_id=request.execution_id,
        provider_id=provider_id,
        provider_version=provider_version,
        model_id=model_id,
        model_version=provider_version if model_id else None,
        routing_decision_id=request.routing_decision_id,
        status=adapter_result.outcome,
        provider_native_status=adapter_result.provider_native_status or adapter_result.outcome.value,
        output_artifact_refs=adapter_result.evidence,
        mutation_summary=adapter_result.blocking_reason,
        completed_substeps=adapter_result.completed_substeps,
        evidence=adapter_result.evidence,
        verification=adapter_result.verification,
        documentation=adapter_result.documentation,
        active_execution_seconds=adapter_result.active_execution_seconds,
        external_capacity_wait_seconds=adapter_result.external_capacity_wait_seconds,
        human_wait_seconds=adapter_result.human_wait_seconds,
        blocked_task_seconds=adapter_result.blocked_task_seconds,
        latency_ms=adapter_result.latency_ms,
        input_tokens=adapter_result.input_tokens,
        output_tokens=adapter_result.output_tokens,
        total_tokens=adapter_result.total_tokens,
        estimated_cost=adapter_result.estimated_cost,
        cost_currency=adapter_result.cost_currency,
        retryability=adapter_result.retryability,
        failure_class=adapter_result.failure_class or adapter_result.blocker_category,
        provider_error_metadata=adapter_result.provider_error_metadata
        or ({"message": adapter_result.error} if adapter_result.error else {}),
        fallback_used=adapter_result.fallback_used,
        failover_from_provider_id=adapter_result.failover_from_provider_id,
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        verification_handoff_metadata=adapter_result.verification_handoff_metadata
        or ({"resume_condition": adapter_result.resume_condition} if adapter_result.resume_condition else {}),
    )


def _planning_context_from_workflow(session: Session, workflow: PersistentWorkflowORM) -> PlanningContext:
    task_id = workflow.task_id or workflow.id
    task = session.get(TaskORM, task_id) if workflow.task_id else None
    contract = _active_contract(session, task_id) if workflow.task_id else None
    return PlanningContext(
        task_id=task_id,
        run_id=workflow.id,
        project_id=workflow.project_id or "PROJECT_UNSPECIFIED",
        task_title=_workflow_title(workflow),
        task_objective=workflow.objective,
        task_status=task.status if task else "READY",
        task_authority_level=AuthorityLevel(task.authority_level) if task else AuthorityLevel.L1,
        task_complexity=task.complexity if task else "T1",
        task_contract_id=contract.id if contract else f"{task_id}_RUNTIME_CONTRACT",
        task_contract_version=contract.version if contract else 1,
        contract_objective=contract.objective if contract else workflow.objective,
        acceptance_criteria=contract.acceptance_criteria
        if contract
        else [{"id": "AC-1", "description": "Runtime-prepared plan can be frozen canonically."}],
        constraints=contract.constraints if contract else ["ONE_LOGICAL_DISPATCHER", "NO_PUSH", "NO_MERGE", "NO_DEPLOY"],
        allowed_actions=contract.allowed_actions
        if contract
        else ["READ_REPOSITORY", "RUN_TESTS", "WRITE_SOURCE", "WRITE_TESTS", "WRITE_DOCUMENTATION"],
        environment=Environment(contract.environment) if contract else Environment.DEVELOPMENT,
        contract_authority_level=AuthorityLevel(contract.authority_level) if contract else AuthorityLevel.L1,
        documentation_required=contract.documentation_required if contract else True,
        documentation_targets=contract.documentation_targets if contract else ["target-project canonical documentation"],
        snapshot_metadata=(
            [{"snapshot_id": workflow.repository_snapshot_id}]
            if workflow.repository_snapshot_id
            else []
        ),
    )


def _default_model_plan(workflow: PersistentWorkflowORM) -> ModelEngineeringPlanOutput:
    task_id = workflow.task_id or workflow.id
    return ModelEngineeringPlanOutput(
        summary=f"Runtime plan for {task_id}",
        objective=workflow.objective,
        risk_level=PlanRiskLevel.LOW,
        required_authority_level=AuthorityLevel.L1,
        affected_components=["native_execution_runtime_loop"],
        affected_files=[AffectedFilePlan(path=workflow.worktree_path, status=AffectedFileStatus.UNKNOWN)],
        steps=[
            PlanStep(
                step_id="STEP-1",
                sequence=1,
                title="Execute bounded queue item",
                description="Dispatch the selected queue item through the configured execution adapter.",
                affected_components=["runtime", "queue"],
                expected_result="Selected queue item is completed or safely blocked with evidence.",
                validation="Runtime records provider result and advances the canonical queue state.",
            )
        ],
        acceptance_coverage=[
            AcceptanceCoverage(
                criterion_id="AC-1",
                status=AcceptanceCoverageStatus.COVERED,
                step_ids=["STEP-1"],
            )
        ],
        test_strategy=[
            TestRecommendation(
                kind=TestStrategyKind.INTEGRATION,
                description="Run focused runtime loop tests and project-appropriate verification.",
                acceptance_criteria=["AC-1"],
            )
        ],
        documentation_requirements=[
            {
                "target": "target-project canonical documentation",
                "reason": "Runtime execution may change behavior, tests, or operating notes.",
                "trigger": "retained target change",
                "target_type": "canonical_target",
                "exact_path_required": False,
                "acceptable_categories": ["canonical_project_documentation"],
            }
        ],
        rollback_considerations=["Block the queue item and preserve checkpoint evidence if execution cannot proceed safely."],
        estimated_scope="bounded-runtime-task",
        confidence=0.8,
    )


def _workflow_title(workflow: PersistentWorkflowORM) -> str:
    for item in workflow.task_backlog or []:
        if item.get("title"):
            return str(item["title"])
    return workflow.objective


def _active_contract(session: Session, task_id: str) -> TaskContractORM | None:
    return session.scalar(
        select(TaskContractORM)
        .where(TaskContractORM.task_id == task_id)
        .order_by(TaskContractORM.version.desc())
        .limit(1)
    )
