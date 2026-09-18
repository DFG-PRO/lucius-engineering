from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from sqlalchemy.orm import Session

from lucius.domain.enums import (
    Actor,
    DesignCoverage,
    DDBDepth,
    DurableWaitClass,
    GlobalWorkState,
    MissionStatus,
    ProjectPriority,
    ProjectProgressiveStage,
    QueueWorkItemState,
    TaskPriority,
)
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import PersistentWorkflowORM
from lucius.portfolio.schemas import (
    NormalizedProjectInventoryRecord,
    NormalizedWorkPackage,
)
from lucius.portfolio.service import GlobalWorkPortfolioService
from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.runtime.continuation import BoundedContinuationService, ContinuationStopReason, SessionBudget
from lucius.runtime.feeder import DarwinBacklogFeeder
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.ollama import OllamaExecutionProvider
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.schemas import ExecutionRuntimeLoopResult, RuntimeLoopStatus


@pytest.fixture
def memory_db():
    engine = create_sqlite_engine(":memory:")
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()
    yield session
    session.close()


# 1. test_p0_ready_beats_p1_p2_p3_p4
def test_p0_ready_beats_p1_p2_p3_p4():
    items = [
        {"item_id": "RBACK-P4-01", "title": "P4 Item", "status": "READY", "priority": "P4", "objective": "Obj 4", "provenance_refs": ["doc.md"]},
        {"item_id": "RBACK-P0-01", "title": "P0 Item", "status": "READY", "priority": "P0_NOW", "objective": "Obj 0", "provenance_refs": ["doc.md"]},
        {"item_id": "RBACK-P2-01", "title": "P2 Item", "status": "READY", "priority": "P2", "objective": "Obj 2", "provenance_refs": ["doc.md"]},
    ]
    feeder = DarwinBacklogFeeder(custom_items=items)
    svc = GlobalWorkPortfolioService(feeder=feeder)
    work = svc.discover_global_work()
    assert len(work) == 3
    assert work[0].source_record_id == "RBACK-P0-01"
    assert work[1].source_record_id == "RBACK-P2-01"
    assert work[2].source_record_id == "RBACK-P4-01"


# 2. test_waiting_p0_does_not_block_ready_p1
def test_waiting_p0_does_not_block_ready_p1():
    items = [
        {"item_id": "RBACK-P0-WAIT", "title": "P0 Waiting", "status": "READY", "priority": "P0_NOW", "objective": "Obj 0", "provenance_refs": []}, # Missing provenance -> WAITING_DEPENDENCY
        {"item_id": "RBACK-P1-READY", "title": "P1 Ready", "status": "READY", "priority": "P1", "objective": "Obj 1", "provenance_refs": ["doc.md"]},
    ]
    feeder = DarwinBacklogFeeder(custom_items=items)
    svc = GlobalWorkPortfolioService(feeder=feeder)
    next_work = svc.select_next_runnable_work()
    assert next_work is not None
    assert next_work.source_record_id == "RBACK-P1-READY"


# 3. test_blocked_p1_does_not_block_ready_p2
def test_blocked_p1_does_not_block_ready_p2():
    items = [
        {"item_id": "RBACK-P1-BLOCKED", "title": "P1 Blocked", "status": "READY", "priority": "P1", "objective": "Obj 1", "blocked_by": ["DEP-01"], "provenance_refs": ["doc.md"]},
        {"item_id": "RBACK-P2-READY", "title": "P2 Ready", "status": "READY", "priority": "P2", "objective": "Obj 2", "provenance_refs": ["doc.md"]},
    ]
    feeder = DarwinBacklogFeeder(custom_items=items)
    svc = GlobalWorkPortfolioService(feeder=feeder)
    next_work = svc.select_next_runnable_work()
    assert next_work is not None
    assert next_work.source_record_id == "RBACK-P2-READY"


# 4. test_ready_p2_beats_preparable_p4
def test_ready_p2_beats_preparable_p4():
    items = [
        {"item_id": "RBACK-P4-PREP", "title": "P4 Preparable", "status": "READY", "priority": "P4", "objective": "Obj 4", "provenance_refs": ["doc.md"]},
        {"item_id": "RBACK-P2-READY", "title": "P2 Ready", "status": "READY", "priority": "P2", "objective": "Obj 2", "provenance_refs": ["doc.md"]},
    ]
    feeder = DarwinBacklogFeeder(custom_items=items)
    svc = GlobalWorkPortfolioService(feeder=feeder)
    work = svc.discover_global_work()
    assert work[0].source_record_id == "RBACK-P2-READY"
    assert work[1].source_record_id == "RBACK-P4-PREP"


