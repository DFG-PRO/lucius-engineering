from __future__ import annotations

from collections.abc import Iterable
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, EngineeringPlanEvaluationResult
from lucius.persistence.orm import EngineeringPlanEvaluationORM, PlanFreezeORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.schemas import EngineeringPlanEvaluation, EvaluationDimension, PlanCorrection


class EngineeringPlanEvaluationService:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def evaluate(
        self,
        *,
        plan_freeze_id: str,
        implementation_artifact: dict,
        evaluator_version: str = "1.12.0",
        actor: Actor = Actor.SYSTEM,
    ) -> EngineeringPlanEvaluation:
        freeze = self.session.get(PlanFreezeORM, plan_freeze_id)
        if freeze is None:
            raise ValueError(f"Unknown PlanFreeze: {plan_freeze_id}")
        plan = freeze.plan_payload
        dimensions = [
            _set_dimension("architecture_alignment", implementation_artifact.get("architecture", []), _plan_terms(plan)),
            _set_dimension("component_coverage", implementation_artifact.get("components", []), plan.get("affected_components", [])),
            _set_dimension("file_path_prediction", implementation_artifact.get("files", []), _plan_files(plan)),
            _presence_dimension("schema_migration_awareness", implementation_artifact.get("migrations", []), _plan_text(plan), ["migration", "schema", "alembic"]),
            _presence_dimension("testing_strategy", implementation_artifact.get("tests", []), _plan_text(plan), ["test", "pytest", "regression"]),
            _presence_dimension("documentation_strategy", implementation_artifact.get("documentation", []), _plan_text(plan), ["docs", "documentation"]),
            _set_dimension("dependency_awareness", implementation_artifact.get("dependencies", []), plan.get("dependencies", [])),
            _classification_dimension(implementation_artifact, plan),
            _unnecessary_work_dimension(implementation_artifact.get("files", []), _plan_files(plan)),
            _hallucinated_paths_dimension(implementation_artifact.get("known_paths", []), _plan_files(plan)),
            _unsupported_claims_dimension(plan),
            _missed_work_dimension(implementation_artifact.get("material_work", []), _plan_text(plan)),
        ]
        corrections = _corrections(dimensions)
        scores = [item.score for item in dimensions if item.score is not None]
        aggregate = round(sum(scores) / len(scores), 2) if scores else None
        result = _result(aggregate, corrections, dimensions)
        row = EngineeringPlanEvaluationORM(
            id=next_id(self.session, "plan_evaluation"),
            plan_freeze_id=freeze.id,
            result=result.value,
            aggregate_score=aggregate,
            dimensions=[item.model_dump(mode="json") for item in dimensions],
            corrections=[item.model_dump(mode="json") for item in corrections],
            implementation_artifact=implementation_artifact,
            evaluator_version=evaluator_version,
            evaluated_at=utc_now(),
            evaluated_by=actor.value,
        )
        self.session.add(row)
        self.session.flush()
        self.audit.record(
            event_type="ENGINEERING_PLAN_EVALUATED",
            actor=actor.value,
            project_id=freeze.project_id,
            task_id=freeze.task_id,
            action="evaluate_frozen_plan",
            result=result.value,
            metadata={"plan_evaluation_id": row.id, "plan_freeze_id": freeze.id, "aggregate_score": aggregate},
        )
        return EngineeringPlanEvaluation(
            id=row.id,
            plan_freeze_id=row.plan_freeze_id,
            result=result,
            aggregate_score=row.aggregate_score,
            dimensions=dimensions,
            corrections=corrections,
            implementation_artifact=row.implementation_artifact,
            evaluator_version=row.evaluator_version,
            evaluated_at=row.evaluated_at,
            evaluated_by=row.evaluated_by,
        )


