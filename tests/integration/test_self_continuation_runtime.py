from __future__ import annotations

from pathlib import Path
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.domain.enums import (
    Actor,
    AllowedAction,
    AuthorityLevel,
    Environment,
    PersistentWorkflowState,
    ProjectStatus,
    ProjectType,
    QueueWorkItemState,
    RepositoryAccessMode,
    RepositoryAdapterType,
    TaskComplexity,
    TaskPriority,
    TaskStatus,
)
from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import (
    PersistentWorkflowORM,
    ProjectORM,
    ProjectRepositoryAttachmentORM,
    RepositoryRegistrationORM,
    utc_now,
)
from lucius.pilots.workflows import PersistentWorkflowService
from lucius.projects.service import ProjectRegistryService
from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
from lucius.runtime.continuation import (
    BoundedContinuationService,
    ContinuationStopReason,
    SessionBudget,
)
from lucius.runtime.dispatcher import MultiProjectDispatcher
from lucius.runtime.feeder import DarwinBacklogFeeder
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.schemas import ExecutionAdapterResult
from lucius.runtime.service import ExecutionRuntimeLoopService
from lucius.tasks.service import TaskService


@pytest.fixture
def session(tmp_path: Path):
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'test-continuation.sqlite'}")
    create_all(engine)
    factory = make_session_factory(engine)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()


