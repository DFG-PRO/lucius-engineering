from __future__ import annotations

from dataclasses import dataclass


GREEN = "GREEN"
DEGRADED_FIXTURES_MISSING = "DEGRADED_FIXTURES_MISSING"
DEGRADED_TEST_FAILURES = "DEGRADED_TEST_FAILURES"
NO_NEW_REGRESSION = "NO_NEW_REGRESSION"
REGRESSION = "REGRESSION"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SuiteComparison:
    suite_health: str
    change_regression_status: str
    missing_fixtures: list[str]
    baseline_failures: list[str]
    target_failures: list[str]
    new_failures: list[str]

    def as_dict(self) -> dict[str, object]:
        return {
            "suite_health": self.suite_health,
            "change_regression_status": self.change_regression_status,
            "missing_fixtures": self.missing_fixtures,
            "baseline_failures": self.baseline_failures,
            "target_failures": self.target_failures,
            "new_failures": self.new_failures,
        }


def classify_fixture_dependent_suite(
    *,
    baseline_failures: list[str] | None,
    target_failures: list[str],
    missing_fixtures: list[str] | None = None,
) -> SuiteComparison:
    missing = sorted(set(missing_fixtures or []))
    baseline_available = baseline_failures is not None
    baseline = sorted(set(baseline_failures or []))
    target = sorted(set(target_failures))
    new_failures = sorted(set(target) - set(baseline)) if baseline_available else []

    if missing:
        suite_health = DEGRADED_FIXTURES_MISSING
    elif target:
        suite_health = DEGRADED_TEST_FAILURES
    else:
        suite_health = GREEN

    if not baseline_available:
        regression_status = UNKNOWN
    elif new_failures:
        regression_status = REGRESSION
    else:
        regression_status = NO_NEW_REGRESSION

    return SuiteComparison(
        suite_health=suite_health,
        change_regression_status=regression_status,
        missing_fixtures=missing,
        baseline_failures=baseline,
        target_failures=target,
        new_failures=new_failures,
    )
