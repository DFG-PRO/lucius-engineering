from __future__ import annotations

import re
from typing import Any

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "while",
    "with",
}

TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]*")
CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def derive_query_terms(
    *,
    task_title: str,
    task_objective: str,
    contract_objective: str,
    acceptance_criteria: list[dict[str, Any]],
    explicit_terms: list[str] | None = None,
) -> list[str]:
    pieces = [task_title, task_objective, contract_objective]
    pieces.extend(str(item.get("statement", "")) for item in acceptance_criteria)
    pieces.extend(explicit_terms or [])
    terms: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        for token in tokenize_terms(piece):
            if token not in seen:
                terms.append(token)
                seen.add(token)
    return terms


def tokenize_terms(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in TOKEN_RE.findall(text):
        normalized = raw.strip(".,:;()[]{}'\"`").lower()
        if not normalized or normalized in STOP_WORDS:
            continue
        tokens.append(normalized)
        if "_" in normalized:
            tokens.extend(part for part in normalized.split("_") if part and part not in STOP_WORDS)
        if "/" in normalized:
            tokens.extend(part for part in normalized.split("/") if part and part not in STOP_WORDS)
        if "." in normalized and not normalized.startswith("."):
            tokens.extend(part for part in normalized.split(".") if part and part not in STOP_WORDS)
        camel_parts = [part.lower() for part in CAMEL_RE.split(raw) if len(part) > 1]
        tokens.extend(part for part in camel_parts if part not in STOP_WORDS)
    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token not in seen:
            deduped.append(token)
            seen.add(token)
    return deduped