def _ensure_project(session: Session, project_id: str) -> ProjectORM:
    project = session.get(ProjectORM, project_id)
    if project is not None:
        return project
    project = ProjectORM(
        id=project_id,
        name=f"Project {project_id}",
        slug=project_id.lower(),
        project_type=ProjectType.DFG_INTERNAL.value,
        status=ProjectStatus.ACTIVE.value,
        documentation_policy={},
        default_authority_level=AuthorityLevel.L1.value,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(project)
    session.flush()
    return project


def _ensure_repository(session: Session, project_id: str, repository_id: str) -> RepositoryRegistrationORM:
    repo = session.get(RepositoryRegistrationORM, repository_id)
    if repo is not None:
        return repo
    repo = RepositoryRegistrationORM(
        id=repository_id,
        project_id=project_id,
        name=f"Repository {repository_id}",
        adapter_type=RepositoryAdapterType.LOCAL_GIT.value,
        location=f"/private/tmp/{repository_id.lower()}",
        default_branch="main",
        access_mode=RepositoryAccessMode.READ_ONLY.value,
        status="ACTIVE",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(repo)
    session.flush()
    return repo


def _ensure_attachment(session: Session, project_id: str, repository_id: str) -> None:
    existing = session.scalar(
        select(ProjectRepositoryAttachmentORM).where(
            ProjectRepositoryAttachmentORM.project_id == project_id,
            ProjectRepositoryAttachmentORM.repository_id == repository_id,
        )
    )
    if existing is not None:
        return
    ProjectRegistryService(session).attach_repository(
        project_id=project_id,
        repository_id=repository_id,
        actor=Actor.LUCIUS,
    )


def _item(
    item_id: str,
    *,
    state: QueueWorkItemState = QueueWorkItemState.READY,
    priority: str = "NORMAL",
    order: int = 1,
    dependencies: list[str] | None = None,
) -> dict:
    return {
        "item_id": item_id,
        "logical_task_id": item_id,
        "title": f"Runtime task {item_id}",
        "state": state.value,
        "priority": priority,
        "created_order": order,
        "dependencies": dependencies or [],
        "completed_substeps": [],
        "version": 0,
        "allowed_mutation_paths": ["src/lucius/runtime/adapters.py"],
    }


def _workflow(session: Session, *, project_id: str, backlog: list[dict]) -> PersistentWorkflowORM:
    project = _ensure_project(session, project_id)
    repository = _ensure_repository(session, project.id, f"repo-{project.id.lower()}")
    _ensure_attachment(session, project.id, repository.id)

    task = TaskService(session).create_task(
        project_id=project.id,
        title=f"Workflow Task {project.id}",
        objective=f"Objective for {project.id}",
        priority=TaskPriority.NORMAL,
        complexity=TaskComplexity.T1,
        authority_level=AuthorityLevel.L1,
        created_by=Actor.LUCIUS,
    )
    TaskService(session).create_or_update_contract(
        task_id=task.id,
        objective=task.objective,
        acceptance_criteria=["Must complete execution with verified tests."],
        constraints=["ONE_LOGICAL_DISPATCHER", "NO_PUSH", "NO_MERGE"],
        repository_ids=[repository.id],
        allowed_actions=[
            AllowedAction.READ_REPOSITORY,
            AllowedAction.RUN_TESTS,
            AllowedAction.WRITE_SOURCE,
            AllowedAction.WRITE_TESTS,
        ],
        environment=Environment.DEVELOPMENT,
        authority_level=AuthorityLevel.L1,
        documentation_required=False,
        actor=Actor.LUCIUS,
    )
    TaskService(session).mark_ready(task.id, actor=Actor.LUCIUS)
    wf = PersistentWorkflowService(session).create(
        objective=f"Execute {project.id} tasks.",
        expected_main_head="HEAD",
        isolated_branch=f"lucius/{project.id.lower()}-test",
        worktree_path=f"/private/tmp/{project.id.lower()}-worktree",
        authority_tier="ISOLATED_DEVELOPMENT_ONLY",
        project_id=project.id,
        repository_id=repository.id,
        task_id=task.id,
        task_backlog=backlog,
        pending_task_ids=[str(item.get("item_id")) for item in backlog],
    )
    ScriptedRuntimePlanningAdapter().prepare_workflow_plan(session, wf.id)
    session.flush()
    return wf


def _runtime_service(session: Session, provider_map: dict[str, ExecutionAdapterResult]) -> ExecutionRuntimeLoopService:
    provider = ScriptedExecutionAdapter(
        provider_map,
        capabilities=["code_modification", "inspection_reasoning", "reasoning"],
    )
    provider.registration.supported_task_classes = ["engineering", "inspection_reasoning", "inspection", "research"]
    planning = ScriptedRuntimePlanningAdapter()
    router = ModelExecutionRouter(
        session,
        registry=RuntimeProviderRegistry([provider]),
        actor=Actor.LUCIUS,
    )
    dispatcher = MultiProjectDispatcher(session, actor=Actor.LUCIUS)
    return ExecutionRuntimeLoopService(
        session,
        planning_adapter=planning,
        execution_router=router,
        dispatcher=dispatcher,
    )


def test_multi_project_self_continuation_with_feeder_and_blocker(session: Session):
    """Phase 13 Integration Test:
    Executes tasks across Lucius, Darwin, Billy; encounters a blocked task;
    continues independent work; empties queue; feeds from Darwin backlog;
    enters safe IDLE. Zero operator corrections.
    """
    wf_lucius = _workflow(session, project_id="LUCIUS", backlog=[_item("L-TASK-A", order=1)])
    wf_darwin = _workflow(session, project_id="DARWIN", backlog=[_item("D-TASK-B", order=1)])
    wf_billy = _workflow(session, project_id="BILLY", backlog=[_item("P-TASK-A", order=1)])

    # Mixed workflow with an intentionally blocked Class C task, followed by independent task
    blocked_item = _item("BLOCKED-CLASS-C", order=1)
    independent_item = _item("INDEPENDENT-AFTER-BLOCK", order=2)
    wf_mixed = _workflow(
        session,
        project_id="MIXED",
        backlog=[blocked_item, independent_item],
    )

    provider_map = {
        "L-TASK-A": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["verified Lucius state"],
            evidence=[{"item": "state"}],
            verification=[{"command": "pytest tests/unit", "result": "PASS"}],
        ),
        "D-TASK-B": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["conducted research"],
            evidence=[{"item": "research"}],
            verification=[{"command": "pytest tests/darwin", "result": "PASS"}],
        ),
        "P-TASK-A": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["inspected production blueprint"],
            evidence=[{"item": "blueprint"}],
            verification=[{"command": "pytest tests/billy", "result": "PASS"}],
        ),
        "BLOCKED-CLASS-C": ExecutionAdapterResult(
            outcome="BLOCKED",
            blocking_reason="AUTHORITY_REQUIRED:CLASS_C_MUTATION",
            error="Class C mutation blocked unattended",
        ),
        "INDEPENDENT-AFTER-BLOCK": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["independent task passed"],
            evidence=[{"item": "pass"}],
            verification=[{"command": "pytest tests/unit", "result": "PASS"}],
        ),
        "FEED-RBACK-AUTO-01": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["executed auto-fed research target"],
            evidence=[{"item": "auto_fed_result"}],
            verification=[{"command": "pytest tests/feeder", "result": "PASS"}],
        ),
    }

    raw_backlog_item = {
        "item_id": "RBACK-AUTO-01",
        "title": "Autonomous Ingested Research",
        "status": "READY",
        "priority": "P0_NOW",
        "objective": "Prove dynamic backlog replenishment.",
        "provenance_refs": ["docs/portfolio.md"],
        "dependencies": [],
        "blocked_by": [],
    }
    feeder = DarwinBacklogFeeder(custom_items=[raw_backlog_item])
    runtime = _runtime_service(session, provider_map)
    continuation = BoundedContinuationService(session, runtime_service=runtime, feeder=feeder)

    budget = SessionBudget(max_cycles=10, max_wall_seconds=60.0)
    workflow_ids = [wf_lucius.id, wf_darwin.id, wf_billy.id, wf_mixed.id]

    result = continuation.run_session(budget, workflow_ids=workflow_ids, stop_on_block=False)

    # Core Acceptance Verifications
    assert result.status == "IDLE"
    assert result.stop_reason == ContinuationStopReason.IDLE_NO_ELIGIBLE_WORK.value
    assert result.tasks_selected == 6  # L-A, D-B, P-A, BLOCKED-C, INDEP-D, FEED-AUTO
    assert result.tasks_completed == 5
    assert result.tasks_blocked == 1
    assert result.feeder_invocations >= 1
    assert result.tasks_fed == 1
    assert result.project_switches >= 3
    assert result.operator_corrections == 0
    assert result.post_launch_external_instructions == 0


