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
from lucius.pilots.dependency_policy import package_dependency_change_expected
from lucius.pilots.documentation_contract import canonicalize_documentation_requirements
from lucius.pilots.provenance import (
    find_verified_without_semantic_evidence,
    verified_claim_disposition_passes,
)
from lucius.pilots.schemas import EngineeringPlanEvaluation, EvaluationDimension, PlanCorrection


REQUIRED_ORCHESTRATION_EVIDENCE_FIELDS = {
    "participating_workflows",
    "participating_projects",
    "pre_mutation_release",
    "scheduler_decisions",
    "blockers_capacity_release",
    "checkpoints_resumes",
    "priority",
    "no_preemption",
    "exact_selection_mutation",
    "dependency_isolation",
    "fresh_process_reconstruction",
    "target_isolation",
    "provenance_refs",
    "tests",
    "documentation",
    "authority",
    "closure_claims",
}


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
        effective_mode = _effective_evaluation_mode(plan, evaluation_mode)
        dimensions = (
            _planning_only_dimensions(plan, artifact, self.session)
            if effective_mode == EngineeringPlanEvaluationMode.PLANNING_ONLY
            else _orchestration_control_plane_dimensions(plan, artifact, self.session)
            if effective_mode == EngineeringPlanEvaluationMode.ORCHESTRATION_CONTROL_PLANE
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
            evaluation_mode=effective_mode.value,
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
                "evaluation_mode": effective_mode.value,
                "supersedes_evaluation_id": supersedes_evaluation_id,
                "aggregate_score": aggregate,
            },
        )
        return EngineeringPlanEvaluation(
            id=row.id,
            plan_freeze_id=row.plan_freeze_id,
            supersedes_evaluation_id=row.supersedes_evaluation_id,
            evaluation_mode=effective_mode,
            result=result,
            aggregate_score=row.aggregate_score,
            dimensions=dimensions,
            corrections=corrections,
            implementation_artifact=row.implementation_artifact,
            evaluator_version=row.evaluator_version,
            evaluated_at=row.evaluated_at,
            evaluated_by=row.evaluated_by,
        )


def _effective_evaluation_mode(
    plan: dict,
    requested_mode: EngineeringPlanEvaluationMode,
) -> EngineeringPlanEvaluationMode:
    if requested_mode == EngineeringPlanEvaluationMode.PLANNING_ONLY:
        return requested_mode
    if _requires_orchestration_evaluation(plan):
        return EngineeringPlanEvaluationMode.ORCHESTRATION_CONTROL_PLANE
    return requested_mode


def _requires_orchestration_evaluation(plan: dict) -> bool:
    if plan.get("orchestration_contract_required") is True:
        return True
    if plan.get("orchestration_contract") or plan.get("adversarial_probes"):
        return True
    components = " ".join(str(item).lower() for item in plan.get("affected_components", []))
    return "orchestration" in components or "control-plane" in components or "global queue" in components


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
        _file_path_prediction_dimension(plan, artifact),
        _schema_migration_implementation_dimension(plan, artifact),
        _presence_dimension("testing_strategy", artifact.get("tests", []), _plan_text(plan), ["test", "pytest", "regression"]),
        _documentation_strategy_dimension(plan, artifact),
        _dependency_implementation_dimension(plan, artifact),
        _classification_dimension(artifact, plan),
        _unnecessary_work_dimension(plan, artifact),
        _hallucinated_paths_dimension(artifact.get("known_paths", []), _plan_files(plan)),
        _unsupported_claims_dimension(plan, artifact, session),
        _missed_work_dimension(artifact.get("material_work", []), _plan_text(plan)),
    ]


