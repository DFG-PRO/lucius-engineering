from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from sqlalchemy import func, select

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, DurableWaitClass, MissionStatus, QueueWorkItemState, TaskStatus
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import AuditEventORM, PersistentWorkflowORM, TaskORM
from lucius.portfolio.schemas import NormalizedWorkPackage
from lucius.portfolio.service import GlobalWorkPortfolioService
from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
from lucius.runtime.continuation import BoundedContinuationService, SessionBudget
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.feeder import DarwinBacklogFeeder
from lucius.runtime.mission import DurableMissionSupervisor
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.schemas import RuntimeLoopConfig
from lucius.runtime.service import ExecutionRuntimeLoopService


@pytest.fixture
def memory_db():
    engine = create_sqlite_engine(":memory:")
    create_all(engine)
    factory = make_session_factory(engine)
    session = factory()
    yield session
    session.close()


def _setup_services(session):
    lucius_root = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering")
    darwin_root = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine")

    registry_path = lucius_root / "src" / "lucius" / "projects" / "dfg_canonical_registry.json"
    registry = DFGProjectRegistry.load_json(registry_path) if registry_path.exists() else None

    feeder = DarwinBacklogFeeder(darwin_root=darwin_root, registry=registry)
    portfolio_service = GlobalWorkPortfolioService(registry=registry, feeder=feeder)
    supervisor = DurableMissionSupervisor(session, actor=Actor.LUCIUS)
    audit = AuditService(session)

    scripted_provider = ScriptedExecutionAdapter(provider_id="scripted-provider")
    provider_registry = RuntimeProviderRegistry([scripted_provider])
    router = ModelExecutionRouter(session, registry=provider_registry, actor=Actor.LUCIUS)
    dispatcher = MultiProjectDispatcher(session, actor=Actor.LUCIUS)

    runtime_service = ExecutionRuntimeLoopService(
        session=session,
        planning_adapter=ScriptedRuntimePlanningAdapter(),
        execution_router=router,
        dispatcher=dispatcher,
        actor=Actor.LUCIUS,
    )

    continuation = BoundedContinuationService(
        session=session,
        runtime_service=runtime_service,
        feeder=feeder,
        dispatcher=dispatcher,
        supervisor=supervisor,
        actor=Actor.LUCIUS,
    )

    return {
        "feeder": feeder,
        "portfolio_service": portfolio_service,
        "supervisor": supervisor,
        "audit": audit,
        "dispatcher": dispatcher,
        "runtime_service": runtime_service,
        "continuation": continuation,
    }


def test_shift_10k_a_identical_backlog_refresh(memory_db):
    """A. Repeated feeder passes over identical backlog do not increase canonical task count."""
    svcs = _setup_services(memory_db)
    feeder = svcs["feeder"]

    pass1 = feeder.feed_into_queue(memory_db, max_items=50)
    initial_count = memory_db.scalar(select(func.count()).select_from(TaskORM))
    assert initial_count > 0

    pass2 = feeder.feed_into_queue(memory_db, max_items=50)
    assert len(pass2) == 0
    second_count = memory_db.scalar(select(func.count()).select_from(TaskORM))
    assert second_count == initial_count

    pass3 = feeder.feed_into_queue(memory_db, max_items=50)
    assert len(pass3) == 0
    third_count = memory_db.scalar(select(func.count()).select_from(TaskORM))
    assert third_count == initial_count


def test_shift_10k_b_completed_work_not_rematerialized(memory_db):
    """B. Completed canonical work is not rematerialized."""
    svcs = _setup_services(memory_db)
    feeder = svcs["feeder"]
    runtime = svcs["runtime_service"]

    ingested = feeder.feed_into_queue(memory_db, max_items=1)
    assert len(ingested) == 1
    wf_id = ingested[0]["workflow_id"]
    completed_dedupe_key = ingested[0]["dedupe_key"]

    res = runtime.run(RuntimeLoopConfig(workflow_ids=[wf_id], max_tasks=1))
    assert res.completed_tasks == 1

    pass2 = feeder.feed_into_queue(memory_db, max_items=50)
    assert not any(item.get("dedupe_key") == completed_dedupe_key for item in pass2)


def test_shift_10k_c_active_work_referenced_not_duplicated(memory_db):
    """C. Active canonical work is referenced/resumed rather than duplicated."""
    svcs = _setup_services(memory_db)
    feeder = svcs["feeder"]

    ingested = feeder.feed_into_queue(memory_db, max_items=1)
    assert len(ingested) == 1
    dedupe_key = ingested[0]["dedupe_key"]

    pass2 = feeder.feed_into_queue(memory_db, max_items=50)
    assert not any(item.get("dedupe_key") == dedupe_key for item in pass2)


