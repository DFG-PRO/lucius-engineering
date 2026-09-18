from __future__ import annotations

from enum import StrEnum
import json
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator


class DFGProjectStatus(StrEnum):
    CONCEPT = "CONCEPT"
    RESEARCHING = "RESEARCHING"
    VALIDATING = "VALIDATING"
    DESIGNING = "DESIGNING"
    ENGINEERING_READY = "ENGINEERING_READY"
    IN_DEVELOPMENT = "IN_DEVELOPMENT"
    VALIDATING_BUILD = "VALIDATING_BUILD"
    OPERATIONAL = "OPERATIONAL"
    MAINTENANCE = "MAINTENANCE"
    PARKED = "PARKED"
    BLOCKED = "BLOCKED"


class BriefStatus(StrEnum):
    BRIEF_MISSING = "BRIEF_MISSING"
    BRIEF_PARTIAL = "BRIEF_PARTIAL"
    BRIEF_RESEARCH_REQUIRED = "BRIEF_RESEARCH_REQUIRED"
    BRIEF_DECISION_REQUIRED = "BRIEF_DECISION_REQUIRED"
    BRIEF_ENGINEERING_READY = "BRIEF_ENGINEERING_READY"
    BRIEF_FROZEN_FOR_BUILD = "BRIEF_FROZEN_FOR_BUILD"
    BRIEF_SUPERSEDED = "BRIEF_SUPERSEDED"
    IN_DEVELOPMENT_DO_NOT_REBRIEF = "IN_DEVELOPMENT_DO_NOT_REBRIEF"
    OPERATIONAL_FEATURE_BRIEFS_ONLY = "OPERATIONAL_FEATURE_BRIEFS_ONLY"
    BUSINESS_VALIDATION_BEFORE_SOFTWARE = "BUSINESS_VALIDATION_BEFORE_SOFTWARE"
    RESEARCH_BEFORE_BRIEF = "RESEARCH_BEFORE_BRIEF"


class AuthorityClass(StrEnum):
    CLASS_A = "CLASS_A"  # Deterministic Read-Only
    CLASS_B = "CLASS_B"  # Bounded Research and Synthesis
    CLASS_C = "CLASS_C"  # Controlled Mutation (Supervised Only)
    CLASS_D = "CLASS_D"  # Binding or External Action
    CLASS_E = "CLASS_E"  # Forbidden in Travel Mode


class DFGProjectRecord(BaseModel):
    project_id: str = Field(..., description="Unique slugified identifier, e.g. lucius-engineering")
    display_name: str
    project_type: str = Field(..., description="DFG_INTERNAL, CLIENT, R_AND_D, or COMMERCIAL")
    canonical_repo: str | None = None
    canonical_docs: list[str] = Field(default_factory=list)
    owner_engine: str = Field(..., description="Lucius, Darwin, Billy, Andy, Alfred, or External")
    status: DFGProjectStatus
    current_phase: str
    current_subphase: str
    last_closed_gate: str
    active_work: str
    next_canonical_gate: str
    dependencies: list[str] = Field(default_factory=list)
    priority: str = Field(default="NORMAL")
    brief_status: BriefStatus
    development_status: str
    authority_profile: list[AuthorityClass] = Field(default_factory=lambda: [AuthorityClass.CLASS_A])
    monetization_role: str = "INTERNAL_ENABLER"
    last_verified_sha: str | None = None
    last_verified_at: str | None = None
    source_of_truth: str = Field(..., description="Canonical repository path, documentation, or backlog")
    notes: str = ""

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, v: str) -> str:
        slug = re.sub(r"[^a-z0-9_-]+", "", v.strip().lower())
        if slug != v.strip().lower():
            raise ValueError(f"project_id '{v}' must be alphanumeric with dashes/underscores")
        return slug

    @field_validator("last_verified_sha")
    @classmethod
    def validate_sha(cls, v: str | None) -> str | None:
        if v is not None:
            if not re.match(r"^[0-9a-fA-F]{40}$", v.strip()):
                raise ValueError(f"last_verified_sha '{v}' must be a full 40-character hexadecimal SHA")
            return v.strip().lower()
        return None

    @field_validator("canonical_repo")
    @classmethod
    def validate_repo_path(cls, v: str | None) -> str | None:
        if v is not None and v != "":
            p = Path(v)
            if not p.is_absolute():
                raise ValueError(f"canonical_repo '{v}' must be an absolute path")
        return v


class DFGProjectRegistry(BaseModel):
    version: str = "1.0.0"
    updated_at: str
    projects: dict[str, DFGProjectRecord] = Field(default_factory=dict)

    def get_project(self, project_id: str) -> DFGProjectRecord | None:
        return self.projects.get(project_id.strip().lower())

    def require_project(self, project_id: str) -> DFGProjectRecord:
        proj = self.get_project(project_id)
        if proj is None:
            raise KeyError(f"Project '{project_id}' not found in canonical DFG Project Registry")
        return proj

    def save_json(self, path: Path | str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load_json(cls, path: Path | str) -> DFGProjectRegistry:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Project registry file not found: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls.model_validate(data)
