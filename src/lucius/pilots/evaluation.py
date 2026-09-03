from __future__ import annotations

from collections.abc import Iterable
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import (
    Actor,
    EngineeringPlanEvaluationMode,
    EngineeringPlanEvaluationResult,
    ImplementationChangeEvidenceState,
    MetricApplicability,
)
from lucius.persistence.orm import EngineeringPlanEvaluationORM, PlanFreezeORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.provenance import (
    find_verified_without_semantic_evidence,
    verified_claim_disposition_passes,
)
from lucius.pilots.schemas import EngineeringPlanEvaluation, EvaluationDimension, PlanCorrection


class EngineeringPlanEvaluationService:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def evaluate(
        self,
        *,
        plan_freeze_id: str,
        implementation_artifact: dict | None = None,
        evaluation_mode: EngineeringPlanEvaluationMode = EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION,
        supersedes_evaluation_id: str | None = None,
        evaluator_version: str = "1.12.0",
        actor: Actor = Actor.SYSTEM,
    ) -> EngineeringPlanEvaluation:
        freeze = self.session.get(PlanFreezeORM, plan_freeze_id)
        if freeze is None:
            raise ValueError(f"Unknown PlanFreeze: {plan_freeze_id}")
        if supersedes_evaluation_id and self.session.get(EngineeringPlanEvaluationORM, supersedes_evaluation_id) is None:
            raise ValueError(f"Unknown EngineeringPlanEvaluation: {supersedes_evaluation_id}")
        artifact = implementation_artifact or {}
        plan = freeze.plan_payload
        dimensions = (
            _planning_only_dimensions(plan, artifact, self.session)
            if evaluation_mode == EngineeringPlanEvaluationMode.PLANNING_ONLY
            else _plan_vs_implementation_dimensions(plan, artifact, self.session)
        )
        corrections = _corrections(dimensions)
        scores = [item.score for item in dimensions if item.score is not None]
        aggregate = round(sum(scores) / len(scores), 2) if scores else None
        result = _result(aggregate, corrections, dimensions)
        row = EngineeringPlanEvaluationORM(
            id=next_id(self.session, "plan_evaluation"),
            plan_freeze_id=freeze.id,
            supersedes_evaluation_id=supersedes_evaluation_id,
            evaluation_mode=evaluation_mode.value,
            result=result.value,
            aggregate_score=aggregate,
            dimensions=[item.model_dump(mode="json") for item in dimensions],
            corrections=[item.model_dump(mode="json") for item in corrections],
            implementation_artifact=artifact,
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
            metadata={
                "plan_evaluation_id": row.id,
                "plan_freeze_id": freeze.id,
                "evaluation_mode": evaluation_mode.value,
                "supersedes_evaluation_id": supersedes_evaluation_id,
                "aggregate_score": aggregate,
            },
        )
        return EngineeringPlanEvaluation(
            id=row.id,
            plan_freeze_id=row.plan_freeze_id,
            supersedes_evaluation_id=row.supersedes_evaluation_id,
            evaluation_mode=evaluation_mode,
            result=result,
            aggregate_score=row.aggregate_score,
            dimensions=dimensions,
            corrections=corrections,
            implementation_artifact=row.implementation_artifact,
            evaluator_version=row.evaluator_version,
            evaluated_at=row.evaluated_at,
            evaluated_by=row.evaluated_by,
        )