def _set_dimension(name: str, expected: Iterable[str], actual: Iterable[str]) -> EvaluationDimension:
    expected_set = _normalize_set(expected)
    actual_set = _normalize_set(actual)
    if not expected_set:
        return EvaluationDimension(name=name, status="INSUFFICIENT_EVIDENCE", score=None)
    missing = sorted(expected_set - actual_set)
    unnecessary = sorted(actual_set - expected_set)
    recall = (len(expected_set & actual_set) / len(expected_set)) * 100
    precision = 100 if not actual_set else (len(expected_set & actual_set) / len(actual_set)) * 100
    score = round((recall * 0.7) + (precision * 0.3), 2)
    status = "PASS" if score >= 80 else "WARN" if score >= 60 else "FAIL"
    return EvaluationDimension(
        name=name,
        status=status,
        score=score,
        expected=sorted(expected_set),
        actual=sorted(actual_set),
        missing=missing,
        unnecessary=unnecessary,
    )


def _presence_dimension(name: str, expected: Iterable[str], plan_text: str, keywords: list[str]) -> EvaluationDimension:
    expected_set = _normalize_set(expected)
    if not expected_set:
        return EvaluationDimension(name=name, status="INSUFFICIENT_EVIDENCE", score=None)
    text = plan_text.lower()
    matched = [keyword for keyword in keywords if keyword in text]
    score = 100.0 if matched else 0.0
    return EvaluationDimension(
        name=name,
        status="PASS" if matched else "FAIL",
        score=score,
        expected=sorted(expected_set),
        actual=matched,
        missing=[] if matched else sorted(expected_set),
    )


def _classification_dimension(artifact: dict, plan: dict) -> EvaluationDimension:
    expected_risk = artifact.get("risk_level")
    expected_authority = artifact.get("required_authority_level")
    actual = [str(plan.get("risk_level")), str(plan.get("required_authority_level"))]
    expected = [value for value in [expected_risk, expected_authority] if value]
    if len(expected) < 2:
        return EvaluationDimension(name="authority_risk_classification", status="INSUFFICIENT_EVIDENCE", score=None)
    matches = int(plan.get("risk_level") == expected_risk) + int(plan.get("required_authority_level") == expected_authority)
    score = matches * 50.0
    return EvaluationDimension(
        name="authority_risk_classification",
        status="PASS" if score == 100 else "WARN" if score == 50 else "FAIL",
        score=score,
        expected=expected,
        actual=actual,
        missing=[] if score == 100 else expected,
    )


def _unnecessary_work_dimension(expected_files: Iterable[str], planned_files: Iterable[str]) -> EvaluationDimension:
    expected_set = _normalize_set(expected_files)
    actual_set = _normalize_set(planned_files)
    unnecessary = sorted(actual_set - expected_set)
    score = 100.0 if not unnecessary else max(0.0, 100.0 - len(unnecessary) * 15.0)
    return EvaluationDimension(
        name="unnecessary_work",
        status="PASS" if score >= 85 else "WARN" if score >= 60 else "FAIL",
        score=score,
        expected=sorted(expected_set),
        actual=sorted(actual_set),
        unnecessary=unnecessary,
    )


def _hallucinated_paths_dimension(known_paths: Iterable[str], planned_files: Iterable[str]) -> EvaluationDimension:
    known = _normalize_set(known_paths)
    planned = _normalize_set(planned_files)
    if not known:
        return EvaluationDimension(name="hallucinated_files_paths", status="INSUFFICIENT_EVIDENCE", score=None)
    hallucinated = sorted(path for path in planned if path not in known and not path.startswith("NEW:"))
    score = 100.0 if not hallucinated else 0.0
    return EvaluationDimension(
        name="hallucinated_files_paths",
        status="PASS" if not hallucinated else "FAIL",
        score=score,
        actual=sorted(planned),
        missing=hallucinated,
        notes="missing contains hallucinated paths for this dimension",
    )


