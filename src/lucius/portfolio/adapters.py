from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from pathlib import Path
from typing import Any

from lucius.domain.enums import GlobalWorkState, ProjectPriority
from lucius.portfolio.schemas import NormalizedWorkPackage
from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.runtime.feeder import DarwinBacklogFeeder

logger = logging.getLogger(__name__)


class ProjectWorkSourceAdapter(ABC):
    """Abstract protocol for project-owned work source adapters."""

    @abstractmethod
    def get_supported_project_ids(self) -> list[str]:
        """Returns the list of project IDs supported by this adapter."""
        pass

    @abstractmethod
    def discover_work_packages(
        self,
        allowed_authority_classes: tuple[str, ...] = ("CLASS_A", "CLASS_B"),
    ) -> list[NormalizedWorkPackage]:
        """Discovers and normalizes work packages from project-owned sources."""
        pass


class DarwinBacklogAdapter(ProjectWorkSourceAdapter):
    """Adapter for Darwin Master Research Backlog items."""

    def __init__(self, feeder: DarwinBacklogFeeder | None = None):
        self.feeder = feeder or DarwinBacklogFeeder()

    def get_supported_project_ids(self) -> list[str]:
        return [
            "darwin-research-engine",
            "fp-labs",
            "cinema-collection",
            "commercial-photography",
            "dfg-binance-executor",
            "locations-concierge",
            "commerce-affiliate-engine",
            "fp-criptoclub",
            "billy-the-trader",
            "billy-production-engine",
            "dealhunter",
            "dfg-prediction-engine",
            "arbitrage-engine",
            "section-8-real-estate",
        ]

    def discover_work_packages(
        self,
        allowed_authority_classes: tuple[str, ...] = ("CLASS_A", "CLASS_B"),
    ) -> list[NormalizedWorkPackage]:
        packages: list[NormalizedWorkPackage] = []
        raw_items = self.feeder.load_raw_backlog_items()

        for item in raw_items:
            data = item.model_dump() if hasattr(item, "model_dump") else (dict(item) if isinstance(item, dict) else {})
            item_id = str(data.get("item_id", "")).strip()
            if not item_id:
                continue

            raw_status = str(data.get("status", "QUEUED")).upper()
            raw_p = str(data.get("priority", "P1")).upper()
            blocked_by = data.get("blocked_by", [])
            provenance_refs = [str(r) for r in data.get("provenance_refs", [])]

            # Determine project ID
            pid = "darwin-research-engine"
            if "MON-001" in item_id: pid = "fp-labs"
            elif "MON-002" in item_id: pid = "commercial-photography"
            elif "MON-003" in item_id: pid = "cinema-collection"
            elif "MON-004" in item_id: pid = "dfg-binance-executor"
            elif "MON-005" in item_id: pid = "commerce-affiliate-engine"
            elif "MON-006" in item_id: pid = "fp-criptoclub"
            elif "MON-007" in item_id: pid = "billy-the-trader"
            elif "MON-008" in item_id: pid = "billy-production-engine"
            elif "MON-010" in item_id: pid = "dfg-prediction-engine"
            elif "MON-011" in item_id: pid = "arbitrage-engine"
            elif "MON-012" in item_id: pid = "section-8-real-estate"
            elif "VAL-001" in item_id: pid = "locations-concierge"
            elif "VAL-002" in item_id: pid = "dealhunter"

            # Determine global work state
            if raw_status == "READY":
                if blocked_by:
                    state = GlobalWorkState.WAITING_DEPENDENCY
                elif not provenance_refs:
                    state = GlobalWorkState.NOT_YET_ELIGIBLE
                else:
                    state = GlobalWorkState.READY
            elif raw_status == "NEEDS_EVIDENCE":
                if not provenance_refs or "DECISION" in item_id:
                    state = GlobalWorkState.BLOCKED_DECISION
                else:
                    state = GlobalWorkState.AUTO_PREPARABLE
            elif "BLOCKED" in raw_status:
                if "LEGAL" in raw_status or "LEGAL" in str(blocked_by):
                    state = GlobalWorkState.BLOCKED_AUTHORITY
                else:
                    state = GlobalWorkState.BLOCKED_DECISION
            elif raw_status in ("REJECTED", "SUPERSEDED", "COMPLETED", "CANCELLED", "PARKED"):
                continue
            else:
                state = GlobalWorkState.NOT_YET_ELIGIBLE

            authority_class = str(data.get("authority_class", "CLASS_B")).upper()
            if authority_class not in allowed_authority_classes:
                if authority_class == "CLASS_C":
                    state = GlobalWorkState.BLOCKED_AUTHORITY

            title = str(data.get("title", "")).strip()
            objective = str(data.get("objective", "")).strip()
            criteria = data.get("research_questions", []) or ([data.get("expected_output")] if data.get("expected_output") else [])

            pkg = NormalizedWorkPackage(
                work_id=f"work_{item_id}",
                project_id=pid,
                title=f"[{pid}] {title}",
                objective=objective,
                priority=raw_p,
                project_priority=raw_p,
                work_priority=raw_p,
                work_type=str(data.get("task_type", "RESEARCH_BACKLOG_ITEM")),
                current_state=state,
                readiness="READY" if state == GlobalWorkState.READY else str(state),
                authority_class=authority_class,
                required_capability="qwen3:8b",
                qualified_resource="qwen3:8b",
                dependencies=[str(d) for d in data.get("dependencies", [])],
                provenance_refs=provenance_refs,
                expected_output=data.get("expected_output"),
                acceptance_criteria=criteria,
                source_project=pid,
                source_record_id=item_id,
                dedupe_key=f"darwin:{item_id}",
            )
            packages.append(pkg)

        return packages


