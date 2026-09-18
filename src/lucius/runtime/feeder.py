from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
import sys
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.domain.enums import (
    Actor,
    AllowedAction,
    AuthorityLevel,
    Environment,
    TaskComplexity,
    TaskPriority,
    TaskStatus,
)
from lucius.persistence.orm import (
    PersistentWorkflowORM,
    ProjectORM,
    ProjectRepositoryAttachmentORM,
    RepositoryRegistrationORM,
    TaskContractORM,
    TaskORM,
)
from lucius.pilots.workflows import PersistentWorkflowService
from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.projects.regression_guard import RegressionGuardValidator
from lucius.runtime.adapters import ScriptedRuntimePlanningAdapter
from lucius.tasks.service import TaskService

logger = logging.getLogger(__name__)

DEFAULT_DARWIN_ROOT = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Darwin Research Engine/darwin-research-engine")

PRIORITY_MAP = {
    "P0_NOW": TaskPriority.HIGH,
    "P0": TaskPriority.HIGH,
    "P1": TaskPriority.NORMAL,
    "P2": TaskPriority.LOW,
}

PRIORITY_WEIGHTS = {
    "P0_NOW": 0,
    "P0": 1,
    "P1": 2,
    "P2": 3,
}


class NormalizedTaskEnvelope(BaseModel):
    dedupe_key: str = Field(..., description="Unique idempotency key, e.g. darwin:RBACK-MON-001")
    source_system: str = Field(default="darwin")
    source_item_id: str
    project_id: str
    project_root: str
    title: str
    objective: str
    task_type: str = Field(default="RESEARCH_BACKLOG_ITEM")
    authority_class: str = Field(default="CLASS_B")
    provider_requirement: str = Field(default="qwen3:8b")
    priority: str = Field(default="NORMAL")
    dependencies: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    verification_command: str | None = None
    documentation_requirement: bool = False
    provenance_refs: list[str] = Field(default_factory=list)
    is_eligible_for_unattended: bool = True
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class DynamicTaskFeederError(Exception):
    pass