def _orchestration_control_plane_dimensions(plan: dict, artifact: dict, session: Session) -> list[EvaluationDimension]:
    evidence = artifact.get("orchestration_evidence") or {}
    dimensions = [
        _orchestration_contract_dimension(plan),
        _orchestration_required_evidence_dimension(evidence),
        _orchestration_participants_dimension(plan, evidence),
        _orchestration_boolean_dimension(
            "pre_mutation_release",
            evidence.get("pre_mutation_release"),
            required_result="PASS",
            message="Pre-mutation freeze/release evidence was not captured.",
        ),
        _orchestration_scheduler_dimension(evidence),
        _orchestration_resume_dimension(evidence, session),
        _orchestration_boolean_dimension("priority_correctness", evidence.get("priority")),
        _orchestration_boolean_dimension("no_preemption", evidence.get("no_preemption")),
        _orchestration_boolean_dimension("exact_selection_mutation", evidence.get("exact_selection_mutation")),
        _orchestration_boolean_dimension("dependency_isolation", evidence.get("dependency_isolation")),
        _orchestration_boolean_dimension("fresh_process_reconstruction", evidence.get("fresh_process_reconstruction")),
        _orchestration_boolean_dimension("target_isolation", evidence.get("target_isolation")),
        _orchestration_provenance_dimension(evidence),
        _orchestration_boolean_dimension("authority_compliance", evidence.get("authority")),
        _orchestration_boolean_dimension("tests_documentation", _tests_documentation_evidence(evidence)),
        _orchestration_boolean_dimension("closure_claims", evidence.get("closure_claims")),
        _unsupported_claims_dimension(plan, artifact, session),
    ]
    if _plan_files(plan):
        dimensions.extend(_plan_vs_implementation_dimensions(plan, artifact, session))
    return dimensions


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


def _file_path_prediction_dimension(plan: dict, artifact: dict) -> EvaluationDimension:
    actual_files = _normalize_set(artifact.get("files", []))
    planned_files = _normalize_set(_plan_files(plan))
    if not actual_files:
        return _not_captured_dimension("file_path_prediction", "Implemented file evidence was not captured.")
    missing = actual_files - planned_files
    unnecessary = planned_files - actual_files
    doc_match = _documentation_match(plan, artifact)
    if doc_match["flexible_pass"]:
        exact_docs = doc_match["exact_paths"]
        missing = {path for path in missing if not (_is_doc_path(path) and path not in exact_docs)}
        unnecessary = {path for path in unnecessary if not (_is_doc_path(path) and path not in exact_docs)}
    recall = ((len(actual_files) - len(missing)) / len(actual_files)) * 100
    precision = 100 if not planned_files else ((len(planned_files) - len(unnecessary)) / len(planned_files)) * 100
    score = round((recall * 0.7) + (precision * 0.3), 2)
    return EvaluationDimension(
        name="file_path_prediction",
        status="PASS" if score >= 80 else "WARN" if score >= 60 else "FAIL",
        score=score,
        expected=sorted(actual_files),
        actual=sorted(planned_files),
        missing=sorted(missing),
        unnecessary=sorted(unnecessary),
    )


def _documentation_strategy_dimension(plan: dict, artifact: dict) -> EvaluationDimension:
    requirements = canonicalize_documentation_requirements(plan.get("documentation_requirements", []))
    actual_docs = _actual_documentation_paths(artifact)
    if not requirements:
        if actual_docs:
            return EvaluationDimension(name="documentation_strategy", status="PASS", score=100.0, actual=sorted(actual_docs))
        return _not_captured_dimension("documentation_strategy", "Documentation requirement evidence was not captured.")
    if not _has_semantic_documentation_requirements(requirements):
        return _presence_dimension(
            "documentation_strategy",
            actual_docs,
            _plan_text(plan),
            ["docs", "documentation"],
        )
    match = _documentation_match(plan, artifact)
    score = 100.0 if not match["missing"] else 0.0
    return EvaluationDimension(
        name="documentation_strategy",
        status="PASS" if score == 100 else "FAIL",
        score=score,
        expected=sorted(match["expected"]),
        actual=sorted(actual_docs),
        missing=sorted(match["missing"]),
        notes=None if score == 100 else "Documentation evidence did not satisfy planned exact/flexible targets.",
    )


