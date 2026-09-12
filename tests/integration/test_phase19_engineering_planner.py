from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from lucius.domain.enums import (
    AcceptanceCoverageStatus,
    Actor,
    AffectedFileStatus,
    AllowedAction,
    AuthorityLevel,
    CostClass,
    EngineeringPlanStatus,
    Environment,
    KnowledgeScope,
    MemoryType,
    ModelCapability,
    ModelResponseStatus,
    PlanRiskLevel,
    PlanningBlockerCode,
    PlanStepSupportStatus,
    PrivacyClass,
    ProjectType,
    ProviderType,
    ReplanReason,
    TaskComplexity,
    TestStrategyKind as StrategyKind,
    ValidationStatus,
)
from lucius.memory.service import MemoryService
from lucius.models.gateway import ModelGateway
from lucius.models.providers.base import ProviderAdapter, ProviderGeneration
from lucius.models.schemas import ModelProfile, ModelProvider, ModelRequest
from lucius.persistence.orm import AuditEventORM, EngineeringPlanORM, EvidenceReferenceORM, MemoryEntryORM
from lucius.persistence.repositories import RepositorySnapshotService
from lucius.planning.context import PlanningContextBuilder
from lucius.planning.persistence import EngineeringPlanRepository
from lucius.planning.schemas import DeterministicAcceptanceCheck, PlanningContextBudget, PlanningEvidenceItem
from lucius.planning.service import EngineeringPlannerService
from lucius.planning.validation import validate_and_enrich_plan
from lucius.projects.service import ProjectRegistryService
from lucius.repositories.schemas import WorkspaceContext
from lucius.tasks.service import TaskService

from tests.conftest import run_git


_FIXTURE_COUNTER = 0
_GATEWAY_COUNTER = 0


