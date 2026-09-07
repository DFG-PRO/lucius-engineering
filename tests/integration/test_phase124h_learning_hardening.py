from __future__ import annotations

from pathlib import Path

import pytest

from lucius.domain.enums import (
    Actor,
    AuthorityLevel,
    BenchmarkRunStatus,
    EngineeringPlanEvaluationMode,
    EngineeringPlanEvaluationResult,
    Environment,
    MetricApplicability,
    PersistentWorkflowState,
    QueueWorkItemState,
    RepositoryAccessMode,
    RepositoryAdapterType,
    RepositoryIntegrityResult,
    SnapshotMode,
    RepositoryStateClassification,
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
from lucius.planning.schemas import DocumentationRequirement, ModelEngineeringPlanOutput, PlanningContext
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
from lucius.pilots.operational_evidence import OperationalEvidenceService, build_operational_stage_artifact
from lucius.pilots.queue import NonBlockingQueueService
from lucius.pilots.release import ReleaseGateService
from lucius.pilots.workflows import PersistentWorkflowService

from tests.integration.test_phase112_pilot_infrastructure import _benchmark, _project_task_plan, _repository_state


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


def test_documentation_evaluation_enforces_exact_required_path(session):
    freeze = _documentation_freeze(
        session,
        [{"target": "docs/runtime/exact.md", "reason": "Exact operator contract.", "trigger": "docs", "exact_path_required": True}],
        affected_files=["docs/runtime/exact.md"],
    )

    passed = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_documentation_artifact(["docs/runtime/exact.md"]),
    )
    failed = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_documentation_artifact(["docs/runtime/other.md"]),
    )

    assert _dimension(passed, "documentation_strategy").status == "PASS"
    assert _dimension(failed, "documentation_strategy").status == "FAIL"


def test_documentation_path_alias_freezes_to_canonical_exact_contract(session):
    freeze = _documentation_freeze(
        session,
        [{"path": "README.md", "reason": "Exact target docs.", "trigger": "docs", "exact_path_required": True}],
        affected_files=["README.md"],
    )

    requirement = freeze.plan_payload["documentation_requirements"][0]
    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_documentation_artifact(["README.md"]),
    )

    assert requirement["target"] == "README.md"
    assert requirement["proposed_path"] == "README.md"
    assert "path" not in requirement
    assert _dimension(result, "documentation_strategy").status == "PASS"
    assert all(correction.dimension != "documentation_strategy" for correction in result.corrections)


def test_model_documentation_path_alias_uses_same_canonical_contract():
    requirement = DocumentationRequirement.model_validate(
        {"path": "docs/runtime/operator.md", "reason": "Operator docs.", "trigger": "runtime", "exact_path_required": True}
    )

    payload = requirement.model_dump(mode="json")

    assert payload["target"] == "docs/runtime/operator.md"
    assert payload["proposed_path"] == "docs/runtime/operator.md"
    assert "path" not in payload


def test_flexible_canonical_documentation_target_allows_architecture_correct_path(session):
    freeze = _documentation_freeze(
        session,
        [
            {
                "target": "research observability runtime docs",
                "reason": "Canonical target may be selected during implementation.",
                "trigger": "operator docs",
                "target_type": "CANONICAL_DOCUMENT",
                "exact_path_required": False,
                "acceptable_paths": ["docs/runtime/research-run-lifecycle.md"],
                "acceptable_categories": ["runtime"],
                "canonical_target": "research-run-lifecycle",
            }
        ],
        affected_files=["docs/runtime/research-loop.md"],
    )

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_documentation_artifact(
            ["docs/runtime/research-run-lifecycle.md"],
            canonical_targets=["research-run-lifecycle"],
            categories=["runtime"],
        ),
    )

    assert _dimension(result, "documentation_strategy").status == "PASS"
    assert _dimension(result, "file_path_prediction").status == "PASS"
    assert _dimension(result, "unnecessary_work").status == "PASS"


def test_flexible_documentation_target_rejects_unrelated_documentation(session):
    freeze = _documentation_freeze(
        session,
        [
            {
                "target": "runtime docs",
                "reason": "Runtime docs must be updated.",
                "trigger": "operator docs",
                "target_type": "CANONICAL_DOCUMENT",
                "acceptable_categories": ["runtime"],
            }
        ],
        affected_files=["docs/runtime/research-loop.md"],
    )

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_documentation_artifact(["docs/architecture/unrelated.md"], categories=["architecture"]),
    )

    assert result.result == EngineeringPlanEvaluationResult.FAIL
    assert _dimension(result, "documentation_strategy").status == "FAIL"


