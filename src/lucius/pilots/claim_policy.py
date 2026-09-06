from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from lucius.pilots.provenance import (
    find_verified_without_semantic_evidence,
    is_verified_claim,
)


UNSUPPORTED_VERIFIED_PLAN_CLAIM = "UNSUPPORTED_VERIFIED_PLAN_CLAIM"


@dataclass(frozen=True)
class VerifiedPlanClaimIssue:
    code: str
    field: str
    statement: str
    original_classification: str
    evidence_ids: list[str]
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "field": self.field,
            "statement": self.statement,
            "original_classification": self.original_classification,
            "evidence_ids": self.evidence_ids,
            "reason": self.reason,
        }


def verified_plan_claim_issues(
    plan_payload: dict[str, Any],
    *,
    session: Session,
    require_current: bool = True,
) -> list[VerifiedPlanClaimIssue]:
    """Find VERIFIED/CONFIRMED/PROVEN plan claims lacking semantic support."""

    issues: list[VerifiedPlanClaimIssue] = []
    for field in ("assumptions", "risks", "validation_warnings", "blockers"):
        claims = [claim for claim in plan_payload.get(field, []) if isinstance(claim, dict) and is_verified_claim(claim)]
        unsupported = find_verified_without_semantic_evidence(
            claims,
            session=session,
            allowed_types={"evidence_reference"},
            require_current=require_current,
        )
        for claim in unsupported:
            issues.append(
                VerifiedPlanClaimIssue(
                    code=UNSUPPORTED_VERIFIED_PLAN_CLAIM,
                    field=field,
                    statement=str(claim.get("statement") or claim.get("risk") or claim.get("message") or claim),
                    original_classification=_classification(claim),
                    evidence_ids=[str(item) for item in claim.get("evidence_ids", [])],
                    reason="; ".join(claim.get("evidence_validation", [])) or "semantic evidence validation failed",
                )
            )
    return issues


def _classification(claim: dict[str, Any]) -> str:
    if claim.get("verified") is True:
        return "VERIFIED"
    return str(claim.get("status") or claim.get("claim_status") or "VERIFIED").upper()