def test_shift_10k_d_waiting_work_not_recreated(memory_db):
    """D. Waiting work is not recreated."""
    svcs = _setup_services(memory_db)
    feeder = svcs["feeder"]
    supervisor = svcs["supervisor"]

    ingested = feeder.feed_into_queue(memory_db, max_items=1)
    item_id = ingested[0]["item_id"]

    mission = supervisor.create_mission(canonical_sha="HEAD")
    supervisor.record_wait(
        mission_id=mission.mission_id,
        wait_class=DurableWaitClass.RESOURCE_SHORT,
        reason="Resource busy",
        item_id=item_id,
    )

    pass2 = feeder.feed_into_queue(memory_db, max_items=50)
    assert not any(item.get("dedupe_key") == ingested[0]["dedupe_key"] for item in pass2)


def test_shift_10k_e_wait_timing(memory_db):
    """E. Waiting work does not invoke a provider before retry_after / next_eligible_at."""
    svcs = _setup_services(memory_db)
    feeder = svcs["feeder"]
    supervisor = svcs["supervisor"]
    continuation = svcs["continuation"]

    ingested = feeder.feed_into_queue(memory_db, max_items=1)
    item_id = ingested[0]["item_id"]

    mission = supervisor.create_mission(canonical_sha="HEAD")
    retry_time = datetime.now(timezone.utc) + timedelta(hours=2)
    supervisor.record_wait(
        mission_id=mission.mission_id,
        wait_class=DurableWaitClass.SCHEDULE,
        reason="Scheduled for future execution",
        item_id=item_id,
        retry_after=retry_time,
    )

    res = continuation.run_session(
        SessionBudget(max_cycles=5),
        mission_id=mission.mission_id,
        workflow_ids=[ingested[0]["workflow_id"]],
    )
    assert res.tasks_selected == 0
    assert res.status == "WAITING"


def test_shift_10k_f_authority_block(memory_db):
    """F. BLOCKED_AUTHORITY work does not churn or silently unblock."""
    svcs = _setup_services(memory_db)
    feeder = svcs["feeder"]
    supervisor = svcs["supervisor"]
    continuation = svcs["continuation"]

    ingested = feeder.feed_into_queue(memory_db, max_items=1)
    item_id = ingested[0]["item_id"]

    mission = supervisor.create_mission(canonical_sha="HEAD")
    supervisor.record_wait(
        mission_id=mission.mission_id,
        wait_class=DurableWaitClass.AUTHORITY,
        reason="Class C mutation requires human approval",
        item_id=item_id,
    )

    res = continuation.run_session(
        SessionBudget(max_cycles=5),
        mission_id=mission.mission_id,
        workflow_ids=[ingested[0]["workflow_id"]],
    )
    assert res.tasks_selected == 0


def test_shift_10k_g_decision_block(memory_db):
    """G. BLOCKED_DECISION work does not churn."""
    svcs = _setup_services(memory_db)
    feeder = svcs["feeder"]
    supervisor = svcs["supervisor"]
    continuation = svcs["continuation"]

    ingested = feeder.feed_into_queue(memory_db, max_items=1)
    item_id = ingested[0]["item_id"]

    mission = supervisor.create_mission(canonical_sha="HEAD")
    supervisor.record_wait(
        mission_id=mission.mission_id,
        wait_class=DurableWaitClass.DECISION,
        reason="Architecture decision required",
        item_id=item_id,
    )

    res = continuation.run_session(
        SessionBudget(max_cycles=5),
        mission_id=mission.mission_id,
        workflow_ids=[ingested[0]["workflow_id"]],
    )
    assert res.tasks_selected == 0


def test_shift_10k_j_no_eligible_work_bounded_sleep(memory_db):
    """J. No eligible work results in bounded sleep/wait behavior and no unnecessary model calls."""
    svcs = _setup_services(memory_db)
    supervisor = svcs["supervisor"]
    feeder = DarwinBacklogFeeder(custom_items=[])
    continuation = BoundedContinuationService(
        session=memory_db,
        runtime_service=svcs["runtime_service"],
        feeder=feeder,
        supervisor=supervisor,
    )

    mission = supervisor.create_mission(canonical_sha="HEAD")
    res = continuation.run_session(SessionBudget(max_cycles=5), mission_id=mission.mission_id)

    assert res.status in ("IDLE", "WAITING")
    assert res.tasks_selected == 0
    assert res.provider_calls == 0


