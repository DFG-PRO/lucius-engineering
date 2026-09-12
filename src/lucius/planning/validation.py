from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from lucius.domain.enums import (
    AcceptanceCoverageStatus,
    AffectedFileStatus,
    AuthorityLevel,
    PlanRiskLevel,
    PlanningBlockerCode,
    PlanStepSupportStatus,
)
from lucius.persistence.orm import EvidenceReferenceORM, RepositorySnapshotORM
from lucius.planning.schemas import (
    AffectedFilePlan,
    ModelEngineeringPlanOutput,
    PlanningBlocker,
    PlanningContext,
)
from lucius.tasks.policy import AUTHORITY_RANK

RISK_RANK = {
    PlanRiskLevel.LOW: 0,
    PlanRiskLevel.MEDIUM: 1,
    PlanRiskLevel.HIGH: 2,
    PlanRiskLevel.CRITICAL: 3,
}

RISK_BY_RANK = {value: key for key, value in RISK_RANK.items()}


def validate_and_enrich_plan(
    session: Session,
    *,
    context: PlanningContext,
    plan: ModelEngineeringPlanOutput,
) -> tuple[ModelEngineeringPlanOutput, list[PlanningBlocker], list[dict[str, Any]]]:
    blockers: list[PlanningBlocker] = []
    warnings: list[dict[str, Any]] = []
    evidence_ids = {item.evidence_id for item in context.evidence}
    memory_ids = {item.memory_id for item in context.memory}
    step_ids = {step.step_id for step in plan.steps}
    for step in plan.steps:
        missing_evidence = sorted(set(step.required_evidence_ids) - evidence_ids)
        missing_memory = sorted(set(step.memory_ids) - memory_ids)
        if missing_evidence:
            blockers.append(
                PlanningBlocker(
                    code=PlanningBlockerCode.ENGINEERING_PLAN_INVALID,
                    message="Plan step references nonexistent or unauthorized evidence.",
                    metadata={"step_id": step.step_id, "evidence_ids": missing_evidence},
                )
            )
        if missing_memory:
            blockers.append(
                PlanningBlocker(
                    code=PlanningBlockerCode.ENGINEERING_PLAN_INVALID,
                    message="Plan step references nonexistent or unauthorized memory.",
                    metadata={"step_id": step.step_id, "memory_ids": missing_memory},
                )
            )
    for item in plan.acceptance_coverage:
        missing_steps = sorted(set(item.step_ids) - step_ids)
        missing_evidence = sorted(set(item.evidence_ids) - evidence_ids)
        if missing_steps or missing_evidence:
            blockers.append(
                PlanningBlocker(
                    code=PlanningBlockerCode.ENGINEERING_PLAN_INVALID,
                    message="Acceptance coverage references missing plan steps or evidence.",
                    metadata={
                        "criterion_id": item.criterion_id,
                        "step_ids": missing_steps,
                        "evidence_ids": missing_evidence,
                    },
                )
            )
    blockers.extend(_validate_reference_scope(session, context, evidence_ids))
    blockers.extend(_validate_acceptance_coverage(context, plan))
    for assumption in plan.assumptions:
        if assumption.verified:
            blockers.append(
                PlanningBlocker(
                    code=PlanningBlockerCode.ENGINEERING_PLAN_INVALID,
                    message="Assumptions must not be claimed as verified facts.",
                    metadata={"assumption": assumption.statement},
                )
            )
    plan.affected_files = _classify_affected_files(session, context, plan)
    blockers.extend(_validate_deterministic_acceptance_checks(plan))
    _classify_step_support(plan)
    deterministic_risk = _risk_floor(context, plan)
    if RISK_RANK[deterministic_risk] > RISK_RANK[plan.risk_level]:
        warnings.append(
            {
                "code": "RISK_FLOOR_ELEVATED",
                "model_risk": plan.risk_level.value,
                "deterministic_risk": deterministic_risk.value,
            }
        )
        plan.risk_level = deterministic_risk
    required_authority = _required_authority(context, plan)
    if AUTHORITY_RANK[required_authority] > AUTHORITY_RANK[plan.required_authority_level]:
        warnings.append(
            {
                "code": "AUTHORITY_FLOOR_ELEVATED",
                "model_authority": plan.required_authority_level.value,
                "deterministic_authority": required_authority.value,
            }
        )
        plan.required_authority_level = required_authority
    if AUTHORITY_RANK[plan.required_authority_level] > AUTHORITY_RANK[context.contract_authority_level]:
        blockers.append(
            PlanningBlocker(
                code=PlanningBlockerCode.AUTHORITY_ESCALATION_REQUIRED,
                message="Plan requires authority above TaskContract authority.",
                metadata={
                    "required_authority_level": plan.required_authority_level.value,
                    "contract_authority_level": context.contract_authority_level.value,
                },
            )
        )
    return plan, blockers, warnings


