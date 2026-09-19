"""Unit tests for Shift 10N — Travel Mission Manifest & Backlog Management."""

from pathlib import Path
import pytest
from pydantic import ValidationError

from lucius.portfolio.manifest import (
    TravelMissionManifestSchema,
    TravelWorkPackageManifestItem,
    build_travel_mission_manifest,
    calculate_travel_metrics,
    get_project_priority_tier,
    load_travel_mission_manifest,
    replenish_from_canonical_roadmap,
    save_travel_mission_manifest,
    sort_manifest_items,
)


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).parent.parent.parent.resolve()


@pytest.fixture
def sample_manifest_item() -> TravelWorkPackageManifestItem:
    return TravelWorkPackageManifestItem(
        work_package_id="TEST-001",
        canonical_logical_task_id="TEST-001:research",
        project_id="darwin-research-engine",
        title="Test Research Package",
        objective="Verify research workflow",
        authority_class="CLASS_A",
        authority_level="L0",
        allowed_mutations=["READ_ONLY_RESEARCH", "DOCUMENTATION"],
        forbidden_mutations=["REAL_TRADING", "EXTERNAL_DEPLOYMENT"],
        dependencies=[],
        required_provider_class="qwen3:8b",
        minimum_sufficient_resource="LOCAL_COMPUTATION",
        completion_evidence="Test evidence",
        current_state="READY",
        priority="P0",
    )


# 1. Manifest Item Schema Validation
def test_manifest_item_defaults(sample_manifest_item: TravelWorkPackageManifestItem):
    assert sample_manifest_item.work_package_id == "TEST-001"
    assert sample_manifest_item.authority_class == "CLASS_A"
    assert "REAL_TRADING" in sample_manifest_item.forbidden_mutations
    assert sample_manifest_item.required_provider_class == "qwen3:8b"


# 2. Manifest Schema Defaults
def test_manifest_schema_defaults():
    manifest = TravelMissionManifestSchema(
        created_at="2026-09-18T23:50:00Z",
        canonical_sha="080680b4ffb7ddcc5f6400b8f1fa7e33d477a375",
    )
    assert manifest.mission_id == "LUCIUS_SHIFT_10L_TRAVEL_MODE"
    assert manifest.travel_window["start"] == "2026-09-19T01:37:09.956275-06:00"
    assert manifest.replenishment_policy == "CANONICAL_ROADMAP_ONLY"
    assert "PROVENANCE_VIOLATION" in manifest.stop_conditions


# 3. Build Manifest Discovers Global Work
def test_build_travel_mission_manifest(repo_root: Path):
    manifest = build_travel_mission_manifest(repo_root)
    assert manifest.total_packages_count > 0
    assert manifest.eligible_packages_count > 0
    assert manifest.total_packages_count >= manifest.eligible_packages_count


# 4. Correct Eligibility Counts
def test_manifest_eligibility_filter(repo_root: Path):
    manifest = build_travel_mission_manifest(repo_root)
    eligible_items = [item for item in manifest.work_packages if item.current_state in ("READY", "AUTO_PREPARABLE")]
    assert len(eligible_items) == manifest.eligible_packages_count


# 5. Productive Hours Estimation Limits
def test_productive_hours_calculation(repo_root: Path):
    manifest = build_travel_mission_manifest(repo_root)
    assert manifest.estimated_productive_hours["low"] > 0
    assert manifest.estimated_productive_hours["base"] > manifest.estimated_productive_hours["low"]
    assert manifest.estimated_productive_hours["high"] > manifest.estimated_productive_hours["base"]


# 6. Save and Load Roundtrip
def test_save_and_load_manifest(repo_root: Path, tmp_path: Path):
    manifest = build_travel_mission_manifest(repo_root)
    out_file = tmp_path / "test_manifest.json"
    save_path = save_travel_mission_manifest(manifest, out_file)
    assert save_path.exists()

    loaded = load_travel_mission_manifest(out_file)
    assert loaded is not None
    assert loaded.total_packages_count == manifest.total_packages_count
    assert loaded.canonical_sha == manifest.canonical_sha


# 7. Non-existent Manifest Load
def test_load_non_existent_manifest(tmp_path: Path):
    res = load_travel_mission_manifest(tmp_path / "does_not_exist.json")
    assert res is None