def test_shift_10k_k_restart_preserves_canonical_identity(memory_db):
    """K. Restart preserves canonical identity and does not duplicate completed/waiting work."""
    svcs = _setup_services(memory_db)
    feeder = svcs["feeder"]
    supervisor = svcs["supervisor"]
    runtime = svcs["runtime_service"]

    ingested = feeder.feed_into_queue(memory_db, max_items=50)
    mission1 = supervisor.create_mission(canonical_sha="HEAD", mission_id="MISSION_10K_K")

    runtime.run(RuntimeLoopConfig(workflow_ids=[ingested[0]["workflow_id"]], max_tasks=1))

    # Simulate restart under same mission_id
    mission2 = supervisor.recover_mission("MISSION_10K_K", current_canonical_sha="HEAD")
    assert mission2.mission_id == "MISSION_10K_K"

    re_fed = feeder.feed_into_queue(memory_db, max_items=50)
    assert len(re_fed) == 0


def test_shift_10k_l_attempt_identity(memory_db):
    """L. Multiple process attempts under one mission remain distinguishable."""
    svcs = _setup_services(memory_db)
    supervisor = svcs["supervisor"]

    mission1 = supervisor.create_mission(canonical_sha="HEAD", mission_id="MISSION_10K_L", attempt_id="ATT_1")
    assert (mission1.metadata or {}).get("current_attempt_id") == "ATT_1"

    mission2 = supervisor.recover_mission("MISSION_10K_L", current_canonical_sha="HEAD", attempt_id="ATT_2")
    assert (mission2.metadata or {}).get("current_attempt_id") == "ATT_2"

    attempts = (mission2.metadata or {}).get("attempts", [])
    assert len(attempts) == 2
    assert attempts[0]["attempt_id"] == "ATT_1"
    assert attempts[1]["attempt_id"] == "ATT_2"


def test_shift_10k_m_interruption(memory_db):
    """M. SIGINT/SIGTERM-style controlled shutdown persists explicit interruption state and checkpoint."""
    svcs = _setup_services(memory_db)
    supervisor = svcs["supervisor"]

    mission = supervisor.create_mission(canonical_sha="HEAD", mission_id="MISSION_10K_M")
    checkpoint = {
        "uptime_seconds": 120.0,
        "completed_count": 2,
        "active_projects": ["darwin-research-engine"],
    }
    rec = supervisor.record_interruption(
        mission_id="MISSION_10K_M",
        reason="SIGINT_TEST",
        checkpoint_data=checkpoint,
    )
    assert rec.status == MissionStatus.PAUSED
    meta = rec.metadata.get("last_interruption")
    assert meta["reason"] == "SIGINT_TEST"
    assert meta["checkpoint"]["completed_count"] == 2


def test_shift_10k_n_audit_payload_size(memory_db):
    """N. Normal audit payload size remains bounded (< 5 KB per event)."""
    svcs = _setup_services(memory_db)
    feeder = svcs["feeder"]
    runtime = svcs["runtime_service"]
    audit = svcs["audit"]

    ingested = feeder.feed_into_queue(memory_db, max_items=2)
    runtime.run(RuntimeLoopConfig(workflow_ids=[item["workflow_id"] for item in ingested], max_tasks=2))

    stats = audit.get_audit_observability()
    assert stats["event_count"] > 0
    assert stats["average_metadata_payload_size"] < 5000.0
    assert len(stats["oversized_events"]) == 0


def test_shift_10k_o_audit_growth(memory_db):
    """O. Hundreds of scheduler cycles do not produce superlinear DB growth due to cumulative payload embedding."""
    svcs = _setup_services(memory_db)
    feeder = svcs["feeder"]
    dispatcher = svcs["dispatcher"]
    audit = svcs["audit"]

    ingested = feeder.feed_into_queue(memory_db, max_items=2)
    wf_ids = [item["workflow_id"] for item in ingested]

    for _ in range(50):
        dispatcher.select_next(wf_ids)
        memory_db.commit()

    stats = audit.get_audit_observability(scheduler_cycles=50)
    assert stats["audit_bytes_per_cycle"] < 5000.0  # < 5 KB per cycle
    assert len(stats["oversized_events"]) == 0


def test_shift_10k_p_useful_completion_accounting(memory_db):
    """P. Useful completions count canonical unique work rather than raw iterations/task clones."""
    svcs = _setup_services(memory_db)
    feeder = svcs["feeder"]
    runtime = svcs["runtime_service"]

    ingested = feeder.feed_into_queue(memory_db, max_items=1)
    wf_id = ingested[0]["workflow_id"]

    res = runtime.run(RuntimeLoopConfig(workflow_ids=[wf_id], max_tasks=1))
    assert res.completed_tasks == 1

    # Re-run loop over completed workflow
    res2 = runtime.run(RuntimeLoopConfig(workflow_ids=[wf_id], max_tasks=1))

    # Count unique completed dedupe keys
    workflows = memory_db.query(PersistentWorkflowORM).all()
    completed_keys = set()
    for wf in workflows:
        for item in wf.task_backlog or []:
            if isinstance(item, dict) and item.get("state") == QueueWorkItemState.COMPLETED.value:
                completed_keys.add(item.get("dedupe_key") or item.get("item_id"))

    assert len(completed_keys) == 1