def _planning_only_dimensions(plan: dict, artifact: dict, session: Session) -> list[EvaluationDimension]:
    plan_text = _plan_text(plan)
    return [
        _repository_understanding_dimension(plan),
        _set_dimension("architecture_alignment", artifact.get("architecture", []), _plan_terms(plan)),
        _set_dimension("component_coverage", artifact.get("components", []), plan.get("affected_components", [])),
        _planned_path_validity_dimension(artifact.get("known_paths", []), plan.get("affected_files", []), _plan_files(plan)),
        _schema_migration_reasoning_dimension(artifact.get("schema_migration_required"), plan_text, _plan_files(plan)),
        _dependency_reasoning_dimension(plan),
        _plan_presence_dimension("testing_strategy", plan.get("test_strategy", [])),
        _plan_presence_dimension("documentation_strategy", plan.get("documentation_requirements", [])),
        _classification_dimension(artifact, plan),
        _provenance_quality_dimension(plan, artifact),
        _hallucinated_paths_dimension(artifact.get("known_paths", []), _plan_files(plan)),
        _unsupported_claims_dimension(plan, artifact, session),
        _novelty_leakage_dimension(artifact),
        _uncertainty_handling_dimension(plan),
        _not_applicable_dimension("file_path_prediction", "Requires post-implementation file evidence."),
        _not_applicable_dimension("unnecessary_work", "Requires post-implementation file evidence."),
        _not_applicable_dimension("missed_material_implementation_work", "Requires post-implementation material-work evidence."),
        _not_applicable_dimension("actual_schema_migration_implementation", "Requires implemented migration evidence."),
        _not_applicable_dimension("actual_tests_added", "Requires implemented test evidence."),
        _not_applicable_dimension("actual_docs_added", "Requires implemented documentation evidence."),
    ]


def _plan_vs_implementation_dimensions(plan: dict, artifact: dict, session: Session) -> list[EvaluationDimension]:
    return [
        _set_dimension("architecture_alignment", artifact.get("architecture", []), _plan_terms(plan)),
        _set_dimension("component_coverage", artifact.get("components", []), plan.get("affected_components", [])),
        _set_dimension("file_path_prediction", artifact.get("files", []), _plan_files(plan)),
        _schema_migration_implementation_dimension(plan, artifact),
        _presence_dimension("testing_strategy", artifact.get("tests", []), _plan_text(plan), ["test", "pytest", "regression"]),
        _presence_dimension("documentation_strategy", artifact.get("documentation", []), _plan_text(plan), ["docs", "documentation"]),
        _dependency_implementation_dimension(plan, artifact),
        _classification_dimension(artifact, plan),
        _unnecessary_work_dimension(artifact.get("files", []), _plan_files(plan)),
        _hallucinated_paths_dimension(artifact.get("known_paths", []), _plan_files(plan)),
        _unsupported_claims_dimension(plan, artifact, session),
        _missed_work_dimension(artifact.get("material_work", []), _plan_text(plan)),
    ]


def _not_captured_dimension(name: str, message: str) -> EvaluationDimension:
    return EvaluationDimension(name=name, status="NOT_CAPTURED", applicability=MetricApplicability.NOT_CAPTURED, score=None, notes=message)


def _not_applicable_dimension(name: str, message: str) -> EvaluationDimension:
    return EvaluationDimension(name=name, status="NOT_APPLICABLE", applicability=MetricApplicability.NOT_APPLICABLE, score=None, notes=message)


def _set_dimension(name: str, expected: Iterable[str], actual: Iterable[str]) -> EvaluationDimension:
    expected_set = _normalize_set(expected)
    actual_set = _normalize_set(actual)
    if not expected_set:
        return _not_captured_dimension(name, "Required comparison evidence was not captured.")
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
        return _not_captured_dimension(name, "Required comparison evidence was not captured.")
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


def _schema_migration_implementation_dimension(plan: dict, artifact: dict) -> EvaluationDimension:
    expected_change = _plan_expects_schema_migration(plan)
    evidence = _change_evidence(artifact, "migrations")
    if evidence is not None:
        return _change_evidence_dimension(
            "schema_migration_awareness",
            expected_change=expected_change,
            evidence=evidence,
            change_label="schema migration",
        )
    actual = _normalize_set(artifact.get("migrations", []))
    if actual:
        return _change_evidence_dimension(
            "schema_migration_awareness",
            expected_change=expected_change,
            evidence={"state": ImplementationChangeEvidenceState.CHANGE_CONFIRMED.value, "changed_files": sorted(actual)},
            change_label="schema migration",
        )
    return _not_captured_dimension("schema_migration_awareness", "Schema/migration implementation evidence was not captured.")


