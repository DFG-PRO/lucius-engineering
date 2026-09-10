from __future__ import annotations

from pathlib import Path

import pytest

from lucius.persistence.orm import AuditEventORM
from lucius.runtime.adapters import ScriptedExecutionAdapter
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.schema_constraints import SKELETON_METADATA_KEY
from lucius.runtime.schemas import (
    RuntimeExecutionContext,
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    RuntimeProviderStatus,
    RuntimeRetryability,
)


def test_router_selects_single_eligible_provider_and_records_identity(session):
    provider = ScriptedExecutionAdapter(
        provider_id="provider-a",
        provider_version="2026.09",
        reliability_score=80,
    )

    result = _router(session, [provider]).execute(_context())

    assert result.outcome == "COMPLETED"
    assert result.provider_id == "provider-a"
    assert result.provider_version == "2026.09"
    assert result.model_id == "scripted-runtime-model"
    assert result.routing_decision_id is not None
    assert _audit_count(session, "MODEL_EXECUTION_ROUTING_DECISION") == 1
    assert _audit_count(session, "MODEL_EXECUTION_PROVIDER_RESULT_NORMALIZED") == 1


def test_router_deterministically_selects_highest_reliability_eligible_provider(session):
    lower = ScriptedExecutionAdapter(provider_id="provider-lower", reliability_score=10)
    higher = ScriptedExecutionAdapter(provider_id="provider-higher", reliability_score=90)

    result = _router(session, [lower, higher]).execute(_context())

    assert result.provider_id == "provider-higher"


def test_router_excludes_capability_mismatch(session):
    provider = ScriptedExecutionAdapter(provider_id="provider-no-docs", capabilities=["code_modification"])

    result = _router(session, [provider]).execute(
        _context(queue_item={"required_capabilities": ["documentation_update"]})
    )

    assert result.outcome == "FAILED"
    assert result.blocker_category == "NO_ELIGIBLE_PROVIDER"
    assert result.provider_id is None
    assert _latest_audit(session, "MODEL_EXECUTION_NO_ELIGIBLE_PROVIDER").result == "FAILED_CLOSED"


def test_router_excludes_unavailable_provider(session):
    provider = ScriptedExecutionAdapter(provider_id="provider-down", available=False)

    result = _router(session, [provider]).execute(_context())

    assert result.outcome == "FAILED"
    assert "No registered runtime execution provider" in result.blocking_reason


def test_router_excludes_policy_project_repository_tool_and_context_mismatches(session):
    provider = ScriptedExecutionAdapter(provider_id="provider-policy", supported_tools=["pytest"])
    provider.registration = provider.registration.model_copy(
        update={
            "allowed_project_ids": ["OTHER_PROJECT"],
            "allowed_repository_ids": ["OTHER_REPO"],
            "max_context_tokens": 500,
        }
    )

    result = _router(session, [provider]).execute(
        _context(
            queue_item={
                "required_capabilities": ["code_modification"],
                "tool_requirements": ["pytest", "git"],
                "context_limits": {"max_context_tokens": 1000},
            }
        )
    )

    assert result.outcome == "FAILED"
    candidates = _latest_audit(session, "MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED").event_metadata["candidates"]
    assert candidates[0]["eligible"] is False
    assert "project not allowed" in candidates[0]["reasons"]


def test_router_retries_retryable_failure_boundedly(session):
    provider = SequencedProvider(
        "provider-retry",
        [
            {"status": "FAILED", "retryability": RuntimeRetryability.RETRYABLE, "failure_class": "TRANSIENT"},
            {"status": "COMPLETED", "completed_substeps": ["retry succeeded"]},
        ],
    )

    result = _router(session, [provider], max_provider_retries=1).execute(_context())

    assert result.outcome == "COMPLETED"
    assert result.retries == 1
    assert provider.calls == 2
    assert _audit_count(session, "MODEL_EXECUTION_PROVIDER_RETRY_ATTEMPTED") == 1


def test_router_does_not_retry_non_retryable_failure(session):
    provider = SequencedProvider(
        "provider-no-retry",
        [{"status": "FAILED", "retryability": RuntimeRetryability.NON_RETRYABLE, "failure_class": "POLICY"}],
    )

    result = _router(session, [provider], max_provider_retries=3).execute(_context())

    assert result.outcome == "FAILED"
    assert result.retries == 0
    assert provider.calls == 1
    assert _audit_count(session, "MODEL_EXECUTION_PROVIDER_RETRY_ATTEMPTED") == 0


