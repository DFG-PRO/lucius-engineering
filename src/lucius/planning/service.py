from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import (
    Actor,
    AuthorityLevel,
    ModelCapability,
    ModelResponseStatus,
    PlanningBlockerCode,
    PrivacyClass,
    ProjectType,
    ReplanReason,
    TaskComplexity,
)
from lucius.models.gateway import ModelGateway
from lucius.models.schemas import ModelMessage, ModelRequest
from lucius.persistence.orm import EngineeringPlanORM, ProjectORM, TaskContractORM, TaskORM
from lucius.planning.context import PlanningContextBuilder
from lucius.planning.persistence import EngineeringPlanRepository, plan_from_row
from lucius.planning.schemas import (
    ModelEngineeringPlanOutput,
    PlanningBlocker,
    PlanningContext,
    PlanningContextBudget,
    PlanningResult,
)
from lucius.planning.validation import validate_and_enrich_plan
from lucius.repositories.schemas import WorkspaceContext

PLANNER_VERSION = "1.9.0"


class EngineeringPlannerService:
    def __init__(self, session: Session, *, model_gateway: ModelGateway | None = None):
        self.session = session
        self.audit = AuditService(session)
        self.model_gateway = model_gateway or ModelGateway(session)
        self.context_builder = PlanningContextBuilder(session)
        self.plans = EngineeringPlanRepository(session)

    def create_plan(
        self,
        *,
        task_id: str,
        snapshot_ids: list[str],
        workspace_contexts: dict[str, WorkspaceContext],
        run_id: str | None = None,
        budget: PlanningContextBudget | None = None,
        allow_candidate_memory: bool = False,
        actor: Actor = Actor.SYSTEM,
    ) -> PlanningResult:
        task = self._task(task_id)
        self.audit.record(
            event_type="PLANNING_STARTED",
            actor=actor.value,
            project_id=task.project_id,
            task_id=task.id,
            run_id=run_id,
            action="create_engineering_plan",
            result="STARTED",
        )
        context, blockers = self.context_builder.build(
            task_id=task_id,
            run_id=run_id,
            snapshot_ids=snapshot_ids,
            workspace_contexts=workspace_contexts,
            budget=budget,
            allow_candidate_memory=allow_candidate_memory,
            actor=actor,
        )
        if blockers or context is None:
            return PlanningResult(context=context, blockers=blockers)
        evidence_blockers = self._evidence_preconditions(context)
        if evidence_blockers:
            self._audit_blocked(context, evidence_blockers, actor=actor)
            return PlanningResult(context=context, blockers=evidence_blockers)
        request = self._model_request(context)
        self.audit.record(
            event_type="PLANNER_MODEL_REQUESTED",
            actor=actor.value,
            project_id=context.project_id,
            task_id=context.task_id,
            run_id=context.run_id,
            action="request_planning_model",
            result="STARTED",
            metadata={
                "request_id": request.request_id,
                "required_capabilities": sorted(capability.value for capability in request.required_capabilities),
                "privacy_class": request.privacy_class.value,
            },
        )
        response = self.model_gateway.execute(request, actor=actor.value)
        if response.status != ModelResponseStatus.SUCCEEDED:
            blocker_code = PlanningBlockerCode.MODEL_POLICY_BLOCK if response.error_code else PlanningBlockerCode.MODEL_UNAVAILABLE
            blocker = PlanningBlocker(
                code=blocker_code,
                message=response.error_message or "Planning model request failed.",
                metadata={"model_error_code": response.error_code.value if response.error_code else None},
            )
            self._audit_blocked(context, [blocker], actor=actor)
            return PlanningResult(context=context, blockers=[blocker], model_response_error=response.error_message)
        try:
            model_plan = ModelEngineeringPlanOutput.model_validate(response.structured_data)
        except Exception as error:
            blocker = PlanningBlocker(
                code=PlanningBlockerCode.ENGINEERING_PLAN_INVALID,
                message="Planning model returned malformed structured output.",
                metadata={"error": str(error)},
            )
            self.audit.record(
                event_type="ENGINEERING_PLAN_INVALID",
                actor=actor.value,
                project_id=context.project_id,
                task_id=context.task_id,
                run_id=context.run_id,
                action="validate_engineering_plan",
                result="BLOCKED",
                metadata={"blockers": [blocker.model_dump(mode="json")]},
            )
            return PlanningResult(context=context, blockers=[blocker], model_response_error=str(error))
        model_plan, validation_blockers, warnings = validate_and_enrich_plan(self.session, context=context, plan=model_plan)
        hard_blockers = [blocker for blocker in validation_blockers if blocker.code != PlanningBlockerCode.AUTHORITY_ESCALATION_REQUIRED]
        if hard_blockers:
            self.audit.record(
                event_type="ENGINEERING_PLAN_INVALID",
                actor=actor.value,
                project_id=context.project_id,
                task_id=context.task_id,
                run_id=context.run_id,
                action="validate_engineering_plan",
                result="BLOCKED",
                metadata={"blockers": [blocker.model_dump(mode="json") for blocker in validation_blockers]},
            )
            return PlanningResult(context=context, blockers=validation_blockers)
        plan = self.plans.create(
            context=context,
            model_plan=model_plan,
            model_execution_ids=[response.execution_id] if response.execution_id else [],
            blockers=validation_blockers,
            validation_warnings=warnings,
            planner_version=PLANNER_VERSION,
            created_by=actor.value,
        )
        self.audit.record(
            event_type="ENGINEERING_PLAN_CREATED",
            actor=actor.value,
            project_id=context.project_id,
            task_id=context.task_id,
            run_id=context.run_id,
            action="create_engineering_plan",
            result="SUCCESS",
            metadata={"plan_id": plan.id, "version": plan.version, "model_execution_ids": plan.model_execution_ids},
        )
        self.audit.record(
            event_type="ENGINEERING_PLAN_PROPOSED",
            actor=actor.value,
            project_id=context.project_id,
            task_id=context.task_id,
            run_id=context.run_id,
            action="propose_engineering_plan",
            result="SUCCESS",
            metadata={"plan_id": plan.id, "risk_level": plan.risk_level.value, "required_authority_level": plan.required_authority_level.value},
        )
        for blocker in validation_blockers:
            if blocker.code == PlanningBlockerCode.AUTHORITY_ESCALATION_REQUIRED:
                self.audit.record(
                    event_type="AUTHORITY_ESCALATION_REQUIRED",
                    actor=actor.value,
                    project_id=context.project_id,
                    task_id=context.task_id,
                    run_id=context.run_id,
                    action="validate_engineering_plan",
                    result="WARNING",
                    metadata=blocker.model_dump(mode="json"),
                )
        return PlanningResult(plan=plan, context=context, blockers=validation_blockers)

    def replan(
        self,
        *,
        previous_plan_id: str,
        reason: ReplanReason,
        snapshot_ids: list[str],
        workspace_contexts: dict[str, WorkspaceContext],
        actor: Actor = Actor.SYSTEM,
        budget: PlanningContextBudget | None = None,
    ) -> PlanningResult:
        previous = self.session.get(EngineeringPlanORM, previous_plan_id)
        if previous is None:
            raise ValueError(f"Unknown engineering plan: {previous_plan_id}")
        result = self.create_plan(
            task_id=previous.task_id,
            run_id=previous.run_id,
            snapshot_ids=snapshot_ids,
            workspace_contexts=workspace_contexts,
            budget=budget,
            actor=actor,
        )
        if result.plan is None:
            return result
        new_row = self.session.get(EngineeringPlanORM, result.plan.id)
        old_row = self.session.get(EngineeringPlanORM, previous_plan_id)
        if new_row and old_row:
            old_row.status = "SUPERSEDED"
            old_row.superseded_by_plan_id = new_row.id
            old_row.supersession_reason = reason.value
            new_row.supersedes_plan_id = old_row.id
            new_row.supersession_reason = reason.value
            self.session.flush()
            result.plan = plan_from_row(new_row)
        self.audit.record(
            event_type="ENGINEERING_PLAN_SUPERSEDED",
            actor=actor.value,
            project_id=previous.project_id,
            task_id=previous.task_id,
            run_id=previous.run_id,
            action="replan",
            result="SUCCESS",
            metadata={"previous_plan_id": previous_plan_id, "new_plan_id": result.plan.id, "reason": reason.value},
        )
        return result

    def reject_plan(self, plan_id: str, *, reason: str, actor: Actor = Actor.HUMAN):
        plan = self.plans.reject(plan_id, reason=reason)
        self.audit.record(
            event_type="ENGINEERING_PLAN_REJECTED",
            actor=actor.value,
            project_id=plan.project_id,
            task_id=plan.task_id,
            run_id=plan.run_id,
            action="reject_engineering_plan",
            result="SUCCESS",
            metadata={"plan_id": plan.id, "reason": reason},
        )
        return plan

    def _model_request(self, context: PlanningContext) -> ModelRequest:
        capabilities = {ModelCapability.PLANNING, ModelCapability.STRUCTURED_OUTPUT}
        if context.task_complexity in {TaskComplexity.T3.value, TaskComplexity.T4.value}:
            capabilities.update({ModelCapability.CODE_REASONING, ModelCapability.HIGH_REASONING})
        return ModelRequest(
            request_id=self._request_id(context.task_id),
            task_id=context.task_id,
            run_id=context.run_id,
            project_id=context.project_id,
            purpose="engineering_planning",
            required_capabilities=capabilities,
            privacy_class=self._privacy_class(context.project_id),
            minimum_quality=40 if ModelCapability.HIGH_REASONING in capabilities else 0,
            input_messages=[ModelMessage(role="user", content=_planning_prompt(context))],
            structured_output_schema=ModelEngineeringPlanOutput,
            fallback_allowed=True,
            metadata={
                "planner_version": PLANNER_VERSION,
                "task_contract_version": context.task_contract_version,
                "context_summary": {
                    "evidence_count": len(context.evidence),
                    "memory_count": len(context.memory),
                    "warnings": context.context_warnings,
                },
            },
        )

    def _evidence_preconditions(self, context: PlanningContext) -> list[PlanningBlocker]:
        writes_repository = any(action in {"WRITE_SOURCE", "WRITE_TESTS", "CREATE_MIGRATION"} for action in context.allowed_actions)
        if writes_repository and context.snapshot_metadata and not context.evidence:
            return [
                PlanningBlocker(
                    code=PlanningBlockerCode.NO_RELEVANT_EVIDENCE,
                    message="Repository-bound implementation planning requires current evidence.",
                )
            ]
        return []

    def _privacy_class(self, project_id: str) -> PrivacyClass:
        project = self.session.get(ProjectORM, project_id)
        if project and project.project_type == ProjectType.CLIENT.value:
            return PrivacyClass.CONFIDENTIAL
        return PrivacyClass.INTERNAL

    def _request_id(self, task_id: str) -> str:
        existing = self.session.scalar(
            select(EngineeringPlanORM).where(EngineeringPlanORM.task_id == task_id).order_by(EngineeringPlanORM.version.desc()).limit(1)
        )
        version = 1 if existing is None else existing.version + 1
        return f"PLANREQ-{task_id}-v{version}"

    def _audit_blocked(self, context: PlanningContext, blockers: list[PlanningBlocker], *, actor: Actor) -> None:
        self.audit.record(
            event_type="PLANNING_BLOCKED",
            actor=actor.value,
            project_id=context.project_id,
            task_id=context.task_id,
            run_id=context.run_id,
            action="create_engineering_plan",
            result="BLOCKED",
            metadata={"blockers": [blocker.model_dump(mode="json") for blocker in blockers]},
        )

    def _task(self, task_id: str) -> TaskORM:
        task = self.session.get(TaskORM, task_id)
        if task is None:
            raise ValueError(f"Unknown task: {task_id}")
        return task