def _dependency_implementation_dimension(plan: dict, artifact: dict) -> EvaluationDimension:
    expected_change = bool(_normalize_set(plan.get("dependencies", [])))
    evidence = _change_evidence(artifact, "dependencies")
    if evidence is not None:
        return _change_evidence_dimension(
            "dependency_awareness",
            expected_change=expected_change,
            evidence=evidence,
            change_label="dependency change",
        )
    actual = _normalize_set(artifact.get("dependencies", []))
    if actual:
        return _change_evidence_dimension(
            "dependency_awareness",
            expected_change=expected_change,
            evidence={"state": ImplementationChangeEvidenceState.CHANGE_CONFIRMED.value, "changed_files": sorted(actual)},
            change_label="dependency change",
        )
    return _not_captured_dimension("dependency_awareness", "Dependency implementation evidence was not captured.")


def _change_evidence(artifact: dict, key: str) -> dict | None:
    evidence = artifact.get("change_evidence", {}).get(key)
    if evidence is None:
        evidence = artifact.get("implementation_evidence_manifest", {}).get("verified_changes", {}).get(key)
    if evidence is None:
        evidence = artifact.get("implementation_evidence_manifest", {}).get("verified_absences", {}).get(key)
    if evidence is None:
        return None
    if isinstance(evidence, str):
        return {"state": evidence}
    if isinstance(evidence, dict):
        return evidence
    return {"state": ImplementationChangeEvidenceState.NOT_CAPTURED.value, "notes": "Change evidence had an unsupported shape."}


def _change_evidence_dimension(
    name: str,
    *,
    expected_change: bool,
    evidence: dict,
    change_label: str,
) -> EvaluationDimension:
    raw_state = str(evidence.get("state", ImplementationChangeEvidenceState.NOT_CAPTURED.value))
    try:
        state = ImplementationChangeEvidenceState(raw_state)
    except ValueError:
        return _not_captured_dimension(name, f"Unknown {change_label} evidence state: {raw_state}")
    changed_files = sorted(_normalize_set(evidence.get("changed_files", [])))
    verified_paths = sorted(_normalize_set(evidence.get("verified_paths", [])))
    actual = [f"state={state.value}", *changed_files, *[f"verified:{path}" for path in verified_paths]]
    expected = [f"expected_change={expected_change}"]
    if state is ImplementationChangeEvidenceState.NOT_CAPTURED:
        return _not_captured_dimension(name, evidence.get("notes") or f"{change_label} evidence was not captured.")
    if expected_change and state is ImplementationChangeEvidenceState.CHANGE_CONFIRMED:
        return EvaluationDimension(name=name, status="PASS", score=100.0, expected=expected, actual=actual)
    if not expected_change and state is ImplementationChangeEvidenceState.NO_CHANGE_CONFIRMED:
        return EvaluationDimension(name=name, status="PASS", score=100.0, expected=expected, actual=actual)
    if expected_change:
        return EvaluationDimension(
            name=name,
            status="FAIL",
            score=0.0,
            expected=[*expected, f"{change_label} implemented"],
            actual=actual,
            missing=[f"expected {change_label}"],
            notes=evidence.get("notes"),
        )
    return EvaluationDimension(
        name=name,
        status="FAIL",
        score=0.0,
        expected=[*expected, f"no {change_label}"],
        actual=actual,
        unnecessary=changed_files or [f"unexpected {change_label}"],
        notes=evidence.get("notes"),
    )


def _classification_dimension(artifact: dict, plan: dict) -> EvaluationDimension:
    expected_risk = artifact.get("risk_level")
    expected_authority = artifact.get("required_authority_level")
    actual = [str(plan.get("risk_level")), str(plan.get("required_authority_level"))]
    expected = [value for value in [expected_risk, expected_authority] if value]
    if len(expected) < 2:
        return _not_captured_dimension("authority_risk_classification", "Required risk/authority expectation was not captured.")
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
        return _not_captured_dimension("hallucinated_files_paths", "Repository manifest paths were not captured.")
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


