from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.persistence.orm import (
    BenchmarkResultORM,
    EngineeringPlanORM,
    EngineeringPlanEvaluationORM,
    EvidenceReferenceORM,
    HumanRubricORM,
    PilotEvaluationRecordORM,
    PilotLearningCandidateORM,
    PlanFreezeORM,
    PersistentWorkflowCheckpointORM,
    PersistentWorkflowORM,
    RepositorySnapshotORM,
    RepositoryStateObservationORM,
    ResumeValidationORM,
)


VERIFIED_STATUS_VALUES = {"VERIFIED", "CONFIRMED", "PROVEN"}
UNVERIFIED_STATUS_VALUES = {"ASSUMPTION", "UNVERIFIED", "SUPPLIED", "INFERRED"}
EVIDENCE_ID_PATTERN = re.compile(r"^[A-Z]+_\d{6}$")
ARTIFACT_PREFIX_TYPES = {
    "LEVID": "evidence_reference",
    "LBENCH": "benchmark_result",
    "LEVALPLAN": "engineering_plan_evaluation",
    "LPILOT": "pilot_record",
    "LRSTATE": "repository_state",
    "LSNAP": "repository_snapshot",
    "LRUBRIC": "human_rubric",
    "LPLEARN": "pilot_learning",
    "LPLAN": "engineering_plan",
    "LFREEZE": "plan_freeze",
    "LWCHK": "workflow_checkpoint",
    "LRESUME": "resume_validation",
    "LWORK": "persistent_workflow",
    "LQCHK": "queue_checkpoint",
}
PREFIX_MODELS = {
    "LEVID": EvidenceReferenceORM,
    "LBENCH": BenchmarkResultORM,
    "LEVALPLAN": EngineeringPlanEvaluationORM,
    "LPILOT": PilotEvaluationRecordORM,
    "LRSTATE": RepositoryStateObservationORM,
    "LSNAP": RepositorySnapshotORM,
    "LRUBRIC": HumanRubricORM,
    "LPLEARN": PilotLearningCandidateORM,
    "LPLAN": EngineeringPlanORM,
    "LFREEZE": PlanFreezeORM,
    "LWCHK": PersistentWorkflowCheckpointORM,
    "LRESUME": ResumeValidationORM,
    "LWORK": PersistentWorkflowORM,
}


@dataclass(frozen=True)
class EvidenceReferenceValidation:
    evidence_id: str | None
    valid: bool
    reason: str
    artifact_type: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClaimEvidenceValidation:
    valid: bool
    reason: str
    validations: list[EvidenceReferenceValidation] = field(default_factory=list)

    @property
    def diagnostics(self) -> list[str]:
        return [
            f"{item.evidence_id or '<missing>'}:{item.reason}"
            for item in self.validations
            if not item.valid
        ]


def has_traceable_evidence(claim: dict[str, Any]) -> bool:
    evidence_refs = (
        claim.get("evidence_ids")
        or claim.get("evidence_refs")
        or claim.get("evidence_artifact_ids")
    )
    evidence_id = claim.get("evidence_id") or claim.get("evidence_artifact_id")
    return bool(evidence_refs or evidence_id)


def is_verified_claim(claim: dict[str, Any]) -> bool:
    if claim.get("verified") is True:
        return True
    status = str(claim.get("status") or claim.get("claim_status") or "").upper()
    return status in VERIFIED_STATUS_VALUES


