from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from lucius.persistence.orm import ModelExecutionORM, PersistentWorkflowORM
from lucius.runtime.preflight import RuntimePreflightService
from lucius.pilots.workflows import PersistentWorkflowService
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.schemas import ModelCapabilityProfile, ModelQualificationStatus, RuntimeProviderModel
from lucius.runtime.adapters import ScriptedExecutionAdapter


class AvailabilityCountingProvider(ScriptedExecutionAdapter):
    def __init__(self, provider_id: str = "ollama-local"):
        super().__init__(provider_id=provider_id, capabilities=["inspection_reasoning"], model_id="qwen3:8b")
        self.availability_checks = 0
        profile = ModelCapabilityProfile(
            provider_id=provider_id,
            model_id="qwen3:8b",
            execution_tier="LOCAL_TIER_1",
            is_local=True,
            supported_task_classes=["inspection"],
            supported_capabilities=["inspection_reasoning"],
            supports_mutation=False,
            deterministic_verification_required=True,
            supervision_required=False,
            unattended_eligible=True,
            max_task_complexity="T1",
            status=ModelQualificationStatus.QUALIFIED_WITH_CONSTRAINTS,
        )
        self.registration = self.registration.model_copy(
            update={
                "supported_task_classes": ["inspection"],
                "metadata": {
                    "explicit_allowed_workspace_roots": True,
                    "read_only_worker_shape": {
                        "max_context_files": 3,
                        "max_context_bytes": 40_000,
                        "max_evidence_refs": 3,
                        "required_capabilities": ["inspection_reasoning"],
                    },
                },
                "models": [
                    RuntimeProviderModel(
                        model_id="qwen3:8b",
                        capabilities=["inspection_reasoning"],
                        capability_profile=profile,
                    )
                ],
            }
        )

    def is_available(self) -> bool:
        self.availability_checks += 1
        return True


def _workflow(session, workspace: Path, *, title: str = "Review bounded observability invariant"):
    workflow = PersistentWorkflowService(session).create(
        objective=title,
        expected_main_head="29db80f7ade0b30ff0b934c4511b7e344acd1213",
        isolated_branch="lucius/overnight02/test",
        worktree_path=str(workspace),
        authority_tier="L0",
        project_id="LPROJ_TEST",
        repository_id="LREPO_TEST",
        task_id="LTASK_TEST",
        task_backlog=[
            {
                "item_id": "ITEM_TEST",
                "logical_task_id": "ITEM_TEST",
                "state": "READY",
                "title": title,
                "priority": "NORMAL",
                "read_only": True,
                "mutation_allowed": False,
                "unattended": True,
                "task_type": "inspection",
                "task_complexity": "T1",
                "task_risk": "LOW",
                "isolation_mode": "ISOLATED_WORKTREE",
                "execution_supervision": "UNSUPERVISED",
                "required_capabilities": ["inspection_reasoning"],
                "context_limits": {
                    "deterministic_verification": True,
                    "evidence_reference_validation_required": True,
                    "read_only_context_paths": ["README.md"],
                    "requested_evidence_refs": 3,
                },
            }
        ],
    )
    row = session.get(PersistentWorkflowORM, workflow.id)
    row.plan_id = "LPLAN_TEST"
    row.plan_freeze_id = "LFREEZE_TEST"
    row.workflow_state = "PLAN_READY"
    session.flush()
    return workflow.id


def test_runtime_preflight_is_read_only_and_reports_launchable_workflow(session, tmp_path):
    (tmp_path / "README.md").write_text("bounded context\n", encoding="utf-8")
    workflow_id = _workflow(session, tmp_path)
    provider = AvailabilityCountingProvider()
    router = ModelExecutionRouter(session, registry=RuntimeProviderRegistry([provider]))
    before = session.get(PersistentWorkflowORM, workflow_id).task_backlog

    result = RuntimePreflightService(session, router=router).inspect([workflow_id])[0]

    after = session.get(PersistentWorkflowORM, workflow_id).task_backlog
    assert result.launchable is True
    assert [item.provider_id for item in result.eligible_providers] == ["ollama-local"]
    assert result.rejected_providers == []
    assert result.worker_shape.valid is True
    assert before == after
    assert provider.availability_checks == 1
    assert session.scalars(select(ModelExecutionORM)).all() == []