def _has_semantic_documentation_requirements(requirements: list[dict]) -> bool:
    semantic_keys = {
        "target_type",
        "kind",
        "exact_path_required",
        "acceptable_paths",
        "acceptable_categories",
        "proposed_path",
        "canonical_target",
    }
    return any(any(key in requirement for key in semantic_keys) for requirement in requirements)


def _documentation_match(plan: dict, artifact: dict) -> dict[str, object]:
    actual_docs = _actual_documentation_paths(artifact)
    evidence = artifact.get("documentation_evidence") or {}
    canonical_targets = _normalize_set(evidence.get("canonical_targets", []))
    categories = _normalize_set(evidence.get("categories", []))
    missing: set[str] = set()
    expected: set[str] = set()
    exact_paths: set[str] = set()
    flexible_pass = False
    for requirement in canonicalize_documentation_requirements(plan.get("documentation_requirements", [])):
        target = str(requirement.get("target", "")).strip()
        proposed_path = str(requirement.get("proposed_path") or "").strip()
        target_type = str(requirement.get("target_type") or requirement.get("kind") or "").upper()
        exact_required = bool(requirement.get("exact_path_required")) or target_type in {"EXACT_PATH", "REQUIRED_PATH"}
        acceptable_paths = _normalize_set(requirement.get("acceptable_paths", []))
        acceptable_categories = _normalize_set(requirement.get("acceptable_categories", []))
        canonical_target = str(requirement.get("canonical_target") or "").strip()
        if proposed_path:
            expected.add(proposed_path)
        elif target:
            expected.add(target)
        if exact_required:
            required_path = proposed_path or target
            exact_paths.add(required_path)
            if required_path not in actual_docs:
                missing.add(required_path)
            continue
        if target_type in {"PROPOSED_PATH", "NEW_DOCUMENT"}:
            required_path = proposed_path or target
            if required_path not in actual_docs:
                missing.add(required_path)
            continue
        path_candidates = acceptable_paths | ({proposed_path} if proposed_path else set())
        target_candidates = {canonical_target, target} - {""}
        category_match = bool(acceptable_categories & categories) or any(
            _doc_path_category(path) in acceptable_categories for path in actual_docs
        )
        path_match = bool(path_candidates & actual_docs)
        target_match = bool(target_candidates & canonical_targets)
        if path_match or category_match or target_match:
            flexible_pass = True
            continue
        missing.add(target or proposed_path or canonical_target or "documentation target")
    return {
        "expected": expected,
        "missing": missing,
        "exact_paths": exact_paths,
        "flexible_pass": flexible_pass,
    }


def _actual_documentation_paths(artifact: dict) -> set[str]:
    paths = _normalize_set(artifact.get("documentation", []))
    paths.update(path for path in _normalize_set(artifact.get("files", [])) if _is_doc_path(path))
    evidence = artifact.get("documentation_evidence") or {}
    paths.update(_normalize_set(evidence.get("paths", [])))
    return paths


def _is_doc_path(path: str) -> bool:
    normalized = str(path).lower()
    return normalized.startswith("docs/") or normalized.endswith(".md") or normalized.endswith(".rst")


def _doc_path_category(path: str) -> str:
    parts = str(path).split("/")
    if len(parts) >= 2 and parts[0] == "docs":
        return parts[1]
    if path.lower().endswith((".md", ".rst")):
        return "documentation"
    return ""


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
    expected_change = package_dependency_change_expected(plan, artifact)
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


