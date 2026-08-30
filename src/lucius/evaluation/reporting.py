from __future__ import annotations

from lucius.evaluation.schemas import EvaluationCaseResult, EvaluationRun


def machine_report(run: EvaluationRun, cases: list[EvaluationCaseResult]) -> dict:
    weakest = sorted(
        [
            {"case_id": result.case_id, "metric": metric.name.value, "score": metric.score}
            for result in cases
            for metric in result.metric_results
        ],
        key=lambda item: (item["score"], item["case_id"], item["metric"]),
    )[:5]
    return {
        "run_id": run.id,
        "suite_id": run.suite_id,
        "suite_version": run.suite_version,
        "status": run.status.value,
        "score": run.aggregate_score,
        "hard_gate_status": run.hard_gate_status.value,
        "release_decision": run.release_decision.value if run.release_decision else None,
        "target_commit_sha": run.target_commit_sha,
        "target_dirty": run.target_dirty,
        "config_hash": run.config_hash,
        "regressions": run.regressions,
        "weakest_metrics": weakest,
        "cases": [
            {
                "case_id": result.case_id,
                "case_version": result.case_version,
                "status": result.status.value,
                "score": result.weighted_score,
                "hard_gate_passed": result.hard_gate_passed,
                "hard_gate_failures": [gate.model_dump(mode="json") for gate in result.hard_gate_failures],
                "duration_ms": result.duration_ms,
            }
            for result in cases
        ],
    }


def markdown_report(run: EvaluationRun, cases: list[EvaluationCaseResult]) -> str:
    lines = [
        "# LUCIUS CORE BENCH v0.1",
        "",
        f"Score: {run.aggregate_score:.2f}" if run.aggregate_score is not None else "Score: n/a",
        f"Hard gates: {run.hard_gate_status.value}",
        f"Release: {run.release_decision.value if run.release_decision else 'n/a'}",
        f"Dirty run: {run.target_dirty}",
        "",
        "## Cases",
        "",
        "| Case | Score | Status | Gates | Duration ms |",
        "| --- | ---: | --- | --- | ---: |",
    ]
    for result in cases:
        gates = "PASS" if result.hard_gate_passed else "FAIL"
        lines.append(f"| {result.case_id} | {result.weighted_score:.2f} | {result.status.value} | {gates} | {result.duration_ms} |")
    lines.extend(["", "## Regressions", ""])
    if run.regressions:
        lines.extend(f"- {item['code']}" for item in run.regressions)
    else:
        lines.append("None")
    weakest = machine_report(run, cases)["weakest_metrics"]
    lines.extend(["", "## Weakest Metrics", ""])
    if weakest:
        lines.extend(f"- {item['case_id']} {item['metric']}: {item['score']:.2f}" for item in weakest)
    else:
        lines.append("None")
    return "\n".join(lines) + "\n"