def find_verified_without_evidence(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [claim for claim in claims if is_verified_claim(claim) and not has_traceable_evidence(claim)]


def find_verified_without_semantic_evidence(
    claims: list[dict[str, Any]],
    *,
    session: Session,
    allowed_types: set[str] | None = None,
    required_context: dict[str, Any] | None = None,
    require_current: bool = False,
) -> list[dict[str, Any]]:
    unsupported = []
    for claim in claims:
        if not is_verified_claim(claim):
            continue
        validation = validate_claim_evidence(
            claim,
            session=session,
            allowed_types=allowed_types,
            required_context=required_context,
            require_current=require_current,
            expected_result=claim.get("observed_result"),
            expected_invariant=claim.get("expected_invariant") or claim.get("statement") or claim.get("claim"),
            test_probe_id=claim.get("test_probe_id"),
            claim_name=claim.get("claim_name"),
        )
        if not validation.valid:
            unsupported.append(claim | {"evidence_validation": validation.diagnostics or [validation.reason]})
    return unsupported


def has_linked_probe_evidence(claim: dict[str, Any]) -> bool:
    evidence_refs = (
        claim.get("evidence_refs")
        or claim.get("evidence_artifact_ids")
        or claim.get("evidence_ids")
    )
    evidence_id = claim.get("evidence_artifact_id") or claim.get("evidence_id")
    test_probe = claim.get("test_probe_id") or claim.get("test_probe_ids") or claim.get("persisted_result_id")
    invariant = claim.get("expected_invariant")
    observed = claim.get("observed_result") or claim.get("result")
    return bool(evidence_refs or evidence_id) and bool(test_probe) and bool(invariant) and observed == "PASS"


def has_semantic_linked_probe_evidence(
    claim: dict[str, Any],
    *,
    session: Session,
    allowed_types: set[str] | None = None,
    required_context: dict[str, Any] | None = None,
    require_current: bool = False,
    claim_name: str | None = None,
) -> bool:
    return validate_claim_evidence(
        claim,
        session=session,
        allowed_types=allowed_types,
        required_context=required_context,
        require_current=require_current,
        expected_result="PASS",
        expected_invariant=claim.get("expected_invariant"),
        test_probe_id=claim.get("test_probe_id") or claim.get("persisted_result_id"),
        claim_name=claim_name,
    ).valid


def validate_claim_evidence(
    claim: dict[str, Any],
    *,
    session: Session,
    allowed_types: set[str] | None = None,
    required_context: dict[str, Any] | None = None,
    require_current: bool = False,
    expected_result: str | None = None,
    expected_invariant: str | None = None,
    test_probe_id: str | None = None,
    claim_name: str | None = None,
) -> ClaimEvidenceValidation:
    evidence_ids = _evidence_ids_from_claim(claim)
    if not evidence_ids:
        return ClaimEvidenceValidation(
            valid=False,
            reason="EVIDENCE_PAYLOAD_INSUFFICIENT",
            validations=[
                EvidenceReferenceValidation(
                    evidence_id=None,
                    valid=False,
                    reason="EVIDENCE_PAYLOAD_INSUFFICIENT",
                )
            ],
        )
    validations = [
        validate_evidence_reference(
            evidence_id,
            session=session,
            allowed_types=allowed_types,
            required_context=required_context,
            require_current=require_current,
            expected_result=expected_result,
            expected_invariant=expected_invariant,
            test_probe_id=test_probe_id,
            claim_name=claim_name,
        )
        for evidence_id in evidence_ids
    ]
    invalid = [item for item in validations if not item.valid]
    if invalid:
        return ClaimEvidenceValidation(valid=False, reason=invalid[0].reason, validations=validations)
    return ClaimEvidenceValidation(valid=True, reason="VALID", validations=validations)


def validate_evidence_reference(
    evidence_id: Any,
    *,
    session: Session,
    allowed_types: set[str] | None = None,
    required_context: dict[str, Any] | None = None,
    require_current: bool = False,
    expected_result: str | None = None,
    expected_invariant: str | None = None,
    test_probe_id: str | None = None,
    claim_name: str | None = None,
) -> EvidenceReferenceValidation:
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        return EvidenceReferenceValidation(evidence_id=None, valid=False, reason="MALFORMED_EVIDENCE_ID")
    evidence_id = evidence_id.strip()
    if not EVIDENCE_ID_PATTERN.match(evidence_id):
        return EvidenceReferenceValidation(evidence_id=evidence_id, valid=False, reason="MALFORMED_EVIDENCE_ID")
    prefix = evidence_id.split("_", 1)[0]
    artifact_type = ARTIFACT_PREFIX_TYPES.get(prefix)
    if artifact_type is None:
        return EvidenceReferenceValidation(evidence_id=evidence_id, valid=False, reason="MALFORMED_EVIDENCE_ID")
    if allowed_types is not None and artifact_type not in allowed_types:
        return EvidenceReferenceValidation(
            evidence_id=evidence_id,
            valid=False,
            reason="EVIDENCE_TYPE_NOT_ALLOWED",
            artifact_type=artifact_type,
        )
    row = _lookup_artifact(session, evidence_id, prefix)
    if row is None:
        return EvidenceReferenceValidation(
            evidence_id=evidence_id,
            valid=False,
            reason="EVIDENCE_NOT_FOUND",
            artifact_type=artifact_type,
        )
    if require_current and _is_superseded(session, evidence_id, row):
        return EvidenceReferenceValidation(
            evidence_id=evidence_id,
            valid=False,
            reason="EVIDENCE_SUPERSEDED",
            artifact_type=artifact_type,
        )
    if required_context:
        context_error = _context_mismatch(row, required_context)
        if context_error is not None:
            return EvidenceReferenceValidation(
                evidence_id=evidence_id,
                valid=False,
                reason="EVIDENCE_CONTEXT_MISMATCH",
                artifact_type=artifact_type,
                details=context_error,
            )
    semantic_error = _semantic_support_error(
        row,
        artifact_type=artifact_type,
        claim_name=claim_name,
        expected_invariant=expected_invariant,
        test_probe_id=test_probe_id,
        expected_result=expected_result,
    )
    if semantic_error is not None:
        return EvidenceReferenceValidation(
            evidence_id=evidence_id,
            valid=False,
            reason=semantic_error,
            artifact_type=artifact_type,
        )
    return EvidenceReferenceValidation(
        evidence_id=evidence_id,
        valid=True,
        reason="VALID",
        artifact_type=artifact_type,
    )


def verified_claim_disposition_passes(
    disposition: dict[str, Any] | None,
    unsupported_claims: list[dict[str, Any]],
    *,
    session: Session | None = None,
    allowed_types: set[str] | None = None,
    required_context: dict[str, Any] | None = None,
    require_current: bool = False,
) -> bool:
    if not unsupported_claims:
        return True
    if not isinstance(disposition, dict) or disposition.get("result") != "PASS":
        return False
    if session is None:
        if not has_linked_probe_evidence(disposition):
            return False
    else:
        validation = validate_claim_evidence(
            disposition,
            session=session,
            allowed_types=allowed_types,
            required_context=required_context,
            require_current=require_current,
            expected_result="PASS",
            expected_invariant=disposition.get("expected_invariant"),
            test_probe_id=disposition.get("test_probe_id"),
            claim_name=disposition.get("claim_name"),
        )
        if not validation.valid:
            return False
    corrected = str(disposition.get("corrected_classification") or "").upper()
    if corrected not in UNVERIFIED_STATUS_VALUES:
        return False
    disposed = set(disposition.get("disposed_claims", []))
    required = {str(claim.get("statement") or claim.get("claim") or claim) for claim in unsupported_claims}
    return required.issubset(disposed)


def _evidence_ids_from_claim(claim: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("evidence_refs", "evidence_artifact_ids", "evidence_ids", "evidence_ref_ids"):
        raw = claim.get(key)
        if isinstance(raw, list):
            values.extend(raw)
        elif raw:
            values.append(raw)
    for key in ("evidence_artifact_id", "evidence_id", "evidence_ref_id"):
        raw = claim.get(key)
        if raw:
            values.append(raw)
    return [str(value) for value in values if value is not None and str(value).strip()]


def _lookup_artifact(session: Session, evidence_id: str, prefix: str) -> Any:
    if prefix == "LQCHK":
        return _lookup_queue_checkpoint(session, evidence_id)
    model = PREFIX_MODELS.get(prefix)
    if model is None:
        return None
    return session.get(model, evidence_id)


def _lookup_queue_checkpoint(session: Session, evidence_id: str) -> dict[str, Any] | None:
    for workflow in session.scalars(select(PersistentWorkflowORM)).all():
        for item in workflow.task_backlog or []:
            for checkpoint in item.get("block_checkpoints", []) or []:
                if checkpoint.get("id") == evidence_id:
                    return checkpoint | {"workflow_id": workflow.id, "project_id": workflow.project_id}
    return None


def _is_superseded(session: Session, evidence_id: str, row: Any) -> bool:
    if isinstance(row, EngineeringPlanEvaluationORM):
        return session.scalar(
            select(EngineeringPlanEvaluationORM.id)
            .where(EngineeringPlanEvaluationORM.supersedes_evaluation_id == evidence_id)
            .limit(1)
        ) is not None
    superseded_by = getattr(row, "superseded_by_plan_id", None) or getattr(row, "superseded_by_id", None)
    return bool(superseded_by)


def _context_mismatch(row: Any, required_context: dict[str, Any]) -> dict[str, Any] | None:
    for key, expected in required_context.items():
        if expected in (None, ""):
            continue
        actual = row.get(key) if isinstance(row, dict) else getattr(row, key, None)
        if actual != expected:
            return {"field": key, "expected": expected, "actual": actual}
    return None


def _semantic_support_error(
    row: Any,
    *,
    artifact_type: str,
    claim_name: str | None,
    expected_invariant: str | None,
    test_probe_id: str | None,
    expected_result: str | None,
) -> str | None:
    expected_result = str(expected_result or "").upper() or None
    observed = _artifact_observed_result(row, artifact_type)
    if expected_result == "PASS" and observed == "FAIL":
        return "EVIDENCE_RESULT_MISMATCH"
    text = _artifact_text(row, artifact_type)
    if claim_name and claim_name not in text:
        return "EVIDENCE_PAYLOAD_INSUFFICIENT"
    if expected_invariant and expected_invariant not in text:
        return "EVIDENCE_PAYLOAD_INSUFFICIENT"
    if test_probe_id and test_probe_id not in text:
        return "EVIDENCE_PAYLOAD_INSUFFICIENT"
    if expected_result and observed == "UNKNOWN" and not _text_contains_result(text, expected_result):
        return "EVIDENCE_RESULT_MISMATCH"
    if expected_result and observed != "UNKNOWN" and observed != expected_result:
        return "EVIDENCE_RESULT_MISMATCH"
    return None


def _artifact_observed_result(row: Any, artifact_type: str) -> str:
    if artifact_type == "benchmark_result":
        return "PASS" if row.status == "PASSED" and row.deterministic_metrics.get("hard_gate_status") == "PASS" else "FAIL"
    if artifact_type == "engineering_plan_evaluation":
        return "FAIL" if row.result in {"FAIL", "INSUFFICIENT_EVIDENCE"} else "PASS"
    if artifact_type == "pilot_record":
        return "PASS" if (row.gate_result or {}).get("passed") is True else "FAIL"
    if artifact_type == "repository_state":
        return "PASS" if row.classification == "CANONICAL_CLEAN" else "FAIL"
    if artifact_type == "repository_snapshot":
        return "PASS" if row.is_dirty is False else "FAIL"
    if artifact_type == "human_rubric":
        return "PASS" if row.status == "CAPTURED" else "FAIL"
    if artifact_type == "resume_validation":
        return "PASS" if row.result == "SAFE_TO_RESUME" else "FAIL"
    return "UNKNOWN"


def _artifact_text(row: Any, artifact_type: str) -> str:
    if isinstance(row, dict):
        return json.dumps(row, sort_keys=True)
    if artifact_type == "evidence_reference":
        return " ".join(
            str(value or "")
            for value in [row.source_type, row.path, row.claim, row.snippet, row.match_reasons]
        )
    payload: dict[str, Any] = {"id": getattr(row, "id", None)}
    for key in (
        "result",
        "status",
        "classification",
        "benchmark_version",
        "deterministic_metrics",
        "implementation_artifact",
        "dimensions",
        "corrections",
        "gate_result",
        "scores",
        "comments",
        "manifest_hash",
        "checkpoint_state",
        "latest_test_results",
        "known_warnings",
    ):
        if hasattr(row, key):
            payload[key] = getattr(row, key)
    return json.dumps(payload, sort_keys=True, default=str)


def _text_contains_result(text: str, expected_result: str) -> bool:
    return any(
        marker in text
        for marker in (
            f"observed_result={expected_result}",
            f"result={expected_result}",
            f'"observed_result": "{expected_result}"',
            f'"result": "{expected_result}"',
        )
    )
