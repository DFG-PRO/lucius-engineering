from __future__ import annotations

from enum import StrEnum
import logging
from typing import Any

from pydantic import BaseModel, Field

from lucius.projects.registry_schema import (
    BriefStatus,
    DFGProjectRecord,
    DFGProjectRegistry,
    DFGProjectStatus,
)

logger = logging.getLogger(__name__)


class RegressionViolationType(StrEnum):
    PHASE_REGRESSION = "PHASE_REGRESSION"
    CLOSED_GATE_REOPEN = "CLOSED_GATE_REOPEN"
    STALE_SHA_ASSUMPTION = "STALE_SHA_ASSUMPTION"
    BRIEF_NOT_ENGINEERING_READY = "BRIEF_NOT_ENGINEERING_READY"
    SOURCE_OF_TRUTH_CONFLICT = "SOURCE_OF_TRUTH_CONFLICT"


class RegressionViolation(BaseModel):
    violation_type: RegressionViolationType
    project_id: str
    message: str
    canonical_value: Any = None
    proposed_value: Any = None


class RegressionCheckResult(BaseModel):
    passed: bool
    project_id: str
    violations: list[RegressionViolation] = Field(default_factory=list)


class RegressionGuardValidator:
    """Deterministic validator preventing project state regressions and stale-context execution."""

    def __init__(self, registry: DFGProjectRegistry):
        self.registry = registry

    def validate_proposed_task(
        self,
        project_id: str,
        *,
        proposed_phase: str | None = None,
        proposed_gate: str | None = None,
        assumed_sha: str | None = None,
        is_engineering_mutation: bool = False,
        declared_source_of_truth: str | None = None,
    ) -> RegressionCheckResult:
        violations: list[RegressionViolation] = []
        project = self.registry.get_project(project_id)

        if project is None:
            # If project does not exist in canonical registry, engineering mutation is blocked
            if is_engineering_mutation:
                violations.append(
                    RegressionViolation(
                        violation_type=RegressionViolationType.BRIEF_NOT_ENGINEERING_READY,
                        project_id=project_id,
                        message=f"Project '{project_id}' is not registered in DFG Project Registry; engineering mutations blocked.",
                        canonical_value="REGISTERED_PROJECT",
                        proposed_value="UNREGISTERED",
                    )
                )
            return RegressionCheckResult(passed=len(violations) == 0, project_id=project_id, violations=violations)

        # 1. Closed Gate Reopening Check
        if proposed_gate is not None and proposed_gate != "":
            norm_proposed_gate = proposed_gate.strip().upper()
            norm_closed_gate = project.last_closed_gate.strip().upper()
            if norm_proposed_gate == norm_closed_gate:
                violations.append(
                    RegressionViolation(
                        violation_type=RegressionViolationType.CLOSED_GATE_REOPEN,
                        project_id=project_id,
                        message=f"Proposed task attempts to re-execute closed gate '{norm_closed_gate}'. Canonical gates cannot be reopened without explicit formal review.",
                        canonical_value=norm_closed_gate,
                        proposed_value=norm_proposed_gate,
                    )
                )

        # 2. Phase Regression Check
        if proposed_phase is not None and proposed_phase != "":
            norm_proposed_phase = proposed_phase.strip().upper()
            norm_current_phase = project.current_phase.strip().upper()

            # Handle phase numbering patterns e.g. "Phase 5" vs "Phase 6"
            import re
            m_curr = re.search(r"PHASE[_\s]*([0-9]+)", norm_current_phase)
            m_prop = re.search(r"PHASE[_\s]*([0-9]+)", norm_proposed_phase)
            if m_curr and m_prop:
                curr_num = int(m_curr.group(1))
                prop_num = int(m_prop.group(1))
                if prop_num < curr_num:
                    violations.append(
                        RegressionViolation(
                            violation_type=RegressionViolationType.PHASE_REGRESSION,
                            project_id=project_id,
                            message=f"Proposed phase '{norm_proposed_phase}' regresses behind canonical current phase '{norm_current_phase}'.",
                            canonical_value=norm_current_phase,
                            proposed_value=norm_proposed_phase,
                        )
                    )

        # 3. Stale SHA Assumption Check
        if assumed_sha is not None and assumed_sha != "":
            norm_assumed_sha = assumed_sha.strip().lower()
            if project.last_verified_sha is not None:
                norm_verified_sha = project.last_verified_sha.strip().lower()
                if norm_assumed_sha != norm_verified_sha:
                    violations.append(
                        RegressionViolation(
                            violation_type=RegressionViolationType.STALE_SHA_ASSUMPTION,
                            project_id=project_id,
                            message=f"Proposed task assumes stale commit '{norm_assumed_sha}', but canonical verified HEAD is '{norm_verified_sha}'.",
                            canonical_value=norm_verified_sha,
                            proposed_value=norm_assumed_sha,
                        )
                    )

        # 4. Engineering Readiness Check
        if is_engineering_mutation:
            allowed_brief_statuses = {
                BriefStatus.BRIEF_ENGINEERING_READY,
                BriefStatus.BRIEF_FROZEN_FOR_BUILD,
                BriefStatus.IN_DEVELOPMENT_DO_NOT_REBRIEF,
                BriefStatus.OPERATIONAL_FEATURE_BRIEFS_ONLY,
            }
            if project.brief_status not in allowed_brief_statuses:
                violations.append(
                    RegressionViolation(
                        violation_type=RegressionViolationType.BRIEF_NOT_ENGINEERING_READY,
                        project_id=project_id,
                        message=f"Project '{project_id}' brief status is '{project.brief_status.value}'; code mutations are blocked until development brief reaches engineering readiness.",
                        canonical_value=list(s.value for s in allowed_brief_statuses),
                        proposed_value=project.brief_status.value,
                    )
                )

        # 5. Source of Truth Conflict Check
        if declared_source_of_truth is not None and declared_source_of_truth != "":
            norm_declared = declared_source_of_truth.strip().lower()
            norm_canonical_sot = project.source_of_truth.strip().lower()
            if "prompt" in norm_declared or "chat" in norm_declared or "conversation" in norm_declared:
                violations.append(
                    RegressionViolation(
                        violation_type=RegressionViolationType.SOURCE_OF_TRUTH_CONFLICT,
                        project_id=project_id,
                        message="Conversation context or prompts cannot serve as source of truth against canonical repository records.",
                        canonical_value=project.source_of_truth,
                        proposed_value=declared_source_of_truth,
                    )
                )

        passed = len(violations) == 0
        return RegressionCheckResult(passed=passed, project_id=project_id, violations=violations)
