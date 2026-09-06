from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, PlanningEvidenceMode
from lucius.persistence.orm import EngineeringPlanORM, PlanFreezeORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.claim_policy import verified_plan_claim_issues
from lucius.pilots.schemas import PlanFreeze


class PlanFreezeSemanticError(ValueError):
    pass


class PlanFreezeService:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def freeze(
        self,
        *,
        plan_id: str,
        repository_state_id: str | None = None,
        planning_mode: PlanningEvidenceMode = PlanningEvidenceMode.CURRENT_STATE_PLANNING,
        evaluation_version: str = "1.12.0",
        actor: Actor = Actor.SYSTEM,
    ) -> PlanFreeze:
        existing = self.session.scalar(select(PlanFreezeORM).where(PlanFreezeORM.plan_id == plan_id))
        if existing:
            return _freeze_from_row(existing)
        plan = self.session.get(EngineeringPlanORM, plan_id)
        if plan is None:
            raise ValueError(f"Unknown EngineeringPlan: {plan_id}")
        payload = _plan_payload(plan)
        claim_issues = verified_plan_claim_issues(payload, session=self.session)
        if claim_issues:
            details = [issue.as_dict() for issue in claim_issues]
            self.audit.record(
                event_type="ENGINEERING_PLAN_FREEZE_BLOCKED",
                actor=actor.value,
                project_id=plan.project_id,
                task_id=plan.task_id,
                action="freeze_engineering_plan",
                result="UNSUPPORTED_VERIFIED_PLAN_CLAIM",
                metadata={"plan_id": plan.id, "issues": details},
            )
            raise PlanFreezeSemanticError(f"UNSUPPORTED_VERIFIED_PLAN_CLAIM: {details}")
        commit_sha = None
        if len(plan.repository_snapshot_ids) == 1:
            from lucius.persistence.orm import RepositorySnapshotORM

            snapshot = self.session.get(RepositorySnapshotORM, plan.repository_snapshot_ids[0])
            commit_sha = snapshot.commit_sha if snapshot else None
        row = PlanFreezeORM(
            id=next_id(self.session, "plan_freeze"),
            plan_id=plan.id,
            task_id=plan.task_id,
            project_id=plan.project_id,
            repository_state_id=repository_state_id,
            repository_snapshot_ids=list(plan.repository_snapshot_ids),
            evidence_ids=list(plan.evidence_ids),
            commit_sha=commit_sha,
            planning_mode=planning_mode.value,
            evaluation_version=evaluation_version,
            plan_payload=payload,
            frozen_at=utc_now(),
            frozen_by=actor.value,
        )
        self.session.add(row)
        self.session.flush()
        self.audit.record(
            event_type="ENGINEERING_PLAN_FROZEN",
            actor=actor.value,
            project_id=plan.project_id,
            task_id=plan.task_id,
            action="freeze_engineering_plan",
            result="SUCCESS",
            metadata={"plan_freeze_id": row.id, "plan_id": plan.id, "evaluation_version": evaluation_version},
        )
        return _freeze_from_row(row)


def _plan_payload(plan: EngineeringPlanORM) -> dict:
    return {
        "id": plan.id,
        "task_id": plan.task_id,
        "project_id": plan.project_id,
        "task_contract_id": plan.task_contract_id,
        "task_contract_version": plan.task_contract_version,
        "summary": plan.summary,
        "objective": plan.objective,
        "risk_level": plan.risk_level,
        "required_authority_level": plan.required_authority_level,
        "repository_snapshot_ids": plan.repository_snapshot_ids,
        "evidence_ids": plan.evidence_ids,
        "memory_ids": plan.memory_ids,
        "assumptions": plan.assumptions,
        "unknowns": plan.unknowns,
        "open_questions": plan.open_questions,
        "affected_components": plan.affected_components,
        "affected_files": plan.affected_files,
        "steps": plan.steps,
        "acceptance_coverage": plan.acceptance_coverage,
        "test_strategy": plan.test_strategy,
        "documentation_requirements": plan.documentation_requirements,
        "dependencies": plan.dependencies,
        "risks": plan.risks,
        "validation_warnings": plan.validation_warnings,
        "blockers": plan.blockers,
        "planner_version": plan.planner_version,
    }


def _freeze_from_row(row: PlanFreezeORM) -> PlanFreeze:
    return PlanFreeze(
        id=row.id,
        plan_id=row.plan_id,
        task_id=row.task_id,
        project_id=row.project_id,
        repository_state_id=row.repository_state_id,
        repository_snapshot_ids=row.repository_snapshot_ids,
        evidence_ids=row.evidence_ids,
        commit_sha=row.commit_sha,
        planning_mode=row.planning_mode,
        evaluation_version=row.evaluation_version,
        plan_payload=row.plan_payload,
        frozen_at=row.frozen_at,
        frozen_by=row.frozen_by,
    )