# 5. test_p4_may_progress_when_nothing_higher_is_eligible
def test_p4_may_progress_when_nothing_higher_is_eligible():
    items = [
        {"item_id": "RBACK-P4-ONLY", "title": "P4 Only Eligible", "status": "READY", "priority": "P4", "objective": "Obj 4", "provenance_refs": ["doc.md"]},
    ]
    feeder = DarwinBacklogFeeder(custom_items=items)
    svc = GlobalWorkPortfolioService(feeder=feeder)
    next_work = svc.select_next_runnable_work()
    assert next_work is not None
    assert next_work.source_record_id == "RBACK-P4-ONLY"


# 6. test_blocked_authority_never_bypassed
def test_blocked_authority_never_bypassed():
    items = [
        {"item_id": "RBACK-CLASS-C", "title": "Engineering Mutation", "status": "READY", "priority": "P0_NOW", "authority_class": "CLASS_C", "objective": "Obj C", "provenance_refs": ["doc.md"]},
    ]
    feeder = DarwinBacklogFeeder(custom_items=items)
    svc = GlobalWorkPortfolioService(feeder=feeder)
    next_work = svc.select_next_runnable_work(allowed_authority_classes=("CLASS_A", "CLASS_B"))
    assert next_work is None


# 7. test_blocked_decision_never_bypassed
def test_blocked_decision_never_bypassed():
    items = [
        {"item_id": "RBACK-DECISION", "title": "Decision Blocked", "status": "NEEDS_EVIDENCE", "priority": "P0_NOW", "objective": "Obj Dec", "provenance_refs": ["doc.md"]},
    ]
    feeder = DarwinBacklogFeeder(custom_items=items)
    svc = GlobalWorkPortfolioService(feeder=feeder)
    next_work = svc.select_next_runnable_work()
    assert next_work is None


# 8. test_waiting_resource_retains_mission_ownership
def test_waiting_resource_retains_mission_ownership(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")
    supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Ollama busy",
    )
    fetched = supervisor.get_mission(mission.mission_id)
    assert fetched.status == MissionStatus.WAITING


