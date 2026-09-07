from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from lucius.domain.enums import PersistentWorkflowState, QueueWorkItemState
from lucius.persistence.orm import EngineeringPlanORM, EvidenceReferenceORM, RepositorySnapshotORM
from lucius.pilots.queue import NonBlockingQueueService


EXISTING_FILE_STATUSES = {"EXISTING_VERIFIED", "EXPECTED_EXISTING", "EXISTING"}
PROPOSED_NEW_FILE_STATUSES = {"PROPOSED_NEW", "NEW_PROPOSED", "NEW", "CREATE"}
UNKNOWN_FILE_STATUSES = {"UNKNOWN", "UNCERTAIN"}
REQUIRED_ORCHESTRATION_CONTRACT_FIELDS = {
    "projects",
    "repositories",
    "workflows",
    "queue_items",
    "freeze_ids",
    "baseline_policy",
    "start_order",
    "resume_rules",
    "artifact_refs",
    "evaluation_scope",
}
REQUIRED_ADVERSARIAL_PROBE_IDS = {
    "missing_overall_freeze",
    "stale_project_freeze",
    "unsupported_verified_claim",
    "invalid_dependency_semantics",
    "missing_evaluation_contract",
    "initial_eligibility_mismatch",
}


@dataclass(frozen=True)
class HardeningIssue:
    code: str
    message: str
    severity: str = "ERROR"
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "details": self.details,
        }


def validate_plan_file_states(session: Session, plan_or_payload: EngineeringPlanORM | dict[str, Any]) -> list[HardeningIssue]:
    payload = _plan_payload(plan_or_payload)
    snapshot_ids = [str(item) for item in payload.get("repository_snapshot_ids", []) if item]
    if not snapshot_ids:
        return []
    manifest_paths: dict[str, dict[str, Any]] = {}
    found_snapshots = 0
    for snapshot_id in snapshot_ids:
        snapshot = session.get(RepositorySnapshotORM, snapshot_id)
        if snapshot is not None:
            found_snapshots += 1
            manifest_paths.update(_snapshot_paths(snapshot))
    if found_snapshots == 0:
        return []

    issues: list[HardeningIssue] = []
    evidence_ids = set(payload.get("evidence_ids", []) or [])
    for file_claim in payload.get("affected_files", []) or []:
        path = _claim_path(file_claim)
        status = _claim_status(file_claim)
        normalized_path = _normalize_repo_path(path)
        if normalized_path is None:
            issues.append(
                HardeningIssue(
                    code="INVALID_PLAN_FILE_PATH",
                    message="Plan affected_files paths must be repository-relative paths.",
                    details={"path": path, "status": status},
                )
            )
            continue

        manifest_entry = manifest_paths.get(normalized_path)
        exists = manifest_entry is not None
        if status in EXISTING_FILE_STATUSES and not exists:
            issues.append(
                HardeningIssue(
                    code="PLAN_FILE_STATE_MISMATCH",
                    message="Plan marked a file as existing, but the frozen snapshot does not contain it.",
                    details={"path": normalized_path, "status": status, "snapshot_ids": snapshot_ids},
                )
            )
        if status in EXISTING_FILE_STATUSES and manifest_entry and _manifest_file_type(manifest_entry) == "directory":
            issues.append(
                HardeningIssue(
                    code="PLAN_FILE_STATE_MISMATCH",
                    message="Plan marked a directory as an existing file.",
                    details={"path": normalized_path, "status": status, "file_type": _manifest_file_type(manifest_entry)},
                )
            )
        if status == "EXISTING_VERIFIED" and exists:
            claim_evidence_ids = [str(item) for item in file_claim.get("evidence_ids", []) or []]
            if evidence_ids and not claim_evidence_ids:
                claim_evidence_ids = sorted(evidence_ids)
            if claim_evidence_ids and not _evidence_supports_snapshot(session, claim_evidence_ids, snapshot_ids):
                issues.append(
                    HardeningIssue(
                        code="STALE_OR_MISSING_FILE_STATE_EVIDENCE",
                        message="EXISTING_VERIFIED file claims must not rely only on evidence from a stale snapshot.",
                        details={"path": normalized_path, "evidence_ids": claim_evidence_ids, "snapshot_ids": snapshot_ids},
                    )
                )
        if status in PROPOSED_NEW_FILE_STATUSES | UNKNOWN_FILE_STATUSES:
            continue
    return issues