def test_proposed_new_documentation_path_remains_enforceable(session):
    freeze = _documentation_freeze(
        session,
        [
            {
                "target": "new repair record",
                "reason": "New phase docs.",
                "trigger": "phase repair",
                "target_type": "NEW_DOCUMENT",
                "proposed_path": "docs/phases/PHASE_X.md",
            }
        ],
        affected_files=["docs/phases/PHASE_X.md"],
    )

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_documentation_artifact(["docs/phases/PHASE_X.md"]),
    )

    assert _dimension(result, "documentation_strategy").status == "PASS"


def test_target_project_canonical_documentation_requirement_matches_semantic_target(session):
    freeze = _documentation_freeze(
        session,
        [
            {
                "target": "target project operational docs",
                "reason": "Implementation may select target canonical document.",
                "trigger": "target docs",
                "target_type": "TARGET_PROJECT_CANONICAL_DOCUMENTATION",
                "canonical_target": "runtime/research-run-lifecycle",
                "acceptable_categories": ["runtime"],
            }
        ],
        affected_files=["docs/runtime/research-loop.md"],
    )

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_documentation_artifact(
            ["docs/runtime/research-run-lifecycle.md"],
            canonical_targets=["runtime/research-run-lifecycle"],
            categories=["runtime"],
        ),
    )

    assert _dimension(result, "documentation_strategy").status == "PASS"


def test_valid_no_file_orchestration_plan_uses_control_plane_evaluation(session):
    freeze = _orchestration_freeze(session)

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_orchestration_artifact(),
    )

    assert result.evaluation_mode == EngineeringPlanEvaluationMode.ORCHESTRATION_CONTROL_PLANE
    assert result.result == EngineeringPlanEvaluationResult.PASS
    assert _dimension(result, "file_path_prediction", required=False) is None


def test_operational_evidence_producer_emits_release_gate_compatible_claims(session):
    freeze = _orchestration_freeze(session)
    repository, snapshot = _repo_snapshot(session, freeze.project_id)
    claims = OperationalEvidenceService(session).create_required_claims(
        project_id=freeze.project_id,
        repository_id=repository.id,
        snapshot_id=snapshot.id,
        task_id=freeze.task_id,
        probe_namespace="phase125",
        context_refs=[freeze.id],
    )
    artifact = build_operational_stage_artifact(
        orchestration_evidence=_orchestration_artifact()["orchestration_evidence"],
        phase_1_22_operational_evidence=claims,
        operational_readiness_recommendation="READY_FOR_ANOTHER_CONTROLLED_MULTI_PROJECT_OPERATIONAL_PILOT",
    )

    evaluation = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=artifact,
    )
    state = _repository_state(session, RepositoryStateClassification.CANONICAL_CLEAN)
    before = _benchmark(session, score=100.0, failed=0)
    after = _benchmark(session, score=100.0, failed=0)

    gate = ReleaseGateService(session).evaluate(
        repository_state_id=state.id,
        deterministic_evaluation_id=evaluation.id,
        benchmark_before_id=before.id,
        benchmark_after_id=after.id,
        repository_integrity_result=RepositoryIntegrityResult.UNCHANGED,
    )

    assert evaluation.result == EngineeringPlanEvaluationResult.PASS
    assert gate.passed is True
    assert gate.blockers == []


def test_orchestration_evaluation_missing_scheduler_evidence_is_insufficient(session):
    freeze = _orchestration_freeze(session)
    artifact = _orchestration_artifact()
    artifact["orchestration_evidence"].pop("scheduler_decisions")

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=artifact,
    )

    assert result.result == EngineeringPlanEvaluationResult.INSUFFICIENT_EVIDENCE
    assert _dimension(result, "scheduler_decisions").applicability == MetricApplicability.NOT_CAPTURED


def test_orchestration_evaluation_missing_resume_evidence_is_insufficient(session):
    freeze = _orchestration_freeze(session)
    artifact = _orchestration_artifact()
    artifact["orchestration_evidence"].pop("checkpoints_resumes")

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=artifact,
    )

    assert result.result == EngineeringPlanEvaluationResult.INSUFFICIENT_EVIDENCE
    assert _dimension(result, "checkpoints_resumes").applicability == MetricApplicability.NOT_CAPTURED


def test_orchestration_evaluation_false_exact_dispatch_claim_fails(session):
    freeze = _orchestration_freeze(session)
    artifact = _orchestration_artifact()
    artifact["orchestration_evidence"]["exact_selection_mutation"] = {"result": "FAIL"}

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=artifact,
    )

    assert result.result == EngineeringPlanEvaluationResult.FAIL
    assert any(correction.severity == "CRITICAL" for correction in result.corrections)