def _unsupported_claims_dimension(plan: dict, artifact: dict | None, session: Session) -> EvaluationDimension:
    unsupported = find_verified_without_semantic_evidence(
        plan.get("assumptions", []),
        session=session,
        allowed_types={"evidence_reference"},
        require_current=True,
    )
    disposition = (artifact or {}).get("verified_claim_disposition") or plan.get("verified_claim_disposition")
    if verified_claim_disposition_passes(
        disposition,
        unsupported,
        session=session,
        allowed_types={"evidence_reference", "engineering_plan_evaluation"},
        require_current=True,
    ):
        return EvaluationDimension(
            name="unsupported_claims",
            status="PASS",
            score=100.0,
            actual=[str(item.get("statement", "")) for item in unsupported],
            notes="Unsupported verified claims were dispositioned with linked evidence.",
        )
    score = 100.0 if not unsupported else 0.0
    return EvaluationDimension(
        name="unsupported_claims",
        status="PASS" if not unsupported else "FAIL",
        score=score,
        actual=[item.get("statement", "") for item in unsupported],
        notes="; ".join(
            f"{item.get('statement', '')}: {', '.join(item.get('evidence_validation', []))}"
            for item in unsupported
        ) or None,
    )


def _missed_work_dimension(material_work: Iterable[str], plan_text: str) -> EvaluationDimension:
    expected = _normalize_set(material_work)
    if not expected:
        return _not_captured_dimension("missed_material_implementation_work", "Required implementation material-work evidence was not captured.")
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
        if dimension.applicability == MetricApplicability.NOT_APPLICABLE:
            continue
        if dimension.applicability == MetricApplicability.NOT_CAPTURED:
            corrections.append(PlanCorrection(severity="MODERATE", dimension=dimension.name, message=dimension.notes or "Required evidence was not captured."))
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
    if aggregate is None or any(item.applicability == MetricApplicability.NOT_CAPTURED for item in dimensions):
        return EngineeringPlanEvaluationResult.INSUFFICIENT_EVIDENCE
    material_change_failure = any(
        item.status == "FAIL"
        and item.name in {"schema_migration_awareness", "dependency_awareness"}
        for item in dimensions
    )
    if material_change_failure:
        return EngineeringPlanEvaluationResult.FAIL
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


def _repository_understanding_dimension(plan: dict) -> EvaluationDimension:
    has_evidence = bool(plan.get("evidence_ids"))
    has_components = bool(plan.get("affected_components"))
    if not has_evidence:
        return _not_captured_dimension("repository_understanding", "Planning evidence ids were not captured.")
    score = 100.0 if has_components else 70.0
    return EvaluationDimension(
        name="repository_understanding",
        status="PASS" if score >= 80 else "WARN",
        score=score,
        expected=["repository evidence", "affected components"],
        actual=["repository evidence", *(["affected components"] if has_components else [])],
        missing=[] if has_components else ["affected components"],
    )


def _planned_path_validity_dimension(known_paths: Iterable[str], affected_files: list, planned_files: Iterable[str]) -> EvaluationDimension:
    known = _normalize_set(known_paths)
    planned = _normalize_set(planned_files)
    if not known:
        return _not_captured_dimension("planned_path_validity", "Repository manifest paths were not captured.")
    proposed_new = {
        str(item.get("path", "")).strip()
        for item in affected_files
        if isinstance(item, dict) and str(item.get("status", "")).upper() in {"NEW_PROPOSED", "NEW", "CREATE"}
    }
    invalid = sorted(path for path in planned if path not in known and path not in proposed_new and not path.startswith("NEW:"))
    score = 100.0 if not invalid else 0.0
    return EvaluationDimension(
        name="planned_path_validity",
        status="PASS" if not invalid else "FAIL",
        score=score,
        actual=sorted(planned),
        missing=invalid,
        notes="missing contains invalid planned paths for this dimension",
    )


def _schema_migration_reasoning_dimension(required: bool | None, plan_text: str, planned_files: Iterable[str]) -> EvaluationDimension:
    if required is None:
        return _not_captured_dimension("schema_migration_reasoning", "Schema/migration expectation was not captured.")
    mentions_schema = any(word in plan_text.lower() for word in ["schema", "migration", "alembic"])
    plans_migration_file = any("alembic/versions" in path for path in planned_files)
    if required:
        score = 100.0 if mentions_schema or plans_migration_file else 0.0
    else:
        no_migration_reasoning = "no migration" in plan_text.lower() or not plans_migration_file
        score = 100.0 if no_migration_reasoning else 60.0
    return EvaluationDimension(
        name="schema_migration_reasoning",
        status="PASS" if score >= 80 else "WARN" if score >= 60 else "FAIL",
        score=score,
        expected=[f"migration_required={required}"],
        actual=[f"mentions_schema={mentions_schema}", f"plans_migration_file={plans_migration_file}"],
    )