def _validate_deterministic_acceptance_checks(
    plan: ModelEngineeringPlanOutput,
) -> list[PlanningBlocker]:
    blockers: list[PlanningBlocker] = []
    affected_paths = {item.path for item in plan.affected_files}

    for index, check in enumerate(plan.deterministic_acceptance_checks, start=1):
        if check.type != "exact_file_content":
            blockers.append(
                PlanningBlocker(
                    code=PlanningBlockerCode.ENGINEERING_PLAN_INVALID,
                    message="Plan contains an unsupported deterministic acceptance check.",
                    metadata={"index": index, "type": check.type},
                )
            )
            continue

        if check.path not in affected_paths:
            blockers.append(
                PlanningBlocker(
                    code=PlanningBlockerCode.ENGINEERING_PLAN_INVALID,
                    message="Deterministic acceptance check references a file outside affected_files.",
                    metadata={"index": index, "path": check.path},
                )
            )

    return blockers


def _validate_reference_scope(
    session: Session,
    context: PlanningContext,
    evidence_ids: set[str],
) -> list[PlanningBlocker]:
    blockers: list[PlanningBlocker] = []
    for evidence_id in evidence_ids:
        row = session.get(EvidenceReferenceORM, evidence_id)
        if row is None or row.project_id != context.project_id:
            blockers.append(
                PlanningBlocker(
                    code=PlanningBlockerCode.ENGINEERING_PLAN_INVALID,
                    message="Plan references evidence outside the planning project.",
                    metadata={"evidence_id": evidence_id},
                )
            )
    return blockers


def _validate_acceptance_coverage(
    context: PlanningContext,
    plan: ModelEngineeringPlanOutput,
) -> list[PlanningBlocker]:
    blockers: list[PlanningBlocker] = []
    expected = {str(item.get("id")) for item in context.acceptance_criteria}
    actual = {item.criterion_id for item in plan.acceptance_coverage}
    missing = sorted(expected - actual)
    if missing:
        blockers.append(
            PlanningBlocker(
                code=PlanningBlockerCode.ENGINEERING_PLAN_INVALID,
                message="Plan omits required acceptance criteria coverage.",
                metadata={"criterion_ids": missing},
            )
        )
    uncovered = sorted(
        item.criterion_id
        for item in plan.acceptance_coverage
        if item.status in {AcceptanceCoverageStatus.NOT_COVERED, AcceptanceCoverageStatus.BLOCKED}
    )
    if uncovered:
        blockers.append(
            PlanningBlocker(
                code=PlanningBlockerCode.ENGINEERING_PLAN_INVALID,
                message="Plan leaves acceptance criteria uncovered or blocked.",
                metadata={"criterion_ids": uncovered},
            )
        )
    return blockers


