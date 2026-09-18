from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, AuthorityLevel, ProjectStatus, QueueWorkItemState, TaskStatus
from lucius.persistence.json_fields import set_json_field
from lucius.persistence.orm import (
    AuditEventORM,
    EngineeringPlanORM,
    PlanFreezeORM,
    PersistentWorkflowORM,
    ProjectORM,
    ProjectRepositoryAttachmentORM,
    RepositoryRegistrationORM,
    TaskContractORM,
    TaskORM,
    utc_now,
)
from lucius.persistence.repositories import next_id
from lucius.pilots.queue import EXECUTION_ELIGIBLE_WORKFLOW_STATES, NonBlockingQueueService, QueueStateError
from lucius.pilots.schemas import GlobalQueueSelection
from lucius.runtime.schemas import DispatchCandidate, DispatchCandidateEvaluation, DispatchSelection


PRIORITY_RANK = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}
STATE_RANK = {QueueWorkItemState.READY_TO_RESUME: 0, QueueWorkItemState.READY: 1}
BLOCKED_STATES = {
    QueueWorkItemState.WAITING_HUMAN,
    QueueWorkItemState.WAITING_EXTERNAL,
    QueueWorkItemState.WAITING_RESOURCE,
    QueueWorkItemState.WAITING_DEPENDENCY,
    QueueWorkItemState.WAITING_SCHEDULE,
    QueueWorkItemState.BLOCKED_DEPENDENCY,
    QueueWorkItemState.BLOCKED_AUTHORITY,
    QueueWorkItemState.BLOCKED_DECISION,
    QueueWorkItemState.RETRY_LATER,
}


@dataclass(frozen=True)
class _WorkflowBundle:
    workflow: PersistentWorkflowORM
    project: ProjectORM | None
    repository: RepositoryRegistrationORM | None
    task: TaskORM | None