def test_orchestration_evaluation_rejects_unrelated_participant_evidence(session):
    freeze = _orchestration_freeze(session)
    artifact = _orchestration_artifact()
    artifact["orchestration_evidence"]["participating_workflows"] = ["UNRELATED_WORK"]

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=artifact,
    )

    assert result.result == EngineeringPlanEvaluationResult.FAIL
    assert _dimension(result, "orchestration_participants").status == "FAIL"


def test_orchestration_evaluation_missing_authority_evidence_is_insufficient(session):
    freeze = _orchestration_freeze(session)
    artifact = _orchestration_artifact()
    artifact["orchestration_evidence"].pop("authority")

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=artifact,
    )

    assert result.result == EngineeringPlanEvaluationResult.INSUFFICIENT_EVIDENCE
    assert _dimension(result, "authority_compliance").applicability == MetricApplicability.NOT_CAPTURED


def test_ordinary_implementation_plan_still_uses_implementation_evaluation(session):
    freeze = _documentation_freeze(
        session,
        [{"target": "docs/runtime/exact.md", "reason": "Docs.", "trigger": "docs", "exact_path_required": True}],
        affected_files=["src/lucius/pilots/evaluation.py", "docs/runtime/exact.md"],
    )

    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact={
            "architecture": ["pilot evaluation"],
            "components": ["pilot evaluation"],
            "files": ["src/lucius/pilots/evaluation.py", "docs/runtime/exact.md"],
            "known_paths": ["src/lucius/pilots/evaluation.py", "docs/runtime/exact.md"],
            "tests": ["pytest"],
            "documentation": ["docs/runtime/exact.md"],
            "change_evidence": {
                "migrations": {"state": "NO_CHANGE_CONFIRMED"},
                "dependencies": {"state": "NO_CHANGE_CONFIRMED"},
            },
            "risk_level": "LOW",
            "required_authority_level": "L1",
            "material_work": ["pilot evaluation"],
        },
    )

    assert result.evaluation_mode == EngineeringPlanEvaluationMode.PLAN_VS_IMPLEMENTATION


def test_mixed_orchestration_plan_requires_control_and_file_evidence(session):
    freeze = _orchestration_freeze(session, affected_files=["src/lucius/pilots/evaluation.py"])
    result = EngineeringPlanEvaluationService(session).evaluate(
        plan_freeze_id=freeze.id,
        implementation_artifact=_orchestration_artifact(files=[]),
    )

    assert result.evaluation_mode == EngineeringPlanEvaluationMode.ORCHESTRATION_CONTROL_PLANE
    assert result.result == EngineeringPlanEvaluationResult.INSUFFICIENT_EVIDENCE
    assert _dimension(result, "file_path_prediction").applicability == MetricApplicability.NOT_CAPTURED