def _plan_expects_schema_migration(plan: dict) -> bool:
    planned_files = _plan_files(plan)
    if any("alembic/versions" in path or "migrations/" in path.lower() for path in planned_files):
        return True
    text = " ".join(
        [
            str(plan.get("summary", "")),
            str(plan.get("objective", "")),
            " ".join(_plan_terms(plan)),
            " ".join(str(item.get("description", "")) for item in plan.get("test_strategy", [])),
            " ".join(str(item.get("target", "")) for item in plan.get("documentation_requirements", [])),
        ]
    ).lower()
    if (
        "no migration" in text
        or "without migration" in text
        or "no database schema" in text
        or "without database schema" in text
    ):
        return False
    return "migration" in text or "alembic" in text or "database schema" in text or "db schema" in text


def _dependency_reasoning_dimension(plan: dict) -> EvaluationDimension:
    dependencies = plan.get("dependencies", [])
    plan_text = _plan_text(plan).lower()
    score = 100.0 if dependencies == [] or "dependency" in plan_text or "dependencies" in plan_text else 70.0
    return EvaluationDimension(
        name="dependency_reasoning",
        status="PASS" if score >= 80 else "WARN",
        score=score,
        actual=[str(item) for item in dependencies] or ["no new dependencies"],
    )


def _plan_presence_dimension(name: str, values: Iterable[object]) -> EvaluationDimension:
    actual = [str(item) for item in values if str(item)]
    score = 100.0 if actual else 0.0
    return EvaluationDimension(name=name, status="PASS" if actual else "FAIL", score=score, actual=actual)


def _provenance_quality_dimension(plan: dict, artifact: dict) -> EvaluationDimension:
    evidence_ids = plan.get("evidence_ids", [])
    if not evidence_ids:
        return _not_captured_dimension("provenance_quality", "Evidence ids were not captured.")
    quality = str(artifact.get("provenance_quality", "SUPPORTED")).upper()
    if quality == "MIXED":
        return EvaluationDimension(name="provenance_quality", status="WARN", score=75.0, actual=list(evidence_ids), notes="Provenance is mixed.")
    if quality in {"DEFICIENT", "WEAK"}:
        return EvaluationDimension(name="provenance_quality", status="FAIL", score=40.0, actual=list(evidence_ids), notes="Provenance is deficient.")
    return EvaluationDimension(name="provenance_quality", status="PASS", score=100.0, actual=list(evidence_ids))


def _novelty_leakage_dimension(artifact: dict) -> EvaluationDimension:
    if "novelty_proven" not in artifact or "leakage_detected" not in artifact:
        return _not_captured_dimension("novelty_leakage_status", "Novelty/leakage audit evidence was not captured.")
    ok = bool(artifact.get("novelty_proven")) and not bool(artifact.get("leakage_detected"))
    return EvaluationDimension(
        name="novelty_leakage_status",
        status="PASS" if ok else "FAIL",
        score=100.0 if ok else 0.0,
        expected=["novelty proven", "no leakage"],
        actual=[f"novelty_proven={bool(artifact.get('novelty_proven'))}", f"leakage_detected={bool(artifact.get('leakage_detected'))}"],
    )


def _uncertainty_handling_dimension(plan: dict) -> EvaluationDimension:
    uncertainties = [*plan.get("unknowns", []), *plan.get("open_questions", [])]
    if uncertainties:
        return EvaluationDimension(name="explicit_uncertainty_handling", status="PASS", score=100.0, actual=[str(item) for item in uncertainties])
    return EvaluationDimension(name="explicit_uncertainty_handling", status="WARN", score=70.0, missing=["unknowns or open questions"])


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