def test_router_failover_uses_next_eligible_provider_after_qualifying_failure(session):
    failing = SequencedProvider(
        "provider-fails",
        [{"status": "FAILED", "retryability": RuntimeRetryability.FAILOVERABLE, "failure_class": "PROVIDER_DOWN"}],
        reliability_score=90,
    )
    fallback = SequencedProvider(
        "provider-fallback",
        [{"status": "COMPLETED", "completed_substeps": ["fallback succeeded"]}],
        reliability_score=10,
    )

    result = _router(session, [failing, fallback], max_failovers=1).execute(_context())

    assert result.outcome == "COMPLETED"
    assert result.provider_id == "provider-fallback"
    assert result.fallback_used is True
    assert result.failover_from_provider_id == "provider-fails"
    assert _audit_count(session, "MODEL_EXECUTION_PROVIDER_FAILOVER_ATTEMPTED") == 1


def test_router_failover_exhaustion_fails_closed(session):
    failing = SequencedProvider(
        "provider-fails",
        [{"status": "FAILED", "retryability": RuntimeRetryability.FAILOVERABLE, "failure_class": "PROVIDER_DOWN"}],
    )

    result = _router(session, [failing], max_failovers=1).execute(_context())

    assert result.outcome == "FAILED"
    assert result.provider_id == "provider-fails"
    assert _audit_count(session, "MODEL_EXECUTION_ROUTING_COMPLETED") == 1


def test_router_normalizes_usage_cost_latency_fields(session):
    provider = SequencedProvider(
        "provider-metered",
        [
            {
                "status": "COMPLETED",
                "latency_ms": 42,
                "input_tokens": 10,
                "output_tokens": 20,
                "total_tokens": 30,
                "estimated_cost": 0.001,
                "cost_currency": "USD",
            }
        ],
    )

    result = _router(session, [provider]).execute(_context())

    assert result.latency_ms == 42
    assert result.input_tokens == 10
    assert result.output_tokens == 20
    assert result.total_tokens == 30
    assert result.estimated_cost == 0.001
    normalized = _latest_audit(session, "MODEL_EXECUTION_PROVIDER_RESULT_NORMALIZED")
    assert normalized.event_metadata["provider_result"]["latency_ms"] == 42
    assert normalized.event_metadata["provider_result"]["estimated_cost"] == 0.001


def test_router_rejects_provider_completion_without_verification_handoff(session):
    provider = SequencedProvider(
        "provider-no-verification",
        [{"status": "COMPLETED", "verification": []}],
    )

    result = _router(session, [provider]).execute(_context())

    assert result.outcome == "FAILED"
    assert result.failure_class == "MISSING_VERIFICATION_HANDOFF"
    assert result.blocking_reason == "Provider reported completion without verification handoff evidence."


def test_router_request_is_provider_neutral_and_contains_release_refs(session):
    provider = SequencedProvider("provider-neutral", [{"status": "COMPLETED"}])
    context = _context()

    result = _router(session, [provider]).execute(context)

    request = provider.requests[0]
    assert result.outcome == "COMPLETED"
    assert request.workflow_id == context.workflow_id
    assert request.plan_id == context.plan_id
    assert request.plan_freeze_id == context.plan_freeze_id
    assert context.plan_freeze_id in request.release_evidence_refs
    assert not hasattr(request, "session")


def test_router_fails_evidence_sensitive_quantitative_output_without_claim_typing(session, tmp_path):
    report = tmp_path / "protocol.md"
    report.write_text("- Win Rate: 55%\n", encoding="utf-8")
    provider = SequencedProvider(
        "provider-claims",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "protocol.md"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(
            worktree_path=tmp_path,
            title="Create OOS walk-forward protocol evidence",
            queue_item={"context_limits": {"evidence_sensitive": True}},
        )
    )

    assert result.outcome == "FAILED"
    assert result.failure_class == "UNSUPPORTED_QUANTITATIVE_CLAIM"
    assert result.provider_error_metadata["quality_issues"][0]["reason"] == (
        "QUANTITATIVE_CLAIM_MISSING_CLASSIFICATION"
    )


