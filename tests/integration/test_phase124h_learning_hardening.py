from __future__ import annotations

from pathlib import Path

import pytest

from lucius.domain.enums import (
    Actor,
    AuthorityLevel,
    BenchmarkRunStatus,
    EngineeringPlanEvaluationMode,
    Environment,
    PersistentWorkflowState,
    QueueWorkItemState,
    RepositoryAccessMode,
    RepositoryAdapterType,
    SnapshotMode,
)
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.json_fields import set_json_field, update_json_field
from lucius.persistence.orm import (
    BenchmarkResultORM,
    EvidenceReferenceORM,
    PersistentWorkflowORM,
    PlanFreezeORM,
    RepositoryRegistrationORM,
    RepositorySnapshotORM,
    utc_now,
)
from lucius.persistence.repositories import next_id
from lucius.planning.persistence import EngineeringPlanRepository
from lucius.planning.schemas import ModelEngineeringPlanOutput, PlanningContext
from lucius.pilots.dependency_policy import package_dependency_change_expected, validate_typed_dependencies
from lucius.pilots.evaluation import EngineeringPlanEvaluationService
from lucius.pilots.freeze import PlanFreezeSemanticError, PlanFreezeService
from lucius.pilots.hardening import (
    REQUIRED_ADVERSARIAL_PROBE_IDS,
    REQUIRED_ORCHESTRATION_CONTRACT_FIELDS,
    validate_initial_eligibility,
    validate_orchestration_contract_completeness,
    validate_persisted_adversarial_probes,
    validate_plan_file_states,
    validate_uncertainty_fields,
)
from lucius.pilots.queue import NonBlockingQueueService
from lucius.pilots.workflows import PersistentWorkflowService

from tests.integration.test_phase112_pilot_infrastructure import _project_task_plan


def test_direct_artifact_insert_drift_repairs_id_counter(session):
    session.add(
        BenchmarkResultORM(
            id="LBENCH_000777",
            suite_name="drift",
            suite_version=1,
            benchmark_version="drift",
            git_head=None,
            target_dirty=False,
            status=BenchmarkRunStatus.PASSED.value,
            total_cases=1,
            passed=1,
            failed=0,
            skipped=0,
            duration_ms=1,
            deterministic_metrics={},
            artifact_result_id=None,
            evaluation_run_id=None,
            environment_metadata={},
            captured_at=utc_now(),
        )
    )
    session.flush()

    assert next_id(session, "benchmark") == "LBENCH_000778"
    assert next_id(session, "benchmark") == "LBENCH_000779"


def test_id_counter_repair_survives_restart_sparse_ids_rollback_and_malformed_ids(tmp_path: Path):
    database = tmp_path / "phase124h-id.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    Session = make_session_factory(engine)

    with Session() as session:
        session.add(
            BenchmarkResultORM(
                id="LBENCH_000010",
                suite_name="sparse",
                suite_version=1,
                benchmark_version="sparse",
                git_head=None,
                target_dirty=False,
                status=BenchmarkRunStatus.PASSED.value,
                total_cases=1,
                passed=1,
                failed=0,
                skipped=0,
                duration_ms=1,
                deterministic_metrics={},
                artifact_result_id=None,
                evaluation_run_id=None,
                environment_metadata={},
                captured_at=utc_now(),
            )
        )
        session.add(
            BenchmarkResultORM(
                id="LBENCH_BAD",
                suite_name="malformed",
                suite_version=1,
                benchmark_version="malformed",
                git_head=None,
                target_dirty=False,
                status=BenchmarkRunStatus.PASSED.value,
                total_cases=1,
                passed=1,
                failed=0,
                skipped=0,
                duration_ms=1,
                deterministic_metrics={},
                artifact_result_id=None,
                evaluation_run_id=None,
                environment_metadata={},
                captured_at=utc_now(),
            )
        )
        session.commit()

    with Session() as session:
        assert next_id(session, "benchmark") == "LBENCH_000011"
        session.rollback()

    with Session() as session:
        assert next_id(session, "benchmark") == "LBENCH_000011"
        session.commit()

    with Session() as session:
        assert next_id(session, "benchmark") == "LBENCH_000012"