class PlannerAdapter(ProviderAdapter):
    def __init__(self, provider_id: str, *, mode: str = "valid"):
        super().__init__(provider_id)
        self.mode = mode
        self.requests: list[ModelRequest] = []

    def validate_configuration(self) -> None:
        return None

    def is_available(self) -> bool:
        return True

    def generate(self, request: ModelRequest, profile: ModelProfile) -> ProviderGeneration:
        del profile
        self.requests.append(request)
        if self.mode == "invalid_schema":
            return ProviderGeneration(structured_data={"summary": "missing required fields"})
        context = json.loads(request.input_messages[0].content)
        evidence = context["current_evidence"]
        memory = context["historical_memory"]
        acceptance = context["contract"]["acceptance_criteria"]
        evidence_id = evidence[0]["evidence_id"] if evidence else "LEVID_MISSING"
        evidence_path = evidence[0]["path"] if evidence else "UNKNOWN"
        memory_ids = [item["memory_id"] for item in memory[:1]]
        step_title = "Update snapshot reuse comparison"
        step_description = "Use current implementation_b evidence to preserve deterministic manifest identity."
        affected_files = [evidence_path, "docs/phases/PHASE_1_9_ENGINEERING_PLANNER.md", "missing_verified.py"]
        if self.mode == "authority":
            step_title = "Run production schema migration"
            step_description = "Apply a production schema migration for persisted planner output."
            affected_files = ["alembic/versions/new_revision.py"]
        if self.mode == "security":
            step_description = "Review security and credential handling before changing planner prompts."
        required_evidence = [evidence_id]
        if self.mode == "bad_evidence":
            required_evidence = ["LEVID_DOES_NOT_EXIST"]
        coverage = [
            {
                "criterion_id": item["id"],
                "status": AcceptanceCoverageStatus.COVERED.value,
                "step_ids": ["STEP-001"],
                "evidence_ids": required_evidence,
                "notes": "Covered by planned validation.",
            }
            for item in acceptance
        ]
        if self.mode == "missing_coverage":
            coverage = coverage[:-1]
        if self.mode == "uncovered":
            coverage[0]["status"] = AcceptanceCoverageStatus.NOT_COVERED.value
        unknowns = []
        if self.mode == "blocking_unknown":
            unknowns = [{"statement": "Deployment target is unknown.", "blocking": True, "needed_to_resolve": "Human decision"}]
            required_evidence = []
            coverage = [{**item, "evidence_ids": []} for item in coverage]
        data = {
            "summary": "Evidence-bound plan for snapshot reuse.",
            "objective": context["contract"]["objective"],
            "risk_level": PlanRiskLevel.LOW.value,
            "required_authority_level": AuthorityLevel.L1.value,
            "assumptions": [{"statement": "Existing test style remains acceptable.", "verified": False, "evidence_ids": []}],
            "unknowns": unknowns or [{"statement": "Exact implementation detail may change during coding.", "blocking": False}],
            "open_questions": [
                {
                    "question": "Should planner output remain JSON-column based for v0.1?",
                    "reason": "It affects later normalization.",
                    "blocking": False,
                    "required_decision_authority": AuthorityLevel.L1.value,
                }
            ],
            "affected_components": ["repository snapshots", "planning"],
            "affected_files": [{"path": path, "status": AffectedFileStatus.UNKNOWN.value, "evidence_ids": []} for path in affected_files],
            "steps": [
                {
                    "step_id": "STEP-001",
                    "sequence": 1,
                    "title": step_title,
                    "description": step_description,
                    "affected_components": ["repository snapshots"],
                    "affected_files": affected_files,
                    "required_evidence_ids": required_evidence,
                    "memory_ids": memory_ids,
                    "dependencies": [],
                    "expected_result": "Planner preserves evidence and memory provenance.",
                    "validation": "Run integration and regression tests.",
                    "risk": PlanRiskLevel.LOW.value,
                    "authority_level": AuthorityLevel.L1.value,
                    "support_status": PlanStepSupportStatus.INFERRED.value,
                }
            ],
            "acceptance_coverage": coverage,
            "test_strategy": [
                {
                    "kind": StrategyKind.UNIT.value,
                    "description": "Validate reference and support classification.",
                    "acceptance_criteria": [item["id"] for item in acceptance],
                    "evidence_ids": required_evidence,
                },
                {
                    "kind": StrategyKind.INTEGRATION.value,
                    "description": "Exercise planner through ModelGateway.",
                    "acceptance_criteria": [item["id"] for item in acceptance],
                    "evidence_ids": required_evidence,
                },
            ],
            "documentation_requirements": [
                {"target": "docs/phases/PHASE_1_9_ENGINEERING_PLANNER.md", "reason": "phase implementation", "trigger": "phase"}
            ],
            "rollback_considerations": ["Discard proposed plan; no repository writes occur in Phase 1.9."],
            "dependencies": [],
            "risks": [{"risk": step_description, "level": PlanRiskLevel.LOW.value, "mitigation": "Use deterministic validation."}],
            "estimated_scope": "small",
            "confidence": 0.72,
        }
        if self.mode == "verified_assumption":
            data["assumptions"][0]["verified"] = True
        return ProviderGeneration(structured_data=data, input_tokens=50, output_tokens=80, total_tokens=130, latency_ms=9)


def _planner_gateway(session, *, mode: str = "valid", privacy: PrivacyClass = PrivacyClass.INTERNAL) -> tuple[ModelGateway, PlannerAdapter]:
    global _GATEWAY_COUNTER
    _GATEWAY_COUNTER += 1
    gateway = ModelGateway(session)
    provider = gateway.register_provider(ModelProvider(name=f"planner-{_GATEWAY_COUNTER}", provider_type=ProviderType.CUSTOM))
    adapter = PlannerAdapter(provider.id, mode=mode)
    gateway.register_provider_adapter(adapter)
    gateway.register_profile(
        ModelProfile(
            provider_id=provider.id,
            model_name="planner-model",
            display_name="Planner Model",
            capabilities={ModelCapability.PLANNING, ModelCapability.STRUCTURED_OUTPUT, ModelCapability.CODE_REASONING, ModelCapability.HIGH_REASONING},
            privacy_class=privacy,
            cost_class=CostClass.LOW,
            quality_score=80,
            supports_structured_output=True,
            supports_code=True,
            supports_reasoning=True,
        )
    )
    return gateway, adapter