def test_router_fails_evidence_sensitive_fact_without_evidence_source(session, tmp_path):
    report = tmp_path / "protocol.md"
    report.write_text("- FACT: Win Rate 55% was observed.\n", encoding="utf-8")
    provider = SequencedProvider(
        "provider-claims",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "protocol.md"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(
            worktree_path=tmp_path,
            title="Create OOS walk-forward protocol evidence",
            queue_item={"context_limits": {"evidence_sensitive": True}},
        )
    )

    assert result.outcome == "FAILED"
    assert result.failure_class == "UNSUPPORTED_QUANTITATIVE_CLAIM"
    assert result.provider_error_metadata["quality_issues"][0]["reason"] == (
        "FACT_OR_DERIVED_VALUE_MISSING_EVIDENCE_REFERENCE"
    )


def test_router_accepts_evidence_sensitive_quantitative_output_with_safe_claim_typing(session, tmp_path):
    report = tmp_path / "protocol.md"
    report.write_text(
        "\n".join(
            [
                "- UNKNOWN: Win Rate 55% is NOT_ESTABLISHED.",
                "- PROPOSED_PARAMETER: Minimum trade count threshold of 20 trades requires owner review before use.",
                "- FACT: Trading Dashboard commit 0363221ea126a7ee3f7fc971b3ae408be7127db8 was inspected as read-only evidence source.",
            ]
        ),
        encoding="utf-8",
    )
    provider = SequencedProvider(
        "provider-claims",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "protocol.md"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(
            worktree_path=tmp_path,
            title="Create OOS walk-forward protocol evidence",
            queue_item={"context_limits": {"evidence_sensitive": True}},
        )
    )

    assert result.outcome == "COMPLETED"


def test_router_accepts_provenance_identifiers_without_claim_typing(session, tmp_path):
    report = tmp_path / "protocol.md"
    report.write_text(
        "\n".join(
            [
                "- Trading Dashboard commit 0363221ea126a7ee3f7fc971b3ae408be7127db8 was inspected read-only.",
                "- Recovery audit LAUDIT_004609 links workflow LWORK_000110, task LTASK_000155, plan LPLAN_000118, and freeze LFREEZE_000118.",
                "- Manifest content hash d41d8cd98f00b204e9800998ecf8427e was recorded.",
                "- Repository identifier repo 12345 was used only as provenance.",
                "- Schema version 2 is an identifier, not a result metric.",
            ]
        ),
        encoding="utf-8",
    )
    provider = SequencedProvider(
        "provider-identifiers",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "protocol.md"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(
            worktree_path=tmp_path,
            title="Create OOS walk-forward protocol evidence",
            queue_item={"context_limits": {"evidence_sensitive": True}},
        )
    )

    assert result.outcome == "COMPLETED"


@pytest.mark.parametrize(
    "line",
    [
        "- Win rate 55%",
        "- Required capital $2,000",
        "- Leverage 10x",
        "- Minimum trade count threshold 20 trades",
        "- Walk-forward window 60 days",
    ],
)
def test_router_still_fails_unclassified_financial_or_research_quantities(session, tmp_path, line):
    report = tmp_path / "protocol.md"
    report.write_text(f"{line}\n", encoding="utf-8")
    provider = SequencedProvider(
        "provider-unsupported-quantity",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "protocol.md"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(
            worktree_path=tmp_path,
            title="Create OOS walk-forward protocol evidence",
            queue_item={"context_limits": {"evidence_sensitive": True}},
        )
    )

    assert result.outcome == "FAILED"
    assert result.failure_class == "UNSUPPORTED_QUANTITATIVE_CLAIM"
    assert result.provider_error_metadata["quality_issues"][0]["reason"] == (
        "QUANTITATIVE_CLAIM_MISSING_CLASSIFICATION"
    )


def test_router_supplies_schema_constrained_skeleton_to_provider(session, tmp_path):
    report = tmp_path / "readiness.md"
    report.write_text(_schema_valid_conservative_content(), encoding="utf-8")
    provider = SequencedProvider(
        "provider-schema",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "readiness.md"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(worktree_path=tmp_path, queue_item=_schema_queue_item("readiness.md"))
    )

    request = provider.requests[0]
    assert result.outcome == "COMPLETED"
    assert "readiness.md" in request.metadata[SKELETON_METADATA_KEY]
    assert "## Current Verified Facts" in request.metadata[SKELETON_METADATA_KEY]
    assert "DERIVED_VALUE" in request.metadata[SKELETON_METADATA_KEY]


