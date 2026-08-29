from __future__ import annotations

from lucius.domain.enums import (
    Actor,
    KnowledgeFirewallClassification,
    KnowledgeScope,
    LearningCandidateStatus,
    ProjectType,
    SanitizationStatus,
)
from lucius.persistence.orm import LearningCandidateORM, ProjectORM


AUTHORIZED_GLOBAL_PROMOTERS = {Actor.HUMAN.value, Actor.SYSTEM.value}


def classify_source(project: ProjectORM | None, proposed_scope: KnowledgeScope) -> KnowledgeFirewallClassification:
    if project and project.project_type == ProjectType.CLIENT.value:
        return KnowledgeFirewallClassification.PRIVATE
    if proposed_scope == KnowledgeScope.CLIENT:
        return KnowledgeFirewallClassification.PRIVATE
    if proposed_scope == KnowledgeScope.PROJECT:
        return KnowledgeFirewallClassification.ABSTRACTABLE
    return KnowledgeFirewallClassification.GLOBAL_SAFE


def default_sanitization(
    classification: KnowledgeFirewallClassification,
    proposed_scope: KnowledgeScope,
) -> SanitizationStatus:
    if classification == KnowledgeFirewallClassification.PRIVATE and proposed_scope == KnowledgeScope.GLOBAL:
        return SanitizationStatus.REQUIRED
    if classification == KnowledgeFirewallClassification.PRIVATE:
        return SanitizationStatus.REQUIRED
    return SanitizationStatus.NOT_REQUIRED


def can_promote_candidate(
    candidate: LearningCandidateORM,
    *,
    target_scope: KnowledgeScope,
    actor: Actor,
) -> tuple[bool, str | None]:
    if actor.value not in AUTHORIZED_GLOBAL_PROMOTERS and target_scope == KnowledgeScope.GLOBAL:
        return False, "Autonomous global promotion is blocked."
    if candidate.status != LearningCandidateStatus.VALIDATED.value:
        return False, "LearningCandidate must be VALIDATED before promotion."
    if not candidate.evidence_ids and not candidate.source_memory_ids and not candidate.source_document_refs:
        return False, "Promotion requires provenance."
    if target_scope == KnowledgeScope.GLOBAL:
        if candidate.source_classification == KnowledgeFirewallClassification.PRIVATE.value:
            return False, "PRIVATE source classification cannot be promoted directly to GLOBAL."
        if candidate.sanitization_status not in {SanitizationStatus.NOT_REQUIRED.value, SanitizationStatus.SANITIZED.value}:
            return False, "GLOBAL promotion requires allowed sanitization status."
    return True, None

