from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import pytest
from sqlalchemy.orm import Session

from lucius.domain.enums import Actor, DurableWaitClass, GlobalWorkState, MissionStatus, TaskPriority
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import DurableMissionORM, PersistentWorkflowORM, TaskORM
from lucius.portfolio.adapters import CanonicalRoadmapAdapter, DarwinBacklogAdapter
from lucius.portfolio.schemas import NormalizedWorkPackage
from lucius.portfolio.service import GlobalWorkPortfolioService
from lucius.runtime.continuation import BoundedContinuationService, ContinuationStopReason, SessionBudget
from lucius.runtime.mission import DurableMissionSupervisor


@pytest.fixture
def memory_db():
    engine = create_sqlite_engine(":memory:")
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()
    yield session
    session.close()


def test_multi_source_discovery_and_adapter_aggregation():
    svc = GlobalWorkPortfolioService()
    packages = svc.discover_global_work()
    assert len(packages) > 0
    project_ids = {p.project_id for p in packages}
    assert "darwin-research-engine" in project_ids
    assert "billy-production-engine" in project_ids
    assert "fp-labs" in project_ids
    assert "lucius-engineering" in project_ids


def test_darwin_adapter_provenance_validation():
    adapter = DarwinBacklogAdapter()
    pkgs = adapter.discover_work_packages()
    assert len(pkgs) > 0
    for pkg in pkgs:
        assert pkg.dedupe_key.startswith("darwin:")
        if not pkg.provenance_refs:
            assert pkg.current_state != GlobalWorkState.READY
            assert pkg.current_state != GlobalWorkState.AUTO_PREPARABLE


def test_canonical_roadmap_adapter_next_gates():
    adapter = CanonicalRoadmapAdapter()
    pkgs = adapter.discover_work_packages()
    assert len(pkgs) >= 22
    for pkg in pkgs:
        assert pkg.work_type == "CANONICAL_NEXT_GATE_PREPARATION"
        assert pkg.provenance_refs is not None
        assert len(pkg.provenance_refs) > 0


def test_auto_preparable_reconciliation_audit():
    svc = GlobalWorkPortfolioService()
    audit = svc.reconcile_auto_preparable()
    assert audit["total_audited"] == 19
    assert audit["counts"]["VALID_AUTO_PREPARABLE"] == 6
    assert audit["counts"]["MISSING_PROVENANCE"] == 13


def test_deduplication_across_adapters():
    svc = GlobalWorkPortfolioService()
    pkgs = svc.discover_global_work()
    keys = [p.dedupe_key for p in pkgs]
    assert len(keys) == len(set(keys))


def test_priority_ranking_ready_first():
    svc = GlobalWorkPortfolioService()
    pkgs = svc.discover_global_work()
    first_pkg = pkgs[0]
    assert first_pkg.current_state in (GlobalWorkState.READY, GlobalWorkState.AUTO_PREPARABLE)
    assert first_pkg.priority in ("P0", "P0_NOW", "CRITICAL", "HIGH", "P1", "P1_HIGH")


def test_waiting_stop_reason_semantics(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="4c48197b146282fc3e1c5383c09d10ebab37f01a")
    
    # Add a workflow item in WAITING_DEPENDENCY state
    wf = PersistentWorkflowORM(
        id="wf_waiting_test",
        objective="Test Waiting",
        expected_main_head="4c48197b146282fc3e1c5383c09d10ebab37f01a",
        isolated_branch="test-branch",
        worktree_path="/tmp/test",
        workflow_state="PLAN_READY",
        authority_tier="L0",
        task_backlog=[{
            "id": "item_wait_01",
            "item_id": "item_wait_01",
            "state": "WAITING_DEPENDENCY",
            "priority": "HIGH",
        }],
    )
    memory_db.add(wf)
    memory_db.flush()

    class MockRuntimeService:
        dispatcher = None
        def run(self, config):
            from lucius.runtime.schemas import ExecutionRuntimeLoopResult, RuntimeLoopStatus
            return ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE, selected_tasks=0)

    svc = BoundedContinuationService(
        session=memory_db,
        runtime_service=MockRuntimeService(),
        supervisor=supervisor,
    )

    result = svc.run_session(SessionBudget(max_cycles=1), mission_id=mission.mission_id)
    assert result.status == "WAITING"
    assert result.stop_reason == ContinuationStopReason.WAITING_NO_CURRENTLY_RUNNABLE_WORK.value


def test_completed_mission_stop_reason_semantics(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="4c48197b146282fc3e1c5383c09d10ebab37f01a")
    
    wf = PersistentWorkflowORM(
        id="wf_comp_test",
        objective="Test Completed",
        expected_main_head="4c48197b146282fc3e1c5383c09d10ebab37f01a",
        isolated_branch="test-branch",
        worktree_path="/tmp/test",
        workflow_state="COMPLETED",
        authority_tier="L0",
        task_backlog=[{
            "id": "item_comp_01",
            "item_id": "item_comp_01",
            "state": "COMPLETED",
            "priority": "HIGH",
        }],
    )
    memory_db.add(wf)
    memory_db.flush()

    class MockRuntimeService:
        dispatcher = None
        def run(self, config):
            from lucius.runtime.schemas import ExecutionRuntimeLoopResult, RuntimeLoopStatus
            return ExecutionRuntimeLoopResult(status=RuntimeLoopStatus.IDLE, selected_tasks=0)

    svc = BoundedContinuationService(
        session=memory_db,
        runtime_service=MockRuntimeService(),
        supervisor=supervisor,
    )

    result = svc.run_session(SessionBudget(max_cycles=1), mission_id=mission.mission_id)
    assert result.status == "COMPLETED"
    assert result.stop_reason == ContinuationStopReason.IDLE_NO_ELIGIBLE_WORK.value


def test_feed_portfolio_into_queue(memory_db: Session):
    svc = GlobalWorkPortfolioService()
    ingested = svc.feed_portfolio_into_queue(memory_db, max_items=3)
    assert len(ingested) == 3
    for item in ingested:
        assert "task_id" in item
        assert "workflow_id" in item
        assert "project_id" in item
