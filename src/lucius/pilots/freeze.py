from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, PlanningEvidenceMode
from lucius.persistence.orm import EngineeringPlanORM, PlanFreezeORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.documentation_contract import canonicalize_documentation_requirements
from lucius.pilots.claim_policy import verified_plan_claim_issues
from lucius.pilots.hardening import (
    validate_orchestration_contract_completeness,
    validate_persisted_adversarial_probes,
    validate_plan_file_states,
    validate_uncertainty_fields,
)
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
        deterministic_acceptance_issues = _deterministic_acceptance_check_issues(payload)
        if deterministic_acceptance_issues:
            self.audit.record(
                event_type="ENGINEERING_PLAN_FREEZE_BLOCKED",
                actor=actor.value,
                project_id=plan.project_id,
                task_id=plan.task_id,
                action="freeze_engineering_plan",
                result="INVALID_DETERMINISTIC_ACCEPTANCE_CHECK",
                metadata={
                    "plan_id": plan.id,
                    "issues": deterministic_acceptance_issues,
                },
            )
            raise PlanFreezeSemanticError(
                f"INVALID_DETERMINISTIC_ACCEPTANCE_CHECK: {deterministic_acceptance_issues}"
            )

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
        hardening_issues = [
            *validate_plan_file_states(self.session, payload),
            *validate_uncertainty_fields(payload, require_uncertainty=_requires_orchestration_contract(payload)),
            *validate_orchestration_contract_completeness(payload),
            *validate_persisted_adversarial_probes(payload),
        ]
        if hardening_issues:
            details = [issue.as_dict() for issue in hardening_issues]
            result = hardening_issues[0].code
            self.audit.record(
                event_type="ENGINEERING_PLAN_FREEZE_BLOCKED",
                actor=actor.value,
                project_id=plan.project_id,
                task_id=plan.task_id,
                action="freeze_engineering_plan",
                result=result,
                metadata={"plan_id": plan.id, "issues": details},
            )
            raise PlanFreezeSemanticError(f"{result}: {details}")
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
        "deterministic_acceptance_checks": plan.deterministic_acceptance_checks,
        "test_strategy": plan.test_strategy,
        "documentation_requirements": canonicalize_documentation_requirements(plan.documentation_requirements),
        "rollback_considerations": plan.rollback_considerations,
        "orchestration_contract_required": plan.orchestration_contract_required,
        "orchestration_contract": plan.orchestration_contract,
        "adversarial_probes": plan.adversarial_probes,
        "dependencies": plan.dependencies,
        "risks": plan.risks,
        "validation_warnings": plan.validation_warnings,
        "blockers": plan.blockers,
        "planner_version": plan.planner_version,
    }


def _deterministic_acceptance_check_issues(payload: dict) -> list[dict]:
    issues: list[dict] = []

    raw_affected_files = payload.get("affected_files", [])
    if not isinstance(raw_affected_files, list):
        return [
            {
                "code": "INVALID_AFFECTED_FILES",
                "message": "affected_files must be a list.",
            }
        ]

    affected_paths: set[str] = set()
    for item in raw_affected_files:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if isinstance(path, str) and path:
            affected_paths.add(path)

    checks = payload.get("deterministic_acceptance_checks", [])
    if not isinstance(checks, list):
        return [
            {
                "code": "INVALID_DETERMINISTIC_ACCEPTANCE_CHECKS",
                "message": "deterministic_acceptance_checks must be a list.",
            }
        ]

    for index, check in enumerate(checks, start=1):
        if not isinstance(check, dict):
            issues.append(
                {
                    "code": "INVALID_DETERMINISTIC_ACCEPTANCE_CHECK",
                    "index": index,
                    "message": "Check must be an object.",
                }
            )
            continue

        check_type = check.get("type")
        path = check.get("path")
        expected_text = check.get("expected_text")

        if check_type != "exact_file_content":
            issues.append(
                {
                    "code": "UNSUPPORTED_DETERMINISTIC_ACCEPTANCE_CHECK",
                    "index": index,
                    "type": check_type,
                }
            )

        if (
            not isinstance(path, str)
            or not path
            or path.strip() != path
            or "\\" in path
            or path.startswith("/")
            or "." in path.split("/")
            or ".." in path.split("/")
        ):
            issues.append(
                {
                    "code": "INVALID_DETERMINISTIC_ACCEPTANCE_PATH",
                    "index": index,
                    "path": path,
                }
            )
        elif path not in affected_paths:
            issues.append(
                {
                    "code": "DETERMINISTIC_ACCEPTANCE_PATH_OUTSIDE_AFFECTED_FILES",
                    "index": index,
                    "path": path,
                }
            )

        if not isinstance(expected_text, str):
            issues.append(
                {
                    "code": "INVALID_DETERMINISTIC_ACCEPTANCE_EXPECTED_TEXT",
                    "index": index,
                    "path": path,
                }
            )

    return issues


def _requires_orchestration_contract(payload: dict) -> bool:
    if payload.get("orchestration_contract_required") is True:
        return True
    for warning in payload.get("validation_warnings", []) or []:
        if not isinstance(warning, dict):
            continue
        code = str(warning.get("code") or "").upper()
        if code in {"ORCHESTRATION_CONTRACT_REQUIRED", "EXTENDED_OPERATIONAL_ORCHESTRATION_CONTRACT"}:
            return True
    return False


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