def test_restart_resume_preserves_state_and_avoids_duplicates(session: Session):
    """Phase 14 Test:
    Controlled interruption after budget limit; restart verifies completed tasks
    are not repeated, queued work resumes, and no duplicate tasks are fed.
    """
    wf = _workflow(
        session,
        project_id="RESUME-TEST",
        backlog=[_item("TASK-1", order=1), _item("TASK-2", order=2), _item("TASK-3", order=3)],
    )

    provider_map = {
        "TASK-1": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["sub1"],
            evidence=[{"e": 1}],
            verification=[{"cmd": "pytest", "result": "PASS"}],
        ),
        "TASK-2": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["sub2"],
            evidence=[{"e": 2}],
            verification=[{"cmd": "pytest", "result": "PASS"}],
        ),
        "TASK-3": ExecutionAdapterResult(
            outcome="COMPLETED",
            completed_substeps=["sub3"],
            evidence=[{"e": 3}],
            verification=[{"cmd": "pytest", "result": "PASS"}],
        ),
    }

    runtime = _runtime_service(session, provider_map)
    continuation = BoundedContinuationService(session, runtime_service=runtime)

    # Run 1: Budget limits to exactly 2 cycles (interruption simulation)
    budget_interrupted = SessionBudget(max_cycles=2, max_wall_seconds=60.0)
    res_1 = continuation.run_session(budget_interrupted, workflow_ids=[wf.id])

    assert res_1.status == "BUDGET_EXHAUSTED"
    assert res_1.stop_reason == ContinuationStopReason.SESSION_CYCLE_BUDGET_REACHED.value
    assert res_1.tasks_completed == 2

    # Run 2: Resume session with generous budget
    budget_resumed = SessionBudget(max_cycles=10, max_wall_seconds=60.0)
    res_2 = continuation.run_session(budget_resumed, workflow_ids=[wf.id])

    assert res_2.status == "IDLE"
    assert res_2.stop_reason == ContinuationStopReason.IDLE_NO_ELIGIBLE_WORK.value
    # Crucial: TASK-1 and TASK-2 were not repeated; only remaining TASK-3 was selected
    assert res_2.tasks_selected == 1
    assert res_2.tasks_completed == 1


def test_session_budget_stops_safely_on_cycles_wall_and_failures(session: Session):
    """Phase 10 Test:
    Verifies hard session budget boundaries enforce safe stopping.
    """
    wf = _workflow(session, project_id="BUDGET", backlog=[_item("FAIL-1", order=1), _item("FAIL-2", order=2)])
    provider_map = {
        "FAIL-1": ExecutionAdapterResult(outcome="FAILED", error="fail 1"),
        "FAIL-2": ExecutionAdapterResult(outcome="FAILED", error="fail 2"),
    }
    runtime = _runtime_service(session, provider_map)
    continuation = BoundedContinuationService(session, runtime_service=runtime)

    # Max consecutive failures = 1
    budget = SessionBudget(max_cycles=10, max_consecutive_failures=1)
    result = continuation.run_session(budget, workflow_ids=[wf.id])

    assert result.status == "FAILED"
    assert result.stop_reason == ContinuationStopReason.FAILURE_BUDGET_REACHED.value
    assert result.consecutive_failures == 1
