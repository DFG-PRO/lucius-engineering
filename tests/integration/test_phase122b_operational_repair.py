from __future__ import annotations

import json
import hashlib
import subprocess
import sys

from lucius.domain.enums import (
    AutonomyRecommendation,
    EngineeringPlanEvaluationMode,
    EngineeringPlanEvaluationResult,
    PersistentWorkflowState,
    QueueWorkItemState,
    RepositoryIntegrityResult,
    RepositoryStateClassification,
    SnapshotMode,
)
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import AuditEventORM, EngineeringPlanEvaluationORM, EvidenceReferenceORM, PersistentWorkflowORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.evaluation import EngineeringPlanEvaluationService
from lucius.pilots.freeze import PlanFreezeService
from lucius.pilots.provenance import find_verified_without_evidence
from lucius.pilots.queue import NonBlockingQueueService, QueueStateError
from lucius.pilots.release import PHASE_1_22_OPERATIONAL_STAGE, ReleaseGateService
from lucius.pilots.repository_state import RepositoryStateService
from lucius.repositories.local_git import LocalGitRepositoryAdapter

from tests.integration.test_phase112_pilot_infrastructure import (
    _benchmark,
    _implementation_artifact,
    _plan_evaluation,
    _project_task_plan,
    _repository_state,
)
from tests.integration.test_phase121_cross_project_queue import _item, _workflow


def test_verified_claim_policy_flags_only_verified_claims_without_evidence():
    claims = [
        {"statement": "verified boolean without evidence", "verified": True},
        {"statement": "verified status without evidence", "status": "VERIFIED"},
        {"statement": "confirmed without evidence", "claim_status": "CONFIRMED"},
        {"statement": "proven without evidence", "status": "PROVEN"},
        {"statement": "verified with evidence", "verified": True, "evidence_ids": ["LEVID_TEST"]},
        {"statement": "supplied is not verified", "status": "SUPPLIED"},
        {"statement": "inferred is not verified", "status": "INFERRED"},
        {"statement": "unverified is not verified", "status": "UNVERIFIED"},
    ]

    unsupported = find_verified_without_evidence(claims)

    assert [claim["statement"] for claim in unsupported] == [
        "verified boolean without evidence",
        "verified status without evidence",
        "confirmed without evidence",
        "proven without evidence",
    ]


def test_plan_evaluation_accepts_linked_disposition_for_frozen_verified_claim(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    plan.affected_files = [{"path": "src/pilots/evaluation.py", "status": "EXISTING_VERIFIED"}]
    plan.steps = [
        {
            "step_id": "STEP-1",
            "title": "service migration test docs",
            "description": "Update service migration pytest regression docs and sqlalchemy dependency handling.",
            "affected_files": ["src/pilots/evaluation.py"],
        }
    ]
    plan.assumptions = [{"statement": "Probe proves operational readiness.", "verified": True}]
    session.flush()
    freeze = PlanFreezeService(session).freeze(plan_id=plan.id)

    failed = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_implementation_artifact(files=["src/pilots/evaluation.py"]),
        evaluation_mode=EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION,
    )
    disposition_evidence = _semantic_disposition_evidence(
        session,
        project_id=freeze.project_id,
        task_id=freeze.task_id,
        invariant="verified claim is not treated as passed without evidence",
        probe="tests/integration/test_phase122b_operational_repair.py::test_plan_evaluation_accepts_linked_disposition_for_frozen_verified_claim",
    )
    passed = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        supersedes_evaluation_id=failed.id,
        implementation_artifact=_implementation_artifact(files=["src/pilots/evaluation.py"])
        | {
            "verified_claim_disposition": {
                "result": "PASS",
                "disposed_claims": ["Probe proves operational readiness."],
                "corrected_classification": "ASSUMPTION",
                "evidence_refs": [disposition_evidence],
                "test_probe_id": "tests/integration/test_phase122b_operational_repair.py::test_plan_evaluation_accepts_linked_disposition_for_frozen_verified_claim",
                "expected_invariant": "verified claim is not treated as passed without evidence",
                "observed_result": "PASS",
            }
        },
        evaluation_mode=EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION,
    )

    assert failed.result == EngineeringPlanEvaluationResult.FAIL
    assert passed.result in {
        EngineeringPlanEvaluationResult.PASS,
        EngineeringPlanEvaluationResult.PASS_WITH_WARNINGS,
    }
    assert next(item for item in passed.dimensions if item.name == "unsupported_claims").status == "PASS"
    assert not any(correction.dimension == "unsupported_claims" for correction in passed.corrections)


