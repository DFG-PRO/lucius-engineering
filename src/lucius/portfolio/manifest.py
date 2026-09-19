"""Shift 10N — Travel Mission Manifest & Backlog Management Module.

Defines canonical Travel Mission Manifest schema, priority policies, work package boundaries,
and deterministic manifest generation/consumption for multi-day Travel Mode operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from lucius.domain.enums import Actor, TaskPriority
from lucius.persistence.orm import utc_now
from lucius.portfolio.schemas import NormalizedWorkPackage
from lucius.portfolio.service import GlobalWorkPortfolioService
from lucius.projects.registry_schema import DFGProjectRegistry

logger = logging.getLogger(__name__)

DEFAULT_MANIFEST_PATH = ".lucius/manifests/shift_10l_travel_work_manifest.json"
CANONICAL_TRACKED_MANIFEST_PATH = "src/lucius/manifests/shift_10l_travel_work_manifest.json"


class TravelWorkPackageManifestItem(BaseModel):
    work_package_id: str
    canonical_logical_task_id: str
    project_id: str
    title: str
    objective: str
    authority_class: str = Field(default="CLASS_B")
    authority_level: str = Field(default="L0")
    allowed_mutations: list[str] = Field(default_factory=lambda: ["READ_ONLY_RESEARCH", "DOCUMENTATION", "TESTS"])
    forbidden_mutations: list[str] = Field(default_factory=lambda: ["REAL_TRADING", "EXTERNAL_DEPLOYMENT", "CREDENTIAL_CHANGE"])
    dependencies: list[str] = Field(default_factory=list)
    required_provider_class: str = Field(default="qwen3:8b")
    minimum_sufficient_resource: str = Field(default="LOCAL_COMPUTATION")
    completion_evidence: str = Field(default="Canonical audit record and test output")
    validation_command: str | None = None
    documentation_requirement: bool = True
    retry_policy: dict[str, Any] = Field(default_factory=lambda: {"max_retries": 3, "backoff_seconds": 300})
    blocked_behavior: str = Field(default="SKIP_AND_CONTINUE_NEXT_PROJECT")
    current_state: str = Field(default="READY")
    priority: str = Field(default="P1")


class TravelMissionManifestSchema(BaseModel):
    mission_id: str = Field(default="LUCIUS_SHIFT_10L_TRAVEL_MODE")
    manifest_version: str = Field(default="1.0.0")
    created_at: str
    canonical_sha: str
    travel_window: dict[str, str] = Field(
        default_factory=lambda: {
            "start": "2026-09-19T01:37:09.956275-06:00",
            "end": "2026-09-23T08:00:00-06:00",
        }
    )
    priority_policy: list[str] = Field(
        default_factory=lambda: [
            "P0_DARWIN_RESEARCH_ENGINE",
            "P0_BILLY_PRODUCTION_ENGINE",
            "P0_P1_ALFRED_UNIVERSE",
            "P1_LUCIUS_ENGINEERING",
            "P1_REVENUE_ENABLING",
            "P2_OTHER_DFG_PROJECTS",
        ]
    )
    total_packages_count: int = 0
    eligible_packages_count: int = 0
    estimated_productive_hours: dict[str, float] = Field(
        default_factory=lambda: {
            "low": 24.0,
            "base": 48.0,
            "high": 72.0,
        }
    )
    replenishment_policy: str = Field(default="CANONICAL_ROADMAP_ONLY")
    stop_conditions: list[str] = Field(
        default_factory=lambda: [
            "MISSION_END_TIME_REACHED",
            "PROVENANCE_VIOLATION",
            "AUTHORITY_VIOLATION",
            "UNSAFE_CONFLICTING_WRITER",
            "OPERATOR_STOP_REQUEST",
        ]
    )
    work_packages: list[TravelWorkPackageManifestItem] = Field(default_factory=list)


def build_travel_mission_manifest(
    repo_root: Path,
    canonical_sha: str = "080680b4ffb7ddcc5f6400b8f1fa7e33d477a375",
) -> TravelMissionManifestSchema:
    """Discovers all canonical work supply across registered DFG projects and builds the Travel Mission Manifest."""
    repo_root = Path(repo_root).resolve()
    reg_path = repo_root / "src" / "lucius" / "projects" / "dfg_canonical_registry.json"
    registry = DFGProjectRegistry.load_json(reg_path) if reg_path.exists() else None

    svc = GlobalWorkPortfolioService(registry=registry, registry_path=reg_path)
    pkgs = svc.discover_global_work(allowed_authority_classes=("CLASS_A", "CLASS_B", "CLASS_C"))

    items: list[TravelWorkPackageManifestItem] = []
    eligible_count = 0

    for pkg in pkgs:
        st_str = str(pkg.current_state)
        is_eligible = st_str in ("READY", "AUTO_PREPARABLE")
        if is_eligible:
            eligible_count += 1

        item = TravelWorkPackageManifestItem(
            work_package_id=pkg.work_id,
            canonical_logical_task_id=pkg.dedupe_key,
            project_id=pkg.project_id,
            title=pkg.title,
            objective=pkg.objective,
            authority_class=pkg.authority_class,
            allowed_mutations=["READ_ONLY_RESEARCH", "DOCUMENTATION", "TESTS"] if pkg.authority_class in ("CLASS_A", "CLASS_B") else ["CODE_EDIT", "TESTS"],
            forbidden_mutations=["REAL_TRADING", "EXTERNAL_DEPLOYMENT", "CREDENTIAL_CHANGE"],
            dependencies=pkg.dependencies,
            required_provider_class="qwen3:8b",
            minimum_sufficient_resource="LOCAL_COMPUTATION",
            completion_evidence=f"Canonical verification of {pkg.source_record_id}",
            validation_command=f"python -m pytest tests/ -k {pkg.project_id.replace('-', '_')}" if "test" in pkg.title.lower() else None,
            documentation_requirement=True,
            current_state=st_str,
            priority=pkg.priority,
        )
        items.append(item)

    manifest = TravelMissionManifestSchema(
        created_at=utc_now().isoformat(),
        canonical_sha=canonical_sha,
        total_packages_count=len(items),
        eligible_packages_count=eligible_count,
        estimated_productive_hours={
            "low": round(eligible_count * 0.8, 1),
            "base": round(eligible_count * 1.8, 1),
            "high": round(eligible_count * 2.7, 1),
        },
        work_packages=items,
    )
    return manifest


def get_project_priority_tier(project_id: str) -> int:
    """Returns numeric priority rank (0 highest, 5 lowest) based on canonical DFG travel policy."""
    pid = project_id.lower()
    if "darwin" in pid:
        return 0
    if "billy" in pid:
        return 1
    if "alfred" in pid:
        return 2
    if "lucius" in pid:
        return 3
    if any(k in pid for k in ("commercial", "fp-labs", "cinema")):
        return 4
    return 5


def sort_manifest_items(items: list[TravelWorkPackageManifestItem]) -> list[TravelWorkPackageManifestItem]:
    """Sorts work packages by priority tier, priority level, and ID deterministically."""
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    return sorted(
        items,
        key=lambda item: (
            get_project_priority_tier(item.project_id),
            priority_order.get(item.priority, 9),
            item.work_package_id,
        ),
    )


def replenish_from_canonical_roadmap(
    manifest: TravelMissionManifestSchema,
    repo_root: Path,
    allow_synthetic: bool = False,
) -> TravelMissionManifestSchema:
    """Replenishes manifest solely from canonical DFG roadmaps. Rejects synthetic work."""
    if allow_synthetic:
        raise ValueError("Synthetic task generation is explicitly forbidden by canonical authority rules.")

    fresh_manifest = build_travel_mission_manifest(repo_root, canonical_sha=manifest.canonical_sha)
    existing_ids = {item.work_package_id for item in manifest.work_packages}

    new_items = list(manifest.work_packages)
    added_count = 0

    for fresh_item in fresh_manifest.work_packages:
        if fresh_item.work_package_id not in existing_ids:
            new_items.append(fresh_item)
            added_count += 1

    sorted_items = sort_manifest_items(new_items)
    manifest.work_packages = sorted_items
    manifest.total_packages_count = len(sorted_items)
    manifest.eligible_packages_count = sum(1 for item in sorted_items if item.current_state in ("READY", "AUTO_PREPARABLE"))
    logger.info("Replenished %d new canonical work packages", added_count)
    return manifest


def calculate_travel_metrics(
    manifest: TravelMissionManifestSchema,
    completed_ids: list[str],
    blocked_ids: list[str],
    useful_work_seconds: float,
    idle_sleep_seconds: float,
    total_scheduler_cycles: int,
) -> dict[str, Any]:
    """Calculates distinct productivity metrics separating useful work from idle backoff sleep."""
    total = manifest.total_packages_count
    eligible = manifest.eligible_packages_count
    unique_completions = len(set(completed_ids))
    unique_blocked = len(set(blocked_ids))

    completion_rate = (unique_completions / eligible * 100.0) if eligible > 0 else 0.0

    return {
        "total_packages": total,
        "eligible_packages": eligible,
        "unique_useful_completions": unique_completions,
        "blocked_packages_count": unique_blocked,
        "completion_rate_percent": round(completion_rate, 2),
        "useful_work_seconds": round(useful_work_seconds, 2),
        "idle_sleep_seconds": round(idle_sleep_seconds, 2),
        "total_scheduler_cycles": total_scheduler_cycles,
        "effective_work_ratio": round(
            useful_work_seconds / (useful_work_seconds + idle_sleep_seconds)
            if (useful_work_seconds + idle_sleep_seconds) > 0
            else 0.0,
            4,
        ),
    }


def save_travel_mission_manifest(manifest: TravelMissionManifestSchema, target_path: Path) -> Path:
    target_path = Path(target_path).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w") as f:
        f.write(manifest.model_dump_json(indent=2))
    logger.info("Travel Mission Manifest saved cleanly to %s (%d packages)", target_path, len(manifest.work_packages))
    return target_path


def load_travel_mission_manifest(target_path: Path) -> TravelMissionManifestSchema | None:
    target_path = Path(target_path).resolve()
    if not target_path.exists():
        return None
    try:
        with open(target_path, "r") as f:
            data = json.load(f)
        return TravelMissionManifestSchema.model_validate(data)
    except Exception as exc:
        logger.warning("Failed to load Travel Mission Manifest from %s: %s", target_path, exc)
        return None

