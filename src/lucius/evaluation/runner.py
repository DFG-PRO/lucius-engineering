from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from lucius.evaluation.service import EvaluationService, TargetExecutor


class EvaluationRunner:
    def __init__(self, session: Session, *, repo_root: Path | None = None):
        self.service = EvaluationService(session, repo_root=repo_root)

    def run(self, *, suite_name: str, target_executor: TargetExecutor | None = None):
        return self.service.run_suite(suite_name=suite_name, target_executor=target_executor)