def test_phase122_gate_requires_linked_operational_evidence(session):
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    evaluation = _plan_evaluation(session, EngineeringPlanEvaluationResult.PASS)
    evaluation.implementation_artifact = {
        "pilot_stage": PHASE_1_22_OPERATIONAL_STAGE,
        "phase_1_22_operational_evidence": {
            name: {"result": "PASS"} for name in _phase122_claim_names()
        },
    }
    before = _benchmark(session, score=100.0, failed=0)
    after = _benchmark(session, score=100.0, failed=0)

    rejected = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=after.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
    )
    evaluation.implementation_artifact = {
        "pilot_stage": PHASE_1_22_OPERATIONAL_STAGE,
        "phase_1_22_operational_evidence": _phase122_pass_claims(evaluation.id),
        "operational_readiness_recommendation": AutonomyRecommendation.READY_FOR_ANOTHER_CONTROLLED_MULTI_PROJECT_OPERATIONAL_PILOT.value,
    }
    session.flush()
    accepted = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=after.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
    )

    assert rejected.passed is False
    assert rejected.recommendation == AutonomyRecommendation.NOT_READY_FOR_MULTI_PROJECT_OPERATIONAL_USE
    assert any("invalid provenance" in blocker for blocker in rejected.blockers)
    assert accepted.passed is True
    assert accepted.recommendation == AutonomyRecommendation.READY_FOR_ANOTHER_CONTROLLED_MULTI_PROJECT_OPERATIONAL_PILOT


def test_complete_item_is_exact_and_tolerates_malformed_siblings(session):
    queue = NonBlockingQueueService(session)
    workflow = _workflow(
        session,
        project_id="PROJECT_PHASE122B",
        backlog=[
            _item("valid", state=QueueWorkItemState.RUNNING),
            _item("missing-state", state=QueueWorkItemState.READY) | {"state": None},
            _item("unknown-state", state=QueueWorkItemState.READY) | {"state": "UNKNOWN_READY"},
            _item("bad-priority", state=QueueWorkItemState.READY) | {"priority": "URGENT_NOW"},
            _item("done", state=QueueWorkItemState.COMPLETED),
        ],
    )
    row = session.get(PersistentWorkflowORM, workflow.id)
    original_siblings = {
        item["item_id"]: dict(item)
        for item in row.task_backlog
        if item["item_id"] != "valid"
    }

    completed = queue.complete_item(workflow.id, "valid", completed_substeps=["phase122b.done"])
    row = session.get(PersistentWorkflowORM, workflow.id)
    audit = (
        session.query(AuditEventORM)
        .filter(AuditEventORM.event_type == "QUEUE_WORK_ITEM_COMPLETED")
        .order_by(AuditEventORM.timestamp.desc())
        .first()
    )

    assert completed["item_id"] == "valid"
    assert completed["state"] == QueueWorkItemState.COMPLETED.value
    assert "valid" in row.completed_task_ids
    assert "done" in row.completed_task_ids
    assert {"missing-state", "unknown-state", "bad-priority"}.issubset(row.pending_task_ids)
    assert {
        item["item_id"]: dict(item)
        for item in row.task_backlog
        if item["item_id"] != "valid"
    } == original_siblings
    assert audit.event_metadata["requested_item_id"] == "valid"
    assert audit.event_metadata["completed_item_id"] == "valid"
    assert audit.event_metadata["mutation_identity_matches_request"] is True


def test_complete_item_rejects_malformed_non_running_and_ineligible_targets(session):
    queue = NonBlockingQueueService(session)
    malformed = _workflow(
        session,
        project_id="PROJECT_BAD_TARGET",
        backlog=[_item("bad", state=QueueWorkItemState.READY) | {"state": "UNKNOWN_READY"}],
    )
    non_running = _workflow(
        session,
        project_id="PROJECT_READY_TARGET",
        backlog=[_item("ready", state=QueueWorkItemState.READY)],
    )
    closed = _workflow(
        session,
        project_id="PROJECT_CLOSED_TARGET",
        backlog=[_item("closed", state=QueueWorkItemState.RUNNING)],
    )
    session.get(PersistentWorkflowORM, closed.id).workflow_state = PersistentWorkflowState.CLOSED.value
    session.flush()

    for workflow_id, item_id, message in [
        (malformed.id, "bad", "unknown queue state"),
        (non_running.id, "ready", "Cannot complete item from state READY"),
        (closed.id, "closed", "Workflow is not executable"),
    ]:
        try:
            queue.complete_item(workflow_id, item_id)
        except QueueStateError as error:
            assert message in str(error)
        else:
            raise AssertionError(f"{item_id} should not have completed")


def test_complete_item_survives_fresh_process_with_malformed_sibling(tmp_path):
    database = tmp_path / "fresh-process.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    Session = make_session_factory(engine)
    with Session() as session:
        workflow = _workflow(
            session,
            project_id="PROJECT_FRESH",
            backlog=[
                _item("valid", state=QueueWorkItemState.RUNNING),
                _item("bad", state=QueueWorkItemState.READY) | {"state": "UNKNOWN_READY"},
            ],
        )
        session.commit()
        workflow_id = workflow.id

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json;"
                "from lucius.persistence.database import create_sqlite_engine, make_session_factory;"
                "from lucius.pilots.queue import NonBlockingQueueService;"
                f"engine=create_sqlite_engine({str(database)!r});"
                "Session=make_session_factory(engine);"
                "session=Session();"
                f"item=NonBlockingQueueService(session).complete_item({workflow_id!r}, 'valid', completed_substeps=['fresh.done']);"
                "session.commit();"
                "print(json.dumps({'item': item['item_id'], 'state': item['state']}));"
                "session.close()"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"item": "valid", "state": "COMPLETED"}
    with Session() as session:
        row = session.get(PersistentWorkflowORM, workflow_id)
        assert next(item for item in row.task_backlog if item["item_id"] == "bad")["state"] == "UNKNOWN_READY"