def _unnecessary_work_dimension(plan: dict, artifact: dict) -> EvaluationDimension:
    expected_set = _normalize_set(artifact.get("files", []))
    actual_set = _normalize_set(_plan_files(plan))
    unnecessary_set = actual_set - expected_set
    doc_match = _documentation_match(plan, artifact)
    if doc_match["flexible_pass"]:
        exact_docs = doc_match["exact_paths"]
        unnecessary_set = {
            path for path in unnecessary_set if not (_is_doc_path(path) and path not in exact_docs)
        }
    unnecessary = sorted(unnecessary_set)
    score = 100.0 if not unnecessary else max(0.0, 100.0 - len(unnecessary) * 15.0)
    return EvaluationDimension(
        name="unnecessary_work",
        status="PASS" if score >= 85 else "WARN" if score >= 60 else "FAIL",
        score=score,
        expected=sorted(expected_set),
        actual=sorted(actual_set),
        unnecessary=unnecessary,
    )


def _orchestration_contract_dimension(plan: dict) -> EvaluationDimension:
    contract = plan.get("orchestration_contract") or {}
    probes = plan.get("adversarial_probes") or []
    if not contract or not probes:
        return _not_captured_dimension(
            "orchestration_contract",
            "Orchestration contract/probe payload was not captured.",
        )
    return EvaluationDimension(
        name="orchestration_contract",
        status="PASS",
        score=100.0,
        expected=["orchestration_contract", "adversarial_probes"],
        actual=["orchestration_contract", "adversarial_probes"],
    )


def _orchestration_required_evidence_dimension(evidence: dict) -> EvaluationDimension:
    captured = set(evidence)
    missing = sorted(REQUIRED_ORCHESTRATION_EVIDENCE_FIELDS - captured)
    if missing:
        return _not_captured_dimension(
            "orchestration_required_evidence",
            f"Missing orchestration evidence fields: {', '.join(missing)}",
        )
    return EvaluationDimension(
        name="orchestration_required_evidence",
        status="PASS",
        score=100.0,
        expected=sorted(REQUIRED_ORCHESTRATION_EVIDENCE_FIELDS),
        actual=sorted(captured),
    )


def _orchestration_participants_dimension(plan: dict, evidence: dict) -> EvaluationDimension:
    contract = plan.get("orchestration_contract") or {}
    expected_workflows = _ids_from_contract(contract.get("workflows"))
    expected_projects = _ids_from_contract(contract.get("projects"))
    actual_workflows = _normalize_set(evidence.get("participating_workflows", []))
    actual_projects = _normalize_set(evidence.get("participating_projects", []))
    if not actual_workflows or not actual_projects:
        return _not_captured_dimension(
            "orchestration_participants",
            "Participating workflow/project evidence was not captured.",
        )
    missing = sorted((expected_workflows - actual_workflows) | (expected_projects - actual_projects))
    extra = sorted((actual_workflows - expected_workflows) | (actual_projects - expected_projects))
    ok = not missing and not extra
    return EvaluationDimension(
        name="orchestration_participants",
        status="PASS" if ok else "FAIL",
        score=100.0 if ok else 0.0,
        expected=sorted(expected_workflows | expected_projects),
        actual=sorted(actual_workflows | actual_projects),
        missing=missing,
        unnecessary=extra,
    )


def _ids_from_contract(values: object) -> set[str]:
    if isinstance(values, dict):
        return _normalize_set(values.keys())
    if isinstance(values, list):
        ids: list[str] = []
        for value in values:
            if isinstance(value, dict):
                ids.append(str(value.get("id") or value.get("workflow_id") or value.get("project_id") or ""))
            else:
                ids.append(str(value))
        return _normalize_set(ids)
    return _normalize_set([])


def _orchestration_boolean_dimension(
    name: str,
    value: object,
    *,
    required_result: str | None = None,
    message: str | None = None,
) -> EvaluationDimension:
    if value is None:
        return _not_captured_dimension(name, message or f"{name} evidence was not captured.")
    if isinstance(value, dict):
        result = str(value.get("result") or value.get("status") or "").upper()
        violations = value.get("violations") or []
        passed = result == (required_result or "PASS") and not violations
        actual = [f"result={result or 'UNKNOWN'}", *[str(item) for item in violations]]
    else:
        passed = bool(value)
        actual = [str(value)]
    return EvaluationDimension(
        name=name,
        status="PASS" if passed else "FAIL",
        score=100.0 if passed else 0.0,
        expected=[required_result or "PASS"],
        actual=actual,
    )


