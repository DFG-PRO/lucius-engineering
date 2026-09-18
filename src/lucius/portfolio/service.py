from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from lucius.domain.enums import (
    Actor,
    AllowedAction,
    AuthorityLevel,
    DesignCoverage,
    DDBDepth,
    Environment,
    GlobalWorkState,
    ProjectPriority,
    ProjectProgressiveStage,
    TaskComplexity,
    TaskPriority,
)
from lucius.portfolio.adapters import (
    CanonicalRoadmapAdapter,
    DarwinBacklogAdapter,
    ProjectWorkSourceAdapter,
)
from lucius.portfolio.schemas import (
    NormalizedProjectInventoryRecord,
    NormalizedWorkPackage,
)
from sqlalchemy import select
from lucius.persistence.orm import ProjectORM, ProjectRepositoryAttachmentORM, RepositoryRegistrationORM
from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.runtime.feeder import DarwinBacklogFeeder
from lucius.tasks.service import TaskService
from lucius.pilots.workflows import PersistentWorkflowService

logger = logging.getLogger(__name__)

PRIORITY_RANK = {
    "P0_CRITICAL": 0,
    "P0_NOW": 0,
    "P0": 0,
    "CRITICAL": 0,
    "P1_HIGH": 1,
    "P1": 1,
    "HIGH": 1,
    "P2_NORMAL": 2,
    "P2": 2,
    "NORMAL": 2,
    "P3_LOW": 3,
    "P3": 3,
    "LOW": 3,
    "P4_SPECULATIVE": 4,
    "P4": 4,
    "SPECULATIVE": 4,
    "PARKED": 98,
    "UNKNOWN": 99,
}