def _snapshot_task_fixture(
    session,
    git_repo: Path,
    workspace: WorkspaceContext,
    *,
    project_type: ProjectType = ProjectType.DFG_INTERNAL,
    authority: AuthorityLevel = AuthorityLevel.L1,
    environment: Environment = Environment.SANDBOX,
    complexity: TaskComplexity = TaskComplexity.T1,
) -> dict[str, Any]:
    global _FIXTURE_COUNTER
    _FIXTURE_COUNTER += 1
    fixture_number = _FIXTURE_COUNTER
    (git_repo / "src" / "lucius" / "persistence").mkdir(parents=True, exist_ok=True)
    (git_repo / "src" / "lucius" / "persistence" / "repositories.py").write_text(
        "class RepositorySnapshotService:\n"
        "    # implementation_b snapshot reuse behavior manifest_hash deterministic identity\n"
        "    # snapshot reuse behavior manifest_hash deterministic identity snapshot reuse behavior\n"
        "    # repository snapshot reuse logic preserves deterministic manifest identity\n"
        "    def reuse_snapshot(self):\n"
        "        implementation_b = 'manifest_hash comparison with branch and dirty state'\n"
        f"        fixture_number = {fixture_number}\n"
        "        return implementation_b\n",
        encoding="utf-8",
    )
    run_git(git_repo, "add", ".")
    run_git(git_repo, "commit", "-m", "snapshot reuse implementation")
    registry = ProjectRegistryService(session)
    project = registry.register_project(f"Planner Project {fixture_number}", project_type=project_type)
    repository = registry.attach_repository(project_id=project.id, name="Repo", location=git_repo, workspace_context=workspace)
    snapshot = RepositorySnapshotService(session).inspect_repository(repository_id=repository.id, workspace_context=workspace)
    tasks = TaskService(session)
    task = tasks.create_task(
        project_id=project.id,
        title="Change snapshot reuse behavior",
        objective="Modify repository snapshot reuse logic while preserving deterministic manifest identity.",
        complexity=complexity,
        authority_level=authority,
        created_by=Actor.CODEX,
    )
    contract = tasks.create_or_update_contract(
        task_id=task.id,
        objective="Modify repository snapshot reuse logic while preserving deterministic manifest identity.",
        acceptance_criteria=[
            "Snapshot reuse compares manifest identity deterministically.",
            "Existing repository core tests remain passing.",
            "Phase documentation records planner behavior.",
        ],
        repository_ids=[repository.id],
        allowed_actions=[AllowedAction.READ_REPOSITORY, AllowedAction.WRITE_SOURCE, AllowedAction.WRITE_TESTS, AllowedAction.WRITE_DOCUMENTATION],
        authority_level=authority,
        environment=environment,
        documentation_required=True,
        documentation_targets=["docs/phases/PHASE_1_9_ENGINEERING_PLANNER.md"],
    )
    tasks.mark_ready(task.id, actor=Actor.CODEX)
    return {"project": project, "repository": repository, "snapshot": snapshot, "task": task, "contract": contract}


def _create_plan(session, fixture, workspace, *, adapter_mode: str = "valid", gateway_privacy: PrivacyClass = PrivacyClass.INTERNAL):
    gateway, adapter = _planner_gateway(session, mode=adapter_mode, privacy=gateway_privacy)
    result = EngineeringPlannerService(session, model_gateway=gateway).create_plan(
        task_id=fixture["task"].id,
        snapshot_ids=[fixture["snapshot"].snapshot_id],
        workspace_contexts={fixture["repository"].id: workspace},
        actor=Actor.CODEX,
    )
    return result, adapter