def _classify_affected_files(
    session: Session,
    context: PlanningContext,
    plan: ModelEngineeringPlanOutput,
) -> list[AffectedFilePlan]:
    evidence_by_path = {}
    for item in context.evidence:
        evidence_by_path.setdefault(item.path, []).append(item.evidence_id)
    manifest_paths = _manifest_paths(session, context)
    by_path = {item.path: item for item in plan.affected_files}
    for step in plan.steps:
        for path in step.affected_files:
            by_path.setdefault(path, AffectedFilePlan(path=path))
    classified: list[AffectedFilePlan] = []
    for path in sorted(by_path):
        item = by_path[path]
        if not path or path.upper() == "UNKNOWN":
            item.status = AffectedFileStatus.UNKNOWN
        elif path in evidence_by_path:
            item.status = AffectedFileStatus.EXISTING_VERIFIED
            item.evidence_ids = sorted(set(item.evidence_ids + evidence_by_path[path]))
        elif path in manifest_paths:
            item.status = AffectedFileStatus.EXISTING_VERIFIED
        elif "/" in path or "." in path:
            item.status = AffectedFileStatus.NEW_PROPOSED
        else:
            item.status = AffectedFileStatus.UNKNOWN
        classified.append(item)
    return classified


def _manifest_paths(session: Session, context: PlanningContext) -> set[str]:
    paths: set[str] = set()
    for snapshot_meta in context.snapshot_metadata:
        snapshot_id = snapshot_meta.get("snapshot_id")
        snapshot = session.get(RepositorySnapshotORM, snapshot_id) if snapshot_id else None
        if not snapshot:
            continue
        for item in snapshot.manifest.get("files", []):
            path = item.get("path")
            if path:
                paths.add(path)
    return paths


def _classify_step_support(plan: ModelEngineeringPlanOutput) -> None:
    blocking_unknown = any(unknown.blocking for unknown in plan.unknowns)
    for step in plan.steps:
        if step.required_evidence_ids:
            step.support_status = PlanStepSupportStatus.SUPPORTED
        elif blocking_unknown:
            step.support_status = PlanStepSupportStatus.BLOCKED_BY_UNKNOWN
        else:
            step.support_status = PlanStepSupportStatus.INFERRED


def _risk_floor(context: PlanningContext, plan: ModelEngineeringPlanOutput) -> PlanRiskLevel:
    text = _plan_text(plan).lower()
    risk = PlanRiskLevel.LOW
    if context.task_complexity in {"T2", "T3"} or len(plan.affected_files) > 3 or any(unknown.blocking for unknown in plan.unknowns):
        risk = _max_risk(risk, PlanRiskLevel.MEDIUM)
    if "migration" in text or "schema" in text or "architecture" in text:
        risk = _max_risk(risk, PlanRiskLevel.HIGH)
    if "security" in text or "secret" in text or "credential" in text:
        risk = _max_risk(risk, PlanRiskLevel.HIGH)
    if context.environment.value == "PRODUCTION" or "production" in text or "destructive" in text or "irreversible" in text:
        risk = _max_risk(risk, PlanRiskLevel.CRITICAL)
    return risk


def _required_authority(context: PlanningContext, plan: ModelEngineeringPlanOutput) -> AuthorityLevel:
    text = _plan_text(plan).lower()
    required = context.contract_authority_level
    for step in plan.steps:
        if AUTHORITY_RANK[step.authority_level] > AUTHORITY_RANK[required]:
            required = step.authority_level
    if "migration" in text or "schema" in text or "security" in text or "credential" in text:
        required = _max_authority(required, AuthorityLevel.L2)
    if context.environment.value == "PRODUCTION" or "production" in text or "destructive" in text or "irreversible" in text:
        required = _max_authority(required, AuthorityLevel.L3)
    return required


def _plan_text(plan: ModelEngineeringPlanOutput) -> str:
    return " ".join(
        [
            plan.summary,
            plan.objective,
            " ".join(component for component in plan.affected_components),
            " ".join(step.title + " " + step.description for step in plan.steps),
            " ".join(risk.risk for risk in plan.risks),
            " ".join(doc.reason for doc in plan.documentation_requirements),
        ]
    )


def _max_risk(first: PlanRiskLevel, second: PlanRiskLevel) -> PlanRiskLevel:
    return RISK_BY_RANK[max(RISK_RANK[first], RISK_RANK[second])]


def _max_authority(first: AuthorityLevel, second: AuthorityLevel) -> AuthorityLevel:
    return first if AUTHORITY_RANK[first] >= AUTHORITY_RANK[second] else second