def test_json_field_helpers_make_nested_workflow_updates_durable(tmp_path: Path):
    database = tmp_path / "phase124h-json.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    Session = make_session_factory(engine)

    with Session() as session:
        workflow = PersistentWorkflowService(session).create(
            objective="JSON durability",
            expected_main_head="a" * 40,
            isolated_branch="phase-124h-json",
            worktree_path="/tmp/phase-124h-json",
            authority_tier="L2",
            task_backlog=[_item("TASK-A", state=QueueWorkItemState.READY)],
            dependency_graph={"TASK-A": []},
            pending_task_ids=["TASK-A"],
        )
        row = session.get(PersistentWorkflowORM, workflow.id)
        set_json_field(row, "full_test_status", {"state": "DEGRADED", "metadata": {"fixture": True}})
        update_json_field(row, "repair_counters", lambda value: value | {"total": value.get("total", 0) + 1}, default={})
        session.commit()

    with Session() as session:
        row = session.get(PersistentWorkflowORM, workflow.id)
        assert row.full_test_status == {"state": "DEGRADED", "metadata": {"fixture": True}}
        assert row.repair_counters["total"] == 1


def test_queue_block_resume_and_completion_json_survive_new_session(tmp_path: Path):
    database = tmp_path / "phase124h-queue.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    Session = make_session_factory(engine)

    with Session() as session:
        workflow = PersistentWorkflowService(session).create(
            objective="Queue JSON durability",
            expected_main_head="a" * 40,
            isolated_branch="phase-124h-queue",
            worktree_path="/tmp/phase-124h-queue",
            authority_tier="L2",
            task_backlog=[_item("TASK-A", state=QueueWorkItemState.READY)],
            dependency_graph={"TASK-A": []},
            pending_task_ids=["TASK-A"],
        )
        row = session.get(PersistentWorkflowORM, workflow.id)
        row.workflow_state = PersistentWorkflowState.PLAN_READY.value
        queue = NonBlockingQueueService(session)
        queue.start_next(workflow.id)
        checkpoint = queue.block_running_item(
            workflow.id,
            "TASK-A",
            blocking_state=QueueWorkItemState.WAITING_EXTERNAL,
            blocking_reason="Need fixture.",
            blocker_category="fixture",
            resume_condition="Fixture supplied.",
            work_completed=["captured start"],
        )
        session.commit()

    with Session() as session:
        row = session.get(PersistentWorkflowORM, workflow.id)
        assert row.checkpoint_history == [checkpoint.id]
        item = row.task_backlog[0]
        assert item["state"] == QueueWorkItemState.WAITING_EXTERNAL.value
        assert item["current_block_checkpoint_id"] == checkpoint.id
        assert item["completed_substeps"] == ["captured start"]

        queue = NonBlockingQueueService(session)
        queue.resolve_blocker(
            workflow.id,
            "TASK-A",
            checkpoint_id=checkpoint.id,
            expected_item_version=item["version"],
            resolution_event="Fixture supplied.",
        )
        queue.complete_item(workflow.id, "TASK-A", completed_substeps=["verified resume"])
        session.commit()

    with Session() as session:
        row = session.get(PersistentWorkflowORM, workflow.id)
        assert row.completed_task_ids == ["TASK-A"]
        assert row.pending_task_ids == []
        assert row.task_backlog[0]["state"] == QueueWorkItemState.COMPLETED.value
        assert row.task_backlog[0]["completed_substeps"] == ["captured start", "verified resume"]


def test_initial_eligibility_simulation_detects_wrong_start_choice_and_ready_resume_defect(session):
    workflow = PersistentWorkflowService(session).create(
        objective="Initial eligibility",
        expected_main_head="a" * 40,
        isolated_branch="phase-124h-eligibility",
        worktree_path="/tmp/phase-124h-eligibility",
        authority_tier="L2",
        task_backlog=[
            _item("LOW-READY", state=QueueWorkItemState.READY, priority="LOW", order=2),
            _item("HIGH-RESUME", state=QueueWorkItemState.READY_TO_RESUME, priority="HIGH", order=1),
            _item("BAD-PRIORITY", state=QueueWorkItemState.READY, priority="URGENT", order=0),
        ],
        dependency_graph={"LOW-READY": [], "HIGH-RESUME": [], "BAD-PRIORITY": []},
        pending_task_ids=["LOW-READY", "HIGH-RESUME", "BAD-PRIORITY"],
    )
    row = session.get(PersistentWorkflowORM, workflow.id)
    row.workflow_state = PersistentWorkflowState.PLAN_READY.value
    session.flush()

    issues = validate_initial_eligibility(
        session,
        [workflow.id],
        {"selected_workflow_id": workflow.id, "selected_item_id": "LOW-READY", "eligible_item_ids": ["LOW-READY"]},
    )

    codes = {issue.code for issue in issues}
    assert "INITIAL_ELIGIBILITY_MISMATCH" in codes
    assert "READY_TO_RESUME_WITHOUT_CHECKPOINT" in codes


