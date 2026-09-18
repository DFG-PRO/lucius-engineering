from __future__ import annotations

from pathlib import Path
import pytest

from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.projects.regression_guard import RegressionGuardValidator
from lucius.runtime.feeder import DarwinBacklogFeeder


@pytest.fixture
def canonical_registry() -> DFGProjectRegistry:
    registry_path = (
        Path(__file__).resolve().parent.parent.parent
        / "src"
        / "lucius"
        / "projects"
        / "dfg_canonical_registry.json"
    )
    return DFGProjectRegistry.load_json(registry_path)


def test_feeder_rejects_stale_production_phase_5(canonical_registry: DFGProjectRegistry):
    guard = RegressionGuardValidator(canonical_registry)
    stale_item = {
        "item_id": "P5-STALE-TASK",
        "title": "Stale Phase 5 Implementation Attempt",
        "status": "READY",
        "priority": "P0",
        "objective": "Attempt to re-implement Phase 5.",
        "phase": "Phase 5 - Core",
        "authority_class": "CLASS_A",
    }
    feeder = DarwinBacklogFeeder(
        custom_items=[stale_item],
        registry=canonical_registry,
        regression_guard=guard,
    )
    # Testing ingestion with target_project_id = 'billy-production-engine'
    tasks = feeder.discover_eligible_tasks(project_id="billy-production-engine")
    assert len(tasks) == 0  # Blocked by phase regression


def test_feeder_accepts_valid_phase_6_5_candidate(canonical_registry: DFGProjectRegistry):
    guard = RegressionGuardValidator(canonical_registry)
    verified_sha = canonical_registry.require_project("billy-production-engine").last_verified_sha
    valid_item = {
        "item_id": "P6-5-ASSET-LIFECYCLE",
        "title": "Phase 6.5 Asset Lifecycle Ingestion Spec",
        "status": "READY",
        "priority": "P0",
        "objective": "Prepare asset lifecycle normalization.",
        "phase": "Phase 6 - Unified Runtime",
        "gate": "PHASE_6_5_ASSET_LIFECYCLE_ACCEPTANCE",
        "assumed_sha": verified_sha,
        "authority_class": "CLASS_A",
    }
    feeder = DarwinBacklogFeeder(
        custom_items=[valid_item],
        registry=canonical_registry,
        regression_guard=guard,
    )
    tasks = feeder.discover_eligible_tasks(project_id="billy-production-engine")
    assert len(tasks) == 1
    assert tasks[0].source_item_id == "P6-5-ASSET-LIFECYCLE"


def test_feeder_blocks_engineering_on_brief_missing_project(canonical_registry: DFGProjectRegistry):
    guard = RegressionGuardValidator(canonical_registry)
    # cinema-collection is not engineering ready
    item = {
        "item_id": "CINEMA-CODE-MUTATION",
        "title": "Write Rental App Code",
        "status": "READY",
        "priority": "P0",
        "objective": "Write web software for cinema collection.",
        "authority_class": "CLASS_C",
    }
    feeder = DarwinBacklogFeeder(
        custom_items=[item],
        registry=canonical_registry,
        regression_guard=guard,
    )
    tasks = feeder.discover_eligible_tasks(
        project_id="cinema-collection",
        allowed_authority_classes=("CLASS_A", "CLASS_B", "CLASS_C"),
    )
    assert len(tasks) == 0  # Blocked because brief is not engineering ready


def test_feeder_permits_research_task_for_researching_project(canonical_registry: DFGProjectRegistry):
    guard = RegressionGuardValidator(canonical_registry)
    # dealhunter is in RESEARCHING status
    item = {
        "item_id": "DEAL-SCOUT-01",
        "title": "Deal Signal Scraping Research",
        "status": "READY",
        "priority": "P0",
        "objective": "Scout market deal signals.",
        "authority_class": "CLASS_B",
    }
    feeder = DarwinBacklogFeeder(
        custom_items=[item],
        registry=canonical_registry,
        regression_guard=guard,
    )
    tasks = feeder.discover_eligible_tasks(project_id="dealhunter")
    assert len(tasks) == 1
    assert tasks[0].source_item_id == "DEAL-SCOUT-01"


def test_concept_project_without_repo_handled_safely(canonical_registry: DFGProjectRegistry):
    guard = RegressionGuardValidator(canonical_registry)
    # social-engine is a concept project without a repo
    item = {
        "item_id": "SOC-CONCEPT-01",
        "title": "Audience Strategy Synthesis",
        "status": "READY",
        "priority": "P1",
        "objective": "Synthesize audience personas.",
        "authority_class": "CLASS_B",
    }
    feeder = DarwinBacklogFeeder(
        custom_items=[item],
        registry=canonical_registry,
        regression_guard=guard,
    )
    tasks = feeder.discover_eligible_tasks(project_id="social-engine")
    assert len(tasks) == 1
    assert tasks[0].project_id == "social-engine"