def test_router_structurally_repairs_missing_required_category_without_inventing_content(session, tmp_path):
    report = tmp_path / "readiness.md"
    report.write_text(
        _schema_valid_conservative_content().replace(
            "- DERIVED_VALUE: NOT_ESTABLISHED; no derived values were supplied by verified local evidence.\n",
            "",
        ),
        encoding="utf-8",
    )
    provider = SequencedProvider(
        "provider-schema-repair",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "readiness.md"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(worktree_path=tmp_path, queue_item=_schema_queue_item("readiness.md"))
    )

    repaired = report.read_text(encoding="utf-8")
    assert result.outcome == "COMPLETED"
    assert "DERIVED_VALUE: NOT_ESTABLISHED" in repaired
    assert "provider did not supply verified content" in repaired
    assert result.verification_handoff_metadata["schema_constraint"]["status"] == "PASS_WITH_STRUCTURAL_REPAIR"


def test_router_fails_missing_structure_when_safe_repair_is_not_allowed(session, tmp_path):
    report = tmp_path / "readiness.md"
    report.write_text("- FACT: evidence source exists.\n", encoding="utf-8")
    provider = SequencedProvider(
        "provider-schema-missing",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "readiness.md"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(
            worktree_path=tmp_path,
            queue_item=_schema_queue_item("readiness.md", allow_safe_structural_repair=False),
        )
    )

    assert result.outcome == "FAILED"
    assert result.failure_class == "SCHEMA_CONSTRAINT_VALIDATION_FAILED"
    assert result.provider_error_metadata["schema_constraint_issues"][0]["reason"] == "MISSING_REQUIRED_STRUCTURE"


def test_router_schema_constrained_output_still_fails_unsupported_numeric_claim(session, tmp_path):
    report = tmp_path / "readiness.md"
    report.write_text(_schema_valid_conservative_content() + "\n- Win rate 55%\n", encoding="utf-8")
    provider = SequencedProvider(
        "provider-schema-quantity",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "readiness.md"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(worktree_path=tmp_path, queue_item=_schema_queue_item("readiness.md"))
    )

    assert result.outcome == "FAILED"
    assert result.failure_class == "UNSUPPORTED_QUANTITATIVE_CLAIM"


def test_router_schema_constrained_output_fails_fabricated_evidence(session, tmp_path):
    report = tmp_path / "readiness.md"
    report.write_text(
        _schema_valid_conservative_content() + "\n- FACT: verified OOS result exists.\n",
        encoding="utf-8",
    )
    provider = SequencedProvider(
        "provider-schema-fabrication",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "readiness.md"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(worktree_path=tmp_path, queue_item=_schema_queue_item("readiness.md"))
    )

    assert result.outcome == "FAILED"
    assert result.failure_class == "SCHEMA_CONSTRAINT_VALIDATION_FAILED"
    assert result.provider_error_metadata["schema_constraint_issues"][0]["reason"] == (
        "FORBIDDEN_SCHEMA_CONSTRAINED_CONTENT"
    )


def test_router_schema_constrained_conservative_output_passes_and_records_provider_identity(session, tmp_path):
    report = tmp_path / "readiness.md"
    report.write_text(_schema_valid_conservative_content(), encoding="utf-8")
    provider = SequencedProvider(
        "provider-schema-valid",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "readiness.md"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(worktree_path=tmp_path, queue_item=_schema_queue_item("readiness.md"))
    )

    assert result.outcome == "COMPLETED"
    assert result.provider_id == "provider-schema-valid"
    assert result.model_id == "scripted-runtime-model"
    assert result.verification_handoff_metadata["schema_constraint"]["status"] == "PASS"


def _router(session, providers, *, max_provider_retries: int = 0, max_failovers: int = 0) -> ModelExecutionRouter:
    return ModelExecutionRouter(
        session,
        registry=RuntimeProviderRegistry(providers),
        max_provider_retries=max_provider_retries,
        max_failovers=max_failovers,
    )


def _schema_queue_item(
    path: str,
    *,
    allow_safe_structural_repair: bool = True,
) -> dict:
    return {
        "context_limits": {
            "evidence_sensitive": True,
            "schema_constraint": {
                "files": [
                    {
                        "path": path,
                        "required_sections": [
                            "## Current Verified Facts",
                            "## Evidence Required Before Controlled Paper Validation",
                            "## Explicit Unknowns",
                            "## Disallowed Conclusions",
                            "## Final Decision",
                        ],
                        "required_labels": [
                            "FACT",
                            "DERIVED_VALUE",
                            "ASSUMPTION",
                            "PROPOSED_PARAMETER",
                            "UNKNOWN",
                            "REQUIRES_VALIDATION",
                            "INSUFFICIENT_EVIDENCE",
                        ],
                        "forbidden_patterns": [
                            r"verified\s+OOS\s+result\s+exists",
                            r"ready\s+for\s+controlled\s+paper\s+validation",
                        ],
                        "allow_safe_structural_repair": allow_safe_structural_repair,
                    }
                ]
            },
        }
    }