# 8. Priority Tier Assignment
def test_get_project_priority_tier():
    assert get_project_priority_tier("darwin-research-engine") == 0
    assert get_project_priority_tier("billy-production-engine") == 1
    assert get_project_priority_tier("alfred-universe") == 2
    assert get_project_priority_tier("lucius-engineering") == 3
    assert get_project_priority_tier("commercial-photography-pipeline") == 4
    assert get_project_priority_tier("fp-labs-web-applications") == 4
    assert get_project_priority_tier("cinema-collection-platform") == 4
    assert get_project_priority_tier("random-other-project") == 5


# 9. Priority Sorting Order
def test_sort_manifest_items(sample_manifest_item: TravelWorkPackageManifestItem):
    item0 = sample_manifest_item.model_copy(update={"work_package_id": "A1", "project_id": "lucius-engineering", "priority": "P1"})
    item1 = sample_manifest_item.model_copy(update={"work_package_id": "A2", "project_id": "darwin-research-engine", "priority": "P0"})
    item2 = sample_manifest_item.model_copy(update={"work_package_id": "A3", "project_id": "billy-production-engine", "priority": "P0"})

    sorted_list = sort_manifest_items([item0, item1, item2])
    assert sorted_list[0].project_id == "darwin-research-engine"
    assert sorted_list[1].project_id == "billy-production-engine"
    assert sorted_list[2].project_id == "lucius-engineering"


# 10. Synthetic Replenishment Forbidden
def test_replenish_rejects_synthetic(repo_root: Path):
    manifest = build_travel_mission_manifest(repo_root)
    with pytest.raises(ValueError, match="Synthetic task generation is explicitly forbidden"):
        replenish_from_canonical_roadmap(manifest, repo_root, allow_synthetic=True)


# 11. Replenishment Idempotency
def test_replenish_idempotent(repo_root: Path):
    manifest = build_travel_mission_manifest(repo_root)
    initial_count = manifest.total_packages_count
    replenished = replenish_from_canonical_roadmap(manifest, repo_root, allow_synthetic=False)
    assert replenished.total_packages_count == initial_count


# 12. Productivity Metrics Calculation
def test_calculate_travel_metrics(repo_root: Path):
    manifest = build_travel_mission_manifest(repo_root)
    completed = ["DARWIN-001", "BILLY-001"]
    blocked = ["ALFRED-002"]

    metrics = calculate_travel_metrics(
        manifest=manifest,
        completed_ids=completed,
        blocked_ids=blocked,
        useful_work_seconds=3600.0,
        idle_sleep_seconds=1200.0,
        total_scheduler_cycles=150,
    )

    assert metrics["total_packages"] == manifest.total_packages_count
    assert metrics["unique_useful_completions"] == 2
    assert metrics["blocked_packages_count"] == 1
    assert metrics["useful_work_seconds"] == 3600.0
    assert metrics["idle_sleep_seconds"] == 1200.0
    assert metrics["effective_work_ratio"] == round(3600.0 / 4800.0, 4)


# 13. Authority Class Mutation Safeguards
def test_authority_class_forbidden_mutations(repo_root: Path):
    manifest = build_travel_mission_manifest(repo_root)
    for item in manifest.work_packages:
        assert "REAL_TRADING" in item.forbidden_mutations
        assert "EXTERNAL_DEPLOYMENT" in item.forbidden_mutations
        assert "CREDENTIAL_CHANGE" in item.forbidden_mutations


# 14. Class A Read-Only Mutation Restrictions
def test_class_a_read_only_mutations(sample_manifest_item: TravelWorkPackageManifestItem):
    item = sample_manifest_item.model_copy(update={"authority_class": "CLASS_A"})
    assert "READ_ONLY_RESEARCH" in item.allowed_mutations
    assert "REAL_TRADING" not in item.allowed_mutations


# 15. Blocked Decision States Preserved
def test_blocked_decision_states_in_manifest(repo_root: Path):
    manifest = build_travel_mission_manifest(repo_root)
    blocked_items = [item for item in manifest.work_packages if item.current_state == "BLOCKED_DECISION"]
    assert len(blocked_items) > 0
    for item in blocked_items:
        assert item.blocked_behavior == "SKIP_AND_CONTINUE_NEXT_PROJECT"