def _orchestration_scheduler_dimension(evidence: dict) -> EvaluationDimension:
    decisions = evidence.get("scheduler_decisions")
    if not decisions:
        return _not_captured_dimension("scheduler_decisions", "Scheduler decision evidence was not captured.")
    false_dispatch = [
        str(item.get("item_id") or item.get("selected_item_id") or index)
        for index, item in enumerate(decisions)
        if isinstance(item, dict) and item.get("mutation_identity_matches_selection") is not True
    ]
    return EvaluationDimension(
        name="scheduler_decisions",
        status="PASS" if not false_dispatch else "FAIL",
        score=100.0 if not false_dispatch else 0.0,
        expected=["all mutation_identity_matches_selection=True"],
        actual=[str(item) for item in decisions],
        missing=false_dispatch,
    )


def _orchestration_resume_dimension(evidence: dict, session: Session) -> EvaluationDimension:
    resumes = evidence.get("checkpoints_resumes")
    if not resumes:
        return _conditional_checkpoint_resume_dimension(evidence, session)
    invalid = [
        str(item.get("checkpoint_id") or index)
        for index, item in enumerate(resumes)
        if not isinstance(item, dict)
        or item.get("resolved") is not True
        or item.get("fresh_session_reconstruction") is not True
    ]
    capacity = evidence.get("blockers_capacity_release")
    capacity_dimension = _orchestration_boolean_dimension("blockers_capacity_release", capacity)
    if capacity_dimension.status != "PASS":
        invalid.append("blockers_capacity_release")
    return EvaluationDimension(
        name="checkpoint_resume_capacity",
        status="PASS" if not invalid else "FAIL",
        score=100.0 if not invalid else 0.0,
        expected=["resolved checkpoints", "fresh-session reconstruction", "capacity release"],
        actual=[*[str(item) for item in resumes], str(capacity)],
        missing=invalid,
    )


