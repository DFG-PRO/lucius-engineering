from __future__ import annotations

from lucius.domain.enums import EvaluationHardGateStatus
from lucius.evaluation.schemas import EvaluationCaseResult, EvaluationRun


def compare_runs(current: EvaluationRun, current_cases: list[EvaluationCaseResult], baseline: EvaluationRun | None, baseline_cases: list[EvaluationCaseResult]) -> list[dict]:
    if baseline is None or current.aggregate_score is None or baseline.aggregate_score is None:
        return []
    regressions: list[dict] = []
    aggregate_drop = baseline.aggregate_score - current.aggregate_score
    if aggregate_drop > 5:
        regressions.append({"code": "AGGREGATE_DROP_FAILURE", "drop": round(aggregate_drop, 4)})
    elif aggregate_drop > 3:
        regressions.append({"code": "AGGREGATE_DROP_WARNING", "drop": round(aggregate_drop, 4)})
    if baseline.hard_gate_status == EvaluationHardGateStatus.PASS and current.hard_gate_status == EvaluationHardGateStatus.FAIL:
        regressions.append({"code": "NEW_HARD_GATE_FAILURE"})
    baseline_by_case = {case.case_id: case for case in baseline_cases}
    for result in current_cases:
        previous = baseline_by_case.get(result.case_id)
        if previous is None:
            continue
        if previous.status.value == "PASSED" and result.status.value != "PASSED":
            regressions.append({"code": "NEW_CASE_FAILURE", "case_id": result.case_id})
        metric_drops = _metric_drops(previous, result)
        regressions.extend(metric_drops)
    return regressions


def _metric_drops(previous: EvaluationCaseResult, current: EvaluationCaseResult) -> list[dict]:
    regressions: list[dict] = []
    previous_scores = {metric.name: metric.score for metric in previous.metric_results}
    for metric in current.metric_results:
        old_score = previous_scores.get(metric.name)
        if old_score is None:
            continue
        drop = old_score - metric.score
        if drop > 10:
            regressions.append({"code": "METRIC_DROP", "case_id": current.case_id, "metric": metric.name.value, "drop": round(drop, 4)})
    return regressions
