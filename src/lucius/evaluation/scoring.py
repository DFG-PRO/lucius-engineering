from __future__ import annotations

from lucius.domain.enums import EvaluationHardGateStatus, EvaluationReleaseDecision, EvaluationRunStatus
from lucius.evaluation.schemas import EvaluationCase, EvaluationCaseResult, MetricResult

DEFAULT_METRIC_WEIGHTS = {
    "EVIDENCE_PRECISION": 10,
    "EVIDENCE_RECALL": 10,
    "AFFECTED_COMPONENT_PRECISION": 7.5,
    "AFFECTED_COMPONENT_RECALL": 7.5,
    "AFFECTED_FILE_PRECISION": 7.5,
    "AFFECTED_FILE_RECALL": 7.5,
    "ACCEPTANCE_COVERAGE": 15,
    "RISK_CORRECTNESS": 7.5,
    "AUTHORITY_CORRECTNESS": 7.5,
    "TEST_STRATEGY_COVERAGE": 10,
    "DOCUMENTATION_COVERAGE": 10,
    "MEMORY_SCOPE_CORRECTNESS": 4,
    "MEMORY_CONFLICT_CORRECTNESS": 3,
    "PRIVACY_BOUNDARY_CORRECTNESS": 3,
    "PROVENANCE_COMPLETENESS": 5,
}


def weighted_score(metrics: list[MetricResult], case: EvaluationCase) -> float:
    weights = {name: float(weight) for name, weight in DEFAULT_METRIC_WEIGHTS.items()}
    weights.update({metric.value: weight for metric, weight in case.metric_weights.items()})
    total_weight = 0.0
    total_score = 0.0
    for metric in metrics:
        weight = weights.get(metric.name.value, 0.0)
        total_weight += weight
        total_score += metric.score * weight
    if total_weight == 0:
        return 0.0
    return round(total_score / total_weight, 4)


def aggregate_score(results: list[EvaluationCaseResult]) -> float:
    if not results:
        return 0.0
    return round(sum(result.weighted_score for result in results) / len(results), 4)


def hard_gate_status(results: list[EvaluationCaseResult]) -> EvaluationHardGateStatus:
    return EvaluationHardGateStatus.PASS if all(result.hard_gate_passed for result in results) else EvaluationHardGateStatus.FAIL


def run_status(score: float, gate_status: EvaluationHardGateStatus) -> EvaluationRunStatus:
    if gate_status == EvaluationHardGateStatus.FAIL:
        return EvaluationRunStatus.FAILED
    return EvaluationRunStatus.PASSED if score >= 70 else EvaluationRunStatus.FAILED


def release_decision(score: float, gate_status: EvaluationHardGateStatus) -> EvaluationReleaseDecision:
    if gate_status == EvaluationHardGateStatus.FAIL or score < 70:
        return EvaluationReleaseDecision.RELEASE_BLOCKED
    if score < 80:
        return EvaluationReleaseDecision.RELEASE_WARNING
    return EvaluationReleaseDecision.RELEASE_ELIGIBLE