def _unsupported_claims_dimension(plan: dict) -> EvaluationDimension:
    unsupported = [item for item in plan.get("assumptions", []) if item.get("verified")]
    score = 100.0 if not unsupported else 0.0
    return EvaluationDimension(
        name="unsupported_claims",
        status="PASS" if not unsupported else "FAIL",
        score=score,
        actual=[item.get("statement", "") for item in unsupported],
    )


def _missed_work_dimension(material_work: Iterable[str], plan_text: str) -> EvaluationDimension:
    expected = _normalize_set(material_work)
    if not expected:
        return EvaluationDimension(name="missed_material_implementation_work", status="INSUFFICIENT_EVIDENCE", score=None)
    text = plan_text.lower()
    missing = sorted(item for item in expected if item.lower() not in text)
    score = round(((len(expected) - len(missing)) / len(expected)) * 100, 2)
    return EvaluationDimension(
        name="missed_material_implementation_work",
        status="PASS" if score >= 80 else "WARN" if score >= 60 else "FAIL",
        score=score,
        expected=sorted(expected),
        missing=missing,
    )


def _corrections(dimensions: list[EvaluationDimension]) -> list[PlanCorrection]:
    corrections: list[PlanCorrection] = []
    for dimension in dimensions:
        if dimension.status == "INSUFFICIENT_EVIDENCE":
            corrections.append(PlanCorrection(severity="MODERATE", dimension=dimension.name, message="Required comparison evidence was not captured."))
        elif dimension.score is not None and dimension.score < 60:
            severity = "CRITICAL" if dimension.name in {"hallucinated_files_paths", "unsupported_claims"} else "MAJOR"
            corrections.append(PlanCorrection(severity=severity, dimension=dimension.name, message=f"Dimension scored {dimension.score}."))
        elif dimension.score is not None and dimension.score < 80:
            corrections.append(PlanCorrection(severity="MODERATE", dimension=dimension.name, message=f"Dimension scored {dimension.score}."))
    return corrections


def _result(
    aggregate: float | None,
    corrections: list[PlanCorrection],
    dimensions: list[EvaluationDimension],
) -> EngineeringPlanEvaluationResult:
    if aggregate is None or any(item.status == "INSUFFICIENT_EVIDENCE" for item in dimensions):
        return EngineeringPlanEvaluationResult.INSUFFICIENT_EVIDENCE
    if any(item.severity == "CRITICAL" for item in corrections) or aggregate < 60:
        return EngineeringPlanEvaluationResult.FAIL
    if corrections or aggregate < 85:
        return EngineeringPlanEvaluationResult.PASS_WITH_WARNINGS
    return EngineeringPlanEvaluationResult.PASS


def _plan_files(plan: dict) -> list[str]:
    files = []
    for item in plan.get("affected_files", []):
        if isinstance(item, dict):
            files.append(str(item.get("path", "")))
        else:
            files.append(str(item))
    for step in plan.get("steps", []):
        files.extend(str(path) for path in step.get("affected_files", []))
    return list(dict.fromkeys(path for path in files if path))


def _plan_terms(plan: dict) -> list[str]:
    values = list(plan.get("affected_components", []))
    values.extend(str(step.get("title", "")) for step in plan.get("steps", []))
    values.extend(str(step.get("description", "")) for step in plan.get("steps", []))
    return values


def _plan_text(plan: dict) -> str:
    return " ".join(
        [
            str(plan.get("summary", "")),
            str(plan.get("objective", "")),
            " ".join(_plan_terms(plan)),
            " ".join(_plan_files(plan)),
            " ".join(str(item.get("description", "")) for item in plan.get("test_strategy", [])),
            " ".join(str(item.get("target", "")) for item in plan.get("documentation_requirements", [])),
            " ".join(str(item) for item in plan.get("dependencies", [])),
        ]
    )


def _normalize_set(values: Iterable[str]) -> set[str]:
    return {str(value).strip() for value in values if str(value).strip()}
