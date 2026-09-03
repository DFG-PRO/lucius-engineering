from __future__ import annotations

from typing import Any


VERIFIED_STATUS_VALUES = {"VERIFIED", "CONFIRMED", "PROVEN"}
UNVERIFIED_STATUS_VALUES = {"ASSUMPTION", "UNVERIFIED", "SUPPLIED", "INFERRED"}


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


def verified_claim_disposition_passes(
    disposition: dict[str, Any] | None,
    unsupported_claims: list[dict[str, Any]],
) -> bool:
    if not unsupported_claims:
        return True
    if not isinstance(disposition, dict) or disposition.get("result") != "PASS":
        return False
    if not has_linked_probe_evidence(disposition):
        return False
    corrected = str(disposition.get("corrected_classification") or "").upper()
    if corrected not in UNVERIFIED_STATUS_VALUES:
        return False
    disposed = set(disposition.get("disposed_claims", []))
    required = {str(claim.get("statement") or claim.get("claim") or claim) for claim in unsupported_claims}
    return required.issubset(disposed)