def test_planning_context_creation_selection_ordering_budget_and_scope(session, git_repo: Path, workspace):
    fixture = _snapshot_task_fixture(session, git_repo, workspace)
    memory = MemoryService(session)
    project_memory = memory.create_memory(
        memory_type=MemoryType.PROJECT,
        scope=KnowledgeScope.PROJECT,
        project_id=fixture["project"].id,
        statement="Snapshot reuse uses implementation B and preserves manifest_hash.",
        confidence=0.9,
        validation_status=ValidationStatus.VALIDATED,
        source_reference="docs",
    )
    dfg_memory = memory.create_memory(
        memory_type=MemoryType.PROCEDURAL,
        scope=KnowledgeScope.DFG,
        statement="Snapshot reuse planning should respect current evidence.",
        confidence=0.8,
        validation_status=ValidationStatus.VALIDATED,
        source_reference="global policy",
    )
    superseded = memory.create_memory(
        memory_type=MemoryType.PROJECT,
        scope=KnowledgeScope.PROJECT,
        project_id=fixture["project"].id,
        statement="Snapshot reuse uses old superseded behavior.",
        confidence=0.8,
        validation_status=ValidationStatus.VALIDATED,
        source_reference="old",
    )
    contradicted = memory.create_memory(
        memory_type=MemoryType.PROJECT,
        scope=KnowledgeScope.PROJECT,
        project_id=fixture["project"].id,
        statement="Snapshot reuse uses contradicted behavior.",
        confidence=0.8,
        validation_status=ValidationStatus.VALIDATED,
        source_reference="old",
    )
    stale = memory.create_memory(
        memory_type=MemoryType.PROJECT,
        scope=KnowledgeScope.PROJECT,
        project_id=fixture["project"].id,
        statement="Snapshot reuse memory requires revalidation.",
        confidence=0.7,
        validation_status=ValidationStatus.VALIDATED,
        source_reference="stale",
    )
    memory.supersede_memory(superseded.id, project_memory.id, reason="newer")
    memory.contradict_memory(contradicted.id, reason="wrong")
    memory.mark_requires_revalidation(stale.id, reason="stale evidence")
    other_project = ProjectRegistryService(session).register_project("Other Client", project_type=ProjectType.CLIENT)
    memory.create_memory(
        memory_type=MemoryType.PROJECT,
        scope=KnowledgeScope.CLIENT,
        project_id=other_project.id,
        statement="Snapshot reuse client-only memory must not leak.",
        confidence=0.9,
        validation_status=ValidationStatus.VALIDATED,
        source_reference="client",
    )
    context, blockers = PlanningContextBuilder(session).build(
        task_id=fixture["task"].id,
        snapshot_ids=[fixture["snapshot"].snapshot_id],
        workspace_contexts={fixture["repository"].id: workspace},
        budget=PlanningContextBudget(max_evidence_items=1, max_memory_items=2, max_total_context_bytes=5000, max_per_source_bytes=200),
        actor=Actor.CODEX,
    )
    assert blockers == []
    assert context.task_id == fixture["task"].id
    assert context.task_contract_version == fixture["contract"].version
    assert context.evidence[0].path == "src/lucius/persistence/repositories.py"
    assert [item.memory_id for item in context.memory] == [project_memory.id, stale.id]
    assert superseded.id not in [item.memory_id for item in context.memory]
    assert contradicted.id not in [item.memory_id for item in context.memory]
    assert dfg_memory.id not in [item.memory_id for item in context.memory]
    assert context.omitted_counts["evidence"] >= 0
    assert any(warning["code"] in {"PLANNING_CONTEXT_BUDGET_REACHED", "MEMORY_REVALIDATION_REQUIRED"} for warning in context.context_warnings)


def test_successful_planner_flow_persists_provenance_and_uses_gateway_abstraction(session, git_repo: Path, workspace):
    fixture = _snapshot_task_fixture(session, git_repo, workspace)
    MemoryService(session).create_memory(
        memory_type=MemoryType.PROJECT,
        scope=KnowledgeScope.PROJECT,
        project_id=fixture["project"].id,
        statement="Snapshot reuse uses implementation B.",
        confidence=0.9,
        validation_status=ValidationStatus.VALIDATED,
        source_reference="evidence",
    )
    result, adapter = _create_plan(session, fixture, workspace)
    plan = result.plan
    assert plan is not None
    assert result.blockers == []
    assert plan.status == EngineeringPlanStatus.PROPOSED
    assert plan.task_id == fixture["task"].id
    assert plan.task_contract_version == fixture["contract"].version
    assert plan.repository_snapshot_ids == [fixture["snapshot"].snapshot_id]
    assert plan.evidence_ids
    assert plan.memory_ids
    assert plan.model_execution_ids
    assert plan.steps[0].support_status == PlanStepSupportStatus.SUPPORTED
    assert plan.affected_files[0].status == AffectedFileStatus.NEW_PROPOSED
    assert any(item.status == AffectedFileStatus.EXISTING_VERIFIED for item in plan.affected_files)
    assert any(item.status == AcceptanceCoverageStatus.COVERED for item in plan.acceptance_coverage)
    assert {item.kind for item in plan.test_strategy} >= {StrategyKind.UNIT, StrategyKind.INTEGRATION}
    assert plan.documentation_requirements[0].target == "docs/phases/PHASE_1_9_ENGINEERING_PLANNER.md"
    assert adapter.requests
    request = adapter.requests[0]
    assert request.required_capabilities >= {ModelCapability.PLANNING, ModelCapability.STRUCTURED_OUTPUT}
    assert request.privacy_class == PrivacyClass.INTERNAL
    assert not hasattr(plan, "openai_response")
    persisted = session.get(EngineeringPlanORM, plan.id)
    assert persisted.summary == plan.summary