def simulate_pre_start_queue(session: Session, workflow_ids: list[str]) -> dict[str, Any]:
    queue = NonBlockingQueueService(session)
    status = queue.inspect_global(workflow_ids)
    selection = queue.select_global_next(workflow_ids)
    return {
        "selected_project_id": selection.selected_project_id,
        "selected_workflow_id": selection.selected_workflow_id,
        "selected_item_id": selection.selected_item_id,
        "selected_item_version": selection.selected_item_version,
        "eligible_item_ids": [item.item_id for item in status.ready + status.ready_to_resume],
        "running_item_ids": [item.item_id for item in status.running],
        "blocked_item_ids": [item.item_id for item in status.blocked],
        "lifecycle_excluded": [item.model_dump(mode="json") for item in status.lifecycle_excluded],
    }


def validate_initial_eligibility(
    session: Session,
    workflow_ids: list[str],
    expected: dict[str, Any],
) -> list[HardeningIssue]:
    observed = simulate_pre_start_queue(session, workflow_ids)
    issues: list[HardeningIssue] = []
    for key in ("selected_workflow_id", "selected_item_id"):
        if key in expected and expected[key] != observed.get(key):
            issues.append(
                HardeningIssue(
                    code="INITIAL_ELIGIBILITY_MISMATCH",
                    message="Pre-start queue selection did not match the expected eligible item.",
                    details={"field": key, "expected": expected[key], "observed": observed.get(key), "snapshot": observed},
                )
            )
    if "eligible_item_ids" in expected:
        expected_items = sorted(str(item) for item in expected["eligible_item_ids"])
        observed_items = sorted(str(item) for item in observed["eligible_item_ids"])
        if expected_items != observed_items:
            issues.append(
                HardeningIssue(
                    code="INITIAL_ELIGIBILITY_MISMATCH",
                    message="Pre-start eligible item set did not match expectation.",
                    details={"expected": expected_items, "observed": observed_items, "snapshot": observed},
                )
            )
    issues.extend(_ready_to_resume_checkpoint_issues(session, workflow_ids))
    return issues


def validate_uncertainty_fields(
    payload: dict[str, Any],
    *,
    require_uncertainty: bool = False,
    degraded_test_health: bool = False,
) -> list[HardeningIssue]:
    if not require_uncertainty and not degraded_test_health:
        return []
    uncertainty_values = [
        payload.get("assumptions", []),
        payload.get("unknowns", []),
        payload.get("open_questions", []),
        payload.get("validation_warnings", []),
        payload.get("risks", []),
    ]
    if payload.get("confidence") is not None:
        uncertainty_values.append([{"confidence": payload["confidence"]}])
    has_uncertainty = any(bool(value) for value in uncertainty_values)
    if require_uncertainty and not has_uncertainty:
        return [
            HardeningIssue(
                code="UNCERTAINTY_FIELD_REQUIRED",
                message="Extended operational plans must preserve explicit uncertainty rather than implying clean confidence.",
            )
        ]
    if degraded_test_health and not _contains_any(uncertainty_values, ("DEGRADED", "FIXTURE", "FLAKY", "BASELINE")):
        return [
            HardeningIssue(
                code="UNCERTAINTY_FIELD_REQUIRED",
                message="Plans made under degraded test health must explicitly preserve the degraded/fixture uncertainty.",
            )
        ]
    return []


def validate_orchestration_contract_completeness(payload: dict[str, Any]) -> list[HardeningIssue]:
    if not _requires_orchestration_contract(payload):
        return []
    contract = payload.get("orchestration_contract") or {}
    if not isinstance(contract, dict):
        contract = {}
    present = {key for key, value in contract.items() if value not in (None, "", [], {})}
    missing = sorted(REQUIRED_ORCHESTRATION_CONTRACT_FIELDS - present)
    if not missing:
        return []
    return [
        HardeningIssue(
            code="ORCHESTRATION_CONTRACT_INCOMPLETE",
            message="Extended multi-project orchestration requires an explicit complete contract.",
            details={"missing": missing},
        )
    ]


def validate_persisted_adversarial_probes(payload: dict[str, Any]) -> list[HardeningIssue]:
    if not _requires_orchestration_contract(payload):
        return []
    probes = payload.get("adversarial_probes") or []
    if not isinstance(probes, list):
        probes = []
    by_id = {str(probe.get("probe_id")): probe for probe in probes if isinstance(probe, dict) and probe.get("probe_id")}
    missing_probe_ids = sorted(REQUIRED_ADVERSARIAL_PROBE_IDS - set(by_id))
    issues: list[HardeningIssue] = []
    if missing_probe_ids:
        issues.append(
            HardeningIssue(
                code="ADVERSARIAL_PROBE_REQUIRED",
                message="Extended multi-project orchestration requires persisted adversarial probes.",
                details={"missing_probe_ids": missing_probe_ids},
            )
        )
    required_fields = {
        "probe_id",
        "invariant",
        "manipulated_condition",
        "expected_result",
        "observed_result",
        "result",
        "context",
        "relevant_artifact_refs",
    }
    for probe_id, probe in sorted(by_id.items()):
        missing_fields = sorted(field for field in required_fields if probe.get(field) in (None, "", [], {}))
        if missing_fields:
            issues.append(
                HardeningIssue(
                    code="ADVERSARIAL_PROBE_INCOMPLETE",
                    message="Persisted adversarial probes must include invariant, condition, result, and artifact context.",
                    details={"probe_id": probe_id, "missing": missing_fields},
                )
            )
    return issues


