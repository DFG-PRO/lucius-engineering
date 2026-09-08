from __future__ import annotations

from sqlalchemy import func, select

from lucius.domain.enums import PersistentWorkflowState, ProjectStatus, QueueWorkItemState
from lucius.persistence.orm import AuditEventORM, PersistentWorkflowORM, ProjectORM, RepositoryRegistrationORM
from lucius.pilots.queue import NonBlockingQueueService
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.schemas import RuntimeLoopConfig
from tests.integration.test_native_execution_runtime_loop import CountingExecutionAdapter, _item, _runtime, _workflow


def test_one_project_one_eligible_task_selected_through_runtime(session):
    workflow = _workflow(session, project_id="PROJECT_A", backlog=[_item("A1")])

    result = _runtime(session, CountingExecutionAdapter()).run(RuntimeLoopConfig(workflow_ids=[workflow.id], max_tasks=1))

    assert result.completed_tasks == 1
    assert result.project_ids == ["PROJECT_A"]
    assert _audit_count(session, "MULTI_PROJECT_DISPATCH_CANDIDATE_SELECTED") == 1
    assert _audit_count(session, "MODEL_EXECUTION_ROUTING_DECISION") == 1


def test_two_projects_deterministic_winner_and_same_state_same_winner(session):
    workflow_a = _workflow(session, project_id="PROJECT_A", backlog=[_item("A1", priority="NORMAL", order=2)])
    workflow_b = _workflow(session, project_id="PROJECT_B", backlog=[_item("B1", priority="HIGH", order=1)])
    _runtime(session, CountingExecutionAdapter())._prepare_workflow_plans([workflow_a.id, workflow_b.id])

    first = MultiProjectDispatcher(session).select_next([workflow_a.id, workflow_b.id])
    second = MultiProjectDispatcher(session).select_next([workflow_a.id, workflow_b.id])

    assert first.selected.project_id == "PROJECT_B"
    assert first.selected.item_id == "B1"
    assert second.selected.model_dump() == first.selected.model_dump()


def test_project_priority_and_task_priority_ordering(session):
    high_project = _workflow(session, project_id="PROJECT_HIGH", backlog=[_item("H1", priority="NORMAL", order=1)])
    critical_task = _workflow(session, project_id="PROJECT_LOW", backlog=[_item("C1", priority="CRITICAL", order=99)])
    project = session.get(ProjectORM, "PROJECT_HIGH")
    project.documentation_policy = {"priority": "HIGH"}
    _runtime(session, CountingExecutionAdapter())._prepare_workflow_plans([high_project.id, critical_task.id])

    selected = MultiProjectDispatcher(session).select_next([high_project.id, critical_task.id])

    assert selected.selected.project_id == "PROJECT_LOW"
    assert selected.selected.item_id == "C1"


def test_blocked_project_does_not_stop_other_eligible_project(session):
    blocked = _workflow(
        session,
        project_id="PROJECT_A",
        backlog=[_item("A1", state=QueueWorkItemState.WAITING_EXTERNAL, priority="CRITICAL")],
    )
    ready = _workflow(session, project_id="PROJECT_B", backlog=[_item("B1", priority="NORMAL")])

    result = _runtime(session, CountingExecutionAdapter()).run(RuntimeLoopConfig(workflow_ids=[blocked.id, ready.id], max_tasks=1))

    assert result.completed_tasks == 1
    assert result.project_ids == ["PROJECT_B"]
    selection = _latest_audit(session, "MULTI_PROJECT_DISPATCH_CANDIDATE_SELECTED").event_metadata
    assert selection["selected"]["project_id"] == "PROJECT_B"
    assert any(
        item["candidate"]["project_id"] == "PROJECT_A"
        and "BLOCKER_UNRESOLVED:WAITING_EXTERNAL" in item["reasons"]
        for item in selection["blocked_candidates"]
    )


def test_resumable_work_becomes_eligible_and_preferred_within_same_priority(session):
    resumed = _workflow(session, project_id="PROJECT_A", backlog=[_item("A1", state=QueueWorkItemState.READY_TO_RESUME, order=5)])
    fresh = _workflow(session, project_id="PROJECT_B", backlog=[_item("B1", order=1)])
    resumed_row = session.get(PersistentWorkflowORM, resumed.id)
    resumed_row.task_backlog[0]["current_block_checkpoint_id"] = "LCHECK_000001"
    _runtime(session, CountingExecutionAdapter())._prepare_workflow_plans([resumed.id, fresh.id])

    selection = MultiProjectDispatcher(session).select_next([resumed.id, fresh.id])

    assert selection.selected.project_id == "PROJECT_A"
    assert selection.selected.resume_state == "RESUME"