# 16. Deduplication Key Uniqueness
def test_dedupe_keys_present(repo_root: Path):
    manifest = build_travel_mission_manifest(repo_root)
    for item in manifest.work_packages:
        assert item.canonical_logical_task_id != ""


# 17. Provider Class Requirement
def test_required_provider_class(repo_root: Path):
    manifest = build_travel_mission_manifest(repo_root)
    for item in manifest.work_packages:
        assert item.required_provider_class == "qwen3:8b"


# 18. Zero Division Protection in Metrics
def test_metrics_zero_division():
    manifest = TravelMissionManifestSchema(
        created_at="2026-09-18T23:50:00Z",
        canonical_sha="080680b4ffb7ddcc5f6400b8f1fa7e33d477a375",
        total_packages_count=0,
        eligible_packages_count=0,
    )
    metrics = calculate_travel_metrics(
        manifest=manifest,
        completed_ids=[],
        blocked_ids=[],
        useful_work_seconds=0.0,
        idle_sleep_seconds=0.0,
        total_scheduler_cycles=0,
    )
    assert metrics["completion_rate_percent"] == 0.0
    assert metrics["effective_work_ratio"] == 0.0


# 19. Duplicate Completed IDs Handled Idempotently
def test_duplicate_completions_deduped(repo_root: Path):
    manifest = build_travel_mission_manifest(repo_root)
    metrics = calculate_travel_metrics(
        manifest=manifest,
        completed_ids=["PKG-1", "PKG-1", "PKG-2"],
        blocked_ids=[],
        useful_work_seconds=100.0,
        idle_sleep_seconds=50.0,
        total_scheduler_cycles=10,
    )
    assert metrics["unique_useful_completions"] == 2


# 20. Manifest Priority Sorting Determinism
def test_manifest_priority_sort_stability(repo_root: Path):
    manifest0 = build_travel_mission_manifest(repo_root)
    manifest1 = build_travel_mission_manifest(repo_root)

    sorted0 = sort_manifest_items(manifest0.work_packages)
    sorted1 = sort_manifest_items(manifest1.work_packages)

    ids0 = [item.work_package_id for item in sorted0]
    ids1 = [item.work_package_id for item in sorted1]
    assert ids0 == ids1


# 21. Stop Conditions Inclusion
def test_stop_conditions_integrity():
    manifest = TravelMissionManifestSchema(
        created_at="2026-09-18T23:50:00Z",
        canonical_sha="080680b4ffb7ddcc5f6400b8f1fa7e33d477a375",
    )
    expected_conditions = {
        "MISSION_END_TIME_REACHED",
        "PROVENANCE_VIOLATION",
        "AUTHORITY_VIOLATION",
        "UNSAFE_CONFLICTING_WRITER",
        "OPERATOR_STOP_REQUEST",
    }
    assert expected_conditions.issubset(set(manifest.stop_conditions))


# 22. Priority Policy Alignment
def test_priority_policy_tiers():
    manifest = TravelMissionManifestSchema(
        created_at="2026-09-18T23:50:00Z",
        canonical_sha="080680b4ffb7ddcc5f6400b8f1fa7e33d477a375",
    )
    assert manifest.priority_policy[0] == "P0_DARWIN_RESEARCH_ENGINE"
    assert manifest.priority_policy[1] == "P0_BILLY_PRODUCTION_ENGINE"


# 23. Invalid JSON Handling in Load Manifest
def test_load_corrupted_manifest(tmp_path: Path):
    bad_file = tmp_path / "bad_manifest.json"
    bad_file.write_text("{ invalid json ...")
    res = load_travel_mission_manifest(bad_file)
    assert res is None


# 24. Valid JSON Item Custom Validation
def test_manifest_item_validation():
    item = TravelWorkPackageManifestItem(
        work_package_id="CUSTOM-1",
        canonical_logical_task_id="CUSTOM-1:task",
        project_id="alfred-universe",
        title="Alfred Core Audit",
        objective="Run core audit",
    )
    assert item.authority_class == "CLASS_B"
    assert item.authority_level == "L0"


# 25. Tracked Manifest File Existence
def test_tracked_manifest_file_exists(repo_root: Path):
    tracked_path = repo_root / "src" / "lucius" / "manifests" / "shift_10l_travel_work_manifest.json"
    assert tracked_path.exists()
    loaded = load_travel_mission_manifest(tracked_path)
    assert loaded is not None
    assert loaded.total_packages_count > 0