class GlobalWorkPortfolioService:
    """Canonical index and scheduler adapter over Universe-wide DFG projects and work supply."""

    def __init__(
        self,
        registry: DFGProjectRegistry | None = None,
        feeder: DarwinBacklogFeeder | None = None,
        registry_path: Path | str | None = None,
        adapters: list[ProjectWorkSourceAdapter] | None = None,
    ):
        if registry is not None:
            self.registry = registry
        elif registry_path is not None and Path(registry_path).exists():
            self.registry = DFGProjectRegistry.load_json(Path(registry_path))
        else:
            default_reg = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering/src/lucius/projects/dfg_canonical_registry.json")
            if default_reg.exists():
                self.registry = DFGProjectRegistry.load_json(default_reg)
            else:
                self.registry = None

        self.feeder = feeder or DarwinBacklogFeeder(registry=self.registry)

        if adapters is not None:
            self.adapters = adapters
        else:
            self.adapters = [
                DarwinBacklogAdapter(feeder=self.feeder),
                CanonicalRoadmapAdapter(registry=self.registry),
            ]

    def get_canonical_inventory(self) -> list[NormalizedProjectInventoryRecord]:
        """Returns normalized inventory records for all registered DFG projects and candidates."""
        if self.registry is None or not hasattr(self.registry, "projects"):
            return []

        records: list[NormalizedProjectInventoryRecord] = []
        for pid, pdata in self.registry.projects.items():
            raw = pdata.model_dump() if hasattr(pdata, "model_dump") else dict(pdata)
            
            p_priority_str = str(raw.get("priority", "NORMAL")).upper()
            if p_priority_str in ("HIGH", "CRITICAL") and pid in ("billy-production-engine",):
                priority_enum = ProjectPriority.P0_CRITICAL
            elif p_priority_str == "HIGH" or pid in ("lucius-engineering", "darwin-research-engine", "dfg-binance-executor", "alfred-orchestration", "fp-labs", "cinema-collection", "universe-core"):
                priority_enum = ProjectPriority.P1_HIGH
            elif p_priority_str == "LOW" or pid in ("dealhunter", "social-engine", "commerce-affiliate-engine", "dfg-prediction-engine", "arbitrage-engine", "section-8-real-estate", "post-assistant"):
                priority_enum = ProjectPriority.P3_LOW
            else:
                priority_enum = ProjectPriority.P2_NORMAL

            status_str = str(raw.get("status", "IN_DEVELOPMENT")).upper()
            if status_str == "OPERATIONAL":
                stage_enum = ProjectProgressiveStage.OPERATIONAL
                design_cov = DesignCoverage.D5_OPERATIONAL_DESIGN
                ddb = DDBDepth.DDB_FULL
            elif status_str == "IN_DEVELOPMENT":
                stage_enum = ProjectProgressiveStage.ACTIVE
                design_cov = DesignCoverage.D4_ENGINEERING_READY
                ddb = DDBDepth.DDB_STANDARD
            elif status_str == "VALIDATING":
                stage_enum = ProjectProgressiveStage.RESEARCHING
                design_cov = DesignCoverage.D2_PARTIAL_DESIGN
                ddb = DDBDepth.DDB_LITE
            elif status_str == "RESEARCHING":
                stage_enum = ProjectProgressiveStage.RESEARCH_REQUIRED
                design_cov = DesignCoverage.D1_CONCEPT_DEFINED
                ddb = DDBDepth.DDB_LITE
            elif status_str == "DESIGNING":
                stage_enum = ProjectProgressiveStage.DESIGN_PARTIAL
                design_cov = DesignCoverage.D3_STRUCTURED_DESIGN
                ddb = DDBDepth.DDB_STANDARD
            elif status_str == "BLOCKED":
                stage_enum = ProjectProgressiveStage.BLOCKED
                design_cov = DesignCoverage.D1_CONCEPT_DEFINED
                ddb = DDBDepth.DDB_LITE
            else:
                stage_enum = ProjectProgressiveStage.ACCEPTED_CONCEPT
                design_cov = DesignCoverage.D0_IDEA_ONLY
                ddb = None

            has_debt = design_cov in (DesignCoverage.D0_IDEA_ONLY, DesignCoverage.D1_CONCEPT_DEFINED, DesignCoverage.D2_PARTIAL_DESIGN)
            debt_reason = "Missing full DDB and engineering readiness spec" if has_debt else None

            rec = NormalizedProjectInventoryRecord(
                project_id=pid,
                canonical_name=str(raw.get("display_name", pid)),
                aliases=[pid],
                project_type=str(raw.get("project_type", "DFG_INTERNAL")),
                repository=raw.get("canonical_repo"),
                canonical_documentation_location=(raw.get("canonical_docs") or [None])[0],
                canonical_current_sha=raw.get("last_verified_sha"),
                priority=priority_enum,
                portfolio_state=stage_enum,
                current_phase=raw.get("current_phase"),
                last_closed_gate=raw.get("last_closed_gate"),
                next_gate=raw.get("next_canonical_gate"),
                design_coverage=design_cov,
                ddb_status=ddb,
                roadmap_status=str(raw.get("brief_status", "ACTIVE")),
                backlog_status=str(raw.get("development_status", "ACTIVE")),
                authority_class=(raw.get("authority_profile") or ["CLASS_A"])[-1],
                dependencies=list(raw.get("dependencies", [])),
                economic_revenue_relevance=str(raw.get("monetization_role", "UNKNOWN")),
                current_executable_work_source=str(raw.get("source_of_truth", "UNSPECIFIED")),
                has_design_debt=has_debt,
                design_debt_reason=debt_reason,
            )
            records.append(rec)
        return records

    def discover_global_work(
        self,
        session: Session | None = None,
        *,
        allowed_authority_classes: tuple[str, ...] = ("CLASS_A", "CLASS_B"),
    ) -> list[NormalizedWorkPackage]:
        """Discovers, normalizes, deduplicates, and ranks work packages across all registered adapters."""
        all_packages: list[NormalizedWorkPackage] = []
        seen_keys: set[str] = set()

        for adapter in self.adapters:
            if getattr(self.feeder, "_custom_items", None) is not None and adapter.__class__.__name__ != "DarwinBacklogAdapter":
                continue
            try:
                pkgs = adapter.discover_work_packages(allowed_authority_classes=allowed_authority_classes)
                for pkg in pkgs:
                    if pkg.dedupe_key not in seen_keys:
                        seen_keys.add(pkg.dedupe_key)
                        all_packages.append(pkg)
            except Exception as exc:
                logger.warning("Adapter %s failed during work discovery: %s", adapter, exc)

        # Rank work packages: GlobalWorkState == READY first (0), AUTO_PREPARABLE second (1), then others (2)
        # then priority rank (P0 < P1 < P2 < P3 < P4), then work_id
        all_packages.sort(
            key=lambda w: (
                0 if w.current_state == GlobalWorkState.READY else (1 if w.current_state == GlobalWorkState.AUTO_PREPARABLE else 2),
                PRIORITY_RANK.get(w.priority, 99),
                w.work_id,
            )
        )
        return all_packages

    def reconcile_auto_preparable(self) -> dict[str, Any]:
        """Audits and classifies all 19 AUTO_PREPARABLE items from Shift 10F."""
        packages = self.discover_global_work(allowed_authority_classes=("CLASS_A", "CLASS_B", "CLASS_C"))

        classification_counts = {
            "VALID_AUTO_PREPARABLE": 0,
            "INVALID_SYNTHETIC": 0,
            "DUPLICATE": 0,
            "MISSING_PROVENANCE": 0,
            "MISSING_ACCEPTANCE": 0,
            "REQUIRES_RESEARCH": 0,
            "REQUIRES_DECISION": 0,
            "ALREADY_COMPLETED": 0,
            "NOT_CURRENTLY_ELIGIBLE": 0,
        }
        details: list[dict[str, Any]] = []

        raw_items = self.feeder.load_raw_backlog_items()
        for item in raw_items:
            data = item.model_dump() if hasattr(item, "model_dump") else (dict(item) if isinstance(item, dict) else {})
            status = str(data.get("status", "")).upper()
            if status != "NEEDS_EVIDENCE":
                continue

            item_id = str(data.get("item_id"))
            refs = data.get("provenance_refs", [])
            criteria = data.get("research_questions", []) or ([data.get("expected_output")] if data.get("expected_output") else [])

            if not refs:
                cat = "MISSING_PROVENANCE"
            elif not criteria:
                cat = "MISSING_ACCEPTANCE"
            elif "BLOCKED" in status:
                cat = "REQUIRES_DECISION"
            else:
                cat = "VALID_AUTO_PREPARABLE"

            classification_counts[cat] += 1
            details.append({"item_id": item_id, "title": data.get("title"), "classification": cat, "provenance_refs": refs})

        return {
            "total_audited": sum(classification_counts.values()),
            "counts": classification_counts,
            "details": details,
        }

    def feed_portfolio_into_queue(
        self,
        session: Session,
        *,
        allowed_authority_classes: tuple[str, ...] = ("CLASS_A", "CLASS_B"),
        max_items: int = 5,
        actor: Actor = Actor.LUCIUS,
    ) -> list[dict[str, Any]]:
        """Atomically ingests eligible runnable/preparable work packages across all projects into persistent workflows."""
        work_packages = self.discover_global_work(session, allowed_authority_classes=allowed_authority_classes)
        existing_keys = self.feeder._get_existing_dedupe_keys(session)
        eligible_pkgs = [
            p for p in work_packages
            if p.current_state in (GlobalWorkState.READY, GlobalWorkState.AUTO_PREPARABLE)
            and p.provenance_refs
            and p.dedupe_key not in existing_keys
            and f"FEED-{p.source_record_id}" not in existing_keys
        ]

        if not eligible_pkgs:
            return []

        task_service = TaskService(session)
        workflow_service = PersistentWorkflowService(session)
        ingested: list[dict[str, Any]] = []

        for pkg in eligible_pkgs[:max_items]:
            # Ensure ProjectORM exists in session
            proj_orm = session.get(ProjectORM, pkg.project_id)
            if proj_orm is None:
                proj_orm = ProjectORM(
                    id=pkg.project_id,
                    name=pkg.project_id,
                    slug=pkg.project_id.lower(),
                    project_type="DFG_INTERNAL",
                    status="ACTIVE",
                    default_authority_level="L0",
                )
                session.add(proj_orm)
                session.flush()

            # Ensure RepositoryRegistrationORM and ProjectRepositoryAttachmentORM exist
            reg_id = f"repo-{pkg.project_id}"
            repo_loc = "/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering"
            if self.registry and hasattr(self.registry, "projects") and pkg.project_id in self.registry.projects:
                pdata = self.registry.projects[pkg.project_id]
                cand_repo = getattr(pdata, "canonical_repo", None) or (pdata.get("canonical_repo") if isinstance(pdata, dict) else None)
                if cand_repo and Path(cand_repo).exists():
                    repo_loc = str(cand_repo)

            reg = session.scalar(
                select(RepositoryRegistrationORM).where(
                    RepositoryRegistrationORM.project_id == pkg.project_id,
                )
            ) or session.get(RepositoryRegistrationORM, reg_id)
            if reg is None:
                reg = RepositoryRegistrationORM(
                    id=reg_id,
                    project_id=pkg.project_id,
                    name=pkg.project_id,
                    adapter_type="git",
                    location=repo_loc,
                    access_mode="READ_ONLY",
                    status="ACTIVE",
                )
                session.add(reg)
                session.flush()

            attachment = session.scalar(
                select(ProjectRepositoryAttachmentORM).where(
                    ProjectRepositoryAttachmentORM.project_id == pkg.project_id,
                    ProjectRepositoryAttachmentORM.repository_id == reg.id,
                )
            )
            if attachment is None:
                attachment = ProjectRepositoryAttachmentORM(
                    project_id=pkg.project_id,
                    repository_id=reg.id,
                    attached_by="LUCIUS",
                )
                session.add(attachment)
                session.flush()

            # Create Task record
            task = task_service.create_task(
                project_id=pkg.project_id,
                title=pkg.title,
                objective=pkg.objective,
                priority=TaskPriority.HIGH if "P0" in pkg.priority else TaskPriority.NORMAL,
                complexity=TaskComplexity.T1,
                authority_level=AuthorityLevel.L0,
                created_by=actor,
            )

            task_service.create_or_update_contract(
                task_id=task.id,
                objective=pkg.objective,
                acceptance_criteria=[{"id": f"AC-{i+1:03d}", "statement": str(c), "status": "PENDING"} for i, c in enumerate(pkg.acceptance_criteria or ["Verify canonical evidence"])],
                constraints=["READ_ONLY_REPOSITORY", "NO_TRADING_MESSAGING_OR_CREDENTIAL_ACTIONS"],
                repository_ids=[reg.id],
                allowed_actions=[AllowedAction.READ_REPOSITORY, AllowedAction.READ_DOCUMENTATION],
                allowed_tools=[],
                environment=Environment.SANDBOX,
                authority_level=AuthorityLevel.L0,
                documentation_required=False,
                stop_conditions=["Evidence is absent or ungrounded."],
                actor=actor,
            )

            ready = task_service.mark_ready(task.id, actor=actor)
            if not ready.valid:
                continue

            # Resolve canonical evidence context path across projects
            ev_repo_loc = repo_loc
            if self.registry and hasattr(self.registry, "projects") and pkg.canonical_evidence_project in self.registry.projects:
                ev_pdata = self.registry.projects[pkg.canonical_evidence_project]
                ev_cand = getattr(ev_pdata, "canonical_repo", None) or (ev_pdata.get("canonical_repo") if isinstance(ev_pdata, dict) else None)
                if ev_cand and Path(ev_cand).exists():
                    ev_repo_loc = str(ev_cand)

            valid_context_paths = []
            for p in (pkg.provenance_refs or []):
                p_path = Path(p)
                if (Path(ev_repo_loc) / p_path).is_file():
                    valid_context_paths.append(str((Path(ev_repo_loc) / p_path).resolve()))
                elif (Path(repo_loc) / p_path).is_file():
                    valid_context_paths.append(str((Path(repo_loc) / p_path).resolve()))
                elif p_path.is_file():
                    valid_context_paths.append(str(p_path.resolve()))
                else:
                    valid_context_paths.append(str(p_path))

            item_id = f"FEED-{pkg.source_record_id}"
            queue_item = {
                "item_id": item_id,
                "logical_task_id": item_id,
                "task_id": task.id,
                "project_id": pkg.project_id,
                "title": pkg.title,
                "state": "READY",
                "status": "READY",
                "priority": pkg.priority,
                "created_order": 1,
                "read_only": True,
                "mutation_allowed": False,
                "task_type": "inspection_reasoning",
                "allowed_actions": ["READ_REPOSITORY", "READ_DOCUMENTATION"],
                "allowed_paths": pkg.provenance_refs or ["docs/"],
                "context_limits": {
                    "read_only_context_paths": valid_context_paths,
                    "deterministic_verification": True,
                    "evidence_reference_validation_required": True,
                },
                "dedupe_key": pkg.dedupe_key,
                "source_project": pkg.source_project,
                "execution_project": pkg.execution_project,
                "canonical_evidence_project": pkg.canonical_evidence_project,
                "canonical_evidence_path": pkg.canonical_evidence_path,
            }

            workflow = workflow_service.create(
                objective=pkg.objective,
                expected_main_head="4c48197b146282fc3e1c5383c09d10ebab37f01a",
                isolated_branch=f"lucius/feed/{pkg.source_record_id.lower()}",
                worktree_path=repo_loc,
                authority_tier=AuthorityLevel.L0.value,
                project_id=pkg.project_id,
                repository_id=reg.id,
                repository_snapshot_id="FEED-SNAPSHOT",
                task_id=task.id,
                task_backlog=[queue_item],
                dependency_graph={item_id: []},
                decisions=[{"type": "DEDUPE_KEY", "key": pkg.dedupe_key}],
                pending_task_ids=[item_id],
                actor=actor,
            )
            ingested.append({
                "task_id": task.id,
                "workflow_id": workflow.id,
                "project_id": pkg.project_id,
                "item_id": item_id,
                "dedupe_key": pkg.dedupe_key,
            })

        return ingested

    def select_next_runnable_work(
        self,
        session: Session | None = None,
        *,
        allowed_authority_classes: tuple[str, ...] = ("CLASS_A", "CLASS_B"),
    ) -> NormalizedWorkPackage | None:
        """Selects the highest priority, eligible, runnable work package."""
        packages = self.discover_global_work(session, allowed_authority_classes=allowed_authority_classes)
        for pkg in packages:
            if pkg.current_state in (GlobalWorkState.READY, GlobalWorkState.AUTO_PREPARABLE):
                if pkg.authority_class in allowed_authority_classes:
                    return pkg
        return None