def test_shift_10k_q_cross_project_non_blocking(memory_db):
    """Q. A blocked/waiting project does not prevent another eligible project from progressing."""
    svcs = _setup_services(memory_db)
    feeder = svcs["feeder"]
    supervisor = svcs["supervisor"]
    runtime = svcs["runtime_service"]

    ingested = feeder.feed_into_queue(memory_db, max_items=2)
    if len(ingested) < 2:
        pytest.skip("Not enough backlog items for cross-project test")

    wf1 = ingested[0]["workflow_id"]
    wf2 = ingested[1]["workflow_id"]

    # Block wf1
    mission = supervisor.create_mission(canonical_sha="HEAD")
    supervisor.record_wait(
        mission_id=mission.mission_id,
        wait_class=DurableWaitClass.AUTHORITY,
        reason="Authority blocked",
        item_id=ingested[0]["item_id"],
    )

    res = runtime.run(RuntimeLoopConfig(workflow_ids=[wf1, wf2], max_tasks=1))
    assert res.completed_tasks == 1
    assert res.task_records[0].workflow_id == wf2


def test_shift_10k_controlled_accelerated_endurance_validation(memory_db):
    """>500 scheduler cycles controlled accelerated endurance validation."""
    svcs = _setup_services(memory_db)
    feeder = svcs["feeder"]
    dispatcher = svcs["dispatcher"]
    supervisor = svcs["supervisor"]
    audit = svcs["audit"]

    mission = supervisor.create_mission(canonical_sha="HEAD", mission_id="MISSION_ENDURANCE_500")

    # Ingest initial backlog
    feeder.feed_into_queue(memory_db, max_items=50)
    initial_tasks = memory_db.scalar(select(func.count()).select_from(TaskORM))

    cycles_completed = 0
    for cycle in range(505):
        # Periodically trigger feeder refresh every 50 cycles
        if cycle % 50 == 0:
            feeder.feed_into_queue(memory_db, max_items=50)

        selection = dispatcher.select_next()
        memory_db.commit()
        cycles_completed += 1

    final_tasks = memory_db.scalar(select(func.count()).select_from(TaskORM))
    assert final_tasks == initial_tasks, f"Task count grew from {initial_tasks} to {final_tasks} over 500 cycles!"

    stats = audit.get_audit_observability(scheduler_cycles=cycles_completed)
    assert stats["audit_bytes_per_cycle"] < 5000.0
    assert len(stats["oversized_events"]) == 0


def test_shift_10k_r_audit_reconstructibility(memory_db):
    """R. Verifies that compact dispatch audit payloads preserve full decision reconstructibility."""
    svcs = _setup_services(memory_db)
    feeder = svcs["feeder"]
    dispatcher = svcs["dispatcher"]

    ingested = feeder.feed_into_queue(memory_db, max_items=5)
    assert len(ingested) > 0

    selection = dispatcher.select_next()
    memory_db.commit()

    assert selection.selected is not None

    # Retrieve Audit Event from DB
    audit_row = memory_db.scalars(
        select(AuditEventORM)
        .where(AuditEventORM.event_type == "MULTI_PROJECT_DISPATCH_CANDIDATE_SELECTED")
        .order_by(AuditEventORM.timestamp.desc())
    ).first()

    assert audit_row is not None
    meta = audit_row.event_metadata
    assert meta is not None

    # Verify 1. Selected candidate identity & lineage
    selected_meta = meta.get("selected")
    assert selected_meta is not None
    assert selected_meta["project_id"] == selection.selected.project_id
    assert selected_meta["workflow_id"] == selection.selected.workflow_id
    assert selected_meta["item_id"] == selection.selected.item_id
    assert selected_meta["logical_task_id"] == selection.selected.logical_task_id
    assert selected_meta["task_id"] == selection.selected.task_id
    assert selected_meta["repository_id"] == selection.selected.repository_id

    # Verify 2. Scheduling decision & policy reason
    assert meta["reason"] == selection.reason
    assert meta["cycle_id"] == selection.cycle_id
    assert "fairness_applied" in meta

    # Verify 3. State & Authority
    assert selected_meta["state"] == selection.selected.state.value
    assert selected_meta["priority"] == selection.selected.priority
    assert selected_meta["blocker_state"] == selection.selected.blocker_state

    # Verify 4. Counts & compact candidate arrays
    assert meta["eligible_count"] >= 1
    assert "eligible_candidates" in meta
    assert len(meta["eligible_candidates"]) <= 3