def _repo_snapshot(session, project_id: str) -> tuple[RepositoryRegistrationORM, RepositorySnapshotORM]:
    repository = RepositoryRegistrationORM(
        id=next_id(session, "repository"),
        project_id=project_id,
        name="phase125-target",
        adapter_type=RepositoryAdapterType.LOCAL_GIT.value,
        location="/tmp/phase125-target",
        canonical_remote=None,
        default_branch="main",
        access_mode=RepositoryAccessMode.READ_ONLY.value,
        status="ACTIVE",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    snapshot = RepositorySnapshotORM(
        id=next_id(session, "snapshot"),
        repository_id=repository.id,
        mode=SnapshotMode.STANDARD.value,
        captured_at=utc_now(),
        branch="main",
        commit_sha="a" * 40,
        is_dirty=False,
        dirty_summary={},
        manifest_hash="b" * 64,
        file_count=1,
        document_count=1,
        test_count=1,
        technology_profile={},
        manifest={},
        documentation_map=[],
        test_map=[],
        configuration_map=[],
        warnings=[],
    )
    session.add(repository)
    session.add(snapshot)
    session.flush()
    return repository, snapshot


def _documentation_freeze(session, requirements: list[dict], *, affected_files: list[str]):
    _project, _task, _contract, plan = _project_task_plan(session)
    plan.affected_components = ["pilot evaluation"]
    plan.affected_files = [
        {"path": path, "status": "LIKELY_EXISTING" if not path.startswith("docs/phases/PHASE_X") else "NEW_PROPOSED"}
        for path in affected_files
    ]
    plan.steps = []
    plan.documentation_requirements = requirements
    session.flush()
    return PlanFreezeService(session).freeze(plan_id=plan.id)


def _documentation_artifact(
    docs: list[str],
    *,
    canonical_targets: list[str] | None = None,
    categories: list[str] | None = None,
) -> dict:
    files = list(docs)
    return {
        "architecture": ["pilot evaluation"],
        "components": ["pilot evaluation"],
        "files": files,
        "known_paths": files,
        "tests": ["pytest"],
        "documentation": docs,
        "documentation_evidence": {
            "paths": docs,
            "canonical_targets": canonical_targets or [],
            "categories": categories or [],
        },
        "change_evidence": {
            "migrations": {"state": "NO_CHANGE_CONFIRMED"},
            "dependencies": {"state": "NO_CHANGE_CONFIRMED"},
        },
        "risk_level": "LOW",
        "required_authority_level": "L1",
        "material_work": ["pilot evaluation"],
    }


def _orchestration_freeze(session, *, affected_files: list[str] | None = None):
    _project, _task, _contract, plan = _project_task_plan(session)
    plan.orchestration_contract_required = True
    contract = _complete_orchestration_contract()
    contract["projects"] = ["LPROJ_A", "LPROJ_B"]
    contract["workflows"] = ["LWORK_A", "LWORK_B"]
    plan.orchestration_contract = contract
    plan.adversarial_probes = _complete_adversarial_probes(plan.id)
    plan.unknowns = [{"statement": "Orchestration evidence must be reconstructed from persisted operational artifacts."}]
    plan.affected_components = ["global queue", "persistent workflows", "plan freeze", "target isolation", "evaluation"]
    plan.affected_files = [{"path": path, "status": "LIKELY_EXISTING"} for path in (affected_files or [])]
    plan.steps = [
        {
            "step_id": "STEP-1",
            "sequence": 1,
            "title": "Evaluate orchestration evidence",
            "description": "Evaluate persisted control-plane evidence.",
            "affected_components": ["evaluation"],
            "affected_files": list(affected_files or []),
            "expected_result": "Control-plane evidence is evaluated.",
            "validation": "Orchestration evaluation dimensions pass or fail closed.",
        }
    ]
    plan.test_strategy = [{"kind": "INTEGRATION", "description": "Evaluate persisted orchestration evidence."}]
    plan.documentation_requirements = [
        {
            "target": "canonical closure report",
            "reason": "Operational pilot evidence.",
            "trigger": "pilot closure",
            "target_type": "CANONICAL_DOCUMENT",
            "exact_path_required": False,
            "canonical_target": "canonical closure report",
        }
    ]
    session.flush()
    return PlanFreezeService(session).freeze(plan_id=plan.id)


def _orchestration_artifact(*, files: list[str] | None = None) -> dict:
    return {
        "architecture": ["global queue", "persistent workflows", "plan freeze", "target isolation", "evaluation"],
        "components": ["global queue", "persistent workflows", "plan freeze", "target isolation", "evaluation"],
        "files": files or [],
        "known_paths": files or [],
        "migrations": [],
        "tests": ["target verification", "full Lucius suite", "POST benchmark"],
        "documentation": ["canonical closure report"],
        "change_evidence": {
            "migrations": {"state": "NO_CHANGE_CONFIRMED"},
            "dependencies": {"state": "NO_CHANGE_CONFIRMED"},
        },
        "risk_level": "LOW",
        "required_authority_level": "L1",
        "material_work": ["global queue", "persistent workflows", "plan freeze", "target isolation", "evaluation"],
        "orchestration_evidence": {
            "participating_workflows": ["LWORK_A", "LWORK_B"],
            "participating_projects": ["LPROJ_A", "LPROJ_B"],
            "pre_mutation_release": {"result": "PASS"},
            "scheduler_decisions": [
                {"item_id": "A", "mutation_identity_matches_selection": True},
                {"item_id": "B", "mutation_identity_matches_selection": True},
            ],
            "blockers_capacity_release": {"result": "PASS"},
            "checkpoints_resumes": [
                {"checkpoint_id": "LQCHK_A", "resolved": True, "fresh_session_reconstruction": True}
            ],
            "priority": {"result": "PASS"},
            "no_preemption": {"result": "PASS"},
            "exact_selection_mutation": {"result": "PASS"},
            "dependency_isolation": {"result": "PASS"},
            "fresh_process_reconstruction": {"result": "PASS"},
            "target_isolation": {"result": "PASS"},
            "provenance_refs": ["LPLAN_000001", "LFREEZE_000001", "LAUDIT_000001", "LQCHK_000001"],
            "tests": {"result": "PASS"},
            "documentation": {"result": "PASS"},
            "authority": {"result": "PASS", "violations": []},
            "closure_claims": {"result": "PASS"},
        },
    }


def _dimension(result, name: str, *, required: bool = True):
    dimension = next((item for item in result.dimensions if item.name == name), None)
    if required and dimension is None:
        raise AssertionError(f"missing dimension {name}")
    return dimension


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
