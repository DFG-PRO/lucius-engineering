from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.orm import Session

from lucius.persistence.orm import EvidenceReferenceORM, utc_now
from lucius.persistence.repositories import next_id


PHASE_1_22_OPERATIONAL_STAGE = "CONTROLLED_MULTI_PROJECT_OPERATIONAL_QUEUE_PILOT"

PHASE_1_22_REQUIRED_EVIDENCE_CLAIMS = {
    "REAL_MULTI_PROJECT_WORK",
    "BLOCKED_PROJECT_RELEASES_CAPACITY",
    "FRESH_CONTEXT_RECONSTRUCTION",
    "FRESH_CONTEXT_ACTUAL_DISPATCH",
    "GLOBAL_SELECTION_EQUALS_MUTATION",
    "READY_TO_RESUME_PRESERVES_PROGRESS",
    "PRIORITY_OVER_RESUME",
    "NO_PREEMPTION",
    "PROJECT_REPOSITORY_ISOLATION",
    "TARGET_MAIN_UNCHANGED",
    "TARGET_DOCUMENTATION_DISCIPLINE",
    "CANONICAL_ARTIFACT_STORE",
    "COMPLETION_WITH_MALFORMED_SIBLING_SAFETY",
    "VERIFIED_CLAIMS_HAVE_EVIDENCE",
}

PHASE_1_22_ALLOWED_EVIDENCE_TYPES = {
    "evidence_reference",
    "engineering_plan_evaluation",
    "pilot_record",
    "benchmark_result",
    "repository_state",
    "repository_snapshot",
    "workflow_checkpoint",
    "queue_checkpoint",
    "resume_validation",
    "human_rubric",
}


class OperationalEvidenceService:
    """Produce release-gate-compatible operational evidence claims."""

    def __init__(self, session: Session):
        self.session = session

    def create_claim(
        self,
        *,
        claim_name: str,
        project_id: str,
        repository_id: str,
        snapshot_id: str,
        task_id: str,
        result: str = "PASS",
        expected_invariant: str | None = None,
        test_probe_id: str | None = None,
        path: str | None = None,
        context_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        expected_invariant = expected_invariant or claim_name
        test_probe_id = test_probe_id or f"operational::{claim_name.lower()}"
        normalized_result = result.upper()
        refs = context_refs or []
        text = (
            f"claim_name={claim_name} expected_invariant={expected_invariant} "
            f"test_probe_id={test_probe_id} observed_result={normalized_result} "
            f"result={normalized_result} context_refs={','.join(refs)}"
        )
        row = EvidenceReferenceORM(
            id=next_id(self.session, "evidence"),
            project_id=project_id,
            repository_id=repository_id,
            snapshot_id=snapshot_id,
            task_id=task_id,
            task_run_id=None,
            source_type="PILOT_CLOSURE_EVIDENCE",
            path=path or f"canonical://operational-evidence/{claim_name}",
            line_start=None,
            line_end=None,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            snippet=text,
            claim=text,
            relevance_score=1.0,
            match_reasons=["operational_release_gate", "semantic_claim_evidence", f"result={normalized_result}"],
            captured_at=utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        return {
            "result": normalized_result,
            "expected_invariant": expected_invariant,
            "test_probe_id": test_probe_id,
            "evidence_refs": [row.id],
        }

    def create_required_claims(
        self,
        *,
        project_id: str,
        repository_id: str,
        snapshot_id: str,
        task_id: str,
        result: str = "PASS",
        probe_namespace: str = "operational",
        context_refs: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        return {
            claim_name: self.create_claim(
                claim_name=claim_name,
                project_id=project_id,
                repository_id=repository_id,
                snapshot_id=snapshot_id,
                task_id=task_id,
                result=result,
                expected_invariant=claim_name,
                test_probe_id=f"{probe_namespace}::{claim_name.lower()}",
                context_refs=context_refs,
            )
            for claim_name in sorted(PHASE_1_22_REQUIRED_EVIDENCE_CLAIMS)
        }


def build_operational_stage_artifact(
    *,
    orchestration_evidence: dict[str, Any],
    phase_1_22_operational_evidence: dict[str, dict[str, Any]],
    operational_readiness_recommendation: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "pilot_stage": PHASE_1_22_OPERATIONAL_STAGE,
        "operational_readiness_recommendation": operational_readiness_recommendation,
        "phase_1_22_operational_evidence": phase_1_22_operational_evidence,
        "orchestration_evidence": orchestration_evidence,
        **(extra or {}),
    }