def test_file_state_validation_blocks_false_existing_claim_and_allows_proposed_new(session):
    project, task, _contract, plan = _project_task_plan(session)
    repository = _repository(session, project.id)
    snapshot = _snapshot(session, repository.id, files=["src/existing.py"])
    evidence = _evidence(session, project.id, task.id, repository.id, snapshot.id, "src/existing.py")

    plan.repository_snapshot_ids = [snapshot.id]
    plan.evidence_ids = [evidence.id]
    plan.affected_files = [
        {"path": "src/missing.py", "status": "EXISTING_VERIFIED", "evidence_ids": [evidence.id]},
        {"path": "src/new.py", "status": "PROPOSED_NEW"},
    ]
    session.flush()

    issues = validate_plan_file_states(session, plan)
    assert [issue.code for issue in issues] == ["PLAN_FILE_STATE_MISMATCH"]
    with pytest.raises(PlanFreezeSemanticError, match="PLAN_FILE_STATE_MISMATCH"):
        PlanFreezeService(session).freeze(plan_id=plan.id)

    plan.affected_files = [
        {"path": "src/existing.py", "status": "EXISTING_VERIFIED", "evidence_ids": [evidence.id]},
        {"path": "src/new.py", "status": "PROPOSED_NEW"},
    ]
    session.flush()
    assert validate_plan_file_states(session, plan) == []


def test_file_state_validation_rejects_stale_evidence_when_snapshot_is_present(session):
    project, task, _contract, plan = _project_task_plan(session)
    repository = _repository(session, project.id)
    current_snapshot = _snapshot(session, repository.id, files=["src/existing.py"])
    stale_snapshot = _snapshot(session, repository.id, files=["src/existing.py"])
    stale_evidence = _evidence(session, project.id, task.id, repository.id, stale_snapshot.id, "src/existing.py")
    plan.repository_snapshot_ids = [current_snapshot.id]
    plan.evidence_ids = [stale_evidence.id]
    plan.affected_files = [{"path": "src/existing.py", "status": "EXISTING_VERIFIED", "evidence_ids": [stale_evidence.id]}]
    session.flush()

    assert [issue.code for issue in validate_plan_file_states(session, plan)] == ["STALE_OR_MISSING_FILE_STATE_EVIDENCE"]


def test_file_state_validation_rejects_absolute_parent_and_directory_paths(session):
    project, task, _contract, plan = _project_task_plan(session)
    repository = _repository(session, project.id)
    snapshot = _snapshot(session, repository.id, files=["src"], file_type="directory")
    evidence = _evidence(session, project.id, task.id, repository.id, snapshot.id, "src")
    plan.repository_snapshot_ids = [snapshot.id]
    plan.evidence_ids = [evidence.id]
    plan.affected_files = [
        {"path": "/tmp/escape.py", "status": "EXISTING_VERIFIED"},
        {"path": "../escape.py", "status": "EXISTING_VERIFIED"},
        {"path": "src", "status": "EXISTING_VERIFIED", "evidence_ids": [evidence.id]},
    ]
    session.flush()

    codes = [issue.code for issue in validate_plan_file_states(session, plan)]
    assert codes == ["INVALID_PLAN_FILE_PATH", "INVALID_PLAN_FILE_PATH", "PLAN_FILE_STATE_MISMATCH"]


def test_dependency_evaluator_distinguishes_orchestration_artifacts_from_package_changes(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    plan.dependencies = [
        {"type": "ORCHESTRATION_ARTIFACT", "artifact_id": "LFREEZE_000001"},
        {"type": "EVIDENCE_ARTIFACT", "artifact_id": "LEVID_000001"},
    ]
    session.flush()
    freeze = PlanFreezeService(session).freeze(plan_id=plan.id)

    assert package_dependency_change_expected({"dependencies": plan.dependencies}, {}) is False
    evaluation = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact={
            "architecture": ["service"],
            "components": ["service", "pilot"],
            "files": ["src/pilots/evaluation.py"],
            "known_paths": ["src/pilots/evaluation.py", "docs/evaluation/CANONICAL_PILOT_METHODOLOGY.md"],
            "migrations": [],
            "tests": ["tests/integration/test_phase124h_learning_hardening.py"],
            "documentation": ["docs/evaluation/CANONICAL_PILOT_METHODOLOGY.md"],
            "change_evidence": {"dependencies": {"state": "NO_CHANGE_CONFIRMED", "changed_files": []}},
            "risk_level": "LOW",
            "required_authority_level": "L1",
            "material_work": ["service"],
        },
        evaluation_mode=EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION,
    )

    dependency_dimension = next(dimension for dimension in evaluation.dimensions if dimension.name == "dependency_awareness")
    assert dependency_dimension.status == "PASS"


