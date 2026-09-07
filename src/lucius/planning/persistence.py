from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.domain.enums import EngineeringPlanStatus
from lucius.persistence.orm import EngineeringPlanORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.planning.schemas import EngineeringPlan, ModelEngineeringPlanOutput, PlanningBlocker, PlanningContext
from lucius.pilots.documentation_contract import canonicalize_documentation_requirements


class EngineeringPlanRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(
        self,
        *,
        context: PlanningContext,
        model_plan: ModelEngineeringPlanOutput,
        model_execution_ids: list[str],
        blockers: list[PlanningBlocker],
        validation_warnings: list[dict],
        planner_version: str,
        created_by: str,
        supersedes_plan_id: str | None = None,
        supersession_reason: str | None = None,
    ) -> EngineeringPlan:
        version = self._next_version(context.task_id)
        plan_id = next_id(self.session, "engineering_plan")
        row = EngineeringPlanORM(
            id=plan_id,
            task_id=context.task_id,
            run_id=context.run_id,
            project_id=context.project_id,
            task_contract_id=context.task_contract_id,
            task_contract_version=context.task_contract_version,
            version=version,
            status=EngineeringPlanStatus.PROPOSED.value,
            summary=model_plan.summary,
            objective=model_plan.objective,
            risk_level=model_plan.risk_level.value,
            required_authority_level=model_plan.required_authority_level.value,
            repository_snapshot_ids=[item["snapshot_id"] for item in context.snapshot_metadata if item.get("snapshot_id")],
            evidence_ids=[item.evidence_id for item in context.evidence],
            memory_ids=[item.memory_id for item in context.memory],
            model_execution_ids=model_execution_ids,
            assumptions=[item.model_dump(mode="json") for item in model_plan.assumptions],
            unknowns=[item.model_dump(mode="json") for item in model_plan.unknowns],
            open_questions=[item.model_dump(mode="json") for item in model_plan.open_questions],
            affected_components=model_plan.affected_components,
            affected_files=[item.model_dump(mode="json") for item in model_plan.affected_files],
            steps=[item.model_dump(mode="json") for item in model_plan.steps],
            acceptance_coverage=[item.model_dump(mode="json") for item in model_plan.acceptance_coverage],
            test_strategy=[item.model_dump(mode="json") for item in model_plan.test_strategy],
            documentation_requirements=canonicalize_documentation_requirements(
                [item.model_dump(mode="json") for item in model_plan.documentation_requirements]
            ),
            rollback_considerations=model_plan.rollback_considerations,
            orchestration_contract_required=model_plan.orchestration_contract_required,
            orchestration_contract=model_plan.orchestration_contract,
            adversarial_probes=model_plan.adversarial_probes,
            dependencies=model_plan.dependencies,
            risks=[item.model_dump(mode="json") for item in model_plan.risks],
            estimated_scope=model_plan.estimated_scope,
            confidence=model_plan.confidence,
            validation_warnings=validation_warnings,
            blockers=[blocker.model_dump(mode="json") for blocker in blockers],
            planner_version=planner_version,
            supersedes_plan_id=supersedes_plan_id,
            supersession_reason=supersession_reason,
            created_by=created_by,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        self.session.add(row)
        if supersedes_plan_id:
            previous = self.session.get(EngineeringPlanORM, supersedes_plan_id)
            if previous:
                previous.status = EngineeringPlanStatus.SUPERSEDED.value
                previous.superseded_by_plan_id = plan_id
                previous.updated_at = utc_now()
        self.session.flush()
        return plan_from_row(row)

    def reject(self, plan_id: str, *, reason: str) -> EngineeringPlan:
        row = self._plan(plan_id)
        row.status = EngineeringPlanStatus.REJECTED.value
        row.supersession_reason = reason
        row.updated_at = utc_now()
        self.session.flush()
        return plan_from_row(row)

    def _next_version(self, task_id: str) -> int:
        latest = self.session.scalar(
            select(EngineeringPlanORM).where(EngineeringPlanORM.task_id == task_id).order_by(EngineeringPlanORM.version.desc()).limit(1)
        )
        return 1 if latest is None else latest.version + 1

    def _plan(self, plan_id: str) -> EngineeringPlanORM:
        row = self.session.get(EngineeringPlanORM, plan_id)
        if row is None:
            raise ValueError(f"Unknown engineering plan: {plan_id}")
        return row


def plan_from_row(row: EngineeringPlanORM) -> EngineeringPlan:
    return EngineeringPlan(
        id=row.id,
        task_id=row.task_id,
        run_id=row.run_id,
        project_id=row.project_id,
        task_contract_id=row.task_contract_id,
        task_contract_version=row.task_contract_version,
        version=row.version,
        status=row.status,
        summary=row.summary,
        objective=row.objective,
        risk_level=row.risk_level,
        required_authority_level=row.required_authority_level,
        repository_snapshot_ids=row.repository_snapshot_ids,
        evidence_ids=row.evidence_ids,
        memory_ids=row.memory_ids,
        model_execution_ids=row.model_execution_ids,
        assumptions=row.assumptions,
        unknowns=row.unknowns,
        open_questions=row.open_questions,
        affected_components=row.affected_components,
        affected_files=row.affected_files,
        steps=row.steps,
        acceptance_coverage=row.acceptance_coverage,
        test_strategy=row.test_strategy,
        documentation_requirements=canonicalize_documentation_requirements(row.documentation_requirements),
        rollback_considerations=row.rollback_considerations,
        orchestration_contract_required=row.orchestration_contract_required,
        orchestration_contract=row.orchestration_contract,
        adversarial_probes=row.adversarial_probes,
        dependencies=row.dependencies,
        risks=row.risks,
        estimated_scope=row.estimated_scope,
        confidence=row.confidence,
        validation_warnings=row.validation_warnings,
        blockers=row.blockers,
        planner_version=row.planner_version,
        supersedes_plan_id=row.supersedes_plan_id,
        superseded_by_plan_id=row.superseded_by_plan_id,
        supersession_reason=row.supersession_reason,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