def _conditional_checkpoint_resume_dimension(evidence: dict, session: Session) -> EvaluationDimension:
    conditional = _conditional_capability_evidence(evidence, "checkpoint_resume_capacity")
    if not conditional:
        return _insufficient_conditional_dimension(
            ["FRESH_EVIDENCE_REQUIRED"],
            "Checkpoint/resume evidence was not captured.",
        )

    trigger_status = str(conditional.get("trigger_status") or "").upper()
    applicability = str(conditional.get("applicability") or "").upper()
    inherited = conditional.get("inherited_evidence") if isinstance(conditional.get("inherited_evidence"), dict) else {}
    inherited_status = str(inherited.get("status") or "").upper()
    fresh_evidence_status = str(conditional.get("fresh_evidence_status") or "").upper()

    if trigger_status != "NOT_TRIGGERED":
        return _insufficient_conditional_dimension(
            [trigger_status or "FRESH_EVIDENCE_REQUIRED"],
            "Checkpoint/resume was required because the trigger condition occurred or was uncertain.",
        )
    if conditional.get("artificially_suppressed") is not False:
        return _insufficient_conditional_dimension(
            ["NOT_TRIGGERED", "INSUFFICIENT_EVIDENCE"],
            "Conditional checkpoint/resume evidence must prove the trigger was not artificially avoided.",
        )
    if not str(conditional.get("not_triggered_reason") or "").strip():
        return _insufficient_conditional_dimension(
            ["NOT_TRIGGERED", "INSUFFICIENT_EVIDENCE"],
            "Conditional checkpoint/resume evidence must explain why the trigger did not occur.",
        )
    if applicability != "NOT_APPLICABLE_IN_THIS_RUN":
        return _insufficient_conditional_dimension(
            ["NOT_TRIGGERED", applicability or "INSUFFICIENT_EVIDENCE"],
            "Conditional checkpoint/resume evidence must explicitly classify run applicability.",
        )
    if fresh_evidence_status != "NOT_REQUIRED":
        return _insufficient_conditional_dimension(
            ["NOT_TRIGGERED", fresh_evidence_status or "FRESH_EVIDENCE_REQUIRED"],
            "Conditional checkpoint/resume evidence must explicitly show fresh evidence is not required.",
        )
    if inherited_status != "INHERITED_VALID_EVIDENCE":
        return _insufficient_conditional_dimension(
            ["NOT_TRIGGERED", inherited_status or "INSUFFICIENT_EVIDENCE"],
            "Inherited checkpoint/resume evidence was missing or not classified as valid.",
        )
    if str(inherited.get("recency") or "").upper() not in {"CURRENT", "RECENT_APPLICABLE"}:
        return _insufficient_conditional_dimension(
            ["NOT_TRIGGERED", "INHERITED_VALID_EVIDENCE", "INSUFFICIENT_EVIDENCE"],
            "Inherited checkpoint/resume evidence was not classified as sufficiently recent.",
        )
    if str(inherited.get("runtime_applicability") or "").upper() != "APPLICABLE":
        return _insufficient_conditional_dimension(
            ["NOT_TRIGGERED", "INHERITED_VALID_EVIDENCE", "INSUFFICIENT_EVIDENCE"],
            "Inherited checkpoint/resume evidence was not classified as applicable to the current runtime.",
        )
    if inherited.get("relevant_implementation_change_invalidates") is not False:
        return _insufficient_conditional_dimension(
            ["NOT_TRIGGERED", "INHERITED_VALID_EVIDENCE", "INSUFFICIENT_EVIDENCE"],
            "Inherited checkpoint/resume evidence was invalidated or did not prove absence of invalidating changes.",
        )
    validation = _validate_inherited_checkpoint_resume_evidence(inherited.get("evidence_refs", []), evidence, session)
    if validation:
        return _insufficient_conditional_dimension(
            ["NOT_TRIGGERED", "INHERITED_VALID_EVIDENCE", "INSUFFICIENT_EVIDENCE"],
            validation,
        )
    return EvaluationDimension(
        name="checkpoint_resume_capacity",
        status="NOT_APPLICABLE_IN_THIS_RUN",
        applicability=MetricApplicability.NOT_APPLICABLE,
        expected=["resolved checkpoints", "fresh-session reconstruction", "capacity release"],
        actual=["NOT_TRIGGERED", "INHERITED_VALID_EVIDENCE"],
        notes="Checkpoint/resume did not naturally trigger in this run; recent applicable canonical evidence demonstrates the capability.",
    )


def _conditional_capability_evidence(evidence: dict, capability: str) -> dict:
    capabilities = evidence.get("conditional_capabilities") or {}
    if isinstance(capabilities, dict):
        value = capabilities.get(capability)
        if isinstance(value, dict):
            return value
    return {}


def _insufficient_conditional_dimension(actual: list[str], message: str) -> EvaluationDimension:
    return EvaluationDimension(
        name="checkpoint_resume_capacity",
        status="INSUFFICIENT_EVIDENCE",
        applicability=MetricApplicability.NOT_CAPTURED,
        score=None,
        expected=[
            "NOT_TRIGGERED",
            "NOT_APPLICABLE_IN_THIS_RUN",
            "INHERITED_VALID_EVIDENCE",
        ],
        actual=actual,
        notes=message,
    )


