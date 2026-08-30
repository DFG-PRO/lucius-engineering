from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, EvaluationCaseStatus, EvaluationHardGateStatus, EvaluationRunStatus
from lucius.evaluation.cases import CORE_SUITE_NAME, golden_actual, lucius_core_bench_v0_1
from lucius.evaluation.evaluators import evaluate_case
from lucius.evaluation.regression import compare_runs
from lucius.evaluation.reporting import machine_report, markdown_report
from lucius.evaluation.schemas import EvaluationCase, EvaluationCaseResult, EvaluationReport, EvaluationRun, EvaluationSuite
from lucius.evaluation.scoring import aggregate_score, hard_gate_status, release_decision, run_status, weighted_score
from lucius.persistence.orm import EvaluationCaseORM, EvaluationCaseResultORM, EvaluationRunORM, EvaluationSuiteORM, utc_now
from lucius.persistence.repositories import next_id

TargetExecutor = Callable[[EvaluationCase], dict[str, Any]]


class EvaluationService:
    def __init__(self, session: Session, *, repo_root: Path | None = None):
        self.session = session
        self.repo_root = repo_root or Path.cwd()
        self.audit = AuditService(session)

    def load_suite(self, name: str = CORE_SUITE_NAME) -> EvaluationSuite:
        if name != CORE_SUITE_NAME:
            raise ValueError(f"Unknown evaluation suite: {name}")
        suite = lucius_core_bench_v0_1()
        suite_row = self._persist_suite(suite)
        suite.id = suite_row.id
        for case in suite.cases:
            case.suite_id = suite_row.id
            row = self._persist_case(case, suite_row.id)
            case.id = row.id
        suite.case_ids = [case.id for case in suite.cases if case.id]
        suite.baseline_run_id = suite_row.baseline_run_id
        suite_row.case_refs = [{"case_id": case.id, "version": case.version, "name": case.name} for case in suite.cases]
        self.session.flush()
        return suite

    def run_suite(
        self,
        *,
        suite_name: str = CORE_SUITE_NAME,
        target_executor: TargetExecutor | None = None,
        baseline_run_id: str | None = None,
        target_version: str | None = None,
        create_baseline: bool = False,
        replace_baseline: bool = False,
        created_by: Actor = Actor.SYSTEM,
    ) -> EvaluationReport:
        suite = self.load_suite(suite_name)
        baseline_id = baseline_run_id or suite.baseline_run_id
        env = self._environment_metadata()
        config_hash = self._config_hash(suite)
        run_row = EvaluationRunORM(
            id=next_id(self.session, "evaluation_run"),
            suite_id=suite.id,
            suite_version=suite.version,
            target_version=target_version,
            target_commit_sha=env.get("commit_sha"),
            target_dirty=bool(env.get("dirty")),
            planner_version="1.9.0",
            started_at=utc_now(),
            status=EvaluationRunStatus.RUNNING.value,
            hard_gate_status=EvaluationHardGateStatus.PASS.value,
            baseline_run_id=baseline_id,
            environment_metadata=env,
            config_hash=config_hash,
            regressions=[],
            machine_report={},
            created_by=created_by.value,
        )
        self.session.add(run_row)
        self.session.flush()
        self.audit.record(
            event_type="EVALUATION_RUN_STARTED",
            actor=created_by.value,
            action="run_evaluation_suite",
            result="STARTED",
            metadata={"run_id": run_row.id, "suite_id": suite.id, "suite_version": suite.version, "config_hash": config_hash},
        )
        case_results = []
        executor = target_executor or (lambda case: golden_actual(case))
        for case in suite.cases:
            case_results.append(self._run_case(run_row.id, case, executor, created_by=created_by))
        score = aggregate_score(case_results)
        gates = hard_gate_status(case_results)
        current = EvaluationRun(
            id=run_row.id,
            suite_id=suite.id,
            suite_version=suite.version,
            target_version=target_version,
            target_commit_sha=run_row.target_commit_sha,
            target_dirty=run_row.target_dirty,
            planner_version=run_row.planner_version,
            started_at=run_row.started_at,
            status=run_status(score, gates),
            aggregate_score=score,
            hard_gate_status=gates,
            release_decision=release_decision(score, gates),
            baseline_run_id=baseline_id,
            environment_metadata=env,
            config_hash=config_hash,
            created_by=created_by.value,
        )
        baseline_run, baseline_cases = self._baseline_for(baseline_id)
        regressions = compare_runs(current, case_results, baseline_run, baseline_cases)
        current.regressions = regressions
        current.machine_report = machine_report(current, case_results)
        current.markdown_report = markdown_report(current, case_results)
        run_row.completed_at = utc_now()
        run_row.status = current.status.value
        run_row.aggregate_score = current.aggregate_score
        run_row.hard_gate_status = current.hard_gate_status.value
        run_row.release_decision = current.release_decision.value
        run_row.regressions = current.regressions
        run_row.machine_report = current.machine_report
        run_row.markdown_report = current.markdown_report
        self.session.flush()
        if regressions:
            self.audit.record(
                event_type="EVALUATION_REGRESSION_DETECTED",
                actor=created_by.value,
                action="compare_evaluation_regression",
                result="WARNING",
                metadata={"run_id": run_row.id, "regressions": regressions},
            )
        if gates == EvaluationHardGateStatus.FAIL:
            self.audit.record(
                event_type="EVALUATION_RELEASE_BLOCKED",
                actor=created_by.value,
                action="evaluate_release_gate",
                result="BLOCKED",
                metadata={"run_id": run_row.id, "release_decision": current.release_decision.value},
            )
        if create_baseline:
            self.create_baseline(run_row.id, replace=replace_baseline, actor=created_by)
        self.audit.record(
            event_type="EVALUATION_RUN_COMPLETED",
            actor=created_by.value,
            action="run_evaluation_suite",
            result=current.status.value,
            metadata={"run_id": run_row.id, "score": score, "hard_gate_status": gates.value, "release_decision": current.release_decision.value},
        )
        return EvaluationReport(run=current, cases=case_results, machine=current.machine_report, markdown=current.markdown_report)

    def create_baseline(self, run_id: str, *, replace: bool = False, actor: Actor = Actor.SYSTEM) -> None:
        run = self.session.get(EvaluationRunORM, run_id)
        if run is None:
            raise ValueError(f"Unknown evaluation run: {run_id}")
        suite = self.session.get(EvaluationSuiteORM, run.suite_id)
        if suite is None:
            raise ValueError(f"Unknown evaluation suite: {run.suite_id}")
        if run.target_dirty:
            raise ValueError("NON_CANONICAL_DIRTY_RUN cannot become canonical baseline")
        if run.status != EvaluationRunStatus.PASSED.value or run.hard_gate_status != EvaluationHardGateStatus.PASS.value:
            raise ValueError("Only passing runs with no hard gate failures can become baseline")
        if suite.baseline_run_id and not replace:
            raise ValueError("Baseline replacement requires explicit action")
        suite.baseline_run_id = run.id
        self.session.flush()
        self.audit.record(
            event_type="EVALUATION_BASELINE_CREATED",
            actor=actor.value,
            action="create_evaluation_baseline",
            result="SUCCESS",
            metadata={"suite_id": suite.id, "run_id": run.id, "replaced": replace},
        )

    def _run_case(self, run_id: str, case: EvaluationCase, executor: TargetExecutor, *, created_by: Actor) -> EvaluationCaseResult:
        self.audit.record(
            event_type="EVALUATION_CASE_STARTED",
            actor=created_by.value,
            action="run_evaluation_case",
            result="STARTED",
            metadata={"run_id": run_id, "case_id": case.id, "case_version": case.version},
        )
        started = time.perf_counter()
        try:
            actual = executor(case)
            metrics, gate_failures = evaluate_case(case, actual)
            score = weighted_score(metrics, case)
            passed = not gate_failures and score >= 70
            status = EvaluationCaseStatus.PASSED if passed else EvaluationCaseStatus.FAILED
            artifact = actual.get("artifact_reference")
        except Exception as error:
            metrics = []
            gate_failures = []
            score = 0.0
            status = EvaluationCaseStatus.ERROR
            artifact = None
            actual = {"error": str(error)}
        duration_ms = int((time.perf_counter() - started) * 1000)
        result = EvaluationCaseResult(
            run_id=run_id,
            case_id=case.id,
            case_version=case.version,
            status=status,
            metric_results=metrics,
            weighted_score=score,
            hard_gate_passed=not gate_failures,
            hard_gate_failures=gate_failures,
            observations=_observations(metrics),
            actual_artifact_reference=artifact,
            duration_ms=duration_ms,
        )
        row = EvaluationCaseResultORM(
            id=next_id(self.session, "evaluation_case_result"),
            run_id=run_id,
            case_id=case.id,
            case_version=case.version,
            status=result.status.value,
            metric_results=[metric.model_dump(mode="json") for metric in result.metric_results],
            weighted_score=result.weighted_score,
            hard_gate_passed=result.hard_gate_passed,
            hard_gate_failures=[gate.model_dump(mode="json") for gate in result.hard_gate_failures],
            observations=result.observations,
            regressions=[],
            actual_artifact_reference=result.actual_artifact_reference,
            duration_ms=result.duration_ms,
            created_at=result.created_at,
        )
        self.session.add(row)
        self.session.flush()
        result.id = row.id
        event_type = "EVALUATION_CASE_COMPLETED" if status != EvaluationCaseStatus.ERROR else "EVALUATION_CASE_FAILED"
        self.audit.record(
            event_type=event_type,
            actor=created_by.value,
            action="run_evaluation_case",
            result=status.value,
            metadata={"run_id": run_id, "case_id": case.id, "score": score, "duration_ms": duration_ms},
        )
        for gate in gate_failures:
            self.audit.record(
                event_type="EVALUATION_HARD_GATE_FAILED",
                actor=created_by.value,
                action="apply_evaluation_hard_gate",
                result="FAILED",
                metadata={"run_id": run_id, "case_id": case.id, "gate": gate.gate.value},
            )
        return result

    def _persist_suite(self, suite: EvaluationSuite) -> EvaluationSuiteORM:
        row = self.session.scalar(select(EvaluationSuiteORM).where(EvaluationSuiteORM.name == suite.name, EvaluationSuiteORM.version == suite.version))
        if row:
            return row
        row = EvaluationSuiteORM(
            id=next_id(self.session, "evaluation_suite"),
            name=suite.name,
            version=suite.version,
            description=suite.description,
            case_refs=[],
            scoring_policy=suite.scoring_policy,
            hard_gate_policy=suite.hard_gate_policy,
            created_at=suite.created_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _persist_case(self, case: EvaluationCase, suite_id: str) -> EvaluationCaseORM:
        row = self.session.scalar(
            select(EvaluationCaseORM).where(EvaluationCaseORM.suite_id == suite_id, EvaluationCaseORM.name == case.name, EvaluationCaseORM.version == case.version)
        )
        if row:
            return row
        row = EvaluationCaseORM(
            id=next_id(self.session, "evaluation_case"),
            suite_id=suite_id,
            name=case.name,
            description=case.description,
            version=case.version,
            target_type=case.target_type.value,
            definition=case.model_dump(mode="json", exclude={"id", "suite_id", "created_at", "updated_at"}),
            tags=case.tags,
            difficulty=case.difficulty,
            created_at=case.created_at,
            updated_at=case.updated_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _baseline_for(self, baseline_run_id: str | None) -> tuple[EvaluationRun | None, list[EvaluationCaseResult]]:
        if not baseline_run_id:
            return None, []
        row = self.session.get(EvaluationRunORM, baseline_run_id)
        if row is None:
            return None, []
        run = _run_from_row(row)
        case_rows = self.session.scalars(select(EvaluationCaseResultORM).where(EvaluationCaseResultORM.run_id == baseline_run_id)).all()
        return run, [_case_result_from_row(item) for item in case_rows]

    def _config_hash(self, suite: EvaluationSuite) -> str:
        payload = {
            "name": suite.name,
            "version": suite.version,
            "description": suite.description,
            "scoring_policy": suite.scoring_policy,
            "hard_gate_policy": suite.hard_gate_policy,
            "cases": [
                case.model_dump(mode="json", exclude={"id", "suite_id", "created_at", "updated_at"})
                for case in suite.cases
            ],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _environment_metadata(self) -> dict[str, Any]:
        return {
            "commit_sha": _git(["rev-parse", "HEAD"], self.repo_root),
            "dirty": bool(_git(["status", "--porcelain"], self.repo_root)),
            "dirty_label": "NON_CANONICAL_DIRTY_RUN" if _git(["status", "--porcelain"], self.repo_root) else None,
        }


def _run_from_row(row: EvaluationRunORM) -> EvaluationRun:
    return EvaluationRun(
        id=row.id,
        suite_id=row.suite_id,
        suite_version=row.suite_version,
        target_version=row.target_version,
        target_commit_sha=row.target_commit_sha,
        target_dirty=row.target_dirty,
        planner_version=row.planner_version,
        model_provider=row.model_provider,
        model_profile=row.model_profile,
        started_at=row.started_at,
        completed_at=row.completed_at,
        status=row.status,
        aggregate_score=row.aggregate_score,
        hard_gate_status=row.hard_gate_status,
        release_decision=row.release_decision,
        baseline_run_id=row.baseline_run_id,
        environment_metadata=row.environment_metadata,
        config_hash=row.config_hash,
        regressions=row.regressions,
        machine_report=row.machine_report,
        markdown_report=row.markdown_report,
        created_by=row.created_by,
    )


def _case_result_from_row(row: EvaluationCaseResultORM) -> EvaluationCaseResult:
    return EvaluationCaseResult(
        id=row.id,
        run_id=row.run_id,
        case_id=row.case_id,
        case_version=row.case_version,
        status=row.status,
        metric_results=row.metric_results,
        weighted_score=row.weighted_score,
        hard_gate_passed=row.hard_gate_passed,
        hard_gate_failures=row.hard_gate_failures,
        observations=row.observations,
        regressions=row.regressions,
        actual_artifact_reference=row.actual_artifact_reference,
        duration_ms=row.duration_ms,
        created_at=row.created_at,
    )


def _observations(metrics) -> list[str]:
    weak = [metric.name.value for metric in metrics if metric.score < 80]
    return [f"Weak metric: {name}" for name in weak]


def _git(args: list[str], cwd: Path) -> str | None:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()