def test_memory_conflict_prefers_current_evidence_and_marks_revalidation(session, git_repo: Path, workspace):
    fixture = _snapshot_task_fixture(session, git_repo, workspace)
    memory = MemoryService(session).create_memory(
        memory_type=MemoryType.PROJECT,
        scope=KnowledgeScope.PROJECT,
        project_id=fixture["project"].id,
        statement="Snapshot reuse uses implementation A.",
        confidence=0.9,
        validation_status=ValidationStatus.VALIDATED,
        source_reference="old evidence",
    )
    context, blockers = PlanningContextBuilder(session).build(
        task_id=fixture["task"].id,
        snapshot_ids=[fixture["snapshot"].snapshot_id],
        workspace_contexts={fixture["repository"].id: workspace},
        actor=Actor.CODEX,
    )
    assert blockers == []
    assert any(warning["code"] == "MEMORY_CONFLICT_WITH_REPOSITORY" for warning in context.context_warnings)
    assert session.get(MemoryEntryORM, memory.id).requires_revalidation is True
    result, _adapter = _create_plan(session, fixture, workspace)
    assert result.plan is not None
    assert "implementation_b" in json.dumps([item.model_dump(mode="json") for item in result.plan.steps])


def test_model_policy_block_and_invalid_structured_output_are_safe_failures(session, git_repo: Path, workspace):
    client_fixture = _snapshot_task_fixture(session, git_repo, workspace, project_type=ProjectType.CLIENT)
    blocked, _adapter = _create_plan(session, client_fixture, workspace, gateway_privacy=PrivacyClass.PUBLIC)
    assert blocked.plan is None
    assert blocked.blockers[0].code == PlanningBlockerCode.MODEL_POLICY_BLOCK
    assert session.scalars(select(EngineeringPlanORM)).all() == []

    valid_fixture = _snapshot_task_fixture(session, git_repo, workspace)
    invalid, _adapter = _create_plan(session, valid_fixture, workspace, adapter_mode="invalid_schema")
    assert invalid.plan is None
    assert invalid.blockers[0].code in {PlanningBlockerCode.MODEL_POLICY_BLOCK, PlanningBlockerCode.ENGINEERING_PLAN_INVALID}


def test_plan_validation_rejects_bad_references_missing_coverage_and_verified_assumptions(session, git_repo: Path, workspace):
    fixture = _snapshot_task_fixture(session, git_repo, workspace)
    for mode in ["bad_evidence", "missing_coverage", "uncovered", "verified_assumption"]:
        result, _adapter = _create_plan(session, fixture, workspace, adapter_mode=mode)
        assert result.plan is None
        assert any(blocker.code == PlanningBlockerCode.ENGINEERING_PLAN_INVALID for blocker in result.blockers)