class CanonicalRoadmapAdapter(ProjectWorkSourceAdapter):
    """Adapter for canonical project registry next-gate specifications and DDB work packages."""

    def __init__(self, registry: DFGProjectRegistry | None = None, registry_path: Path | str | None = None):
        if registry is not None:
            self.registry = registry
        elif registry_path is not None and Path(registry_path).exists():
            self.registry = DFGProjectRegistry.load_json(Path(registry_path))
        else:
            default_reg = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering/src/lucius/projects/dfg_canonical_registry.json")
            self.registry = DFGProjectRegistry.load_json(default_reg) if default_reg.exists() else None

    def get_supported_project_ids(self) -> list[str]:
        if self.registry is None or not hasattr(self.registry, "projects"):
            return []
        return list(self.registry.projects.keys())

    def discover_work_packages(
        self,
        allowed_authority_classes: tuple[str, ...] = ("CLASS_A", "CLASS_B"),
    ) -> list[NormalizedWorkPackage]:
        if self.registry is None or not hasattr(self.registry, "projects"):
            return []

        packages: list[NormalizedWorkPackage] = []
        for pid, pdata in self.registry.projects.items():
            raw = pdata.model_dump() if hasattr(pdata, "model_dump") else dict(pdata)
            next_gate = raw.get("next_canonical_gate")
            if not next_gate:
                continue

            current_phase = raw.get("current_phase", "GENERAL_DEVELOPMENT")
            status_str = str(raw.get("status", "IN_DEVELOPMENT")).upper()
            p_priority = str(raw.get("priority", "NORMAL")).upper()
            docs = raw.get("canonical_docs") or []
            auth_list = raw.get("authority_profile") or ["CLASS_A"]
            auth_class = auth_list[0] if auth_list else "CLASS_A"

            # Determine state based on project status
            if status_str in ("OPERATIONAL", "MAINTENANCE"):
                state = GlobalWorkState.AUTO_PREPARABLE
            elif status_str in ("IN_DEVELOPMENT", "ACTIVE"):
                state = GlobalWorkState.READY if docs else GlobalWorkState.AUTO_PREPARABLE
            elif status_str in ("DESIGNING", "VALIDATING", "RESEARCHING"):
                state = GlobalWorkState.AUTO_PREPARABLE
            elif status_str in ("BLOCKED", "CONCEPT"):
                state = GlobalWorkState.BLOCKED_DECISION
            else:
                state = GlobalWorkState.NOT_YET_ELIGIBLE

            if auth_class not in allowed_authority_classes:
                if auth_class == "CLASS_C":
                    state = GlobalWorkState.BLOCKED_AUTHORITY

            provenance = docs if docs else ["src/lucius/projects/dfg_canonical_registry.json"]
            display_name = str(raw.get("display_name", pid))

            pkg = NormalizedWorkPackage(
                work_id=f"gate_work_{pid}_{next_gate.lower()}",
                project_id=pid,
                title=f"[{display_name} Next Gate] {next_canonical_gate_format(next_gate)}",
                objective=f"Prepare and verify canonical next gate requirements ({next_gate}) for {display_name} phase {current_phase}.",
                priority=p_priority,
                project_priority=p_priority,
                work_priority=p_priority,
                work_type="CANONICAL_NEXT_GATE_PREPARATION",
                current_state=state,
                readiness="READY" if state == GlobalWorkState.READY else str(state),
                authority_class=auth_class,
                required_capability="qwen3:8b",
                qualified_resource="qwen3:8b",
                dependencies=list(raw.get("dependencies", [])),
                provenance_refs=provenance,
                expected_output=f"Verified specification for gate {next_gate}",
                acceptance_criteria=[
                    f"Extract explicit requirements for gate {next_gate}",
                    f"Validate compliance with phase {current_phase} boundaries",
                    "Verify evidence grounding against canonical documentation",
                ],
                source_project=pid,
                source_record_id=f"registry:{pid}:{next_gate}",
                dedupe_key=f"registry_gate:{pid}:{next_gate}",
            )
            packages.append(pkg)

        return packages


def next_canonical_gate_format(gate: str) -> str:
    return gate.replace("_", " ").title()
