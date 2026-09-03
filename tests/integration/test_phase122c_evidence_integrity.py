from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from lucius.domain.enums import (
    AutonomyRecommendation,
    BenchmarkRunStatus,
    EngineeringPlanEvaluationResult,
    RepositoryIntegrityResult,
    RepositoryStateClassification,
)
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import BenchmarkResultORM, EvidenceReferenceORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.provenance import (
    find_verified_without_semantic_evidence,
    validate_claim_evidence,
    validate_evidence_reference,
)
from lucius.pilots.release import PHASE_1_22_OPERATIONAL_STAGE, ReleaseGateService
from tests.integration.test_phase112_pilot_infrastructure import (
    _benchmark,
    _plan_evaluation,
    _project_task_plan,
    _repository_state,
)


def test_evidence_reference_contract_rejects_malformed_missing_unrelated_context_result_and_stale(session):
    project, task, _contract, _plan = _project_task_plan(session)
    valid = _evidence(
        session,
        project_id=project.id,
        task_id=task.id,
        claim_name="GLOBAL_SELECTION_EQUALS_MUTATION",
        invariant="selected identity equals mutated identity",
        probe="phase122c::selection_probe",
        result="PASS",
    )
    unrelated = _evidence(
        session,
        project_id=project.id,
        task_id=task.id,
        claim_name="CANONICAL_ARTIFACT_STORE",
        invariant="artifact store is healthy",
        probe="phase122c::artifact_probe",
        result="PASS",
    )
    wrong_result = _evidence(
        session,
        project_id=project.id,
        task_id=task.id,
        claim_name="GLOBAL_SELECTION_EQUALS_MUTATION",
        invariant="selected identity equals mutated identity",
        probe="phase122c::selection_probe",
        result="FAIL",
    )
    other_project, _other_task, _other_contract, _other_plan = _project_task_plan(session)

    first_eval = _plan_evaluation(session, EngineeringPlanEvaluationResult.PASS)
    superseding = _plan_evaluation(session, EngineeringPlanEvaluationResult.PASS)
    superseding.supersedes_evaluation_id = first_eval.id
    session.flush()

    cases = [
        ("", "MALFORMED_EVIDENCE_ID"),
        ("not-an-id", "MALFORMED_EVIDENCE_ID"),
        ("LEVID_DOES_NOT_EXIST", "MALFORMED_EVIDENCE_ID"),
        ("LEVID_999999", "EVIDENCE_NOT_FOUND"),
        (valid.id, "VALID"),
        (unrelated.id, "EVIDENCE_PAYLOAD_INSUFFICIENT"),
        (wrong_result.id, "EVIDENCE_RESULT_MISMATCH"),
    ]

    for evidence_id, reason in cases:
        result = validate_evidence_reference(
            evidence_id,
            session=session,
            allowed_types={"evidence_reference"},
            expected_result="PASS",
            expected_invariant="selected identity equals mutated identity",
            test_probe_id="phase122c::selection_probe",
            claim_name="GLOBAL_SELECTION_EQUALS_MUTATION",
        )
        assert result.reason == reason
        assert result.valid is (reason == "VALID")

    wrong_type = validate_evidence_reference(
        _benchmark(session, score=100, failed=0).id,
        session=session,
        allowed_types={"evidence_reference"},
    )
    wrong_context = validate_evidence_reference(
        valid.id,
        session=session,
        allowed_types={"evidence_reference"},
        required_context={"project_id": other_project.id},
    )
    stale = validate_evidence_reference(
        first_eval.id,
        session=session,
        allowed_types={"engineering_plan_evaluation"},
        require_current=True,
    )
    historical_allowed = validate_evidence_reference(
        first_eval.id,
        session=session,
        allowed_types={"engineering_plan_evaluation"},
        require_current=False,
    )

    assert wrong_type.reason == "EVIDENCE_TYPE_NOT_ALLOWED"
    assert wrong_context.reason == "EVIDENCE_CONTEXT_MISMATCH"
    assert stale.reason == "EVIDENCE_SUPERSEDED"
    assert historical_allowed.valid is True


def test_verified_claim_semantic_validation_rejects_empty_nonexistent_and_unrelated_but_allows_assumptions(session):
    project, task, _contract, _plan = _project_task_plan(session)
    supporting = _evidence(
        session,
        project_id=project.id,
        task_id=task.id,
        claim_name="Verified claim with support.",
        invariant="Verified claim with support.",
        probe="phase122c::verified_claim",
        result="PASS",
    )
    unrelated = _evidence(
        session,
        project_id=project.id,
        task_id=task.id,
        claim_name="Other claim.",
        invariant="Other claim.",
        probe="phase122c::other_claim",
        result="PASS",
    )

    claims = [
        {"statement": "Empty evidence is unsupported.", "status": "VERIFIED", "evidence_ids": []},
        {"statement": "Missing evidence is unsupported.", "status": "VERIFIED", "evidence_ids": ["LEVID_999999"]},
        {"statement": "Unrelated evidence is unsupported.", "status": "VERIFIED", "evidence_ids": [unrelated.id]},
        {"statement": "Verified claim with support.", "status": "VERIFIED", "evidence_ids": [supporting.id]},
        {"statement": "Assumption without evidence remains allowed.", "status": "ASSUMPTION", "evidence_ids": []},
    ]

    unsupported = find_verified_without_semantic_evidence(
        claims,
        session=session,
        allowed_types={"evidence_reference"},
    )

    assert [claim["statement"] for claim in unsupported] == [
        "Empty evidence is unsupported.",
        "Missing evidence is unsupported.",
        "Unrelated evidence is unsupported.",
    ]


