from __future__ import annotations

from lucius.domain.enums import KnowledgeScope, ValidationStatus


def validate_confidence(confidence: float) -> None:
    if confidence < 0.0 or confidence > 1.0:
        raise ValueError("confidence must be between 0.0 and 1.0")


def has_provenance(
    *,
    source_reference: str | None = None,
    source_task_id: str | None = None,
    source_run_id: str | None = None,
    source_evidence_ids: list[str] | None = None,
) -> bool:
    return bool(source_reference or source_task_id or source_run_id or source_evidence_ids)


def validate_memory_creation(
    *,
    scope: KnowledgeScope,
    project_id: str | None,
    validation_status: ValidationStatus,
    confidence: float,
    source_reference: str | None,
    source_task_id: str | None,
    source_run_id: str | None,
    source_evidence_ids: list[str],
) -> None:
    validate_confidence(confidence)
    if scope in {KnowledgeScope.PROJECT, KnowledgeScope.CLIENT} and not project_id:
        raise ValueError(f"{scope.value} memory requires project_id")
    provenance = has_provenance(
        source_reference=source_reference,
        source_task_id=source_task_id,
        source_run_id=source_run_id,
        source_evidence_ids=source_evidence_ids,
    )
    if validation_status == ValidationStatus.VALIDATED and not provenance:
        raise ValueError("VALIDATED memory requires provenance")
    if scope == KnowledgeScope.GLOBAL and validation_status == ValidationStatus.VALIDATED and len(source_evidence_ids) == 0:
        raise ValueError("VALIDATED GLOBAL memory requires evidence provenance")

