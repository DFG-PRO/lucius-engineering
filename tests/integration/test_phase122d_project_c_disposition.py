from __future__ import annotations

from lucius.domain.enums import EngineeringPlanEvaluationResult, RepositoryIntegrityResult, RepositoryStateClassification
from lucius.persistence.orm import EngineeringPlanEvaluationORM, EvidenceReferenceORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.provenance import validate_claim_evidence
from lucius.pilots.release import ReleaseGateService
from tests.integration.test_phase112_pilot_infrastructure import _benchmark, _plan_evaluation, _project_task_plan, _repository_state


def test_project_c_shape_era_disposition_fails_and_semantic_disposition_passes_gate(session):
    project, task, _contract, _plan = _project_task_plan(session)
    old = {
        "result": "PASS",
        "disposed_correction_dimensions": ["authority_risk_classification", "documentation_strategy"],
        "evidence_refs": ["LEVID_999999"],
        "test_probe_id": "phase122b::project_c_warning_disposition",
        "expected_invariant": "Project C warning is documented as correction rather than forced pass",
        "observed_result": "PASS",
    }

    old_validation = validate_claim_evidence(
        old,
        session=session,
        allowed_types={"evidence_reference"},
        require_current=True,
        expected_result="PASS",
        expected_invariant=old["expected_invariant"],
        test_probe_id=old["test_probe_id"],
    )

    authority = _semantic_project_c_evidence(
        session,
        project_id=project.id,
        task_id=task.id,
        claim_name="project_c_authority_correction",
        invariant="Project C exercised L0 control-plane verification only.",
        probe="phase122d::project_c_authority",
    )
    warning = _semantic_project_c_evidence(
        session,
        project_id=project.id,
        task_id=task.id,
        claim_name="project_c_warning_disposition",
        invariant="Project C authority and documentation warnings are semantically dispositioned.",
        probe="phase122d::project_c_warning_disposition",
    )
    evaluation = _project_c_warning_evaluation(
        session,
        warning_evidence_id=warning.id,
        authority_evidence_id=authority.id,
    )
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    benchmark = _benchmark(session, score=100.0, failed=0)

    accepted = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=benchmark.id,
        benchmark_after_id=benchmark.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
    )

    artifact = dict(evaluation.implementation_artifact)
    disposition = dict(artifact["evaluation_warning_disposition"])
    disposition["evidence_refs"] = ["LEVID_999999"]
    artifact["evaluation_warning_disposition"] = disposition
    evaluation.implementation_artifact = artifact
    session.flush()
    rejected = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=benchmark.id,
        benchmark_after_id=benchmark.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
    )

    assert old_validation.valid is False
    assert old_validation.reason == "EVIDENCE_NOT_FOUND"
    assert accepted.passed is True
    assert rejected.passed is False
    assert any("invalid provenance" in blocker for blocker in rejected.blockers)


def _project_c_warning_evaluation(
    session,
    *,
    warning_evidence_id: str,
    authority_evidence_id: str,
) -> EngineeringPlanEvaluationORM:
    row = _plan_evaluation(session, EngineeringPlanEvaluationResult.PASS_WITH_WARNINGS)
    row.corrections = [
        {"severity": "MAJOR", "dimension": "authority_risk_classification", "message": "historical Project C plan authority was L2"},
        {"severity": "MAJOR", "dimension": "documentation_strategy", "message": "historical Project C docs were absent"},
    ]
    row.implementation_artifact = {
        "project_c_authority_correction": {
            "result": "PASS",
            "corrected_authority_level": "L0",
            "evidence_refs": [authority_evidence_id],
            "test_probe_id": "phase122d::project_c_authority",
            "expected_invariant": "Project C exercised L0 control-plane verification only.",
            "observed_result": "PASS",
        },
        "evaluation_warning_disposition": {
            "result": "PASS",
            "disposed_correction_dimensions": ["authority_risk_classification", "documentation_strategy"],
            "status_by_dimension": {
                "authority_risk_classification": "RESOLVED",
                "documentation_strategy": "RESOLVED",
            },
            "evidence_refs": [warning_evidence_id],
            "test_probe_id": "phase122d::project_c_warning_disposition",
            "expected_invariant": "Project C authority and documentation warnings are semantically dispositioned.",
            "observed_result": "PASS",
        },
    }
    session.flush()
    return row


def _semantic_project_c_evidence(
    session,
    *,
    project_id: str,
    task_id: str,
    claim_name: str,
    invariant: str,
    probe: str,
) -> EvidenceReferenceORM:
    row = EvidenceReferenceORM(
        id=next_id(session, "evidence"),
        project_id=project_id,
        repository_id="LREPO_TEST",
        snapshot_id="LSNAP_TEST",
        task_id=task_id,
        task_run_id=None,
        source_type="PHASE_1_22D_TEST",
        path="tests/integration/test_phase122d_project_c_disposition.py",
        line_start=None,
        line_end=None,
        content_hash="d" * 64,
        snippet=f"{claim_name}: {invariant}; {probe}; observed_result=PASS; result=PASS.",
        claim=f"{claim_name}: {invariant}; {probe}; observed_result=PASS; result=PASS.",
        relevance_score=1.0,
        match_reasons=["Project C semantic disposition test"],
        captured_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row