class MultiProjectDispatcher:
    """Single-dispatcher selector for eligible work across registered projects."""

    consecutive_project_limit = 2

    def __init__(self, session: Session, *, actor: Actor = Actor.LUCIUS):
        self.session = session
        self.actor = actor
        self.audit = AuditService(session)
        self.queue = NonBlockingQueueService(session)

    def start_next(self, workflow_ids: list[str] | None = None) -> DispatchSelection:
        selection = self.select_next(workflow_ids)
        if selection.selected is None:
            return selection

        candidate = selection.selected
        workflow = self.session.get(PersistentWorkflowORM, candidate.workflow_id)
        if workflow is None or not workflow.task_backlog:
            raise QueueStateError(f"Selected workflow no longer exists or is empty: {candidate.workflow_id}")

        items = [dict(item) for item in workflow.task_backlog]
        target_item = None
        for item in items:
            item_id = str(item.get("item_id") or item.get("task_id"))
            if item_id == candidate.item_id:
                target_item = item
                break

        if target_item is None:
            raise QueueStateError(f"Selected queue item no longer exists in workflow: {candidate.item_id}")

        previous_state = str(target_item.get("state"))
        previous_version = int(target_item.get("version", 0))

        target_item["state"] = QueueWorkItemState.RUNNING.value
        target_item["version"] = previous_version + 1
        target_item["started_at"] = utc_now().isoformat()
        target_item["run_generation"] = int(target_item.get("run_generation", 0)) + 1
        workflow.active_task_id = candidate.item_id
        set_json_field(workflow, "task_backlog", items)
        workflow.updated_at = utc_now()
        self.session.flush()

        new_version = int(target_item.get("version", 0))

        result = selection.model_copy(
            update={
                "started": True,
                "started_previous_state": previous_state,
                "started_new_state": QueueWorkItemState.RUNNING.value,
                "started_previous_version": previous_version,
                "started_new_version": new_version,
            }
        )
        self._audit(
            "MULTI_PROJECT_DISPATCH_CANDIDATE_HANDED_TO_RUNTIME",
            "hand_dispatch_candidate_to_runtime",
            "SUCCESS",
            selected=result.selected,
            metadata=result.model_dump(mode="json"),
        )
        return result

    def select_next(self, workflow_ids: list[str] | None = None) -> DispatchSelection:
        cycle_id = next_id(self.session, "audit")
        bundles = self._workflow_bundles(workflow_ids)
        self._audit(
            "MULTI_PROJECT_DISPATCH_CYCLE_STARTED",
            "start_multi_project_dispatch_cycle",
            cycle_id,
            metadata={"cycle_id": cycle_id, "workflow_ids": workflow_ids, "single_active_execution": True},
        )
        self._audit(
            "MULTI_PROJECT_DISPATCH_PROJECTS_INSPECTED",
            "inspect_dispatchable_projects",
            str(len(bundles)),
            metadata={"cycle_id": cycle_id, "projects": [_project_payload(bundle) for bundle in bundles]},
        )

        running = [evaluation for bundle in bundles for evaluation in self._evaluations(bundle) if evaluation.candidate.state == QueueWorkItemState.RUNNING]
        if running:
            selection = DispatchSelection(
                cycle_id=cycle_id,
                reason="RUNNING_ITEM_ACTIVE_NO_PREEMPTION",
                blocked_candidates=running,
            )
            self._audit_selection(selection, "NO_PREEMPTION")
            return selection

        evaluations = [evaluation for bundle in bundles for evaluation in self._evaluations(bundle)]
        eligible = [evaluation for evaluation in evaluations if evaluation.eligible]
        blocked = [
            evaluation
            for evaluation in evaluations
            if evaluation.candidate.state in BLOCKED_STATES or "DEPENDENCIES_UNRESOLVED" in evaluation.reasons
        ]
        excluded = [evaluation for evaluation in evaluations if not evaluation.eligible and evaluation not in blocked]

        for evaluation in evaluations:
            self._audit(
                "MULTI_PROJECT_DISPATCH_CANDIDATE_ELIGIBILITY_EVALUATED",
                "evaluate_dispatch_candidate",
                "ELIGIBLE" if evaluation.eligible else "EXCLUDED",
                selected=evaluation.candidate,
                metadata={"cycle_id": cycle_id, **evaluation.model_dump(mode="json")},
            )
        if not eligible:
            selection = DispatchSelection(
                cycle_id=cycle_id,
                reason="NO_ELIGIBLE_MULTI_PROJECT_WORK",
                excluded_candidates=excluded,
                blocked_candidates=blocked,
            )
            self._audit_selection(selection, "NO_ELIGIBLE_WORK")
            return selection

        ordered = self._ordered_candidates(eligible)
        selected_evaluation = ordered[0]
        reason = _selection_reason(selected_evaluation, ordered)
        selection = DispatchSelection(
            cycle_id=cycle_id,
            selected=selected_evaluation.candidate,
            reason=reason,
            eligible_candidates=ordered,
            excluded_candidates=excluded,
            blocked_candidates=blocked,
            fairness_applied="fairness_boost" in selected_evaluation.candidate.scheduling_metadata,
            deterministic_tie_break_reason=reason,
        )
        self._audit_selection(selection, "SELECTED")
        return selection

    def _ordered_candidates(self, eligible: list[DispatchCandidateEvaluation]) -> list[DispatchCandidateEvaluation]:
        recent_projects = _recent_started_projects(self.session, limit=self.consecutive_project_limit)
        exhausted_project = None
        if len(recent_projects) == self.consecutive_project_limit and len(set(recent_projects)) == 1:
            exhausted_project = recent_projects[0]
        alternatives = {evaluation.candidate.project_id for evaluation in eligible if evaluation.candidate.project_id != exhausted_project}
        ordered: list[DispatchCandidateEvaluation] = []
        for evaluation in eligible:
            key = list(_candidate_key(evaluation.candidate, exhausted_project if alternatives else None))
            metadata = dict(evaluation.candidate.scheduling_metadata)
            if exhausted_project and alternatives and evaluation.candidate.project_id == exhausted_project:
                metadata["fairness_deferred_after_consecutive_dispatches"] = self.consecutive_project_limit
            elif exhausted_project and alternatives:
                metadata["fairness_boost"] = f"project {exhausted_project} reached consecutive dispatch limit"
            candidate = evaluation.candidate.model_copy(update={"scheduling_metadata": metadata})
            ordered.append(evaluation.model_copy(update={"candidate": candidate, "scheduling_key": key}))
        return sorted(ordered, key=lambda item: item.scheduling_key)

    def _evaluations(self, bundle: _WorkflowBundle) -> list[DispatchCandidateEvaluation]:
        workflow = bundle.workflow
        evaluations: list[DispatchCandidateEvaluation] = []
        for item in workflow.task_backlog:
            candidate = _candidate_from_item(workflow, item, bundle.project)
            reasons = self._eligibility_reasons(bundle, candidate, item)
            candidate = candidate.model_copy(
                update={"dependency_state": "UNRESOLVED" if "DEPENDENCIES_UNRESOLVED" in reasons else "COMPLETE"}
            )
            evaluations.append(
                DispatchCandidateEvaluation(
                    candidate=candidate,
                    eligible=not reasons,
                    reasons=reasons or ["ELIGIBLE"],
                    scheduling_key=list(_candidate_key(candidate, None)),
                )
            )
        return evaluations

    def _eligibility_reasons(
        self,
        bundle: _WorkflowBundle,
        candidate: DispatchCandidate,
        item: dict[str, Any],
    ) -> list[str]:
        reasons: list[str] = []
        workflow = bundle.workflow
        project = bundle.project
        repository = bundle.repository
        task = bundle.task
        if project is None:
            reasons.append("PROJECT_MISSING")
        elif project.status != ProjectStatus.ACTIVE.value:
            reasons.append(f"PROJECT_DISABLED:{project.status}")
        if workflow.workflow_state not in {state.value for state in EXECUTION_ELIGIBLE_WORKFLOW_STATES}:
            reasons.append(f"WORKFLOW_TERMINAL_OR_INELIGIBLE:{workflow.workflow_state}")
        if task is None:
            reasons.append("TASK_MISSING")
        elif task.project_id != workflow.project_id:
            reasons.append("TASK_PROJECT_MISMATCH")
        elif task.status != TaskStatus.READY.value:
            reasons.append(f"TASK_NOT_READY:{task.status}")
        if workflow.project_id is None:
            reasons.append("WORKFLOW_PROJECT_MISSING")
        if repository is None:
            reasons.append("REPOSITORY_MISSING")
        elif repository.project_id != workflow.project_id:
            reasons.append("REPOSITORY_PROJECT_MISMATCH")
        elif repository.status != "ACTIVE":
            reasons.append(f"REPOSITORY_INACTIVE:{repository.status}")
        if workflow.repository_id:
            attachment = self.session.scalar(
                select(ProjectRepositoryAttachmentORM).where(
                    ProjectRepositoryAttachmentORM.project_id == workflow.project_id,
                    ProjectRepositoryAttachmentORM.repository_id == workflow.repository_id,
                )
            )
            if attachment is None:
                reasons.append("REPOSITORY_UNATTACHED")
        if workflow.plan_id is None or workflow.plan_freeze_id is None:
            reasons.append("FROZEN_PLAN_MISSING")
        elif not self._plan_authorizes_workflow(workflow):
            reasons.append("PLAN_FREEZE_PROJECT_OR_TASK_MISMATCH")
        state = _safe_state(item)
        if state is None:
            reasons.append("QUEUE_STATE_MALFORMED_OR_MISSING")
        elif state not in {QueueWorkItemState.READY, QueueWorkItemState.READY_TO_RESUME}:
            if state in BLOCKED_STATES:
                reasons.append(f"BLOCKER_UNRESOLVED:{state.value}")
            elif state == QueueWorkItemState.RUNNING:
                reasons.append("RUNNING_ITEM_ACTIVE")
            else:
                reasons.append(f"QUEUE_STATE_NOT_EXECUTABLE:{state.value}")
        completed = {
            _item_id(candidate_item)
            for candidate_item in workflow.task_backlog
            if _safe_state(candidate_item) == QueueWorkItemState.COMPLETED
        }
        dependencies = [str(dependency) for dependency in item.get("dependencies", [])]
        if any(dependency not in completed for dependency in dependencies):
            reasons.append("DEPENDENCIES_UNRESOLVED")
        if state == QueueWorkItemState.RETRY_LATER:
            reasons.append("RETRY_BACKOFF_ACTIVE")
        if state == QueueWorkItemState.READY_TO_RESUME and not item.get("current_block_checkpoint_id"):
            reasons.append("RESUME_PREREQUISITE_MISSING")
        return list(dict.fromkeys(reasons))

    def _plan_authorizes_workflow(self, workflow: PersistentWorkflowORM) -> bool:
        plan = self.session.get(EngineeringPlanORM, workflow.plan_id)
        freeze = self.session.get(PlanFreezeORM, workflow.plan_freeze_id)
        if plan is None or freeze is None:
            return False
        if plan.project_id != workflow.project_id or freeze.project_id != workflow.project_id:
            return False
        if workflow.task_id and (plan.task_id != workflow.task_id or freeze.task_id != workflow.task_id):
            return False
        if freeze.plan_id != plan.id:
            return False
        return True

    def _workflow_bundles(self, workflow_ids: list[str] | None) -> list[_WorkflowBundle]:
        query = select(PersistentWorkflowORM).order_by(PersistentWorkflowORM.project_id, PersistentWorkflowORM.id)
        if workflow_ids:
            query = query.where(PersistentWorkflowORM.id.in_(workflow_ids))
        workflows = list(self.session.scalars(query).all())
        if workflow_ids and len(workflows) != len(set(workflow_ids)):
            found = {workflow.id for workflow in workflows}
            missing = sorted(set(workflow_ids) - found)
            raise QueueStateError(f"Unknown persistent workflows: {missing}")
        bundles = []
        for workflow in workflows:
            if not workflow.task_backlog:
                continue
            bundles.append(
                _WorkflowBundle(
                    workflow=workflow,
                    project=self.session.get(ProjectORM, workflow.project_id) if workflow.project_id else None,
                    repository=self.session.get(RepositoryRegistrationORM, workflow.repository_id) if workflow.repository_id else None,
                    task=self.session.get(TaskORM, workflow.task_id) if workflow.task_id else None,
                )
            )
        return bundles

    def _audit_selection(self, selection: DispatchSelection, result: str) -> None:
        metadata = selection.model_dump(mode="json")
        self._audit(
            "MULTI_PROJECT_DISPATCH_ELIGIBLE_CANDIDATE_SET",
            "record_eligible_dispatch_candidates",
            str(len(selection.eligible_candidates)),
            metadata=metadata,
        )
        self._audit(
            "MULTI_PROJECT_DISPATCH_SCHEDULING_POLICY_APPLIED",
            "apply_deterministic_multi_project_policy",
            selection.reason,
            selected=selection.selected,
            metadata=metadata,
        )
        event_type = "MULTI_PROJECT_DISPATCH_CANDIDATE_SELECTED" if selection.selected else "MULTI_PROJECT_DISPATCH_NO_ELIGIBLE_WORK"
        self._audit(
            event_type,
            "select_multi_project_dispatch_candidate",
            result,
            selected=selection.selected,
            metadata=metadata,
        )

    def _audit(
        self,
        event_type: str,
        action: str,
        result: str,
        *,
        selected: DispatchCandidate | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.audit.record(
            event_type=event_type,
            actor=self.actor.value,
            project_id=selected.project_id if selected else None,
            repository_id=selected.repository_id if selected else None,
            task_id=selected.task_id if selected else None,
            action=action,
            result=result,
            authority_level=AuthorityLevel.L1,
            metadata=metadata or {},
        )


def _candidate_from_item(
    workflow: PersistentWorkflowORM,
    item: dict[str, Any],
    project: ProjectORM | None,
) -> DispatchCandidate:
    state = _safe_state(item) or QueueWorkItemState.FAILED
    project_policy = project.documentation_policy if project else {}
    return DispatchCandidate(
        project_id=str(workflow.project_id or ""),
        workflow_id=workflow.id,
        task_id=workflow.task_id,
        repository_id=workflow.repository_id,
        item_id=_item_id(item),
        logical_task_id=str(item.get("logical_task_id") or item.get("task_id") or _item_id(item)),
        priority=str(item.get("priority") or "NORMAL").upper(),
        project_priority=str(project_policy.get("priority") or project_policy.get("scheduling_priority") or "NORMAL").upper(),
        state=state,
        dependency_state="UNKNOWN",
        blocker_state="BLOCKED" if state in BLOCKED_STATES else "NONE",
        retry_state="BACKOFF" if state == QueueWorkItemState.RETRY_LATER else "READY",
        resume_state="RESUME" if state == QueueWorkItemState.READY_TO_RESUME else "NEW",
        created_order=int(item.get("created_order", 0)),
        ready_timestamp=item.get("updated_at") or item.get("ready_at") or item.get("created_at"),
        created_at=item.get("created_at"),
        required_capabilities=[str(value) for value in item.get("required_capabilities", ["code_modification"])],
        plan_id=workflow.plan_id,
        plan_freeze_id=workflow.plan_freeze_id,
        item_version=int(item.get("version", 0)),
        scheduling_metadata={
            "workflow_state": workflow.workflow_state,
            "authority_tier": workflow.authority_tier,
            "is_resumed": state == QueueWorkItemState.READY_TO_RESUME,
        },
        tie_break_fields={
            "project_id": workflow.project_id,
            "workflow_id": workflow.id,
            "item_id": _item_id(item),
            "created_order": int(item.get("created_order", 0)),
        },
    )


def _candidate_key(candidate: DispatchCandidate, fairness_deferred_project: str | None) -> tuple[int, int, int, int, int, str, str, str]:
    fairness_rank = 1 if fairness_deferred_project and candidate.project_id == fairness_deferred_project and candidate.priority != "CRITICAL" else 0
    return (
        PRIORITY_RANK.get(candidate.priority, PRIORITY_RANK["NORMAL"]),
        fairness_rank,
        PRIORITY_RANK.get(candidate.project_priority, PRIORITY_RANK["NORMAL"]),
        STATE_RANK.get(candidate.state, 9),
        candidate.created_order,
        candidate.project_id,
        candidate.workflow_id,
        candidate.item_id,
    )


def _selection_reason(selected: DispatchCandidateEvaluation, ordered: list[DispatchCandidateEvaluation]) -> str:
    return (
        f"project={selected.candidate.project_id} workflow={selected.candidate.workflow_id} "
        f"item={selected.candidate.item_id} priority={selected.candidate.priority} "
        f"project_priority={selected.candidate.project_priority} state={selected.candidate.state.value} "
        f"tie_break=priority, fairness, project_priority, resume, created_order, project_id, workflow_id, item_id "
        f"eligible_count={len(ordered)}"
    )


def _recent_started_projects(session: Session, *, limit: int) -> list[str]:
    rows = session.scalars(
        select(AuditEventORM)
        .where(AuditEventORM.event_type == "MULTI_PROJECT_DISPATCH_CANDIDATE_HANDED_TO_RUNTIME")
        .order_by(AuditEventORM.timestamp.desc(), AuditEventORM.id.desc())
        .limit(limit)
    )
    return [row.project_id for row in rows if row.project_id]


def _project_payload(bundle: _WorkflowBundle) -> dict[str, Any]:
    project = bundle.project
    workflow = bundle.workflow
    repository = bundle.repository
    return {
        "project_id": workflow.project_id,
        "project_name": project.name if project else None,
        "project_status": project.status if project else None,
        "project_priority": (project.documentation_policy or {}).get("priority") if project else None,
        "repository_id": workflow.repository_id,
        "repository_status": repository.status if repository else None,
        "workflow_id": workflow.id,
        "workflow_state": workflow.workflow_state,
        "task_id": workflow.task_id,
        "item_count": len(workflow.task_backlog),
    }


def _item_id(item: dict[str, Any]) -> str:
    return str(item.get("item_id") or item.get("task_id"))


def _safe_state(item: dict[str, Any]) -> QueueWorkItemState | None:
    try:
        raw_state = item.get("state")
        if raw_state is None:
            return None
        return QueueWorkItemState(str(raw_state))
    except ValueError:
        return None
