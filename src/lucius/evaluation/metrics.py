from __future__ import annotations

from collections.abc import Iterable


def normalize_set(values: Iterable[str]) -> set[str]:
    return {str(value).strip().lower() for value in values if str(value).strip()}


def precision(expected: Iterable[str], actual: Iterable[str]) -> tuple[float, dict]:
    expected_set = normalize_set(expected)
    actual_set = normalize_set(actual)
    if not actual_set:
        score = 100.0 if not expected_set else 0.0
        return score, {"true_positives": [], "false_positives": [], "empty_actual": True}
    true_positives = sorted(expected_set & actual_set)
    false_positives = sorted(actual_set - expected_set)
    score = len(true_positives) / len(actual_set) * 100
    return score, {"true_positives": true_positives, "false_positives": false_positives}


def recall(expected: Iterable[str], actual: Iterable[str]) -> tuple[float, dict]:
    expected_set = normalize_set(expected)
    actual_set = normalize_set(actual)
    if not expected_set:
        score = 100.0 if not actual_set else 0.0
        return score, {"missing": [], "empty_expected": True}
    true_positives = sorted(expected_set & actual_set)
    missing = sorted(expected_set - actual_set)
    score = len(true_positives) / len(expected_set) * 100
    return score, {"true_positives": true_positives, "missing": missing}


def coverage_score(expected: Iterable[str], actual: Iterable[str]) -> tuple[float, dict]:
    return recall(expected, actual)
