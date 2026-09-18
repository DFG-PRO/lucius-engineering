from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from lucius.domain.enums import (
    Actor,
    DesignCoverage,
    DDBDepth,
    GlobalWorkState,
    ProjectPriority,
    ProjectProgressiveStage,
)
from lucius.portfolio.schemas import (
    NormalizedProjectInventoryRecord,
    NormalizedWorkPackage,
)
from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.runtime.feeder import DarwinBacklogFeeder, NormalizedTaskEnvelope

logger = logging.getLogger(__name__)

PRIORITY_RANK = {
    "P0_CRITICAL": 0,
    "P0_NOW": 0,
    "P0": 0,
    "P1_HIGH": 1,
    "P1": 1,
    "P2_NORMAL": 2,
    "P2": 2,
    "P3_LOW": 3,
    "P3": 3,
    "P4_SPECULATIVE": 4,
    "P4": 4,
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

        self.feeder = feeder or DarwinBacklogFeeder()

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
        """Discovers, normalizes, deduplicates, and ranks work packages across all Universe projects."""
        work_packages: list[NormalizedWorkPackage] = []
        raw_envelopes = self.feeder.discover_eligible_tasks(allowed_authority_classes=allowed_authority_classes)

        for env in raw_envelopes:
            item_id = env.source_item_id
            payload = env.raw_payload or {}
            raw_p = payload.get("priority", "P1")
            
            # Map item status to GlobalWorkState
            state = GlobalWorkState.READY
            if payload.get("provenance_refs") == []:
                state = GlobalWorkState.WAITING_DEPENDENCY
            elif payload.get("blocked_by"):
                state = GlobalWorkState.BLOCKED_DEPENDENCY
            elif env.authority_class not in allowed_authority_classes:
                state = GlobalWorkState.BLOCKED_AUTHORITY

            pkg = NormalizedWorkPackage(
                work_id=f"work_{item_id}",
                project_id=env.project_id,
                title=env.title,
                objective=env.objective,
                priority=raw_p,
                project_priority=raw_p,
                work_priority=raw_p,
                work_type=env.task_type,
                current_state=state,
                readiness="READY" if state == GlobalWorkState.READY else str(state),
                authority_class=env.authority_class,
                required_capability=env.provider_requirement,
                qualified_resource=env.provider_requirement,
                dependencies=env.dependencies,
                provenance_refs=env.provenance_refs,
                expected_output=payload.get("expected_output"),
                acceptance_criteria=payload.get("research_questions", []),
                source_project=env.project_id,
                source_record_id=item_id,
                dedupe_key=env.dedupe_key,
            )
            work_packages.append(pkg)

        # Rank work packages: GlobalWorkState == READY first, then priority rank (P0 < P1 < P2 < P3 < P4), then item_id
        work_packages.sort(
            key=lambda w: (
                0 if w.current_state in (GlobalWorkState.READY, GlobalWorkState.AUTO_PREPARABLE) else 1,
                PRIORITY_RANK.get(w.priority, 99),
                w.work_id,
            )
        )
        return work_packages

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
