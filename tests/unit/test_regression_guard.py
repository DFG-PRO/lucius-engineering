from __future__ import annotations

from pathlib import Path
import pytest

from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.projects.regression_guard import (
    RegressionGuardValidator,
    RegressionViolationType,
)


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


def test_phase_regression_detected(canonical_registry: DFGProjectRegistry):
    guard = RegressionGuardValidator(canonical_registry)
    # Billy Production is canonically at Phase 6
    result = guard.validate_proposed_task(
        "billy-production-engine",
        proposed_phase="Phase 5 - Core Execution",
    )
    assert result.passed is False
    assert any(v.violation_type == RegressionViolationType.PHASE_REGRESSION for v in result.violations)


def test_closed_gate_reopening_detected(canonical_registry: DFGProjectRegistry):
    guard = RegressionGuardValidator(canonical_registry)
    # Billy Production has closed PHASE_6_4B_2B_LIVE_GPT_IMAGE_EXECUTION_AND_PROOF
    result = guard.validate_proposed_task(
        "billy-production-engine",
        proposed_gate="PHASE_6_4B_2B_LIVE_GPT_IMAGE_EXECUTION_AND_PROOF",
    )
    assert result.passed is False
    assert any(v.violation_type == RegressionViolationType.CLOSED_GATE_REOPEN for v in result.violations)


def test_stale_sha_detected(canonical_registry: DFGProjectRegistry):
    guard = RegressionGuardValidator(canonical_registry)
    # Stale SHA from earlier in Phase 6.4B
    stale_sha = "48aa476fa774a8d3a4768bae3be6bdd0356798e4"
    result = guard.validate_proposed_task(
        "billy-production-engine",
        assumed_sha=stale_sha,
    )
    assert result.passed is False
    assert any(v.violation_type == RegressionViolationType.STALE_SHA_ASSUMPTION for v in result.violations)


def test_brief_not_engineering_ready_blocks_mutation(canonical_registry: DFGProjectRegistry):
    guard = RegressionGuardValidator(canonical_registry)
    # cinema-collection is at BUSINESS_VALIDATION_BEFORE_SOFTWARE
    result = guard.validate_proposed_task(
        "cinema-collection",
        is_engineering_mutation=True,
    )
    assert result.passed is False
    assert any(v.violation_type == RegressionViolationType.BRIEF_NOT_ENGINEERING_READY for v in result.violations)


def test_source_of_truth_conflict_detected(canonical_registry: DFGProjectRegistry):
    guard = RegressionGuardValidator(canonical_registry)
    result = guard.validate_proposed_task(
        "billy-production-engine",
        declared_source_of_truth="Historical prompt conversation",
    )
    assert result.passed is False
    assert any(v.violation_type == RegressionViolationType.SOURCE_OF_TRUTH_CONFLICT for v in result.violations)


def test_valid_forward_task_passes(canonical_registry: DFGProjectRegistry):
    guard = RegressionGuardValidator(canonical_registry)
    # Phase 6.5 forward task on Billy with current verified SHA
    verified_sha = canonical_registry.require_project("billy-production-engine").last_verified_sha
    result = guard.validate_proposed_task(
        "billy-production-engine",
        proposed_phase="Phase 6 - Unified Runtime",
        proposed_gate="PHASE_6_5_ASSET_LIFECYCLE_ACCEPTANCE",
        assumed_sha=verified_sha,
        is_engineering_mutation=True,
        declared_source_of_truth="docs/project_bible/phase_6/PHASE_6_BLUEPRINT_V1.md",
    )
    assert result.passed is True
    assert len(result.violations) == 0
