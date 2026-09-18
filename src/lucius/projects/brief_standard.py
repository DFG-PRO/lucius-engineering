from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from lucius.projects.registry_schema import AuthorityClass, BriefStatus


class DDBTier(StrEnum):
    DDB_LITE = "DDB_LITE"          # For small internal tools, maintenance tasks, or isolated subphases
    DDB_STANDARD = "DDB_STANDARD"  # For standard features, commercial services, or system adapters
    DDB_FULL = "DDB_FULL"          # For major cross-project systems, financial pipelines, or client contracts


class DecisionOwner(StrEnum):
    OPERATOR_DECISION_REQUIRED = "OPERATOR_DECISION_REQUIRED"
    CLIENT_DECISION_REQUIRED = "CLIENT_DECISION_REQUIRED"
    DARWIN_RESEARCH_REQUIRED = "DARWIN_RESEARCH_REQUIRED"
    ANDY_FINANCIAL_REVIEW_REQUIRED = "ANDY_FINANCIAL_REVIEW_REQUIRED"
    LEGAL_COMPLIANCE_REVIEW_REQUIRED = "LEGAL_COMPLIANCE_REVIEW_REQUIRED"
    LUCIUS_ARCHITECTURE_DECISION = "LUCIUS_ARCHITECTURE_DECISION"
    ENGINEERING_DISCOVERY_REQUIRED = "ENGINEERING_DISCOVERY_REQUIRED"


class UnresolvedQuestion(BaseModel):
    id: str
    question: str
    owner: DecisionOwner
    is_critical_for_build: bool = True
    resolution: str | None = None


class DDBIdentity(BaseModel):
    project_id: str
    feature_id: str
    version: str = "1.0.0"
    owner: str
    is_external_client: bool = False
    priority: str = "NORMAL"
    tier: DDBTier = DDBTier.DDB_STANDARD


class DDBScope(BaseModel):
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    future_scope: list[str] = Field(default_factory=list)


class DDBAuthority(BaseModel):
    required_authority: AuthorityClass
    unattended_eligible: bool = False
    forbidden_actions: list[str] = Field(default_factory=list)


class DevelopmentBrief(BaseModel):
    """Canonical DFG Development Brief (DDB v1) schema."""
    identity: DDBIdentity
    problem_statement: str
    desired_outcome: str
    users_and_actors: list[str] = Field(default_factory=list)
    scope: DDBScope
    functional_requirements: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    architecture_constraints: list[str] = Field(default_factory=list)
    authority: DDBAuthority
    unresolved_questions: list[UnresolvedQuestion] = Field(default_factory=list)
    status: BriefStatus = BriefStatus.BRIEF_PARTIAL

    # Optional fields for STANDARD / FULL
    economics_notes: str | None = None
    security_legal_notes: str | None = None
    data_persistence_notes: str | None = None


class BriefReadinessEvaluation(BaseModel):
    brief_status: BriefStatus
    is_engineering_ready: bool
    missing_requirements: list[str] = Field(default_factory=list)
    unresolved_critical_questions: list[UnresolvedQuestion] = Field(default_factory=list)


class DevelopmentBriefValidator:
    """Deterministic readiness evaluator for DFG Development Briefs."""

    @staticmethod
    def evaluate_readiness(brief: DevelopmentBrief) -> BriefReadinessEvaluation:
        missing = []
        unresolved_crit = []

        if not brief.problem_statement.strip():
            missing.append("Problem statement is missing or empty")

        if not brief.desired_outcome.strip():
            missing.append("Desired measurable outcome is missing or empty")

        if not brief.scope.in_scope:
            missing.append("In-scope definition is empty")

        if not brief.functional_requirements:
            missing.append("Functional requirements list is empty")

        if not brief.acceptance_criteria:
            missing.append("Acceptance criteria list is empty")

        if brief.identity.tier in (DDBTier.DDB_STANDARD, DDBTier.DDB_FULL):
            if not brief.users_and_actors:
                missing.append("Users and actors list is empty for STANDARD/FULL tier")

        # Evaluate unresolved questions
        for q in brief.unresolved_questions:
            if q.is_critical_for_build and (q.resolution is None or not q.resolution.strip()):
                unresolved_crit.append(q)

        if unresolved_crit:
            # Determine primary blocking category
            has_operator = any(q.owner == DecisionOwner.OPERATOR_DECISION_REQUIRED for q in unresolved_crit)
            has_client = any(q.owner == DecisionOwner.CLIENT_DECISION_REQUIRED for q in unresolved_crit)
            has_research = any(q.owner == DecisionOwner.DARWIN_RESEARCH_REQUIRED for q in unresolved_crit)

            if has_operator or has_client:
                status = BriefStatus.BRIEF_DECISION_REQUIRED
            elif has_research:
                status = BriefStatus.BRIEF_RESEARCH_REQUIRED
            else:
                status = BriefStatus.BRIEF_PARTIAL

            return BriefReadinessEvaluation(
                brief_status=status,
                is_engineering_ready=False,
                missing_requirements=missing,
                unresolved_critical_questions=unresolved_crit,
            )

        if missing:
            return BriefReadinessEvaluation(
                brief_status=BriefStatus.BRIEF_PARTIAL,
                is_engineering_ready=False,
                missing_requirements=missing,
                unresolved_critical_questions=[],
            )

        return BriefReadinessEvaluation(
            brief_status=BriefStatus.BRIEF_ENGINEERING_READY,
            is_engineering_ready=True,
            missing_requirements=[],
            unresolved_critical_questions=[],
        )