def _validate_inherited_checkpoint_resume_evidence(
    evidence_refs: object,
    current_evidence: dict,
    session: Session,
) -> str | None:
    refs = _normalize_set(evidence_refs if isinstance(evidence_refs, list) else [])
    if not refs:
        return "Inherited checkpoint/resume evidence requires canonical evidence refs."
    provenance_refs = _normalize_set(current_evidence.get("provenance_refs", []))
    if not refs <= provenance_refs:
        return "Inherited checkpoint/resume evidence refs must also appear in orchestration provenance refs."
    for ref in sorted(refs):
        if not ref.startswith("LEVALPLAN_"):
            continue
        evaluation = session.get(EngineeringPlanEvaluationORM, ref)
        if evaluation is None:
            return f"Inherited checkpoint/resume evaluation ref was not found: {ref}"
        if evaluation.result != EngineeringPlanEvaluationResult.PASS.value:
            return f"Inherited checkpoint/resume evaluation ref is not PASS: {ref}"
        if evaluation.evaluation_mode != EngineeringPlanEvaluationMode.ORCHESTRATION_CONTROL_PLANE.value:
            return f"Inherited checkpoint/resume evidence is not an orchestration evaluation: {ref}"
        dimensions = evaluation.dimensions or []
        for dimension in dimensions:
            if dimension.get("name") == "checkpoint_resume_capacity" and dimension.get("status") == "PASS":
                return None
        return f"Inherited orchestration evaluation lacks passing checkpoint/resume dimension: {ref}"
    return "Inherited checkpoint/resume evidence requires a passing LEVALPLAN orchestration evaluation ref."


def _orchestration_provenance_dimension(evidence: dict) -> EvaluationDimension:
    refs = _normalize_set(evidence.get("provenance_refs", []))
    if not refs:
        return _not_captured_dimension("orchestration_provenance", "Semantic provenance refs were not captured.")
    unrelated = sorted(ref for ref in refs if not ref.startswith(("LPLAN_", "LFREEZE_", "LEVALPLAN_", "LBENCH_", "LAUDIT_", "LQCHK_", "LWORK_", "LRSTATE_")))
    return EvaluationDimension(
        name="orchestration_provenance",
        status="PASS" if not unrelated else "FAIL",
        score=100.0 if not unrelated else 0.0,
        expected=["canonical artifact refs"],
        actual=sorted(refs),
        unnecessary=unrelated,
    )


def _tests_documentation_evidence(evidence: dict) -> dict:
    tests = evidence.get("tests") or {}
    docs = evidence.get("documentation") or {}
    tests_pass = tests.get("result") == "PASS" if isinstance(tests, dict) else bool(tests)
    docs_pass = docs.get("result") == "PASS" if isinstance(docs, dict) else bool(docs)
    return {"result": "PASS" if tests_pass and docs_pass else "FAIL"}


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
    critical_dimensions = {
        "hallucinated_files_paths",
        "unsupported_claims",
        "scheduler_decisions",
        "orchestration_participants",
        "exact_selection_mutation",
        "dependency_isolation",
        "target_isolation",
        "authority_compliance",
    }
    for dimension in dimensions:
        if dimension.applicability == MetricApplicability.NOT_APPLICABLE:
            continue
        if dimension.applicability == MetricApplicability.NOT_CAPTURED:
            corrections.append(PlanCorrection(severity="MODERATE", dimension=dimension.name, message=dimension.notes or "Required evidence was not captured."))
        elif dimension.score is not None and dimension.score < 60:
            severity = "CRITICAL" if dimension.name in critical_dimensions else "MAJOR"
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
    critical_control_failure = any(item.severity == "CRITICAL" for item in corrections)
    if material_change_failure or critical_control_failure:
        return EngineeringPlanEvaluationResult.FAIL
    if aggregate < 60:
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
            " ".join(
                str(item.get("target", ""))
                for item in canonicalize_documentation_requirements(plan.get("documentation_requirements", []))
            ),
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
            " ".join(
                str(item.get("target", ""))
                for item in canonicalize_documentation_requirements(plan.get("documentation_requirements", []))
            ),
            " ".join(str(item) for item in plan.get("dependencies", [])),
        ]
    )


def _normalize_set(values: Iterable[str]) -> set[str]:
    return {str(value).strip() for value in values if str(value).strip()}
