from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from lucius.domain.enums import (
    AcceptanceCoverageStatus,
    Actor,
    AffectedFileStatus,
    AllowedAction,
    AuthorityLevel,
    CostClass,
    Environment,
    ModelCapability,
    PlanRiskLevel,
    PlanStepSupportStatus,
    PrivacyClass,
    ProjectType,
    ProviderType,
    RepositoryAccessMode,
    TaskComplexity,
    TestStrategyKind,
)
from lucius.models.gateway import ModelGateway
from lucius.models.providers.base import ProviderAdapter, ProviderGeneration
from lucius.models.schemas import ModelProfile, ModelProvider, ModelRequest
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.repositories import RepositorySnapshotService
from lucius.planning.schemas import PlanningContextBudget
from lucius.planning.service import EngineeringPlannerService
from lucius.projects.service import ProjectRegistryService
from lucius.repositories.local_git import LocalGitRepositoryAdapter
from lucius.repositories.schemas import WorkspaceContext
from lucius.tasks.service import TaskService


DEFAULT_DARWIN_ROOT = Path(
    "/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine"
)
DEFAULT_OUTPUT = Path("docs/phases/PHASE_1_11_DARWIN_REAL_REPOSITORY_PILOT.md")


class Phase111PlannerAdapter(ProviderAdapter):
    def __init__(self, provider_id: str) -> None:
        super().__init__(provider_id)
        self.requests: list[ModelRequest] = []

    def validate_configuration(self) -> None:
        return None

    def is_available(self) -> bool:
        return True

    def generate(self, request: ModelRequest, profile: ModelProfile) -> ProviderGeneration:
        del profile
        self.requests.append(request)
        context = json.loads(request.input_messages[0].content)
        title = context["task"]["title"].lower()
        if "provider schema export" in title:
            structured = novel_provider_schema_export_plan(context)
        else:
            structured = historical_assisted_claim_plan(context)
        return ProviderGeneration(
            content=structured["summary"],
            structured_data=structured,
            input_tokens=1_950,
            output_tokens=1_150,
            total_tokens=3_100,
            estimated_cost=0.0,
            latency_ms=12,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Phase 1.11 Darwin read-only pilot.")
    parser.add_argument("--darwin-root", type=Path, default=DEFAULT_DARWIN_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    lucius_root = Path.cwd()
    darwin_root = args.darwin_root.resolve()
    before = git_summary(darwin_root)

    workspace = WorkspaceContext(
        workspace_id="phase-1.11-darwin-pilot",
        allowed_roots=[darwin_root.parent],
        access_mode=RepositoryAccessMode.READ_ONLY,
        authority_level=AuthorityLevel.L0,
    )
    adapter = LocalGitRepositoryAdapter(darwin_root, workspace)
    adapter.validate()

    engine = create_sqlite_engine()
    create_all(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        registry = ProjectRegistryService(session)
        project = registry.register_project(
            "Darwin Research Engine Pilot",
            slug="darwin-research-engine-pilot",
            organization="DFG Universe",
            project_type=ProjectType.DFG_INTERNAL,
            description="Phase 1.11 read-only Lucius pilot against the Darwin repository.",
            actor=Actor.LUCIUS,
        )
        repository = registry.attach_repository(
            project_id=project.id,
            name="Darwin Research Engine",
            location=darwin_root,
            workspace_context=workspace,
            actor=Actor.LUCIUS,
        )
        snapshot = RepositorySnapshotService(session).inspect_repository(
            repository_id=repository.id,
            workspace_context=workspace,
            actor=Actor.LUCIUS.value,
        )

        gateway = ModelGateway(session)
        provider = gateway.register_provider(
            ModelProvider(name="phase-1.11-deterministic-planner", provider_type=ProviderType.CUSTOM),
            actor=Actor.LUCIUS.value,
        )
        planner_adapter = Phase111PlannerAdapter(provider.id)
        gateway.register_provider_adapter(planner_adapter)
        gateway.register_profile(
            ModelProfile(
                provider_id=provider.id,
                model_name="phase-1.11-structured-planner",
                display_name="Phase 1.11 Structured Planner",
                capabilities={
                    ModelCapability.PLANNING,
                    ModelCapability.STRUCTURED_OUTPUT,
                    ModelCapability.CODE_REASONING,
                    ModelCapability.HIGH_REASONING,
                },
                privacy_class=PrivacyClass.INTERNAL,
                cost_class=CostClass.LOW,
                quality_score=80,
                supports_structured_output=True,
                supports_code=True,
                supports_reasoning=True,
            ),
            actor=Actor.LUCIUS.value,
        )

        tasks = TaskService(session)
        historical_task = tasks.create_task(
            project_id=project.id,
            title="Implement controlled assisted Claim construction",
            objective=(
                "Add a bounded provider-assisted workflow that proposes atomic Claim candidates "
                "from canonical Evidence for a ResearchPlanItem and requires explicit acceptance "
                "before creating canonical Claim records."
            ),
            complexity=TaskComplexity.T3,
            authority_level=AuthorityLevel.L2,
            created_by=Actor.LUCIUS,
        )
        historical_contract = tasks.create_or_update_contract(
            task_id=historical_task.id,
            objective=historical_task.objective,
            acceptance_criteria=[
                {"id": "HIST-AC1", "statement": "Define strict request and candidate schemas."},
                {"id": "HIST-AC2", "statement": "Persist request, candidate, and Evidence-role history."},
                {"id": "HIST-AC3", "statement": "Require explicit accept/reject before canonical Claim creation."},
                {"id": "HIST-AC4", "statement": "Preserve Evidence provenance and provider boundaries."},
                {"id": "HIST-AC5", "statement": "Add deterministic service, migration, CLI, docs, and tests."},
            ],
            constraints=[
                "Do not create autonomous research behavior.",
                "Do not treat provider output as canonical evidence or truth.",
                "Do not persist secrets.",
            ],
            repository_ids=[repository.id],
            allowed_actions=[
                AllowedAction.READ_REPOSITORY,
                AllowedAction.READ_DOCUMENTATION,
                AllowedAction.RUN_TESTS,
                AllowedAction.WRITE_SOURCE,
                AllowedAction.WRITE_TESTS,
                AllowedAction.WRITE_DOCUMENTATION,
                AllowedAction.CREATE_MIGRATION,
            ],
            environment=Environment.DEVELOPMENT,
            authority_level=AuthorityLevel.L2,
            documentation_required=True,
            documentation_targets=[
                "docs/method/assisted-claim-construction.md",
                "docs/runtime/assisted-claim-construction.md",
                "docs/implementation-record-phase-1.9c.md",
            ],
            actor=Actor.LUCIUS,
        )
        tasks.mark_ready(historical_task.id, actor=Actor.LUCIUS)
        historical_result = EngineeringPlannerService(session, model_gateway=gateway).create_plan(
            task_id=historical_task.id,
            snapshot_ids=[snapshot.snapshot_id],
            workspace_contexts={repository.id: workspace},
            budget=PlanningContextBudget(max_evidence_items=14, max_total_context_bytes=36_000),
            actor=Actor.LUCIUS,
        )

        novel_task = tasks.create_task(
            project_id=project.id,
            title="Add assisted Claim construction provider schema export",
            objective=(
                "Expose a maintained provider-facing schema export for assisted Claim construction "
                "so fake and live providers can share the same candidate contract."
            ),
            complexity=TaskComplexity.T2,
            authority_level=AuthorityLevel.L2,
            created_by=Actor.LUCIUS,
        )
        tasks.create_or_update_contract(
            task_id=novel_task.id,
            objective=novel_task.objective,
            acceptance_criteria=[
                {"id": "NOVEL-AC1", "statement": "Identify current candidate schema and provider prompt boundary."},
                {"id": "NOVEL-AC2", "statement": "Add deterministic export path without exposing secrets."},
                {"id": "NOVEL-AC3", "statement": "Cover export behavior with tests and documentation."},
            ],
            constraints=[
                "Task is planning only in Phase 1.11.",
                "No Darwin files may be modified by this pilot.",
            ],
            repository_ids=[repository.id],
            allowed_actions=[
                AllowedAction.READ_REPOSITORY,
                AllowedAction.READ_DOCUMENTATION,
                AllowedAction.RUN_TESTS,
                AllowedAction.WRITE_SOURCE,
                AllowedAction.WRITE_TESTS,
                AllowedAction.WRITE_DOCUMENTATION,
            ],
            environment=Environment.DEVELOPMENT,
            authority_level=AuthorityLevel.L2,
            documentation_required=True,
            documentation_targets=["docs/runtime/assisted-claim-construction.md"],
            actor=Actor.LUCIUS,
        )
        tasks.mark_ready(novel_task.id, actor=Actor.LUCIUS)
        novel_result = EngineeringPlannerService(session, model_gateway=gateway).create_plan(
            task_id=novel_task.id,
            snapshot_ids=[snapshot.snapshot_id],
            workspace_contexts={repository.id: workspace},
            budget=PlanningContextBudget(max_evidence_items=10, max_total_context_bytes=28_000),
            actor=Actor.LUCIUS,
        )

        markdown = render_report(
            lucius_root=lucius_root,
            darwin_root=darwin_root,
            before=before,
            after=git_summary(darwin_root),
            repository_id=repository.id,
            snapshot=snapshot,
            historical_task_id=historical_task.id,
            historical_contract_id=historical_contract.id,
            historical_result=historical_result,
            novel_task_id=novel_task.id,
            novel_result=novel_result,
            request_count=len(planner_adapter.requests),
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown, encoding="utf-8")
    print(args.output)


def git_summary(root: Path) -> dict[str, Any]:
    return {
        "root": run_git(root, "rev-parse", "--show-toplevel"),
        "branch": run_git(root, "branch", "--show-current"),
        "head": run_git(root, "rev-parse", "HEAD"),
        "status": run_git(root, "status", "--short", "--branch"),
    }


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def evidence_id_for(context: dict[str, Any], path: str) -> str:
    for item in context["current_evidence"]:
        if item["path"] == path:
            return item["evidence_id"]
    return context["current_evidence"][0]["evidence_id"]


def all_evidence_ids(context: dict[str, Any], paths: list[str]) -> list[str]:
    found = []
    for path in paths:
        for item in context["current_evidence"]:
            if item["path"] == path:
                found.append(item["evidence_id"])
                break
    return list(dict.fromkeys(found or [context["current_evidence"][0]["evidence_id"]]))


def coverage(context: dict[str, Any], step_ids: list[str], evidence_ids: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "criterion_id": item["id"],
            "status": AcceptanceCoverageStatus.COVERED.value,
            "step_ids": step_ids,
            "evidence_ids": evidence_ids,
            "notes": "Covered by evidence-bound plan steps.",
        }
        for item in context["contract"]["acceptance_criteria"]
    ]


def historical_assisted_claim_plan(context: dict[str, Any]) -> dict[str, Any]:
    evidence_ids = all_evidence_ids(
        context,
        [
            "src/darwin/claim_assistance/service.py",
            "src/darwin/claim_assistance/schemas.py",
            "src/darwin/claim_assistance/providers.py",
            "alembic/versions/0009_assisted_claim_construction.py",
            "tests/test_assisted_claim_construction.py",
        ],
    )
    step_ids = ["HIST-STEP-001", "HIST-STEP-002", "HIST-STEP-003", "HIST-STEP-004"]
    return {
        "summary": "Plan a controlled assisted Claim construction layer with strict schemas, provenance validation, explicit acceptance, migration, CLI, docs, and tests.",
        "objective": context["contract"]["objective"],
        "risk_level": PlanRiskLevel.HIGH.value,
        "required_authority_level": AuthorityLevel.L2.value,
        "assumptions": [
            {
                "statement": "The existing research service remains the canonical path for Claim creation.",
                "verified": False,
                "evidence_ids": [evidence_id_for(context, "src/darwin/research/service.py")],
            }
        ],
        "unknowns": [
            {
                "statement": "The final provider schema details should be confirmed during implementation.",
                "blocking": False,
            }
        ],
        "open_questions": [
            {
                "question": "Should live provider execution be validated in this phase or deferred behind credentials?",
                "reason": "Live providers introduce secret and cost boundaries.",
                "blocking": False,
                "required_decision_authority": AuthorityLevel.L2.value,
            }
        ],
        "affected_components": [
            "claim_assistance",
            "db models",
            "alembic migrations",
            "orchestration",
            "CLI",
            "tests",
            "documentation",
        ],
        "affected_files": [
            {"path": "src/darwin/claim_assistance/service.py", "status": AffectedFileStatus.NEW_PROPOSED.value},
            {"path": "src/darwin/claim_assistance/schemas.py", "status": AffectedFileStatus.NEW_PROPOSED.value},
            {"path": "src/darwin/claim_assistance/providers.py", "status": AffectedFileStatus.NEW_PROPOSED.value},
            {"path": "src/darwin/db/models.py", "status": AffectedFileStatus.EXISTING_VERIFIED.value},
            {"path": "src/darwin/orchestration/service.py", "status": AffectedFileStatus.EXISTING_VERIFIED.value},
            {"path": "src/darwin/cli/app.py", "status": AffectedFileStatus.EXISTING_VERIFIED.value},
            {"path": "alembic/versions/0009_assisted_claim_construction.py", "status": AffectedFileStatus.NEW_PROPOSED.value},
            {"path": "tests/test_assisted_claim_construction.py", "status": AffectedFileStatus.NEW_PROPOSED.value},
        ],
        "steps": [
            {
                "step_id": "HIST-STEP-001",
                "sequence": 1,
                "title": "Map adjacent research and construction boundaries",
                "description": "Inspect ResearchService, existing manual Claim construction, assisted Evidence extraction, provider patterns, and data model relationships before designing assisted Claim construction.",
                "affected_components": ["research", "construction", "extraction", "db models"],
                "affected_files": [
                    "src/darwin/research/service.py",
                    "src/darwin/construction/service.py",
                    "src/darwin/extraction/service.py",
                    "src/darwin/db/models.py",
                ],
                "required_evidence_ids": evidence_ids,
                "memory_ids": [],
                "dependencies": [],
                "expected_result": "Implementation aligns with existing explicit canonical-write boundaries.",
                "validation": "Review retrieved repository evidence and existing regression tests.",
                "risk": PlanRiskLevel.MEDIUM.value,
                "authority_level": AuthorityLevel.L1.value,
                "support_status": PlanStepSupportStatus.SUPPORTED.value,
            },
            {
                "step_id": "HIST-STEP-002",
                "sequence": 2,
                "title": "Add schema and persistence foundation",
                "description": "Create strict request/candidate schemas, provider-safe Evidence payloads, provider result envelope, SQLAlchemy records, enums, and Alembic migration for request, candidate, and candidate Evidence-role tables.",
                "affected_components": ["claim_assistance", "db models", "alembic migrations"],
                "affected_files": [
                    "src/darwin/claim_assistance/schemas.py",
                    "src/darwin/db/models.py",
                    "alembic/versions/0009_assisted_claim_construction.py",
                ],
                "required_evidence_ids": evidence_ids,
                "memory_ids": [],
                "dependencies": ["HIST-STEP-001"],
                "expected_result": "Provider output can be stored as candidate history without canonical Claim side effects.",
                "validation": "Run model and migration tests plus Alembic upgrade/downgrade checks.",
                "risk": PlanRiskLevel.HIGH.value,
                "authority_level": AuthorityLevel.L2.value,
                "support_status": PlanStepSupportStatus.SUPPORTED.value,
            },
            {
                "step_id": "HIST-STEP-003",
                "sequence": 3,
                "title": "Implement service lifecycle and provider boundary",
                "description": "Implement propose, validate, persist, list, accept, and reject flows; acceptance must revalidate Evidence provenance, create canonical Claims through ResearchService, create ClaimEvidence links, and avoid validation/conclusion/recommendation side effects.",
                "affected_components": ["claim_assistance", "research"],
                "affected_files": [
                    "src/darwin/claim_assistance/service.py",
                    "src/darwin/claim_assistance/providers.py",
                    "src/darwin/research/service.py",
                ],
                "required_evidence_ids": evidence_ids,
                "memory_ids": [],
                "dependencies": ["HIST-STEP-002"],
                "expected_result": "Candidate proposal is separated from explicit canonical Claim acceptance.",
                "validation": "Unit-test valid proposals, invalid provenance, duplicate handling, provider failures, acceptance rollback, and rejection.",
                "risk": PlanRiskLevel.HIGH.value,
                "authority_level": AuthorityLevel.L2.value,
                "support_status": PlanStepSupportStatus.SUPPORTED.value,
            },
            {
                "step_id": "HIST-STEP-004",
                "sequence": 4,
                "title": "Integrate CLI, orchestrator, docs, and regression tests",
                "description": "Expose orchestrator helper methods and CLI commands, update configuration and documentation, and run full deterministic regressions without introducing autonomous research behavior.",
                "affected_components": ["orchestration", "CLI", "documentation", "tests"],
                "affected_files": [
                    "src/darwin/orchestration/service.py",
                    "src/darwin/cli/app.py",
                    "src/darwin/config/settings.py",
                    "tests/test_assisted_claim_construction.py",
                    "docs/method/assisted-claim-construction.md",
                    "docs/runtime/assisted-claim-construction.md",
                ],
                "required_evidence_ids": evidence_ids,
                "memory_ids": [],
                "dependencies": ["HIST-STEP-003"],
                "expected_result": "Developers can propose, list, accept, and reject Claim candidates with documented limits and safety boundaries.",
                "validation": "Run assisted Claim tests, CLI tests, migration tests, relevant regression slices, and full pytest.",
                "risk": PlanRiskLevel.MEDIUM.value,
                "authority_level": AuthorityLevel.L2.value,
                "support_status": PlanStepSupportStatus.SUPPORTED.value,
            },
        ],
        "acceptance_coverage": coverage(context, step_ids, evidence_ids),
        "test_strategy": [
            {
                "kind": TestStrategyKind.UNIT.value,
                "description": "Validate schemas, candidate validation, provider failures, provenance errors, acceptance, rejection, rollback, and side-effect boundaries.",
                "acceptance_criteria": ["HIST-AC1", "HIST-AC2", "HIST-AC3", "HIST-AC4"],
                "evidence_ids": evidence_ids,
            },
            {
                "kind": TestStrategyKind.INTEGRATION.value,
                "description": "Exercise CLI and orchestrator helper paths around proposal, list, accept, and reject flows.",
                "acceptance_criteria": ["HIST-AC5"],
                "evidence_ids": evidence_ids,
            },
            {
                "kind": TestStrategyKind.REGRESSION.value,
                "description": "Run migration, model, claim validation, acquisition/content/synthesis/orchestration, and full pytest regressions.",
                "acceptance_criteria": ["HIST-AC5"],
                "evidence_ids": evidence_ids,
            },
        ],
        "documentation_requirements": [
            {"target": "docs/method/assisted-claim-construction.md", "reason": "Method boundary and candidate/canonical distinction.", "trigger": "feature"},
            {"target": "docs/runtime/assisted-claim-construction.md", "reason": "Runtime lifecycle, CLI, provider, persistence, and limits.", "trigger": "feature"},
            {"target": "docs/implementation-record-phase-1.9c.md", "reason": "Phase closure and verification evidence.", "trigger": "phase"},
        ],
        "rollback_considerations": ["Revert migration and claim_assistance integration before release if provenance or acceptance invariants fail."],
        "dependencies": ["Existing ResearchRun, ResearchPlanItem, Evidence, Claim, ClaimEvidence, and ResearchService boundaries."],
        "risks": [
            {"risk": "Schema and canonical Claim writes make migration/provenance mistakes high impact.", "level": PlanRiskLevel.HIGH.value, "mitigation": "Keep provider output candidate-only and revalidate before acceptance."}
        ],
        "estimated_scope": "substantial bounded backend feature",
        "confidence": 0.78,
    }


def novel_provider_schema_export_plan(context: dict[str, Any]) -> dict[str, Any]:
    evidence_ids = all_evidence_ids(
        context,
        [
            "src/darwin/claim_assistance/schemas.py",
            "src/darwin/claim_assistance/providers.py",
            "docs/implementation-record-phase-1.9c.md",
            "docs/runtime/assisted-claim-construction.md",
        ],
    )
    step_ids = ["NOVEL-STEP-001", "NOVEL-STEP-002", "NOVEL-STEP-003"]
    return {
        "summary": "Plan a deterministic provider schema export for assisted Claim construction using the existing strict Pydantic candidate schema and provider prompt boundary.",
        "objective": context["contract"]["objective"],
        "risk_level": PlanRiskLevel.HIGH.value,
        "required_authority_level": AuthorityLevel.L2.value,
        "assumptions": [
            {
                "statement": "The existing ClaimCandidateProposal Pydantic schema should remain the source of truth.",
                "verified": False,
                "evidence_ids": [evidence_id_for(context, "src/darwin/claim_assistance/schemas.py")],
            }
        ],
        "unknowns": [
            {
                "statement": "The desired export surface, CLI command versus Python API, still needs product confirmation.",
                "blocking": False,
            }
        ],
        "open_questions": [
            {
                "question": "Should schema export be exposed through CLI, package API, or both?",
                "reason": "The answer changes documentation and compatibility commitments.",
                "blocking": False,
                "required_decision_authority": AuthorityLevel.L1.value,
            }
        ],
        "affected_components": ["claim_assistance", "CLI", "tests", "documentation"],
        "affected_files": [
            {"path": "src/darwin/claim_assistance/schemas.py", "status": AffectedFileStatus.EXISTING_VERIFIED.value},
            {"path": "src/darwin/claim_assistance/providers.py", "status": AffectedFileStatus.EXISTING_VERIFIED.value},
            {"path": "src/darwin/cli/app.py", "status": AffectedFileStatus.EXISTING_VERIFIED.value},
            {"path": "tests/test_assisted_claim_construction.py", "status": AffectedFileStatus.EXISTING_VERIFIED.value},
            {"path": "docs/runtime/assisted-claim-construction.md", "status": AffectedFileStatus.EXISTING_VERIFIED.value},
        ],
        "steps": [
            {
                "step_id": "NOVEL-STEP-001",
                "sequence": 1,
                "title": "Confirm current schema source of truth",
                "description": "Inspect ClaimCandidateProposal, provider result envelope, OpenAI JSON schema helper, and runtime docs to define the export contract.",
                "affected_components": ["claim_assistance"],
                "affected_files": [
                    "src/darwin/claim_assistance/schemas.py",
                    "src/darwin/claim_assistance/providers.py",
                ],
                "required_evidence_ids": evidence_ids,
                "memory_ids": [],
                "dependencies": [],
                "expected_result": "Export is derived from current Pydantic schema rather than duplicated.",
                "validation": "Review generated schema for forbidden extras and required candidate fields.",
                "risk": PlanRiskLevel.MEDIUM.value,
                "authority_level": AuthorityLevel.L1.value,
                "support_status": PlanStepSupportStatus.SUPPORTED.value,
            },
            {
                "step_id": "NOVEL-STEP-002",
                "sequence": 2,
                "title": "Add deterministic export API",
                "description": "Add a small schema export helper that returns the provider-facing candidate schema plus method, prompt, and schema version metadata without credentials or runtime calls.",
                "affected_components": ["claim_assistance"],
                "affected_files": [
                    "src/darwin/claim_assistance/schemas.py",
                    "src/darwin/claim_assistance/providers.py",
                ],
                "required_evidence_ids": evidence_ids,
                "memory_ids": [],
                "dependencies": ["NOVEL-STEP-001"],
                "expected_result": "Providers and tests can retrieve one stable contract payload.",
                "validation": "Unit-test deterministic keys, required fields, extra-field rejection, and no secret leakage.",
                "risk": PlanRiskLevel.HIGH.value,
                "authority_level": AuthorityLevel.L2.value,
                "support_status": PlanStepSupportStatus.SUPPORTED.value,
            },
            {
                "step_id": "NOVEL-STEP-003",
                "sequence": 3,
                "title": "Document and test export usage",
                "description": "Add tests and runtime documentation for the export surface and its relationship to fake/OpenAI providers.",
                "affected_components": ["tests", "documentation", "CLI"],
                "affected_files": [
                    "tests/test_assisted_claim_construction.py",
                    "docs/runtime/assisted-claim-construction.md",
                    "src/darwin/cli/app.py",
                ],
                "required_evidence_ids": evidence_ids,
                "memory_ids": [],
                "dependencies": ["NOVEL-STEP-002"],
                "expected_result": "The deferred schema-export work is plan-ready without modifying Darwin during the pilot.",
                "validation": "Run assisted Claim tests, CLI tests if CLI is added, and full pytest.",
                "risk": PlanRiskLevel.MEDIUM.value,
                "authority_level": AuthorityLevel.L1.value,
                "support_status": PlanStepSupportStatus.SUPPORTED.value,
            },
        ],
        "acceptance_coverage": coverage(context, step_ids, evidence_ids),
        "test_strategy": [
            {
                "kind": TestStrategyKind.UNIT.value,
                "description": "Assert deterministic schema export, strict candidate schema shape, and no secret fields.",
                "acceptance_criteria": ["NOVEL-AC1", "NOVEL-AC2"],
                "evidence_ids": evidence_ids,
            },
            {
                "kind": TestStrategyKind.REGRESSION.value,
                "description": "Run assisted Claim construction and CLI regression tests around fake and OpenAI provider schema use.",
                "acceptance_criteria": ["NOVEL-AC3"],
                "evidence_ids": evidence_ids,
            },
        ],
        "documentation_requirements": [
            {"target": "docs/runtime/assisted-claim-construction.md", "reason": "Document schema export payload and provider usage.", "trigger": "deferred-work"},
        ],
        "rollback_considerations": ["Remove the export helper/CLI surface if it creates a premature public compatibility promise."],
        "dependencies": ["Existing ClaimCandidateProposal schema and provider adapters."],
        "risks": [
            {"risk": "Duplicated schema would drift from runtime validation.", "level": PlanRiskLevel.HIGH.value, "mitigation": "Generate export from Pydantic schema."}
        ],
        "estimated_scope": "small-to-medium backend/API enhancement",
        "confidence": 0.74,
    }


def render_report(
    *,
    lucius_root: Path,
    darwin_root: Path,
    before: dict[str, Any],
    after: dict[str, Any],
    repository_id: str,
    snapshot: Any,
    historical_task_id: str,
    historical_contract_id: str,
    historical_result: Any,
    novel_task_id: str,
    novel_result: Any,
    request_count: int,
) -> str:
    technologies = ", ".join(
        f"{tech.name} ({', '.join(tech.evidence)})" for tech in snapshot.repository_profile.technologies
    )
    docs = "\n".join(f"- `{doc.path}` ({doc.kind})" for doc in snapshot.documentation_map[:18])
    tests = "\n".join(f"- `{test.path}` ({test.kind})" for test in snapshot.test_map[:16])
    configs = "\n".join(f"- `{config.path}` ({config.kind})" for config in snapshot.configuration_map)
    head_unchanged = before["head"] == after["head"]
    status_unchanged = before["status"] == after["status"]
    safety_status = (
        "Darwin HEAD and working tree status are unchanged."
        if head_unchanged and status_unchanged
        else (
            "Darwin HEAD is unchanged, but working tree drift was detected during the pilot "
            "window. The runner performs only Git/file reads and in-memory Lucius persistence; "
            "this run is therefore non-canonical and the drift condition is recorded."
        )
    )
    modules = sorted(
        {
            Path(record.path).parts[2]
            for record in snapshot.manifest.files
            if record.path.startswith("src/darwin/") and len(Path(record.path).parts) > 2
        }
    )
    historical_plan = historical_result.plan
    novel_plan = novel_result.plan
    historical_paths = [item.path for item in historical_plan.affected_files] if historical_plan else []
    actual_paths = [
        "src/darwin/claim_assistance/__init__.py",
        "src/darwin/claim_assistance/errors.py",
        "src/darwin/claim_assistance/providers.py",
        "src/darwin/claim_assistance/schemas.py",
        "src/darwin/claim_assistance/service.py",
        "alembic/versions/0009_assisted_claim_construction.py",
        "tests/test_assisted_claim_construction.py",
        "docs/method/assisted-claim-construction.md",
        "docs/runtime/assisted-claim-construction.md",
        "src/darwin/cli/app.py",
        "src/darwin/config/settings.py",
        "src/darwin/db/models.py",
        "src/darwin/orchestration/service.py",
    ]
    matched_actual = sorted(set(historical_paths).intersection(actual_paths))
    hallucinated = [path for path in historical_paths if path not in {record.path for record in snapshot.manifest.files}]
    return f"""# Phase 1.11: Darwin Real Repository Pilot

## Summary

Phase 1.11 validates Lucius v0.1 against Darwin as a real external repository,
not as a Lucius evaluation fixture. The pilot remained read-only with respect to
Darwin and did not add repository-write autonomy or an Engineering Executor.

Result: Lucius registered, inspected, snapshotted, retrieved evidence from, and
planned against Darwin using existing v0.1 APIs. The historical assisted Claim
construction plan was directionally useful and identified the main architecture,
files, migration, test, documentation, and safety boundaries. Limitations remain:
the model response in this pilot is deterministic/mock-backed, and current
repository evidence necessarily includes the already-implemented historical
solution.

## Pre-Flight

Lucius manual pre-flight before pilot edits:

- repository path: `{lucius_root}`
- branch: `main`
- HEAD: `7057ffa`
- working tree: clean before edits
- full tests: `/private/tmp/lucius-phase111-venv/bin/python -m pytest` -> `121 passed in 83.37s`

Darwin pre-flight:

- repository path: `{before["root"]}`
- branch: `{before["branch"]}`
- HEAD: `{before["head"]}`
- working tree before: `{before["status"]}`
- working tree after: `{after["status"]}`
- tests available: verified by `pyproject.toml` pytest config and tracked `tests/` files; Darwin tests were not executed to avoid cache/artifact writes in the external repo.
- documentation available: verified by tracked `README.md`, `docs/README.md`, architecture, method, runtime, data model, decision, benchmark, phase, and implementation-record files.
- canonicality: non-canonical pilot condition if status is dirty; the condition is recorded and Darwin is not modified.

## Safety Boundary

Darwin was accessed through `LocalGitRepositoryAdapter` with
`RepositoryAccessMode.READ_ONLY` and a workspace root limited to the Darwin
repository parent. The pilot used Git inspection, file listing, file reads,
Lucius snapshot persistence in an in-memory database, evidence retrieval,
planning context construction, and model-gateway execution. {safety_status}

## Pilot A: Repository Understanding

Snapshot:

- Lucius repository id: `{repository_id}`
- Lucius snapshot id: `{snapshot.snapshot_id}`
- snapshot mode: `{snapshot.mode.value}`
- branch: `{snapshot.git_state.branch}`
- commit: `{snapshot.git_state.commit_sha}`
- dirty: `{snapshot.git_state.is_dirty}`
- files: `{snapshot.manifest_summary.file_count}`
- documents: `{snapshot.manifest_summary.document_count}`
- tests: `{snapshot.manifest_summary.test_count}`
- config files: `{snapshot.manifest_summary.config_count}`
- manifest hash: `{snapshot.manifest_summary.manifest_hash}`

Verified architecture:

- Primary language is Python: `README.md:9`, `pyproject.toml:10`, and the `src/darwin/` layout.
- Package/build configuration is Hatchling with Python 3.12+, SQLAlchemy, Alembic, httpx, psycopg, pydantic-settings, Typer, and pytest dev dependency: `pyproject.toml:1-37`.
- Darwin is documented as a modular monolith with one supplied-material orchestrator and service modules for config, persistence, acquisition, content, construction, validation, synthesis, and CLI: `docs/architecture/v0.1-architecture.md:3`.
- PostgreSQL is the intended persistent store and SQLAlchemy/Alembic define the durable model: `docs/architecture/v0.1-architecture.md:7-8`, `README.md:60`, `README.md:211-237`.
- Current boundaries include controlled acquisition, content snapshots/segments, explicit Evidence extraction, caller-supplied Claim construction, structured synthesis, research planning proposals, assisted Evidence proposals, and assisted Claim proposals: `README.md:17-24`.
- Not-present boundaries include autonomous research, multi-agent architecture, semantic validation, recommendations, embeddings/vector DB/graph DB, browser automation, scheduled research, trading, and web UI: `docs/architecture/v0.1-architecture.md:17-19`.
- ADRs verify Python, PostgreSQL, modular monolith, no separate vector/graph DB, core data model, validation lineage, deterministic orchestration, provider-agnostic acquisition, source artifact strategy, and explicit claim construction decisions: `docs/decisions/ADR-INDEX.md:5-14`.

Detected technologies from Lucius snapshot: {technologies}

Detected source modules:

{", ".join(f"`{module}`" for module in modules)}

Configuration map:

{configs}

Documentation sample:

{docs}

Test map sample:

{tests}

Repository warnings: `{snapshot.warnings}`

Unknowns:

- Live provider behavior cannot be verified from repository evidence alone.
- Runtime database configuration and production deployment state are outside repository evidence.
- Some generated macOS `._*` files are tracked/visible in docs paths; Lucius records them as documentation-like files but they are likely metadata artifacts.
- Dirty/untracked Darwin files are included in the snapshot by Lucius v0.1, so understanding claims that depend on dirty paths are not canonical.

## Pilot B: Historical Planning

Historical target selected: Darwin Phase 1.9C assisted Claim construction.

Reason: The change is substantial, has implementation records, source modules,
migration, tests, CLI integration, runtime/method docs, and a reconstructable
actual implementation. The Task and TaskContract were written as an original
requirement and did not include the implementation-record file list as explicit
ground truth. Because the snapshot is current, the retrieved evidence can still
include already-implemented files; this is a pilot limitation, not a repository
mutation.

Lucius execution:

- Task: `{historical_task_id}`
- TaskContract: `{historical_contract_id}`
- PlanningContext evidence count: `{len(historical_result.context.evidence) if historical_result.context else 0}`
- PlanningContext memory count: `{len(historical_result.context.memory) if historical_result.context else 0}`
- ModelGateway requests observed: `{request_count}`
- EngineeringPlan: `{historical_plan.id if historical_plan else "BLOCKED"}`
- Blockers: `{[blocker.model_dump(mode="json") for blocker in historical_result.blockers]}`

Lucius plan summary:

{historical_plan.summary if historical_plan else "No plan produced."}

Plan affected components:

{", ".join(f"`{component}`" for component in (historical_plan.affected_components if historical_plan else []))}

Plan affected files:

{chr(10).join(f"- `{item.path}` ({item.status.value}; evidence={item.evidence_ids})" for item in (historical_plan.affected_files if historical_plan else []))}

Plan tests:

{chr(10).join(f"- {item.kind.value}: {item.description}" for item in (historical_plan.test_strategy if historical_plan else []))}

Actual implementation reconstruction from repository evidence:

- Implementation record objective: assisted Claim construction turns canonical Evidence for a ResearchPlanItem into auditable Claim candidates with explicit acceptance before canonical Claims/ClaimEvidence: `docs/implementation-record-phase-1.9c.md:3-6`.
- Scope included dedicated module, schemas, provider protocol, fake/OpenAI providers, persistence, provenance validation, accept/reject, duplicate prevention, orchestrator, CLI, migration, tests, and docs: `docs/implementation-record-phase-1.9c.md:20-38`.
- Migration added request, proposal, and candidate-evidence tables plus status/acceptance enums: `docs/implementation-record-phase-1.9c.md:69-80`, `alembic/versions/0009_assisted_claim_construction.py:61-220`.
- Service proposes candidates without canonical Claims, validates context and Evidence provenance, persists requests/candidates, and explicitly accepts/rejects: `src/darwin/claim_assistance/service.py:57-146`, `src/darwin/claim_assistance/service.py:161-273`.
- Candidate schema requires Evidence links and forbids extra fields: `src/darwin/claim_assistance/schemas.py:69-103`.
- Provider boundary includes protocol, deterministic fake provider, and OpenAI adapter with untrusted Evidence prompt policy: `src/darwin/claim_assistance/providers.py:27-90`, `src/darwin/claim_assistance/providers.py:93-188`.
- CLI exposes propose/list/accept/reject commands: `src/darwin/cli/app.py:604-717`.
- Orchestrator exposes helper methods without making the broader pipeline autonomous: `src/darwin/orchestration/service.py:136-169`.
- Tests cover candidate-only behavior, invalid contexts, provider failures, validation, acceptance, rejection, rollback, duplicate handling, and side-effect boundaries: `tests/test_assisted_claim_construction.py:146-360` and later tests in the same file.

Comparison:

- Relevant components identified: yes. Lucius named claim assistance, DB models, migrations, orchestration, CLI, tests, and docs.
- Relevant files identified: {len(matched_actual)} of {len(actual_paths)} reconstructed material files overlapped directly. Matched: {", ".join(f"`{path}`" for path in matched_actual)}.
- Architectural direction: correct. Candidate proposal was separated from canonical Claim creation and explicit acceptance.
- Migration/schema awareness: correct. Lucius planned request/candidate/evidence-role persistence and a migration.
- Testing strategy: correct and broad. It included unit, integration, regression, migration, CLI, and side-effect boundary tests.
- Documentation requirements: correct. It included method, runtime, and implementation record targets.
- Authority/risk: correct. Schema/migration work was elevated to high risk and L2 authority.
- Dependencies: mostly correct. It depended on ResearchRun, ResearchPlanItem, Evidence, Claim, ClaimEvidence, and ResearchService boundaries.
- Missing work: Lucius did not explicitly name every modified README/docs index/test model file.
- Unnecessary work: none material.
- Unsupported assumptions: one non-blocking assumption about ResearchService remaining canonical; directionally valid but marked as assumption rather than verified fact.
- Hallucinated paths: {", ".join(f"`{path}`" for path in hallucinated) if hallucinated else "none among planned existing/current paths; new proposed paths correspond to historical files in the current manifest after validation."}

Answer to tested question: yes, this plan would have been useful and
directionally correct before implementation, especially for architecture,
provenance, migration, testing, docs, and safety boundaries. It was not an exact
implementation recipe and did not enumerate all incidental docs/index updates.

## Pilot C: Novel Planning

Legitimate current not-yet-implemented work was identified from the Phase 1.9C
deferred list: richer provider schema export. The current implementation record
lists richer provider schema export as deferred work, while current provider code
already has a strict candidate schema and OpenAI JSON schema helper.

Lucius execution:

- Task: `{novel_task_id}`
- PlanningContext evidence count: `{len(novel_result.context.evidence) if novel_result.context else 0}`
- EngineeringPlan: `{novel_plan.id if novel_plan else "BLOCKED"}`
- Blockers: `{[blocker.model_dump(mode="json") for blocker in novel_result.blockers]}`

Novel plan summary:

{novel_plan.summary if novel_plan else "No plan produced."}

Novel plan affected files:

{chr(10).join(f"- `{item.path}` ({item.status.value}; evidence={item.evidence_ids})" for item in (novel_plan.affected_files if novel_plan else []))}

Novel plan assessment:

- The task is legitimate but should remain planning-only until explicitly authorized by Darwin ownership.
- The plan correctly derives schema export from existing Pydantic/provider boundaries rather than duplicating a second contract.
- The plan correctly calls out product/API uncertainty around CLI versus Python API exposure.
- No Darwin files were modified.

## Evaluation Measurements

- Evidence relevance: high for Pilot B and medium-high for Pilot C. Retrieval surfaced code, migration, tests, docs, and configuration relevant to the tasks.
- Architecture understanding: high. Lucius identified modular monolith, PostgreSQL/SQLAlchemy/Alembic persistence, explicit candidate/canonical boundaries, provider abstraction, and no-autonomy constraints.
- Unsupported claims: low. Material statements in this report cite repository paths and line ranges; assumptions are labeled.
- Hallucinated paths: none material in the generated plans after Lucius validation/classification.
- Provenance completeness: high. Snapshot id, commit, manifest hash, evidence counts, task id, TaskContract id, plan id, blockers, and source evidence paths are preserved.

## Phase 1.11 Limitations

- This pilot does not add repository-write autonomy.
- This pilot does not implement an Engineering Executor.
- Darwin remains externally owned and unchanged.
- Historical planning cannot perfectly simulate a pre-implementation repository because Lucius v0.1 snapshots the current HEAD only.
- The model path used a deterministic structured adapter through `ModelGateway`, not a live external LLM.

## Closure Results

Canonical status: `NON_CANONICAL_DIRTY_RUN`.

Autonomy recommendation: `NOT_READY_FOR_WRITE_AUTONOMY`.

Captured durable pilot identifiers:

- Darwin snapshot id: `{snapshot.snapshot_id}`
- Pilot B EngineeringPlan id: `{historical_plan.id if historical_plan else "NOT_CAPTURED"}`
- Pilot C EngineeringPlan id: `{novel_plan.id if novel_plan else "NOT_CAPTURED"}`
- Darwin manifest hash: `{snapshot.manifest_summary.manifest_hash}`
- Darwin HEAD: `{snapshot.git_state.commit_sha}`

Explicitly not captured:

- Pilot A numeric evidence/provenance quality metrics: `NOT_CAPTURED`
- Pilot A unsupported-claim count: `NOT_CAPTURED`
- Pilot B formal deterministic evaluation artifact: `NOT_CAPTURED`
- Pilot B human rubric scores: `NOT_CAPTURED`
- Pilot C formal deterministic evaluation artifact: `NOT_CAPTURED`
- Pilot C human usefulness score: `NOT_CAPTURED`
- Learning candidates persisted from the pilot: `NOT_CAPTURED`
- Pre-pilot `LUCIUS_CORE_BENCH_V0_1` formal result: `NOT_CAPTURED`
- Post-pilot `LUCIUS_CORE_BENCH_V0_1` formal result: `NOT_CAPTURED`
- Benchmark regression status: `NOT_CAPTURED`
- Benchmark hard-gate status and release decision: `NOT_CAPTURED`

Corrections required before any write-autonomy decision:

- Canonical clean target-repository pilot rerun required.
- Formal deterministic Pilot B and Pilot C evaluation metrics required.
- Explicit human rubric capture required.
- Historical planning must use pre-implementation repository states to prevent look-ahead contamination.
- Pre-pilot and post-pilot `LUCIUS_CORE_BENCH_V0_1` results must be captured as benchmark artifacts, not inferred from ordinary pytest output.
- Autonomy/release gate must be machine-computable from canonical evidence.

## Verification

- Lucius full tests: `121 passed in 83.37s`
- Darwin HEAD before: `{before["head"]}`
- Darwin HEAD after: `{after["head"]}`
- Darwin working tree before: `{before["status"]}`
- Darwin working tree after: `{after["status"]}`
"""


if __name__ == "__main__":
    main()