def test_fairness_defers_repeated_same_project_when_alternative_exists(session):
    workflow_a = _workflow(session, project_id="PROJECT_A", backlog=[_item("A1", priority="NORMAL", order=1)])
    workflow_b = _workflow(session, project_id="PROJECT_B", backlog=[_item("B1", priority="NORMAL", order=2)])
    runtime = _runtime(session, CountingExecutionAdapter())
    runtime.run(RuntimeLoopConfig(workflow_ids=[workflow_a.id], max_tasks=1))

    workflow_a2 = _workflow(session, project_id="PROJECT_A", backlog=[_item("A2", priority="NORMAL", order=1)])
    runtime.run(RuntimeLoopConfig(workflow_ids=[workflow_a2.id], max_tasks=1))
    runtime._prepare_workflow_plans([workflow_b.id])

    selection = MultiProjectDispatcher(session).select_next([workflow_a2.id, workflow_b.id])

    assert selection.selected.project_id == "PROJECT_B"
    assert selection.fairness_applied is True


def test_repository_project_mismatch_fails_closed(session):
    workflow = _workflow(session, project_id="PROJECT_A", backlog=[_item("A1")])
    _runtime(session, CountingExecutionAdapter())._prepare_workflow_plans([workflow.id])
    row = session.get(PersistentWorkflowORM, workflow.id)
    repository_id = row.repository_id
    repository = session.get(RepositoryRegistrationORM, repository_id)

    repository.project_id = "PROJECT_B"
    selection = MultiProjectDispatcher(session).select_next([workflow.id])

    assert selection.selected is None
    assert any("REPOSITORY_PROJECT_MISMATCH" in item.reasons for item in selection.excluded_candidates)


def test_wrong_project_plan_cannot_authorize_dispatch(session):
    workflow_a = _workflow(session, project_id="PROJECT_A", backlog=[_item("A1")])
    workflow_b = _workflow(session, project_id="PROJECT_B", backlog=[_item("B1")])
    runtime = _runtime(session, CountingExecutionAdapter())
    runtime._prepare_workflow_plans([workflow_a.id, workflow_b.id])
    row_a = session.get(PersistentWorkflowORM, workflow_a.id)
    row_b = session.get(PersistentWorkflowORM, workflow_b.id)
    row_b.plan_id = row_a.plan_id
    row_b.plan_freeze_id = row_a.plan_freeze_id

    selection = MultiProjectDispatcher(session).select_next([workflow_b.id])

    assert selection.selected is None
    assert any("PLAN_FREEZE_PROJECT_OR_TASK_MISMATCH" in item.reasons for item in selection.excluded_candidates)


def test_runtime_dispatches_one_active_task_then_reevaluates_other_project(session):
    workflow_a = _workflow(session, project_id="PROJECT_A", backlog=[_item("A1", order=1)])
    workflow_b = _workflow(session, project_id="PROJECT_B", backlog=[_item("B1", order=2)])
    adapter = CountingExecutionAdapter()

    result = _runtime(session, adapter).run(RuntimeLoopConfig(workflow_ids=[workflow_a.id, workflow_b.id], max_tasks=2))

    assert result.completed_tasks == 2
    assert result.project_ids == ["PROJECT_A", "PROJECT_B"]
    assert result.project_switches == 1
    assert adapter.calls == ["A1", "B1"]
    assert _audit_count(session, "MULTI_PROJECT_DISPATCH_CANDIDATE_HANDED_TO_RUNTIME") == 2


def test_dispatcher_never_invokes_provider_and_router_never_selects_global_task(session):
    workflow = _workflow(session, project_id="PROJECT_A", backlog=[_item("A1")])
    adapter = CountingExecutionAdapter()
    _runtime(session, adapter)._prepare_workflow_plans([workflow.id])

    selection = MultiProjectDispatcher(session).select_next([workflow.id])

    assert selection.selected.item_id == "A1"
    assert adapter.calls == []
    assert _audit_count(session, "MODEL_EXECUTION_ROUTING_DECISION") == 0


def test_audit_reconstructs_selection_and_exclusion_reasons(session):
    disabled = _workflow(session, project_id="PROJECT_DISABLED", backlog=[_item("D1", priority="CRITICAL")])
    ready = _workflow(session, project_id="PROJECT_READY", backlog=[_item("R1")])
    session.get(ProjectORM, "PROJECT_DISABLED").status = ProjectStatus.PAUSED.value

    _runtime(session, CountingExecutionAdapter()).run(RuntimeLoopConfig(workflow_ids=[disabled.id, ready.id], max_tasks=1))

    audit = _latest_audit(session, "MULTI_PROJECT_DISPATCH_CANDIDATE_SELECTED").event_metadata
    assert audit["selected"]["project_id"] == "PROJECT_READY"
    assert any("PROJECT_DISABLED:PAUSED" in item["reasons"] for item in audit["excluded_candidates"])


def _audit_count(session, event_type: str) -> int:
    return session.scalar(select(func.count()).select_from(AuditEventORM).where(AuditEventORM.event_type == event_type))


def _latest_audit(session, event_type: str) -> AuditEventORM:
    return session.scalars(
        select(AuditEventORM).where(AuditEventORM.event_type == event_type).order_by(AuditEventORM.timestamp.desc(), AuditEventORM.id.desc())
    ).first()