def _plan_payload(plan_or_payload: EngineeringPlanORM | dict[str, Any]) -> dict[str, Any]:
    if isinstance(plan_or_payload, dict):
        return plan_or_payload
    return {
        "repository_snapshot_ids": plan_or_payload.repository_snapshot_ids,
        "evidence_ids": plan_or_payload.evidence_ids,
        "affected_files": plan_or_payload.affected_files,
        "assumptions": plan_or_payload.assumptions,
        "unknowns": plan_or_payload.unknowns,
        "open_questions": plan_or_payload.open_questions,
        "validation_warnings": plan_or_payload.validation_warnings,
        "risks": plan_or_payload.risks,
        "confidence": plan_or_payload.confidence,
    }


def _snapshot_paths(snapshot: RepositorySnapshotORM) -> dict[str, dict[str, Any]]:
    raw_files = snapshot.manifest.get("files", []) if isinstance(snapshot.manifest, dict) else []
    paths: dict[str, dict[str, Any]] = {}
    if isinstance(raw_files, dict):
        raw_files = [{"path": path, **(value if isinstance(value, dict) else {})} for path, value in raw_files.items()]
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        path = _normalize_repo_path(str(item.get("path") or ""))
        if path is not None:
            paths[path] = item
    return paths


def _manifest_file_type(entry: dict[str, Any]) -> str:
    return str(entry.get("file_type") or entry.get("type") or "file").lower()


def _claim_path(file_claim: dict[str, Any]) -> str:
    return str(file_claim.get("path") or file_claim.get("file") or file_claim.get("filepath") or "")


def _claim_status(file_claim: dict[str, Any]) -> str:
    return str(file_claim.get("state") or file_claim.get("status") or "UNKNOWN").upper()


def _normalize_repo_path(path: str) -> str | None:
    if not path or path.startswith("/"):
        return None
    pure = PurePosixPath(path)
    if any(part in {"", ".", ".."} for part in pure.parts):
        return None
    return str(pure)


def _evidence_supports_snapshot(session: Session, evidence_ids: list[str], snapshot_ids: list[str]) -> bool:
    for evidence_id in evidence_ids:
        evidence = session.get(EvidenceReferenceORM, evidence_id)
        if evidence is None:
            continue
        if evidence.snapshot_id in snapshot_ids:
            return True
    return False


def _ready_to_resume_checkpoint_issues(session: Session, workflow_ids: list[str]) -> list[HardeningIssue]:
    from lucius.persistence.orm import PersistentWorkflowORM

    issues: list[HardeningIssue] = []
    for workflow_id in workflow_ids:
        workflow = session.get(PersistentWorkflowORM, workflow_id)
        if workflow is None:
            continue
        for item in workflow.task_backlog or []:
            state = str(item.get("state") or item.get("item_state") or "").upper()
            if state == QueueWorkItemState.READY_TO_RESUME.value and not item.get("current_block_checkpoint_id"):
                issues.append(
                    HardeningIssue(
                        code="READY_TO_RESUME_WITHOUT_CHECKPOINT",
                        message="READY_TO_RESUME items must retain their current block checkpoint id.",
                        details={"workflow_id": workflow_id, "item_id": item.get("item_id") or item.get("task_id")},
                    )
                )
    return issues


def _contains_any(values: list[Any], needles: tuple[str, ...]) -> bool:
    text = repr(values).upper()
    return any(needle in text for needle in needles)


def _requires_orchestration_contract(payload: dict[str, Any]) -> bool:
    if payload.get("orchestration_contract_required") is True:
        return True
    for warning in payload.get("validation_warnings", []) or []:
        if not isinstance(warning, dict):
            continue
        code = str(warning.get("code") or "").upper()
        if code in {"ORCHESTRATION_CONTRACT_REQUIRED", "EXTENDED_OPERATIONAL_ORCHESTRATION_CONTRACT"}:
            return True
    return False
