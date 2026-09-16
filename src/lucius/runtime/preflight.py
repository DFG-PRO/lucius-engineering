from __future__ import annotations

from sqlalchemy.orm import Session

from lucius.runtime.router import ModelExecutionRouter
from lucius.runtime.schemas import RuntimePreflightResult, RuntimePreflightProvider, RuntimeWorkerShape
from lucius.runtime.service import ExecutionRuntimeLoopService


class RuntimePreflightService:
    """Read-only launch eligibility report for persisted runtime workflows."""

    def __init__(self, session: Session, *, router: ModelExecutionRouter):
        self.session = session
        self.router = router
        self.runtime = ExecutionRuntimeLoopService(
            session,
            planning_adapter=_NoopPlanningAdapter(),
            execution_router=router,
        )

    def inspect(self, workflow_ids: list[str]) -> list[RuntimePreflightResult]:
        results: list[RuntimePreflightResult] = []
        for workflow_id in workflow_ids:
            context = self.runtime.execution_context_for_preflight(workflow_id)
            preview = self.router.preview(context)
            results.append(
                RuntimePreflightResult(
                    workflow_id=workflow_id,
                    eligible_providers=[
                        RuntimePreflightProvider(
                            provider_id=item.provider_id,
                            model_id=item.selected_model_id,
                            eligible=True,
                            reasons=item.reasons,
                        )
                        for item in preview["candidates"]
                        if item.eligible
                    ],
                    rejected_providers=[
                        RuntimePreflightProvider(
                            provider_id=item.provider_id,
                            model_id=item.selected_model_id,
                            eligible=False,
                            reasons=item.reasons,
                        )
                        for item in preview["candidates"]
                        if not item.eligible
                    ],
                    worker_shape=preview["worker_shape"],
                    launchable=bool(preview["worker_shape"].valid and any(item.eligible for item in preview["candidates"])),
                )
            )
        return results


class _NoopPlanningAdapter:
    provider_id = "preflight-noop"

    def prepare_workflow_plan(self, session: Session, workflow_id: str):
        raise RuntimeError("Planning is not available during runtime preflight.")