def test_dependency_semantics_flags_invalid_typed_dependencies():
    issues = validate_typed_dependencies([{"type": "mystery"}, {"type": "PACKAGE_DEPENDENCY"}])
    assert [issue.code for issue in issues] == ["UNKNOWN_TYPED_DEPENDENCY_SEMANTICS", "MISSING_TYPED_DEPENDENCY_ID"]


def test_uncertainty_invariant_is_required_only_for_extended_or_degraded_contexts():
    assert validate_uncertainty_fields({}, require_uncertainty=False) == []
    assert [issue.code for issue in validate_uncertainty_fields({}, require_uncertainty=True)] == [
        "UNCERTAINTY_FIELD_REQUIRED"
    ]
    assert validate_uncertainty_fields(
        {"unknowns": [{"statement": "Fixture baseline remains DEGRADED."}]},
        require_uncertainty=True,
        degraded_test_health=True,
    ) == []


def test_orchestration_contract_and_adversarial_probe_policy():
    payload = {"orchestration_contract_required": True, "orchestration_contract": {"projects": ["A"]}}
    assert [issue.code for issue in validate_orchestration_contract_completeness(payload)] == [
        "ORCHESTRATION_CONTRACT_INCOMPLETE"
    ]

    complete_contract = {
        key: [key] for key in REQUIRED_ORCHESTRATION_CONTRACT_FIELDS
    }
    complete_probes = [
        {
            "probe_id": probe_id,
            "invariant": "must block",
            "manipulated_condition": "missing evidence",
            "expected_result": "BLOCKED",
            "observed_result": "BLOCKED",
            "result": "PASS",
            "context": {"phase": "1.24H"},
            "relevant_artifact_refs": ["LPLAN_000029"],
        }
        for probe_id in REQUIRED_ADVERSARIAL_PROBE_IDS
    ]
    assert validate_orchestration_contract_completeness(
        {"orchestration_contract_required": True, "orchestration_contract": complete_contract}
    ) == []
    assert validate_persisted_adversarial_probes(
        {"orchestration_contract_required": True, "adversarial_probes": complete_probes}
    ) == []
    assert [issue.code for issue in validate_persisted_adversarial_probes({"orchestration_contract_required": True})] == [
        "ADVERSARIAL_PROBE_REQUIRED"
    ]


