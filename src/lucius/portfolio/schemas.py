from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

from lucius.domain.enums import (
    DesignCoverage,
    DDBDepth,
    GlobalWorkState,
    ProjectPriority,
    ProjectProgressiveStage,
)


class NormalizedProjectInventoryRecord(BaseModel):
    project_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    project_type: str = Field(default="DFG_INTERNAL")
    repository: str | None = None
    canonical_documentation_location: str | None = None
    canonical_current_sha: str | None = None
    priority: ProjectPriority = Field(default=ProjectPriority.P2_NORMAL)
    portfolio_state: ProjectProgressiveStage = Field(default=ProjectProgressiveStage.STRUCTURED_CONCEPT)
    current_phase: str | None = None
    last_closed_gate: str | None = None
    next_gate: str | None = None
    design_coverage: DesignCoverage = Field(default=DesignCoverage.D1_CONCEPT_DEFINED)
    ddb_status: DDBDepth | None = None
    roadmap_status: str | None = None
    backlog_status: str | None = None
    authority_class: str = Field(default="CLASS_A")
    dependencies: list[str] = Field(default_factory=list)
    resource_requirements: list[str] = Field(default_factory=list)
    research_requirements: list[str] = Field(default_factory=list)
    operator_decisions_required: list[str] = Field(default_factory=list)
    legal_compliance_constraints: list[str] = Field(default_factory=list)
    economic_revenue_relevance: str | None = None
    current_executable_work_source: str | None = None
    documentation_freshness: str = Field(default="VERIFIED")
    canonical_state_confidence: str = Field(default="HIGH")
    has_design_debt: bool = False
    design_debt_reason: str | None = None


class NormalizedWorkPackage(BaseModel):
    work_id: str
    project_id: str
    title: str
    objective: str = Field(default="")
    priority: str = Field(default="P2")
    project_priority: str = Field(default="P2")
    work_priority: str = Field(default="P2")
    work_type: str = Field(default="RESEARCH_OR_ENGINEERING")
    current_state: GlobalWorkState = Field(default=GlobalWorkState.READY)
    readiness: str = Field(default="READY")
    authority_class: str = Field(default="CLASS_B")
    required_capability: str = Field(default="qwen3:8b")
    qualified_resource: str = Field(default="qwen3:8b")
    dependencies: list[str] = Field(default_factory=list)
    provenance_refs: list[str] = Field(default_factory=list)
    expected_output: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    estimated_scope: str | None = None
    economic_relevance: str | None = None
    next_gate_relationship: str | None = None
    preemptibility: str = Field(default="SAFE_AT_CYCLE_BOUNDARY")
    source_project: str = Field(default="darwin-research-engine")
    source_record_id: str | None = None
    dedupe_key: str