def test_wrong_project_evidence_rejected_by_validator(session, git_repo: Path, workspace):
    fixture = _snapshot_task_fixture(session, git_repo, workspace)
    result, _adapter = _create_plan(session, fixture, workspace)
    model_plan = result.plan
    assert model_plan is not None
    context, blockers = PlanningContextBuilder(session).build(
        task_id=fixture["task"].id,
        snapshot_ids=[fixture["snapshot"].snapshot_id],
        workspace_contexts={fixture["repository"].id: workspace},
    )
    assert blockers == []
    other = _snapshot_task_fixture(session, git_repo, workspace)
    PlanningContextBuilder(session).build(
        task_id=other["task"].id,
        snapshot_ids=[other["snapshot"].snapshot_id],
        workspace_contexts={other["repository"].id: workspace},
    )
    wrong_evidence = session.scalar(select(EvidenceReferenceORM).where(EvidenceReferenceORM.project_id == other["project"].id))
    context.evidence = [
        PlanningEvidenceItem(
            evidence_id=wrong_evidence.id,
            repository_id=wrong_evidence.repository_id,
            snapshot_id=wrong_evidence.snapshot_id,
            source_type=wrong_evidence.source_type,
            path=wrong_evidence.path,
            snippet=wrong_evidence.snippet,
            relevance_score=wrong_evidence.relevance_score,
        )
    ]
    enriched, blockers, _warnings = validate_and_enrich_plan(session, context=context, plan=model_plan)
    del enriched
    assert any(blocker.code == PlanningBlockerCode.ENGINEERING_PLAN_INVALID for blocker in blockers)


def test_risk_authority_and_support_guardrails(session, git_repo: Path, workspace):
    low_fixture = _snapshot_task_fixture(session, git_repo, workspace)
    low, _adapter = _create_plan(session, low_fixture, workspace)
    assert low.plan.risk_level == PlanRiskLevel.LOW
    assert low.plan.required_authority_level == AuthorityLevel.L1

    migration_fixture = _snapshot_task_fixture(session, git_repo, workspace, authority=AuthorityLevel.L1, environment=Environment.PRODUCTION)
    authority, _adapter = _create_plan(session, migration_fixture, workspace, adapter_mode="authority")
    assert authority.plan is not None
    assert authority.plan.risk_level == PlanRiskLevel.CRITICAL
    assert authority.plan.required_authority_level == AuthorityLevel.L3
    assert authority.blockers[0].code == PlanningBlockerCode.AUTHORITY_ESCALATION_REQUIRED

    security_fixture = _snapshot_task_fixture(session, git_repo, workspace)
    security, _adapter = _create_plan(session, security_fixture, workspace, adapter_mode="security")
    assert security.plan.risk_level == PlanRiskLevel.HIGH

    unknown_fixture = _snapshot_task_fixture(session, git_repo, workspace)
    unknown, _adapter = _create_plan(session, unknown_fixture, workspace, adapter_mode="blocking_unknown")
    assert unknown.plan is not None
    assert unknown.plan.steps[0].support_status == PlanStepSupportStatus.BLOCKED_BY_UNKNOWN


def test_plan_versioning_replanning_and_rejection_preserve_history(session, git_repo: Path, workspace):
    fixture = _snapshot_task_fixture(session, git_repo, workspace)
    gateway, _adapter = _planner_gateway(session)
    service = EngineeringPlannerService(session, model_gateway=gateway)
    first = service.create_plan(
        task_id=fixture["task"].id,
        snapshot_ids=[fixture["snapshot"].snapshot_id],
        workspace_contexts={fixture["repository"].id: workspace},
        actor=Actor.CODEX,
    ).plan
    second = service.replan(
        previous_plan_id=first.id,
        reason=ReplanReason.NEW_EVIDENCE,
        snapshot_ids=[fixture["snapshot"].snapshot_id],
        workspace_contexts={fixture["repository"].id: workspace},
        actor=Actor.CODEX,
    ).plan
    old_row = session.get(EngineeringPlanORM, first.id)
    assert first.version == 1
    assert second.version == 2
    assert second.supersedes_plan_id == first.id
    assert old_row.status == EngineeringPlanStatus.SUPERSEDED.value
    rejected = service.reject_plan(second.id, reason="Human requested revision", actor=Actor.HUMAN)
    assert rejected.status == EngineeringPlanStatus.REJECTED


