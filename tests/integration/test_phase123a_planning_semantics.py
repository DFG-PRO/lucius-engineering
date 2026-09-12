from __future__ import annotations

from pathlib import Path

import pytest

from lucius.domain.enums import PlanningEvidenceMode, RepositoryStateClassification
from lucius.persistence.orm import AuditEventORM, EngineeringPlanORM, EvidenceReferenceORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.claim_policy import UNSUPPORTED_VERIFIED_PLAN_CLAIM, verified_plan_claim_issues
from lucius.pilots.dependency_policy import package_dependency_change_expected, validate_executable_dependencies
from lucius.pilots.fixture_policy import (
    DEGRADED_FIXTURES_MISSING,
    NO_NEW_REGRESSION,
    REGRESSION,
    classify_fixture_dependent_suite,
)
from lucius.pilots.freeze import PlanFreezeSemanticError, PlanFreezeService
from lucius.pilots.operational_policy import validate_overall_orchestration_freeze
from lucius.pilots.repository_state import RepositoryStateService
from lucius.repositories.schemas import WorkspaceContext
from tests.conftest import run_git
from tests.integration.test_phase112_pilot_infrastructure import _project_task_plan


def test_future_test_expectation_cannot_be_verified_without_semantic_evidence(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    plan.assumptions = [
        {
            "statement": "Full suite is expected to pass after implementation.",
            "verified": True,
            "evidence_ids": [],
        }
    ]

    issues = verified_plan_claim_issues(
        {
            "assumptions": plan.assumptions,
            "risks": [],
            "validation_warnings": [],
            "blockers": [],
        },
        session=session,
    )

    assert issues[0].code == UNSUPPORTED_VERIFIED_PLAN_CLAIM
    assert issues[0].statement == "Full suite is expected to pass after implementation."


@pytest.mark.parametrize(
    ("evidence_ids", "expected_reason"),
    [
        (["not-an-id"], "MALFORMED_EVIDENCE_ID"),
        (["LEVID_999999"], "EVIDENCE_NOT_FOUND"),
    ],
)
def test_unsupported_verified_claim_blocks_plan_freeze(session, evidence_ids, expected_reason):
    _project, _task, _contract, plan = _project_task_plan(session)
    plan.assumptions = [{"statement": "Unsupported future test expectation.", "verified": True, "evidence_ids": evidence_ids}]

    with pytest.raises(PlanFreezeSemanticError) as error:
        PlanFreezeService(session).freeze(plan_id=plan.id)

    assert UNSUPPORTED_VERIFIED_PLAN_CLAIM in str(error.value)
    assert expected_reason in str(error.value)


def test_unrelated_evidence_blocks_verified_claim_and_valid_current_evidence_allows_it(session):
    project, task, _contract, plan = _project_task_plan(session)
    unrelated = _semantic_evidence(session, project_id=project.id, task_id=task.id, claim="Different repository fact.")
    plan.assumptions = [{"statement": "Supported repository fact.", "verified": True, "evidence_ids": [unrelated.id]}]

    with pytest.raises(PlanFreezeSemanticError) as error:
        PlanFreezeService(session).freeze(plan_id=plan.id)

    assert "EVIDENCE_PAYLOAD_INSUFFICIENT" in str(error.value)

    supported = _semantic_evidence(session, project_id=project.id, task_id=task.id, claim="Supported repository fact.")
    plan.assumptions = [{"statement": "Supported repository fact.", "verified": True, "evidence_ids": [supported.id]}]
    freeze = PlanFreezeService(session).freeze(plan_id=plan.id, planning_mode=PlanningEvidenceMode.CURRENT_STATE_PLANNING)

    assert freeze.plan_id == plan.id


def test_assumption_can_be_frozen_without_proof(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    plan.assumptions = [{"statement": "Targeted tests should cover operator error handling.", "verified": False, "evidence_ids": []}]

    freeze = PlanFreezeService(session).freeze(plan_id=plan.id)

    assert freeze.plan_payload["assumptions"][0]["verified"] is False


def test_dependency_executable_item_ids_validate_and_prose_is_rejected():
    backlog = [
        {"item_id": "implement", "dependencies": []},
        {"item_id": "tests", "dependencies": ["implement"]},
    ]

    assert validate_executable_dependencies(backlog) == []

    prose = validate_executable_dependencies([{"item_id": "tests", "dependencies": ["local test environment only"]}])
    assert prose[0].code == "PROSE_OR_MALFORMED_EXECUTABLE_DEPENDENCY"


def test_package_dependency_semantics_do_not_treat_local_test_environment_as_package_change():
    plan_payload = {"dependencies": ["local test environment only"]}
    artifact = {"dependency_semantics": {"package_dependency_change_expected": False}}

    assert package_dependency_change_expected(plan_payload, artifact) is False


def test_stale_item_id_cannot_satisfy_active_dependency():
    issues = validate_executable_dependencies(
        [{"item_id": "tests", "dependencies": ["old-implement"]}],
        historical_item_ids={"old-implement"},
    )

    assert issues[0].code == "STALE_EXECUTABLE_DEPENDENCY"


def test_overall_operational_plan_required_for_future_extended_pilots():
    issues = validate_overall_orchestration_freeze(
        pilot_stage="EXTENDED_MULTI_PROJECT_OPERATIONAL_PILOT",
        mutation_started=True,
        overall_plan_freeze_id=None,
    )

    assert issues[0]["code"] == "OVERALL_ORCHESTRATION_FREEZE_REQUIRED"
    assert validate_overall_orchestration_freeze(
        pilot_stage="EXTENDED_MULTI_PROJECT_OPERATIONAL_PILOT",
        mutation_started=True,
        overall_plan_freeze_id="LFREEZE_123456",
    ) == []


def test_fixture_missing_suite_classification_separates_health_from_regression():
    comparison = classify_fixture_dependent_suite(
        baseline_failures=["tests/test_visual.py::test_151"],
        target_failures=["tests/test_visual.py::test_151"],
        missing_fixtures=["output/151/visual/151_VISUAL_PLAN.json"],
    )

    assert comparison.suite_health == DEGRADED_FIXTURES_MISSING
    assert comparison.change_regression_status == NO_NEW_REGRESSION


def test_new_failure_vs_baseline_is_regression():
    comparison = classify_fixture_dependent_suite(
        baseline_failures=["tests/test_visual.py::test_151"],
        target_failures=["tests/test_visual.py::test_151", "tests/test_audio.py::test_new"],
        missing_fixtures=[],
    )

    assert comparison.change_regression_status == REGRESSION
    assert comparison.new_failures == ["tests/test_audio.py::test_new"]


def test_dirty_noncanonical_target_baseline_classification_preserved(session, git_repo: Path, workspace: WorkspaceContext):
    (git_repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")
    run_git(git_repo, "status", "--short")

    state = RepositoryStateService(session).inspect(repository_path=git_repo, workspace_context=workspace)

    assert state.classification == RepositoryStateClassification.NON_CANONICAL_UNTRACKED_STATE
    assert state.untracked_files == ["scratch.txt"]


def _semantic_evidence(session, *, project_id: str, task_id: str, claim: str) -> EvidenceReferenceORM:
    row = EvidenceReferenceORM(
        id=next_id(session, "evidence"),
        project_id=project_id,
        repository_id="LREPO_TEST",
        snapshot_id="LSNAP_TEST",
        task_id=task_id,
        task_run_id=None,
        source_type="TEST",
        path="tests/integration/test_phase123a_planning_semantics.py",
        line_start=None,
        line_end=None,
        content_hash="a" * 64,
        snippet=claim,
        claim=claim,
        relevance_score=1.0,
        match_reasons=["semantic test evidence"],
        captured_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row



def test_phase130_freeze_enforces_deterministic_acceptance_scope(session):
    _project, _task, _contract, plan = _project_task_plan(session)

    row = session.get(EngineeringPlanORM, plan.id)

    assert row is not None
    assert row.affected_files

    target_path = row.affected_files[0]["path"]
    expected_text = "phase 1.30 frozen deterministic acceptance\n"

    row.deterministic_acceptance_checks = [
        {
            "type": "exact_file_content",
            "path": "outside-frozen-scope.txt",
            "expected_text": "must fail closed\n",
        }
    ]
    session.flush()

    with pytest.raises(PlanFreezeSemanticError) as error:
        PlanFreezeService(session).freeze(plan_id=plan.id)

    assert "INVALID_DETERMINISTIC_ACCEPTANCE_CHECK" in str(error.value)
    assert "outside-frozen-scope.txt" in str(error.value)

    blocked_audit = (
        session.query(AuditEventORM)
        .filter(
            AuditEventORM.event_type == "ENGINEERING_PLAN_FREEZE_BLOCKED",
            AuditEventORM.task_id == row.task_id,
        )
        .order_by(AuditEventORM.id.desc())
        .first()
    )

    assert blocked_audit is not None
    assert blocked_audit.result == "INVALID_DETERMINISTIC_ACCEPTANCE_CHECK"

    row.deterministic_acceptance_checks = [
        {
            "type": "exact_file_content",
            "path": target_path,
            "expected_text": expected_text,
        }
    ]
    session.flush()

    freeze = PlanFreezeService(session).freeze(plan_id=plan.id)

    assert freeze.plan_payload["deterministic_acceptance_checks"] == [
        {
            "type": "exact_file_content",
            "path": target_path,
            "expected_text": expected_text,
        }
    ]


def test_phase130_freeze_rejects_malformed_deterministic_acceptance_check(session):
    _project, _task, _contract, plan = _project_task_plan(session)

    row = session.get(EngineeringPlanORM, plan.id)

    assert row is not None
    assert row.affected_files

    row.deterministic_acceptance_checks = [
        {
            "type": "unsupported_runtime_check",
            "path": row.affected_files[0]["path"],
            "expected_text": "invalid\n",
        }
    ]
    session.flush()

    with pytest.raises(PlanFreezeSemanticError) as error:
        PlanFreezeService(session).freeze(plan_id=plan.id)

    assert "INVALID_DETERMINISTIC_ACCEPTANCE_CHECK" in str(error.value)