def test_completion_does_not_disturb_scheduler_ordering(session):
    queue = NonBlockingQueueService(session)
    workflow = _workflow(
        session,
        project_id="PROJECT_ORDER",
        backlog=[
            _item("running", state=QueueWorkItemState.RUNNING, priority="NORMAL", order=1),
            _item("critical", state=QueueWorkItemState.READY, priority="CRITICAL", order=2),
            _item("resume", state=QueueWorkItemState.READY_TO_RESUME, priority="NORMAL", order=3),
        ],
    )

    queue.complete_item(workflow.id, "running")
    selection = queue.select_next(workflow.id)

    assert selection.selected_item_id == "critical"
    assert selection.eligible_item_ids == ["critical", "resume"]


def test_controlled_operational_replay_with_malformed_sibling(session):
    queue = NonBlockingQueueService(session)
    workflow_a = _workflow(
        session,
        project_id="PROJECT_A",
        backlog=[_item("A1", state=QueueWorkItemState.READY, priority="NORMAL", order=1)],
    )
    workflow_b = _workflow(
        session,
        project_id="PROJECT_B",
        backlog=[
            _item("B1", state=QueueWorkItemState.READY, priority="HIGH", order=1),
            _item("B-bad", state=QueueWorkItemState.READY, priority="LOW", order=2) | {"state": "UNKNOWN_READY"},
        ],
    )
    workflow_c = _workflow(
        session,
        project_id="PROJECT_C",
        backlog=[_item("C1", state=QueueWorkItemState.WAITING_EXTERNAL, priority="CRITICAL", order=1)],
    )

    first = queue.start_global_next([workflow_a.id, workflow_b.id, workflow_c.id])
    assert first.selected_project_id == "PROJECT_B"
    assert first.selected_item_id == "B1"
    queue.complete_item(workflow_b.id, "B1")
    replay = queue.inspect_global([workflow_a.id, workflow_b.id, workflow_c.id])

    assert replay.next_selection.selected_project_id == "PROJECT_A"
    assert replay.next_selection.selected_item_id == "A1"
    assert [item.item_id for item in replay.legacy_unschedulable] == ["B-bad"]


def test_snapshot_uses_single_git_state_and_repository_state_uses_fast_mode(session, git_repo, workspace, monkeypatch):
    adapter = LocalGitRepositoryAdapter(git_repo, workspace)
    original_get_git_state = adapter.get_git_state
    calls = 0

    def counted_get_git_state():
        nonlocal calls
        calls += 1
        return original_get_git_state()

    monkeypatch.setattr(adapter, "get_git_state", counted_get_git_state)
    snapshot = adapter.build_snapshot(SnapshotMode.STANDARD)
    observation = RepositoryStateService(session).inspect(repository_path=git_repo, workspace_context=workspace)

    assert calls == 1
    assert snapshot.manifest_summary.file_count >= 3
    assert observation.details["snapshot_mode"] == SnapshotMode.FAST.value
    assert observation.details["manifest_scope"] == "bounded tracked/untracked git file manifest without content hashes"


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


def _phase122_pass_claims(evidence_id: str) -> dict[str, dict[str, str | list[str]]]:
    return {
        name: {
            "result": "PASS",
            "evidence_refs": [evidence_id],
            "test_probe_id": f"phase122b::{name.lower()}",
            "expected_invariant": f"{name} is supported by a replayable probe",
            "observed_result": "PASS",
        }
        for name in _phase122_claim_names()
    }


def _semantic_disposition_evidence(
    session,
    *,
    project_id: str,
    task_id: str,
    invariant: str,
    probe: str,
) -> str:
    snippet = (
        f"claim_name=verified_claim_disposition expected_invariant={invariant} "
        f"test_probe_id={probe} observed_result=PASS"
    )
    row = EvidenceReferenceORM(
        id=next_id(session, "evidence"),
        project_id=project_id,
        repository_id="LREPO_TEST",
        snapshot_id="LSNAP_TEST",
        task_id=task_id,
        task_run_id=None,
        source_type="TEST_PROBE",
        path="tests/integration/test_phase122b_operational_repair.py",
        line_start=None,
        line_end=None,
        content_hash=hashlib.sha256(snippet.encode()).hexdigest(),
        snippet=snippet,
        claim="verified_claim_disposition",
        relevance_score=1.0,
        match_reasons=["phase-1.22b-semantic-disposition"],
        captured_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row.id