def test_planning_audit_no_chain_of_thought_and_no_model_response_promotion(session, git_repo: Path, workspace):
    fixture = _snapshot_task_fixture(session, git_repo, workspace)
    result, _adapter = _create_plan(session, fixture, workspace)
    assert result.plan is not None
    event_types = [event.event_type for event in session.scalars(select(AuditEventORM)).all()]
    assert "PLANNING_STARTED" in event_types
    assert "PLANNING_CONTEXT_CREATED" in event_types
    assert "PLANNER_MODEL_REQUESTED" in event_types
    assert "ENGINEERING_PLAN_CREATED" in event_types
    audit_json = json.dumps([event.event_metadata for event in session.scalars(select(AuditEventORM)).all()])
    assert "chain_of_thought" not in audit_json
    assert not session.scalars(select(EvidenceReferenceORM).where(EvidenceReferenceORM.source_type == "MODEL_RESPONSE")).all()
    assert not session.scalars(select(MemoryEntryORM).where(MemoryEntryORM.source_reference == result.plan.model_execution_ids[0])).all()


def test_alembic_0005_to_head_and_clean_db_to_head(tmp_path):
    root = Path(__file__).resolve().parents[2]

    def config_for(path: Path) -> Config:
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{path}")
        return config

    upgrade_db = tmp_path / "from_0005.sqlite"
    command.upgrade(config_for(upgrade_db), "0005_model_gateway")
    command.upgrade(config_for(upgrade_db), "head")

    clean_db = tmp_path / "clean.sqlite"
    command.upgrade(config_for(clean_db), "head")



def test_phase130_deterministic_acceptance_validation_and_persistence(
    session,
    git_repo: Path,
    workspace,
):
    fixture = _snapshot_task_fixture(session, git_repo, workspace)
    result, _adapter = _create_plan(session, fixture, workspace)

    assert result.plan is not None
    assert result.context is not None
    assert result.plan.affected_files

    target_path = result.plan.affected_files[0].path
    expected_text = "phase 1.30 deterministic acceptance\n"

    valid_check = DeterministicAcceptanceCheck(
        type="exact_file_content",
        path=target_path,
        expected_text=expected_text,
    )

    valid_candidate = result.plan.model_copy(
        update={
            "deterministic_acceptance_checks": [valid_check],
        }
    )

    enriched, valid_blockers, warnings = validate_and_enrich_plan(
        session,
        context=result.context,
        plan=valid_candidate,
    )

    invalid_validation_blockers = [
        blocker
        for blocker in valid_blockers
        if blocker.code == PlanningBlockerCode.ENGINEERING_PLAN_INVALID
    ]

    assert invalid_validation_blockers == []
    assert enriched.deterministic_acceptance_checks == [valid_check]

    persisted = EngineeringPlanRepository(session).create(
        context=result.context,
        model_plan=enriched,
        model_execution_ids=[],
        blockers=[],
        validation_warnings=warnings,
        planner_version="phase-1.30-test",
        created_by=Actor.CODEX.value,
    )

    row = session.get(EngineeringPlanORM, persisted.id)

    assert row is not None
    assert row.deterministic_acceptance_checks == [
        {
            "type": "exact_file_content",
            "path": target_path,
            "expected_text": expected_text,
        }
    ]

    assert persisted.deterministic_acceptance_checks[0].type == "exact_file_content"
    assert persisted.deterministic_acceptance_checks[0].path == target_path
    assert persisted.deterministic_acceptance_checks[0].expected_text == expected_text

    outside_check = DeterministicAcceptanceCheck(
        type="exact_file_content",
        path="outside-authorized-scope.txt",
        expected_text="must never be accepted\n",
    )

    outside_candidate = result.plan.model_copy(
        update={
            "deterministic_acceptance_checks": [outside_check],
        }
    )

    _outside_enriched, outside_blockers, _outside_warnings = validate_and_enrich_plan(
        session,
        context=result.context,
        plan=outside_candidate,
    )

    deterministic_blockers = [
        blocker
        for blocker in outside_blockers
        if blocker.code == PlanningBlockerCode.ENGINEERING_PLAN_INVALID
        and "outside-authorized-scope.txt" in repr(blocker.model_dump(mode="json"))
    ]

    assert deterministic_blockers, [
        blocker.model_dump(mode="json")
        for blocker in outside_blockers
    ]
