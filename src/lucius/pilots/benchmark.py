from __future__ import annotations

import time
from pathlib import Path

from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, BenchmarkRegressionStatus, BenchmarkRunStatus
from lucius.evaluation.cases import CORE_SUITE_NAME
from lucius.evaluation.service import EvaluationService
from lucius.persistence.orm import BenchmarkResultORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.pilots.schemas import BenchmarkComparison, BenchmarkResult


class BenchmarkRunnerService:
    def __init__(self, session: Session, *, repo_root: Path | None = None):
        self.session = session
        self.repo_root = repo_root or Path.cwd()
        self.audit = AuditService(session)

    def run_core_benchmark(
        self,
        *,
        benchmark_version: str = "LUCIUS_CORE_BENCH_V0_1",
        actor: Actor = Actor.SYSTEM,
    ) -> BenchmarkResult:
        started = time.perf_counter()
        report = EvaluationService(self.session, repo_root=self.repo_root).run_suite(
            suite_name=CORE_SUITE_NAME,
            created_by=actor,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        passed = sum(1 for case in report.cases if case.status.value == "PASSED")
        failed = sum(1 for case in report.cases if case.status.value in {"FAILED", "ERROR"})
        status = BenchmarkRunStatus.PASSED if failed == 0 else BenchmarkRunStatus.FAILED
        row = BenchmarkResultORM(
            id=next_id(self.session, "benchmark"),
            suite_name=CORE_SUITE_NAME,
            suite_version=report.run.suite_version,
            benchmark_version=benchmark_version,
            git_head=report.run.target_commit_sha,
            target_dirty=report.run.target_dirty,
            status=status.value,
            total_cases=len(report.cases),
            passed=passed,
            failed=failed,
            skipped=0,
            duration_ms=duration_ms,
            deterministic_metrics={
                "aggregate_score": report.run.aggregate_score,
                "hard_gate_status": report.run.hard_gate_status.value,
                "release_decision": report.run.release_decision.value if report.run.release_decision else None,
                "regressions": report.run.regressions,
            },
            artifact_result_id=report.run.id,
            evaluation_run_id=report.run.id,
            environment_metadata=report.run.environment_metadata,
            captured_at=utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        self.audit.record(
            event_type="BENCHMARK_RESULT_CAPTURED",
            actor=actor.value,
            action="run_core_benchmark",
            result=status.value,
            metadata={"benchmark_id": row.id, "evaluation_run_id": row.evaluation_run_id},
        )
        return _benchmark_from_row(row)

    def compare(
        self,
        before_id: str | None,
        after_id: str | None,
        *,
        allowed_score_drop: float = 0.0,
    ) -> BenchmarkComparison:
        if before_id is None or after_id is None:
            return BenchmarkComparison(status=BenchmarkRegressionStatus.NOT_RUN, before_id=before_id, after_id=after_id, reasons=["Required benchmark result missing."])
        before = self.session.get(BenchmarkResultORM, before_id)
        after = self.session.get(BenchmarkResultORM, after_id)
        if before is None or after is None:
            return BenchmarkComparison(status=BenchmarkRegressionStatus.NOT_RUN, before_id=before_id, after_id=after_id, reasons=["Benchmark result id not found."])
        if before.status == BenchmarkRunStatus.NOT_RUN.value or after.status == BenchmarkRunStatus.NOT_RUN.value:
            return BenchmarkComparison(status=BenchmarkRegressionStatus.NOT_RUN, before_id=before_id, after_id=after_id, reasons=["Benchmark was not run."])
        before_score = before.deterministic_metrics.get("aggregate_score")
        after_score = after.deterministic_metrics.get("aggregate_score")
        if before_score is None or after_score is None:
            return BenchmarkComparison(status=BenchmarkRegressionStatus.INCONCLUSIVE, before_id=before_id, after_id=after_id, reasons=["Aggregate score missing."])
        reasons = []
        if after.failed > before.failed:
            reasons.append("Failed case count increased.")
        if float(before_score) - float(after_score) > allowed_score_drop:
            reasons.append("Aggregate score dropped beyond allowed threshold.")
        if after.deterministic_metrics.get("hard_gate_status") == "FAIL":
            reasons.append("After benchmark has hard gate failure.")
        return BenchmarkComparison(
            status=BenchmarkRegressionStatus.REGRESSION if reasons else BenchmarkRegressionStatus.NO_REGRESSION,
            before_id=before_id,
            after_id=after_id,
            reasons=reasons,
        )


def _benchmark_from_row(row: BenchmarkResultORM) -> BenchmarkResult:
    return BenchmarkResult(
        id=row.id,
        suite_name=row.suite_name,
        suite_version=row.suite_version,
        benchmark_version=row.benchmark_version,
        git_head=row.git_head,
        target_dirty=row.target_dirty,
        status=row.status,
        total_cases=row.total_cases,
        passed=row.passed,
        failed=row.failed,
        skipped=row.skipped,
        duration_ms=row.duration_ms,
        deterministic_metrics=row.deterministic_metrics,
        artifact_result_id=row.artifact_result_id,
        evaluation_run_id=row.evaluation_run_id,
        environment_metadata=row.environment_metadata,
        captured_at=row.captured_at,
    )