def test_phase122_gate_rejects_fake_arbitrary_wrong_probe_and_passes_supported_evidence(session):
    project, task, _contract, _plan = _project_task_plan(session)
    support = {
        name: _evidence(
            session,
            project_id=project.id,
            task_id=task.id,
            claim_name=name,
            invariant=f"{name} invariant",
            probe=f"phase122c::{name.lower()}",
            result="PASS",
        )
        for name in _phase122_claim_names()
    }
    unrelated_benchmark = _benchmark(session, score=100, failed=0)
    wrong_probe = _evidence(
        session,
        project_id=project.id,
        task_id=task.id,
        claim_name="REAL_MULTI_PROJECT_WORK",
        invariant="REAL_MULTI_PROJECT_WORK invariant",
        probe="phase122c::wrong_probe",
        result="PASS",
    )

    assert _gate_passes(session, {name: True for name in _phase122_claim_names()}) is False
    assert _gate_passes(session, _claims_with("LEVID_999999")) is False
    assert _gate_passes(session, _claims_with("not-an-id")) is False
    assert _gate_passes(session, _claims_with(unrelated_benchmark.id)) is False
    assert _gate_passes(
        session,
        _claims_with(wrong_probe.id)
        | {
            "REAL_MULTI_PROJECT_WORK": {
                "result": "PASS",
                "observed_result": "PASS",
                "evidence_refs": [wrong_probe.id],
                "test_probe_id": "phase122c::real_multi_project_work",
                "expected_invariant": "REAL_MULTI_PROJECT_WORK invariant",
            }
        },
    ) is False
    assert _gate_passes(
        session,
        {
            name: {
                "result": "PASS",
                "observed_result": "PASS",
                "evidence_refs": [evidence.id],
                "test_probe_id": f"phase122c::{name.lower()}",
                "expected_invariant": f"{name} invariant",
            }
            for name, evidence in support.items()
        },
    ) is True


def test_validation_reads_do_not_mutate_file_backed_artifact_store(tmp_path: Path):
    database = tmp_path / "artifact.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    Session = make_session_factory(engine)
    with Session() as session:
        project, task, _contract, _plan = _project_task_plan(session)
        evidence = _evidence(
            session,
            project_id=project.id,
            task_id=task.id,
            claim_name="READ_ONLY_VALIDATION",
            invariant="validation is read only",
            probe="phase122c::read_only",
            result="PASS",
        )
        session.commit()

    before = _database_hash(database)
    with Session() as session:
        for _ in range(5):
            result = validate_evidence_reference(
                evidence.id,
                session=session,
                allowed_types={"evidence_reference"},
                expected_result="PASS",
                expected_invariant="validation is read only",
                test_probe_id="phase122c::read_only",
                claim_name="READ_ONLY_VALIDATION",
            )
            assert result.valid is True
        session.rollback()
    after = _database_hash(database)

    assert before == after
    with sqlite3.connect(database) as connection:
        assert connection.execute("pragma integrity_check").fetchone()[0] == "ok"


def _evidence(
    session,
    *,
    project_id: str,
    task_id: str,
    claim_name: str,
    invariant: str,
    probe: str,
    result: str,
) -> EvidenceReferenceORM:
    evidence_id = next_id(session, "evidence")
    snippet = (
        f"claim_name={claim_name} expected_invariant={invariant} "
        f"test_probe_id={probe} observed_result={result}"
    )
    row = EvidenceReferenceORM(
        id=evidence_id,
        project_id=project_id,
        repository_id="LREPO_TEST",
        snapshot_id="LSNAP_TEST",
        task_id=task_id,
        task_run_id=None,
        source_type="TEST_PROBE",
        path=f"tests/{probe}.txt",
        line_start=None,
        line_end=None,
        content_hash=hashlib.sha256(snippet.encode()).hexdigest(),
        snippet=snippet,
        claim=claim_name,
        relevance_score=1.0,
        match_reasons=["phase-1.22c"],
        captured_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row


def _phase122_claim_names() -> list[str]:
    return [
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
    ]


def _claims_with(evidence_id: str) -> dict[str, dict[str, str | list[str]]]:
    return {
        name: {
            "result": "PASS",
            "observed_result": "PASS",
            "evidence_refs": [evidence_id],
            "test_probe_id": f"phase122c::{name.lower()}",
            "expected_invariant": f"{name} invariant",
        }
        for name in _phase122_claim_names()
    }


def _gate_passes(session, claims: dict) -> bool:
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _plan_evaluation(session, EngineeringPlanEvaluationResult.PASS)
    evaluation.implementation_artifact = {
        "pilot_stage": PHASE_1_22_OPERATIONAL_STAGE,
        "phase_1_22_operational_evidence": claims,
        "operational_readiness_recommendation": AutonomyRecommendation.READY_FOR_ANOTHER_CONTROLLED_MULTI_PROJECT_OPERATIONAL_PILOT.value,
    }
    before = _benchmark(session, score=100, failed=0)
    after = _benchmark(session, score=100, failed=0)
    result = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=after.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
    )
    return result.passed


def _database_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