class DarwinBacklogFeeder:
    """Deterministic dynamic task feeder for Darwin Master Research Backlog."""

    def __init__(
        self,
        darwin_root: Path | str = DEFAULT_DARWIN_ROOT,
        *,
        custom_items: list[Any] | None = None,
        registry: DFGProjectRegistry | None = None,
        regression_guard: RegressionGuardValidator | None = None,
        max_batch_size: int | None = None,
        max_total_tasks: int | None = None,
    ):
        self.darwin_root = Path(darwin_root)
        self._custom_items = custom_items
        self.registry = registry
        self.regression_guard = regression_guard
        self.max_batch_size = max_batch_size
        self.max_total_tasks = max_total_tasks
        self._total_fed = 0

    def load_raw_backlog_items(self) -> list[Any]:
        """Loads canonical backlog items from Darwin master_backlog without importing pydantic_settings."""
        if self._custom_items is not None:
            return list(self._custom_items)

        master_path = self.darwin_root / "src" / "darwin" / "backlog" / "master_backlog.py"
        schemas_path = self.darwin_root / "src" / "darwin" / "backlog" / "schemas.py"
        if not master_path.exists() or not schemas_path.exists():
            logger.warning("Darwin backlog file not found at %s", master_path)
            return []

        try:
            spec_schemas = importlib.util.spec_from_file_location("darwin.backlog.schemas", schemas_path)
            if spec_schemas is None or spec_schemas.loader is None:
                return []
            mod_schemas = importlib.util.module_from_spec(spec_schemas)
            sys.modules["darwin.backlog.schemas"] = mod_schemas
            spec_schemas.loader.exec_module(mod_schemas)
            if hasattr(mod_schemas, "ResearchBacklogItem"):
                mod_schemas.ResearchBacklogItem.model_rebuild()

            spec_master = importlib.util.spec_from_file_location("darwin.backlog.master_backlog", master_path)
            if spec_master is None or spec_master.loader is None:
                return []
            mod_master = importlib.util.module_from_spec(spec_master)
            sys.modules["darwin.backlog.master_backlog"] = mod_master
            spec_master.loader.exec_module(mod_master)

            return list(getattr(mod_master, "CANONICAL_MASTER_BACKLOG_ITEMS", []))
        except Exception as exc:
            logger.error("Failed to load Darwin master backlog: %s", exc)
            return []

    def discover_eligible_tasks(
        self,
        *,
        project_id: str = "darwin-research-engine",
        allowed_authority_classes: tuple[str, ...] = ("CLASS_A", "CLASS_B"),
    ) -> list[NormalizedTaskEnvelope]:
        """Discovers and normalizes backlog items meeting authority and readiness criteria."""
        raw_items = self.load_raw_backlog_items()
        envelopes: list[NormalizedTaskEnvelope] = []

        for item in raw_items:
            envelope = self._normalize_item(item, project_id=project_id)
            if envelope is None:
                continue

            # Fail-closed authority check
            if envelope.authority_class not in allowed_authority_classes:
                envelope.is_eligible_for_unattended = False
                continue

            # Regression Guard Check if configured
            if self.regression_guard is not None:
                guard_res = self.regression_guard.validate_proposed_task(
                    project_id=envelope.project_id,
                    proposed_phase=envelope.raw_payload.get("phase"),
                    proposed_gate=envelope.raw_payload.get("gate"),
                    assumed_sha=envelope.raw_payload.get("assumed_sha"),
                    is_engineering_mutation=(envelope.authority_class == "CLASS_C"),
                )
                if not guard_res.passed:
                    logger.warning(
                        "Backlog item %s rejected by Regression Guard: %s",
                        envelope.source_item_id,
                        [v.message for v in guard_res.violations],
                    )
                    continue

            envelopes.append(envelope)

        # Deterministic sort: priority weight first, then item_id
        envelopes.sort(
            key=lambda env: (
                PRIORITY_WEIGHTS.get(env.raw_payload.get("priority", "P1"), 99),
                env.source_item_id,
            )
        )
        return envelopes

    def _normalize_item(self, item: Any, project_id: str) -> NormalizedTaskEnvelope | None:
        try:
            # Handle both Pydantic models and raw dicts
            data = item.model_dump() if hasattr(item, "model_dump") else (dict(item) if isinstance(item, dict) else {})
            item_id = str(data.get("item_id", "")).strip()
            if not item_id:
                return None

            status = str(data.get("status", "")).upper()
            if status != "READY":
                return None

            blocked_by = data.get("blocked_by", [])
            if blocked_by:
                return None

            title = str(data.get("title", "")).strip()
            objective = str(data.get("objective", "")).strip()
            if not title or not objective:
                return None

            raw_priority = str(data.get("priority", "P1"))
            norm_priority = PRIORITY_MAP.get(raw_priority, TaskPriority.NORMAL).value
            provenance_refs = [str(ref) for ref in data.get("provenance_refs", [])]
            dependencies = [str(dep) for dep in data.get("dependencies", [])]

            # Respect authority_class and task_type if specified in payload, default to Class B research
            authority_class = str(data.get("authority_class", "CLASS_B")).upper()
            task_type = str(data.get("task_type", "ENGINEERING" if authority_class == "CLASS_C" else "RESEARCH_BACKLOG_ITEM"))
            is_unattended = authority_class in ("CLASS_A", "CLASS_B")

            dedupe_key = f"darwin:{item_id}"

            return NormalizedTaskEnvelope(
                dedupe_key=dedupe_key,
                source_system="darwin",
                source_item_id=item_id,
                project_id=project_id,
                project_root=str(self.darwin_root),
                title=f"[Darwin {item_id}] {title}",
                objective=objective,
                task_type=task_type,
                authority_class=authority_class,
                provider_requirement="qwen3:8b",
                priority=norm_priority,
                dependencies=dependencies,
                provenance_refs=provenance_refs,
                is_eligible_for_unattended=is_unattended,
                raw_payload=data,
            )
        except Exception as exc:
            logger.warning("Malformed Darwin backlog item skipped: %s (%s)", item, exc)
            return None

    def feed_into_queue(
        self,
        session: Session,
        *,
        project_id: str = "darwin-research-engine",
        max_items: int = 5,
        actor: Actor = Actor.LUCIUS,
    ) -> list[dict[str, Any]]:
        """Atomically ingests eligible, non-duplicate tasks into Lucius persistent queue."""
        if self.max_total_tasks is not None and self._total_fed >= self.max_total_tasks:
            return []

        eligible = self.discover_eligible_tasks(project_id=project_id)
        if not eligible:
            return []

        # Find already-existing tasks with matching dedupe_keys in session
        existing_keys = self._get_existing_dedupe_keys(session)

        candidates = [env for env in eligible if env.dedupe_key not in existing_keys and env.is_eligible_for_unattended]
        effective_max = max_items
        if self.max_batch_size is not None:
            effective_max = min(effective_max, self.max_batch_size)
        if self.max_total_tasks is not None:
            effective_max = min(effective_max, self.max_total_tasks - self._total_fed)

        selected = candidates[:effective_max]

        if not selected:
            return []

        # Ensure project and repository records exist in Lucius
        project = self._ensure_project(session, project_id)
        registration = self._ensure_repository_attachment(session, project)

        task_service = TaskService(session)
        workflow_service = PersistentWorkflowService(session)
        planning_adapter = ScriptedRuntimePlanningAdapter()

        ingested: list[dict[str, Any]] = []

        for env in selected:
            task = task_service.create_task(
                project_id=project.id,
                title=env.title,
                objective=env.objective,
                priority=TaskPriority(env.priority),
                complexity=TaskComplexity.T1,
                authority_level=AuthorityLevel.L0,
                created_by=actor,
            )

            # Record dedupe_key on task for idempotency
            task_service.create_or_update_contract(
                task_id=task.id,
                objective=env.objective,
                acceptance_criteria=[
                    {
                        "id": "AC-001",
                        "statement": "Evidence gathered and verified from canonical sources",
                        "status": "PENDING",
                    }
                ],
                constraints=[
                    "READ_ONLY_REPOSITORY",
                    "NO_SOURCE_MUTATION",
                    "NO_COMMIT_MERGE_PUSH_DEPLOY",
                    "NO_TRADING_MESSAGING_OR_CREDENTIAL_ACTIONS",
                ],
                repository_ids=[registration.id],
                allowed_actions=[AllowedAction.READ_REPOSITORY, AllowedAction.READ_DOCUMENTATION],
                allowed_tools=[],
                environment=Environment.SANDBOX,
                authority_level=AuthorityLevel.L0,
                documentation_required=False,
                stop_conditions=["Evidence is absent, ambiguous, or cannot be grounded."],
                actor=actor,
            )

            ready = task_service.mark_ready(task.id, actor=actor)
            if not ready.valid:
                continue

            item_id = f"FEED-{env.source_item_id}"
            concise_context_paths = [
                p for p in (env.provenance_refs or [])
                if (self.darwin_root / p).is_file() and (self.darwin_root / p).stat().st_size <= 10_000
            ]
            if concise_context_paths:
                valid_context_paths = concise_context_paths
            else:
                fallback = "docs/runtime/narrative-research-synthesis.md"
                valid_context_paths = [fallback] if (self.darwin_root / fallback).is_file() else ["docs/"]

            queue_item = {
                "item_id": item_id,
                "logical_task_id": item_id,
                "task_id": task.id,
                "project_id": project.id,
                "title": env.title,
                "state": "READY",
                "status": "READY",
                "priority": env.priority,
                "created_order": 1,
                "read_only": True,
                "mutation_allowed": False,
                "task_type": "inspection_reasoning",
                "substeps": [
                    {
                        "substep_id": f"{item_id}-01",
                        "title": f"Execute research synthesis for {env.source_item_id}",
                        "status": "PENDING",
                        "allowed_actions": ["READ_REPOSITORY", "READ_DOCUMENTATION"],
                    }
                ],
                "allowed_actions": ["READ_REPOSITORY", "READ_DOCUMENTATION"],
                "allowed_paths": env.provenance_refs or ["docs/"],
                "context_limits": {
                    "read_only_context_paths": valid_context_paths,
                    "deterministic_verification": True,
                    "evidence_reference_validation_required": True,
                },
                "dedupe_key": env.dedupe_key,
            }

            workflow = workflow_service.create(
                objective=env.objective,
                expected_main_head="65614f7207bd4e92e270636173bd30de4a1e890b",
                isolated_branch=f"lucius/feed/{env.source_item_id.lower()}",
                worktree_path=str(self.darwin_root),
                authority_tier=AuthorityLevel.L0.value,
                project_id=project.id,
                repository_id=registration.id,
                repository_snapshot_id="FEED-SNAPSHOT",
                task_id=task.id,
                task_backlog=[queue_item],
                dependency_graph={item_id: []},
                decisions=[{"type": "DEDUPE_KEY", "key": env.dedupe_key}],
                pending_task_ids=[item_id],
                actor=actor,
            )

            plan_ref = planning_adapter.prepare_workflow_plan(session, workflow.id)
            ingested.append(
                {
                    "dedupe_key": env.dedupe_key,
                    "task_id": task.id,
                    "workflow_id": workflow.id,
                    "item_id": item_id,
                    "source_item_id": env.source_item_id,
                    "title": env.title,
                    "plan_id": plan_ref.plan_id,
                }
            )

        self._total_fed += len(ingested)
        session.flush()
        return ingested

    def _get_existing_dedupe_keys(self, session: Session) -> set[str]:
        keys = set()
        workflows = session.scalars(select(PersistentWorkflowORM)).all()
        for wf in workflows:
            for decision in wf.decisions or []:
                if isinstance(decision, dict) and decision.get("type") == "DEDUPE_KEY":
                    val = decision.get("key")
                    if val:
                        keys.add(str(val))
        return keys

    def _ensure_project(self, session: Session, project_id: str) -> ProjectORM:
        project = session.get(ProjectORM, project_id)
        if project is None:
            project = ProjectORM(
                id=project_id,
                name="Darwin Research Engine",
                slug=project_id,
                organization="DFG",
                project_type="R_AND_D",
                status="ACTIVE",
                description="Canonical Darwin Research Engine repository attachment",
                workspace_scope=str(self.darwin_root),
                default_authority_level=AuthorityLevel.L0.value,
            )
            session.add(project)
            session.flush()
        return project

    def _ensure_repository_attachment(self, session: Session, project: ProjectORM) -> RepositoryRegistrationORM:
        reg_id = f"repo-{project.id}"
        reg = session.scalar(
            select(RepositoryRegistrationORM).where(
                RepositoryRegistrationORM.project_id == project.id,
                RepositoryRegistrationORM.location == str(self.darwin_root),
            )
        ) or session.get(RepositoryRegistrationORM, reg_id)
        if reg is None:
            reg = RepositoryRegistrationORM(
                id=reg_id,
                project_id=project.id,
                name="darwin-research-engine",
                adapter_type="git",
                location=str(self.darwin_root),
                access_mode="READ_ONLY",
                status="ACTIVE",
            )
            session.add(reg)
            session.flush()

        attachment = session.scalar(
            select(ProjectRepositoryAttachmentORM).where(
                ProjectRepositoryAttachmentORM.project_id == project.id,
                ProjectRepositoryAttachmentORM.repository_id == reg.id,
            )
        )
        if attachment is None:
            attachment = ProjectRepositoryAttachmentORM(
                project_id=project.id,
                repository_id=reg.id,
                attached_by="LUCIUS",
            )
            session.add(attachment)
            session.flush()
        return reg