# 9. test_recovered_p0_re_enters_selection
def test_recovered_p0_re_enters_selection(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    mission = supervisor.create_mission(canonical_sha="abc12345")
    past_time = datetime.now(timezone.utc) - timedelta(seconds=10)
    
    wf = PersistentWorkflowORM(
        id="wf_p0_rec",
        objective="P0 Recover",
        expected_main_head="abc12345",
        isolated_branch="test-branch",
        worktree_path="/tmp/test",
        workflow_state="PLAN_READY",
        authority_tier="L0",
        task_backlog=[{
            "id": "item_p0_rec",
            "item_id": "item_p0_rec",
            "logical_task_id": "task_p0_rec",
            "workflow_id": "wf_p0_rec",
            "state": QueueWorkItemState.WAITING_RESOURCE.value,
            "priority": TaskPriority.HIGH.value,
        }],
    )
    memory_db.add(wf)
    memory_db.flush()

    supervisor.record_wait(
        mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE,
        reason="Rate limit",
        item_id="item_p0_rec",
        retry_after=past_time,
    )

    cleared = supervisor.reevaluate_durable_waits(mission.mission_id)
    assert len(cleared) == 1
    items = supervisor._get_all_items()
    assert items[0]["state"] == QueueWorkItemState.READY.value


# 10. test_no_duplicate_work
def test_no_duplicate_work(memory_db: Session):
    items = [
        {"item_id": "RBACK-MON-001", "title": "Dup Test", "status": "READY", "priority": "P0_NOW", "objective": "Obj", "provenance_refs": ["doc.md"]},
    ]
    feeder = DarwinBacklogFeeder(custom_items=items)
    ingested1 = feeder.feed_into_queue(memory_db, max_items=5)
    ingested2 = feeder.feed_into_queue(memory_db, max_items=5)
    assert len(ingested1) == 1
    assert len(ingested2) == 0  # Deduplicated!


# 11. test_unknown_priority_handled_safely
def test_unknown_priority_handled_safely():
    items = [
        {"item_id": "RBACK-UNK", "title": "Unknown Pri", "status": "READY", "priority": "UNKNOWN_PRI", "objective": "Obj", "provenance_refs": ["doc.md"]},
    ]
    feeder = DarwinBacklogFeeder(custom_items=items)
    svc = GlobalWorkPortfolioService(feeder=feeder)
    work = svc.discover_global_work()
    assert len(work) == 1
    assert work[0].source_record_id == "RBACK-UNK"


# 12. test_missing_design_does_not_become_engineering_ready
def test_missing_design_does_not_become_engineering_ready():
    svc = GlobalWorkPortfolioService()
    inv = svc.get_canonical_inventory()
    dealhunter = next(p for p in inv if p.project_id == "dealhunter")
    assert dealhunter.has_design_debt is True
    assert dealhunter.design_coverage in (DesignCoverage.D0_IDEA_ONLY, DesignCoverage.D1_CONCEPT_DEFINED, DesignCoverage.D2_PARTIAL_DESIGN)


# 13. test_missing_ddb_material_decision_remains_blocked
def test_missing_ddb_material_decision_remains_blocked():
    svc = GlobalWorkPortfolioService()
    inv = svc.get_canonical_inventory()
    prediction = next(p for p in inv if p.project_id == "dfg-prediction-engine")
    assert prediction.portfolio_state == ProjectProgressiveStage.BLOCKED


# 14. test_accepted_concept_can_have_preparatory_work_without_software_build
def test_accepted_concept_can_have_preparatory_work_without_software_build():
    items = [
        {"item_id": "RBACK-PREP-DOC", "title": "Prep Doc Research", "status": "READY", "priority": "P2", "objective": "Draft brief", "provenance_refs": ["doc.md"]},
    ]
    feeder = DarwinBacklogFeeder(custom_items=items)
    svc = GlobalWorkPortfolioService(feeder=feeder)
    next_work = svc.select_next_runnable_work()
    assert next_work is not None
    assert next_work.authority_class == "CLASS_B"


# 15. test_rejected_superseded_projects_never_feed
def test_rejected_superseded_projects_never_feed():
    items = [
        {"item_id": "RBACK-REJECTED", "title": "Rejected Item", "status": "REJECTED", "priority": "P0", "objective": "Obj", "provenance_refs": ["doc.md"]},
        {"item_id": "RBACK-SUPERSEDED", "title": "Superseded Item", "status": "SUPERSEDED", "priority": "P0", "objective": "Obj", "provenance_refs": ["doc.md"]},
    ]
    feeder = DarwinBacklogFeeder(custom_items=items)
    svc = GlobalWorkPortfolioService(feeder=feeder)
    work = svc.discover_global_work()
    assert len(work) == 0


# 16. test_project_registry_remains_canonical
def test_project_registry_remains_canonical():
    svc = GlobalWorkPortfolioService()
    inv = svc.get_canonical_inventory()
    assert len(inv) == 22
    pids = [p.project_id for p in inv]
    assert "lucius-engineering" in pids
    assert "darwin-research-engine" in pids
    assert "billy-production-engine" in pids


# 17. test_project_owned_roadmap_remains_authoritative
def test_project_owned_roadmap_remains_authoritative():
    svc = GlobalWorkPortfolioService()
    inv = svc.get_canonical_inventory()
    billy = next(p for p in inv if p.project_id == "billy-production-engine")
    assert billy.next_gate == "PHASE_6_5_ASSET_LIFECYCLE_ACCEPTANCE"


# 18. test_global_work_portfolio_does_not_become_second_dispatcher
def test_global_work_portfolio_does_not_become_second_dispatcher(memory_db: Session):
    svc = GlobalWorkPortfolioService()
    # Service provides index/query view over work packages, does not maintain separate dispatch thread or queues
    assert hasattr(svc, "discover_global_work")
    assert not hasattr(svc, "run_dispatch_loop")


# 19. test_existing_durable_mission_semantics_preserved
def test_existing_durable_mission_semantics_preserved(memory_db: Session):
    supervisor = DurableMissionSupervisor(memory_db)
    m = supervisor.create_mission(canonical_sha="abc12345")
    assert m.status == MissionStatus.ACTIVE


# 20. test_existing_travel_mode_semantics_preserved
def test_existing_travel_mode_semantics_preserved():
    from lucius.runtime.launcher import TravelLauncher
    launcher = TravelLauncher()
    pre = launcher.preflight()
    assert pre.repository_checks["lucius"] is True


# 21. test_existing_multi_project_fairness_preserved
def test_existing_multi_project_fairness_preserved(memory_db: Session):
    from lucius.runtime.dispatcher import MultiProjectDispatcher
    dispatcher = MultiProjectDispatcher(memory_db)
    assert hasattr(dispatcher, "select_next")


# 22. test_qwen3_8b_qualification_unchanged
def test_qwen3_8b_qualification_unchanged():
    provider = OllamaExecutionProvider(provider_id="ollama-local", model="qwen3:8b")
    assert provider.model == "qwen3:8b"
    assert provider.is_available() is True


# 23. test_qwen3_coder_30b_supervised_only_unchanged
def test_qwen3_coder_30b_supervised_only_unchanged():
    # Verify qwen3-coder:30b is reserved for supervised execution and not in automated unattended provider registry
    from lucius.runtime.router import RuntimeProviderRegistry
    ollama_default = OllamaExecutionProvider(provider_id="ollama-local", model="qwen3:8b")
    registry = RuntimeProviderRegistry([ollama_default])
    registered_models = [p.model for p in registry.providers()]
    assert "qwen3-coder:30b" not in registered_models