def _planning_prompt(context: PlanningContext) -> str:
    payload: dict[str, Any] = {
        "planner_contract": {
            "epistemic_rules": [
                "Distinguish current evidence, memory, inference, assumptions, and unknowns.",
                "Do not invent evidence IDs or treat memory as current repository reality.",
                "Return only fields compatible with the structured schema.",
            ],
            "expected_fields": [
                "summary",
                "steps",
                "evidence references",
                "memory references",
                "assumptions",
                "unknowns",
                "risks",
                "tests",
                "documentation",
                "authority requirement",
            ],
        },
        "task": {
            "id": context.task_id,
            "title": context.task_title,
            "objective": context.task_objective,
            "authority_level": context.task_authority_level.value,
        },
        "contract": {
            "id": context.task_contract_id,
            "version": context.task_contract_version,
            "objective": context.contract_objective,
            "acceptance_criteria": context.acceptance_criteria,
            "constraints": context.constraints,
            "authority_level": context.contract_authority_level.value,
            "environment": context.environment.value,
            "documentation_required": context.documentation_required,
            "documentation_targets": context.documentation_targets,
        },
        "snapshots": context.snapshot_metadata,
        "current_evidence": [item.model_dump(mode="json") for item in context.evidence],
        "historical_memory": [item.model_dump(mode="json") for item in context.memory],
        "warnings": context.context_warnings,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