def _schema_valid_conservative_content() -> str:
    return "\n".join(
        [
            "# Paper Validation Readiness",
            "",
            "## Current Verified Facts",
            "- FACT: Darwin isolated workspace evidence source exists.",
            "- DERIVED_VALUE: NOT_ESTABLISHED; no derived values were supplied by verified local evidence.",
            "",
            "## Evidence Required Before Controlled Paper Validation",
            "- REQUIRES_VALIDATION: Exported historical result artifact must be produced and verified.",
            "",
            "## Explicit Unknowns",
            "- UNKNOWN: Strategy performance remains NOT_ESTABLISHED.",
            "",
            "## Disallowed Conclusions",
            "- ASSUMPTION: No unsupported performance conclusion may be treated as fact.",
            "- PROPOSED_PARAMETER: Protocol values require review before use.",
            "",
            "## Final Decision",
            "- INSUFFICIENT_EVIDENCE: Existing strategy remains in evidence-gathering status.",
            "",
        ]
    )


def _context(
    queue_item: dict | None = None,
    *,
    worktree_path: Path | str = "/private/tmp/runtime-router-test",
    title: str = "Implement a bounded runtime task",
) -> RuntimeExecutionContext:
    return RuntimeExecutionContext(
        workflow_id="LWORK_TEST",
        item_id="ITEM_A",
        logical_task_id="ITEM_A",
        project_id="PROJECT_A",
        repository_id="REPO_A",
        workflow_task_id="LTASK_TEST",
        title=title,
        workflow_objective="Execute a bounded runtime task",
        worktree_path=str(worktree_path),
        authority_tier="ISOLATED_DEVELOPMENT_ONLY",
        plan_id="LPLAN_TEST",
        plan_freeze_id="LFREEZE_TEST",
        queue_item={
            "item_id": "ITEM_A",
            "state": "RUNNING",
            "priority": "NORMAL",
            "version": 1,
            "required_capabilities": ["code_modification"],
            "task_type": "engineering",
            **(queue_item or {}),
        },
    )


def _audit_count(session, event_type: str) -> int:
    return session.query(AuditEventORM).filter(AuditEventORM.event_type == event_type).count()


def _latest_audit(session, event_type: str) -> AuditEventORM:
    return (
        session.query(AuditEventORM)
        .filter(AuditEventORM.event_type == event_type)
        .order_by(AuditEventORM.id.desc())
        .first()
    )


class SequencedProvider(ScriptedExecutionAdapter):
    def __init__(self, provider_id: str, outcomes: list[dict], *, reliability_score: int = 50):
        super().__init__(provider_id=provider_id, reliability_score=reliability_score)
        self.outcomes = outcomes
        self.calls = 0
        self.requests: list[RuntimeExecutionRequest] = []

    def invoke(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        self.calls += 1
        self.requests.append(request)
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        return RuntimeExecutionResult(
            execution_id=request.execution_id,
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            model_id="scripted-runtime-model",
            model_version=self.provider_version,
            routing_decision_id=request.routing_decision_id,
            status=outcome.get("status", "COMPLETED"),
            provider_native_status=outcome.get("provider_native_status", outcome.get("status", "COMPLETED")),
            output_artifact_refs=outcome.get("output_artifact_refs", []),
            completed_substeps=outcome.get("completed_substeps", []),
            evidence=outcome.get("evidence", [{"artifact": "router-provider-result"}]),
            verification=outcome.get("verification", [{"result": "PASS"}]),
            documentation=outcome.get("documentation", []),
            active_execution_seconds=outcome.get("active_execution_seconds", 0.01),
            latency_ms=outcome.get("latency_ms"),
            input_tokens=outcome.get("input_tokens"),
            output_tokens=outcome.get("output_tokens"),
            total_tokens=outcome.get("total_tokens"),
            estimated_cost=outcome.get("estimated_cost"),
            cost_currency=outcome.get("cost_currency"),
            retryability=outcome.get("retryability", RuntimeRetryability.NON_RETRYABLE),
            failure_class=outcome.get("failure_class"),
            provider_error_metadata=outcome.get("provider_error_metadata", {}),
            mutation_summary=outcome.get("mutation_summary"),
        )