def test_complete_canonical_extended_orchestration_plan_freezes_successfully(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    plan.orchestration_contract_required = True
    plan.orchestration_contract = _complete_orchestration_contract()
    plan.adversarial_probes = _complete_adversarial_probes(plan.id)
    plan.unknowns = [{"statement": "Extended orchestration remains bounded to one logical dispatcher."}]
    session.flush()

    freeze = PlanFreezeService(session).freeze(plan_id=plan.id)

    assert freeze.plan_payload["orchestration_contract_required"] is True
    assert freeze.plan_payload["orchestration_contract"] == _complete_orchestration_contract()
    assert {probe["probe_id"] for probe in freeze.plan_payload["adversarial_probes"]} == REQUIRED_ADVERSARIAL_PROBE_IDS


def test_previously_failing_planning_repository_to_freeze_path_preserves_extended_payload(tmp_path: Path):
    database = tmp_path / "canonical-extended-orchestration.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    Session = make_session_factory(engine)

    with Session() as session:
        project, task, contract, _seed_plan = _project_task_plan(session)
        context = PlanningContext(
            task_id=task.id,
            project_id=project.id,
            task_title=task.title,
            task_objective=task.objective,
            task_status=task.status,
            task_authority_level=AuthorityLevel.L1,
            task_complexity=task.complexity,
            task_contract_id=contract.id,
            task_contract_version=contract.version,
            contract_objective=contract.objective,
            acceptance_criteria=contract.acceptance_criteria,
            constraints=contract.constraints,
            allowed_actions=contract.allowed_actions,
            environment=Environment.DEVELOPMENT,
            contract_authority_level=AuthorityLevel.L1,
            documentation_required=contract.documentation_required,
            documentation_targets=contract.documentation_targets,
        )
        model_plan = ModelEngineeringPlanOutput(
            summary="Extended canonical orchestration plan",
            objective="Freeze a multi-project orchestration contract through the canonical plan path.",
            orchestration_contract_required=True,
            orchestration_contract=_complete_orchestration_contract(),
            adversarial_probes=_complete_adversarial_probes("MODEL_PLAN"),
            unknowns=[{"statement": "Synthetic fixture verifies durability rather than target implementation."}],
            affected_components=["planning", "freeze"],
            steps=[
                {
                    "step_id": "STEP-1",
                    "sequence": 1,
                    "title": "Freeze",
                    "description": "Freeze canonical extended orchestration payload.",
                    "expected_result": "Plan freezes.",
                    "validation": "PlanFreezeService succeeds.",
                }
            ],
        )
        created = EngineeringPlanRepository(session).create(
            context=context,
            model_plan=model_plan,
            model_execution_ids=[],
            blockers=[],
            validation_warnings=[],
            planner_version="1.24H1-test",
            created_by=Actor.SYSTEM.value,
        )
        plan_id = created.id
        session.commit()

    with Session() as session:
        freeze = PlanFreezeService(session).freeze(plan_id=plan_id)
        session.commit()

    with Session() as session:
        persisted = session.get(PlanFreezeORM, freeze.id)
        assert persisted.plan_payload["orchestration_contract_required"] is True
        assert persisted.plan_payload["orchestration_contract"] == _complete_orchestration_contract()
        assert {probe["probe_id"] for probe in persisted.plan_payload["adversarial_probes"]} == REQUIRED_ADVERSARIAL_PROBE_IDS


def test_freeze_blocks_explicit_extended_orchestration_without_contract(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    plan.orchestration_contract_required = True
    plan.adversarial_probes = _complete_adversarial_probes(plan.id)
    plan.unknowns = [{"statement": "Extended orchestration requires explicit uncertainty."}]
    session.flush()

    with pytest.raises(PlanFreezeSemanticError, match="ORCHESTRATION_CONTRACT_INCOMPLETE"):
        PlanFreezeService(session).freeze(plan_id=plan.id)


def test_freeze_blocks_incomplete_extended_orchestration_contract(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    plan.orchestration_contract_required = True
    plan.orchestration_contract = {"projects": ["Darwin"]}
    plan.adversarial_probes = _complete_adversarial_probes(plan.id)
    plan.unknowns = [{"statement": "Extended orchestration requires explicit uncertainty."}]
    session.flush()

    with pytest.raises(PlanFreezeSemanticError, match="ORCHESTRATION_CONTRACT_INCOMPLETE"):
        PlanFreezeService(session).freeze(plan_id=plan.id)


def test_freeze_blocks_missing_required_adversarial_probes(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    plan.orchestration_contract_required = True
    plan.orchestration_contract = _complete_orchestration_contract()
    plan.unknowns = [{"statement": "Extended orchestration requires explicit uncertainty."}]
    session.flush()

    with pytest.raises(PlanFreezeSemanticError, match="ADVERSARIAL_PROBE_REQUIRED"):
        PlanFreezeService(session).freeze(plan_id=plan.id)


def test_freeze_blocks_incomplete_required_adversarial_probe_set(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    plan.orchestration_contract_required = True
    plan.orchestration_contract = _complete_orchestration_contract()
    probes = _complete_adversarial_probes(plan.id)
    plan.adversarial_probes = [probe for probe in probes if probe["probe_id"] != "initial_eligibility_mismatch"]
    plan.unknowns = [{"statement": "Extended orchestration requires explicit uncertainty."}]
    session.flush()

    with pytest.raises(PlanFreezeSemanticError, match="ADVERSARIAL_PROBE_REQUIRED"):
        PlanFreezeService(session).freeze(plan_id=plan.id)


def test_freeze_blocks_malformed_required_adversarial_probe(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    plan.orchestration_contract_required = True
    plan.orchestration_contract = _complete_orchestration_contract()
    probes = _complete_adversarial_probes(plan.id)
    probes[0] = {key: value for key, value in probes[0].items() if key != "observed_result"}
    plan.adversarial_probes = probes
    plan.unknowns = [{"statement": "Extended orchestration requires explicit uncertainty."}]
    session.flush()

    with pytest.raises(PlanFreezeSemanticError, match="ADVERSARIAL_PROBE_INCOMPLETE"):
        PlanFreezeService(session).freeze(plan_id=plan.id)


def test_non_extended_plan_freeze_remains_backward_compatible(session):
    _project, _task, _contract, plan = _project_task_plan(session)

    freeze = PlanFreezeService(session).freeze(plan_id=plan.id)

    assert freeze.plan_payload["orchestration_contract_required"] is False
    assert freeze.plan_payload["orchestration_contract"] == {}
    assert freeze.plan_payload["adversarial_probes"] == []


def test_unsupported_verified_claim_still_blocks_before_extended_orchestration_freeze(session):
    _project, _task, _contract, plan = _project_task_plan(session)
    plan.orchestration_contract_required = True
    plan.orchestration_contract = _complete_orchestration_contract()
    plan.adversarial_probes = _complete_adversarial_probes(plan.id)
    plan.assumptions = [{"statement": "Unsupported verified claim.", "verified": True, "evidence_ids": []}]
    plan.unknowns = [{"statement": "Extended orchestration requires explicit uncertainty."}]
    session.flush()

    with pytest.raises(PlanFreezeSemanticError, match="UNSUPPORTED_VERIFIED_PLAN_CLAIM"):
        PlanFreezeService(session).freeze(plan_id=plan.id)


def _complete_orchestration_contract() -> dict:
    return {key: [key] for key in REQUIRED_ORCHESTRATION_CONTRACT_FIELDS}


def _complete_adversarial_probes(plan_id: str) -> list[dict]:
    return [
        {
            "probe_id": probe_id,
            "invariant": "must block unsafe extended orchestration state",
            "manipulated_condition": "synthetic missing or stale evidence",
            "expected_result": "BLOCKED",
            "observed_result": "BLOCKED",
            "result": "PASS",
            "context": {"phase": "1.24H1"},
            "relevant_artifact_refs": [plan_id],
        }
        for probe_id in REQUIRED_ADVERSARIAL_PROBE_IDS
    ]


def _item(
    item_id: str,
    *,
    state: QueueWorkItemState,
    priority: str = "NORMAL",
    order: int = 1,
    dependencies: list[str] | None = None,
) -> dict:
    return {
        "item_id": item_id,
        "logical_task_id": item_id,
        "project_id": "fixture-project",
        "workflow_id": "fixture-workflow",
        "title": item_id,
        "state": state.value,
        "priority": priority,
        "created_order": order,
        "dependencies": dependencies or [],
        "completed_substeps": [],
        "version": 0,
    }


def _repository(session, project_id: str) -> RepositoryRegistrationORM:
    repository = RepositoryRegistrationORM(
        id=next_id(session, "repository"),
        project_id=project_id,
        name="phase124h",
        adapter_type=RepositoryAdapterType.LOCAL_GIT.value,
        location="/tmp/phase124h",
        canonical_remote=None,
        default_branch="main",
        access_mode=RepositoryAccessMode.READ_ONLY.value,
        status="ACTIVE",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(repository)
    session.flush()
    return repository


def _snapshot(
    session,
    repository_id: str,
    *,
    files: list[str],
    file_type: str = "file",
) -> RepositorySnapshotORM:
    snapshot = RepositorySnapshotORM(
        id=next_id(session, "snapshot"),
        repository_id=repository_id,
        mode=SnapshotMode.STANDARD.value,
        branch="main",
        commit_sha="a" * 40,
        is_dirty=False,
        dirty_summary={},
        manifest_hash="b" * 64,
        file_count=len(files),
        document_count=0,
        test_count=0,
        technology_profile={},
        manifest={"files": [{"path": path, "file_type": file_type} for path in files]},
        documentation_map=[],
        test_map=[],
        configuration_map=[],
        warnings=[],
        captured_at=utc_now(),
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def _evidence(
    session,
    project_id: str,
    task_id: str,
    repository_id: str,
    snapshot_id: str,
    path: str,
) -> EvidenceReferenceORM:
    evidence = EvidenceReferenceORM(
        id=next_id(session, "evidence"),
        project_id=project_id,
        repository_id=repository_id,
        snapshot_id=snapshot_id,
        task_id=task_id,
        task_run_id=None,
        source_type="CODE",
        path=path,
        line_start=1,
        line_end=1,
        content_hash="c" * 64,
        snippet="fixture",
        claim="file exists",
        relevance_score=1.0,
        match_reasons=["phase124h"],
        captured_at=utc_now(),
    )
    session.add(evidence)
    session.flush()
    return evidence
