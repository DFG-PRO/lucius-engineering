from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from lucius.persistence.orm import AuditEventORM, ModelExecutionORM, PlanFreezeORM
from lucius.runtime.adapters import ScriptedExecutionAdapter
from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
from lucius.runtime.schema_constraints import SKELETON_METADATA_KEY
from lucius.runtime.schemas import (
    ModelCapabilityProfile,
    ModelQualificationStatus,
    RuntimeExecutionContext,
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    RuntimeExecutionSupervision,
    RuntimeProviderModel,
    RuntimeProviderStatus,
    RuntimeRetryability,
)



def test_router_derives_allowed_mutation_paths_from_frozen_plan(session):
    provider = SequencedProvider(
        "provider-scope-derivation",
        [{"status": "COMPLETED"}],
    )

    result = _router(session, [provider]).execute(_context())

    assert result.outcome == "COMPLETED"
    assert provider.calls == 1
    assert provider.requests[0].allowed_mutation_paths == [
        "provider-proof.txt",
        "readiness.md",
        "protocol.md",
    ]


def test_router_read_only_request_does_not_require_plan_freeze(session):
    provider = SequencedProvider(
        "provider-read-only-no-freeze",
        [{"status": "COMPLETED"}],
    )
    provider.registration = provider.registration.model_copy(
        update={
            "capabilities": ["inspection_reasoning"],
            "models": [
                RuntimeProviderModel(
                    model_id="scripted-runtime-model",
                    capabilities=["inspection_reasoning"],
                )
            ],
        }
    )

    router = ModelExecutionRouter(
        session,
        registry=RuntimeProviderRegistry([provider]),
    )

    context = _context(
        queue_item={
            "read_only": True,
            "required_capabilities": ["inspection_reasoning"],
            "task_type": "engineering",
        }
    )
    context = context.model_copy(
        update={
            "plan_freeze_id": "LFREEZE_DOES_NOT_EXIST",
        }
    )

    result = router.execute(context)

    assert result.outcome == "COMPLETED"
    assert provider.calls == 1
    assert provider.requests[0].allowed_mutation_paths == []


@pytest.mark.parametrize(
    ("freeze_setup", "expected_error"),
    [
        ("missing", "PLAN_FREEZE_NOT_FOUND"),
        ("mismatch", "PLAN_FREEZE_PLAN_MISMATCH"),
        ("empty", "EMPTY_FROZEN_MUTATION_SCOPE"),
    ],
)
def test_router_rejects_invalid_mutation_scope_before_provider(
    session,
    freeze_setup,
    expected_error,
):
    provider = SequencedProvider(
        f"provider-scope-{freeze_setup}",
        [{"status": "COMPLETED"}],
    )

    if freeze_setup == "missing":
        freeze_id = "LFREEZE_MISSING"

    elif freeze_setup == "mismatch":
        freeze_id = "LFREEZE_MISMATCH"
        session.add(
            PlanFreezeORM(
                id=freeze_id,
                plan_id="LPLAN_OTHER",
                task_id="LTASK_TEST",
                project_id="PROJECT_A",
                repository_state_id=None,
                repository_snapshot_ids=[],
                evidence_ids=[],
                commit_sha=None,
                planning_mode="CURRENT_STATE_PLANNING",
                evaluation_version="phase-1.30-router-test",
                plan_payload={
                    "affected_files": [
                        {"path": "provider-proof.txt"},
                    ]
                },
                frozen_by="SYSTEM",
            )
        )
        session.flush()

    else:
        freeze_id = "LFREEZE_EMPTY"
        session.add(
            PlanFreezeORM(
                id=freeze_id,
                plan_id="LPLAN_TEST",
                task_id="LTASK_TEST",
                project_id="PROJECT_A",
                repository_state_id=None,
                repository_snapshot_ids=[],
                evidence_ids=[],
                commit_sha=None,
                planning_mode="CURRENT_STATE_PLANNING",
                evaluation_version="phase-1.30-router-test",
                plan_payload={"affected_files": []},
                frozen_by="SYSTEM",
            )
        )
        session.flush()

    router = ModelExecutionRouter(
        session,
        registry=RuntimeProviderRegistry([provider]),
    )

    context = _context().model_copy(
        update={
            "plan_freeze_id": freeze_id,
        }
    )

    result = router.execute(context)

    assert result.outcome == "FAILED"
    assert result.failure_class == "MUTATION_SCOPE_VIOLATION"
    assert result.blocker_category == "MUTATION_SCOPE_VIOLATION"
    assert result.error == expected_error
    assert result.routing_decision_id is None
    assert provider.calls == 0
    assert provider.requests == []
    assert session.scalars(select(ModelExecutionORM)).all() == []

    audit = _latest_audit(session, "MODEL_EXECUTION_MUTATION_SCOPE_REJECTED")
    assert audit is not None
    assert audit.event_metadata["mutation_scope_error"] == expected_error


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


def test_router_accepts_allowed_evidence_reference_when_required(session, tmp_path):
    report = tmp_path / "protocol.md"
    report.write_text("- FACT: Win Rate 55% was observed per evidence LPLAN_000119_EVIDENCE_001.\n", encoding="utf-8")
    provider = SequencedProvider(
        "provider-allowed-evidence",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "protocol.md"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(
            worktree_path=tmp_path,
            title="Create OOS walk-forward protocol evidence",
            queue_item={
                "context_limits": {
                    "evidence_sensitive": True,
                    "evidence_reference_validation_required": True,
                    "allowed_evidence_refs": ["LPLAN_000119_EVIDENCE_001"],
                }
            },
        )
    )

    assert result.outcome == "COMPLETED"


def test_router_rejects_canonical_looking_nonexistent_evidence_reference(session, tmp_path):
    report = tmp_path / "protocol.md"
    report.write_text("- FACT: Win Rate 55% was observed per evidence LPLAN_000119_EVIDENCE_001.\n", encoding="utf-8")
    provider = SequencedProvider(
        "provider-fabricated-evidence",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "protocol.md"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(
            worktree_path=tmp_path,
            title="Create OOS walk-forward protocol evidence",
            queue_item={"context_limits": {"evidence_sensitive": True, "evidence_reference_validation_required": True}},
        )
    )

    assert result.outcome == "FAILED"
    assert result.failure_class == "EVIDENCE_REFERENCE_VALIDATION_FAILED"
    assert result.provider_error_metadata["quality_issues"][0]["reason"] == (
        "FABRICATED_OR_UNAUTHORIZED_EVIDENCE_REFERENCE"
    )


def test_router_rejects_missing_evidence_reference_when_required(session, tmp_path):
    report = tmp_path / "protocol.md"
    report.write_text("- FACT: Win Rate 55% was observed per verified evidence source.\n", encoding="utf-8")
    provider = SequencedProvider(
        "provider-missing-evidence",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "protocol.md"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(
            worktree_path=tmp_path,
            title="Create OOS walk-forward protocol evidence",
            queue_item={"context_limits": {"evidence_sensitive": True, "evidence_reference_validation_required": True}},
        )
    )

    assert result.outcome == "FAILED"
    assert result.provider_error_metadata["quality_issues"][0]["reason"] == (
        "FACT_OR_DERIVED_VALUE_MISSING_ALLOWED_EVIDENCE_REFERENCE"
    )


def test_router_ignores_structural_json_counters_but_preserves_semantic_quantity_gate(session, tmp_path):
    report = tmp_path / "summary.json"
    report.write_text('{\n  "total_statements": 2,\n  "categorized_count": 1\n}\n', encoding="utf-8")
    provider = SequencedProvider(
        "provider-json-counters",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "summary.json"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(
            worktree_path=tmp_path,
            title="Create evidence-sensitive summary",
            queue_item={"context_limits": {"evidence_sensitive": True}},
        )
    )

    assert result.outcome == "COMPLETED"

    report.write_text('{"win_rate": 55}\n', encoding="utf-8")
    result = _router(session, [provider]).execute(
        _context(
            worktree_path=tmp_path,
            title="Create evidence-sensitive summary",
            queue_item={"context_limits": {"evidence_sensitive": True}},
        )
    )

    assert result.outcome == "FAILED"
    assert result.failure_class == "UNSUPPORTED_QUANTITATIVE_CLAIM"


@pytest.mark.parametrize("line", ['"trade_count": 20,', '"profit_total": 1200,', '"num_trades": 40,'])
def test_router_treats_domain_count_and_total_fields_as_semantic_claims(session, tmp_path, line):
    report = tmp_path / "summary.json"
    report.write_text("{\n  " + line + "\n}\n", encoding="utf-8")
    provider = SequencedProvider(
        "provider-domain-counters",
        [{"status": "COMPLETED", "output_artifact_refs": [{"path": "summary.json"}]}],
    )

    result = _router(session, [provider]).execute(
        _context(
            worktree_path=tmp_path,
            title="Create evidence-sensitive trading summary",
            queue_item={"context_limits": {"evidence_sensitive": True}},
        )
    )

    assert result.outcome == "FAILED"
    assert result.failure_class == "UNSUPPORTED_QUANTITATIVE_CLAIM"


def test_router_explicit_evidence_reference_validation_runs_without_heuristic_sensitivity(session):
    provider = SequencedProvider(
        "provider-ref-payload",
        [
            {
                "status": "COMPLETED",
                "evidence": [{"type": "provider_claim", "evidence_reference": "LPLAN_000119_EVIDENCE_001"}],
            }
        ],
    )

    result = _router(session, [provider]).execute(
        _context(
            title="Inspect simple implementation notes",
            queue_item={"context_limits": {"evidence_reference_validation_required": True}},
        )
    )

    assert result.outcome == "FAILED"
    assert result.failure_class == "EVIDENCE_REFERENCE_VALIDATION_FAILED"
    assert result.provider_error_metadata["quality_issues"][0]["reason"] == (
        "FABRICATED_OR_UNAUTHORIZED_EVIDENCE_REFERENCE"
    )


def test_router_rejects_malformed_evidence_reference_payload(session):
    provider = SequencedProvider(
        "provider-ref-malformed",
        [
            {
                "status": "COMPLETED",
                "evidence": [{"type": "provider_claim", "evidence_reference": {"id": "LEVID_000001"}}],
            }
        ],
    )

    result = _router(session, [provider]).execute(
        _context(queue_item={"context_limits": {"evidence_reference_validation_required": True}})
    )

    assert result.outcome == "FAILED"
    assert result.failure_class == "EVIDENCE_REFERENCE_VALIDATION_FAILED"
    assert result.provider_error_metadata["quality_issues"][0]["reason"] == "MALFORMED_EVIDENCE_REFERENCE_FIELD"


def test_router_accepts_allowed_evidence_reference_list_payload(session):
    provider = SequencedProvider(
        "provider-ref-list",
        [
            {
                "status": "COMPLETED",
                "evidence": [
                    {
                        "type": "provider_claim",
                        "evidence_references": ["LEVID_000001", "LPLAN_000119_EVIDENCE_001"],
                    }
                ],
            }
        ],
    )

    result = _router(session, [provider]).execute(
        _context(
            queue_item={
                "context_limits": {
                    "evidence_reference_validation_required": True,
                    "allowed_evidence_refs": ["LEVID_000001", "LPLAN_000119_EVIDENCE_001"],
                }
            }
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


def test_router_read_only_inspection_uses_explicit_non_mutation_capability(session):
    provider = SequencedProvider(
        "provider-readonly",
        [{"status": "COMPLETED"}],
    )
    provider.registration = provider.registration.model_copy(
        update={
            "capabilities": ["inspection_reasoning"],
            "supported_task_classes": ["engineering", "inspection"],
            "supports_code_modification": False,
            "models": [
                RuntimeProviderModel(
                    model_id="readonly-model",
                    capabilities=["inspection_reasoning"],
                )
            ],
        }
    )

    result = _router(session, [provider]).execute(
        _context(queue_item={"read_only": True, "task_type": "inspection", "required_capabilities": None})
    )

    assert result.outcome == "COMPLETED"
    assert provider.requests[0].required_capabilities == ["inspection_reasoning"]


def test_router_mutation_task_still_requires_mutation_capable_provider(session):
    provider = SequencedProvider("provider-readonly-only", [{"status": "COMPLETED"}])
    provider.registration = provider.registration.model_copy(
        update={
            "capabilities": ["inspection_reasoning"],
            "supports_code_modification": False,
            "models": [
                RuntimeProviderModel(
                    model_id="readonly-model",
                    capabilities=["inspection_reasoning"],
                )
            ],
        }
    )

    result = _router(session, [provider]).execute(_context())

    assert result.outcome == "FAILED"
    candidates = _latest_audit(session, "MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED").event_metadata["candidates"]
    assert "missing capabilities: ['code_modification']" in candidates[0]["reasons"]


def test_router_allows_small_unattended_tier1_task_with_strict_constraints(session):
    provider = _profiled_provider("provider-tier1", _tier1_profile("provider-tier1", "qualified-small-model"))

    result = _router(session, [provider]).execute(
        _context(
            queue_item={
                "unattended": True,
                "task_complexity": "T1",
                "task_risk": "LOW",
                "isolation_mode": "ISOLATED_WORKTREE",
                "timeout_seconds": 60,
                "context_limits": {"deterministic_verification": True},
            }
        )
    )

    assert result.outcome == "COMPLETED"
    assert provider.requests[0].timeout_seconds == 60


def test_router_allows_qwen3_8b_read_only_when_other_unattended_metadata_is_valid(session):
    profile = _qwen3_8b_support_profile("ollama-local")
    provider = _profiled_provider("ollama-local", profile)

    result = _router(session, [provider]).execute(
        _context(
            queue_item={
                "read_only": True,
                "required_capabilities": ["inspection_reasoning"],
                "task_type": "inspection",
                "unattended": True,
                "task_complexity": "T1",
                "task_risk": "LOW",
                "isolation_mode": "ISOLATED_WORKTREE",
                "context_limits": {"deterministic_verification": True},
            }
        )
    )

    assert result.outcome == "COMPLETED"
    assert provider.calls == 1
    assert provider.requests[0].read_only is True
    assert provider.requests[0].required_capabilities == ["inspection_reasoning"]


def test_router_rejects_qwen3_8b_unattended_mutation_before_provider_invocation(session):
    profile = _qwen3_8b_support_profile("ollama-local")
    provider = _profiled_provider("ollama-local", profile)

    result = _router(session, [provider]).execute(
        _context(
            queue_item={
                "unattended": True,
                "task_complexity": "T1",
                "task_risk": "LOW",
                "isolation_mode": "ISOLATED_WORKTREE",
                "context_limits": {"deterministic_verification": True},
            }
        )
    )

    assert result.outcome == "FAILED"
    assert provider.calls == 0
    candidates = _latest_audit(session, "MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED").event_metadata["candidates"]
    assert "MODEL_NOT_QUALIFIED_FOR_UNATTENDED_MUTATION" in candidates[0]["reasons"]
    assert _audit_count(session, "MODEL_EXECUTION_PROVIDER_INVOCATION_STARTED") == 0


def test_router_still_allows_other_unattended_mutation_qualified_provider(session):
    provider = _profiled_provider("provider-qualified-mutation", _tier1_profile("provider-qualified-mutation", "qualified-small-model"))

    result = _router(session, [provider]).execute(
        _context(
            queue_item={
                "unattended": True,
                "task_complexity": "T1",
                "task_risk": "LOW",
                "isolation_mode": "ISOLATED_WORKTREE",
                "context_limits": {"deterministic_verification": True},
            }
        )
    )

    assert result.outcome == "COMPLETED"
    assert provider.calls == 1


def test_router_rejects_unattended_tier1_when_complexity_is_missing(session):
    provider = _profiled_provider("provider-tier1", _tier1_profile("provider-tier1", "qwen3:8b"))

    result = _router(session, [provider]).execute(
        _context(
            queue_item={
                "unattended": True,
                "task_risk": "LOW",
                "isolation_mode": "ISOLATED_WORKTREE",
                "context_limits": {"deterministic_verification": True},
            }
        )
    )

    assert result.outcome == "FAILED"
    candidates = _latest_audit(session, "MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED").event_metadata["candidates"]
    assert "TASK_COMPLEXITY_UNKNOWN" in candidates[0]["reasons"]


def test_router_rejects_unattended_tier1_when_risk_is_missing(session):
    provider = _profiled_provider("provider-tier1", _tier1_profile("provider-tier1", "qwen3:8b"))

    result = _router(session, [provider]).execute(
        _context(
            queue_item={
                "unattended": True,
                "task_complexity": "T1",
                "isolation_mode": "ISOLATED_WORKTREE",
                "context_limits": {"deterministic_verification": True},
            }
        )
    )

    assert result.outcome == "FAILED"
    candidates = _latest_audit(session, "MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED").event_metadata["candidates"]
    assert "TASK_RISK_UNKNOWN" in candidates[0]["reasons"]


def test_router_rejects_unattended_tier1_when_allowed_roots_not_explicit(session):
    provider = _profiled_provider(
        "provider-tier1",
        _tier1_profile("provider-tier1", "qwen3:8b"),
        explicit_allowed_workspace_roots=False,
    )

    result = _router(session, [provider]).execute(
        _context(
            queue_item={
                "unattended": True,
                "task_complexity": "T1",
                "task_risk": "LOW",
                "isolation_mode": "ISOLATED_WORKTREE",
                "context_limits": {"deterministic_verification": True},
            }
        )
    )

    assert result.outcome == "FAILED"
    candidates = _latest_audit(session, "MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED").event_metadata["candidates"]
    assert "WORKSPACE_ALLOWED_ROOT_UNKNOWN" in candidates[0]["reasons"]


def test_router_rejects_complex_task_for_tier1_unattended_model(session):
    provider = _profiled_provider("provider-tier1", _tier1_profile("provider-tier1", "qwen3:8b"))

    result = _router(session, [provider]).execute(
        _context(
            queue_item={
                "unattended": True,
                "task_complexity": "T2",
                "task_risk": "LOW",
                "isolation_mode": "ISOLATED_WORKTREE",
                "context_limits": {"deterministic_verification": True},
            }
        )
    )

    assert result.outcome == "FAILED"
    candidates = _latest_audit(session, "MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED").event_metadata["candidates"]
    assert "TASK_COMPLEXITY_EXCEEDS_MODEL_PROFILE" in candidates[0]["reasons"]


def test_router_rejects_supervised_only_model_for_unattended(session):
    profile = _tier1_profile("provider-tier2", "qwen3-coder:30b").model_copy(
        update={
            "execution_tier": "TIER_2_LOCAL_SUPERVISED_SCHEMA_CONSTRAINED",
            "status": ModelQualificationStatus.SUPERVISED_ONLY,
            "supervision_required": True,
            "unattended_eligible": False,
        }
    )
    provider = _profiled_provider("provider-tier2", profile)

    result = _router(session, [provider]).execute(
        _context(queue_item={"unattended": True, "context_limits": {"deterministic_verification": True}})
    )

    assert result.outcome == "FAILED"
    candidates = _latest_audit(session, "MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED").event_metadata["candidates"]
    assert "MODEL_SUPERVISION_REQUIRED" in candidates[0]["reasons"]


def test_router_rejects_supervised_only_model_without_explicit_supervision(session):
    profile = _tier1_profile("provider-tier2", "qwen3-coder:30b").model_copy(
        update={
            "execution_tier": "TIER_2_LOCAL_SUPERVISED_SCHEMA_CONSTRAINED",
            "status": ModelQualificationStatus.SUPERVISED_ONLY,
            "supervision_required": True,
            "unattended_eligible": False,
        }
    )
    provider = _profiled_provider("provider-tier2", profile)

    result = _router(session, [provider]).execute(_context())

    assert result.outcome == "FAILED"
    candidates = _latest_audit(session, "MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED").event_metadata["candidates"]
    assert "MODEL_SUPERVISION_REQUIRED" in candidates[0]["reasons"]


def test_router_rejects_supervised_schema_required_model_without_schema_constraint(session):
    profile = _tier1_profile("provider-tier2", "qwen3-coder:30b").model_copy(
        update={
            "execution_tier": "TIER_2_LOCAL_SUPERVISED_SCHEMA_CONSTRAINED",
            "status": ModelQualificationStatus.SUPERVISED_ONLY,
            "supervision_required": True,
            "unattended_eligible": False,
            "schema_constrained_required": True,
        }
    )
    provider = _profiled_provider("provider-tier2", profile)

    result = _router(session, [provider]).execute(
        _context(queue_item={"execution_supervision": RuntimeExecutionSupervision.SUPERVISED.value})
    )

    assert result.outcome == "FAILED"
    candidates = _latest_audit(session, "MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED").event_metadata["candidates"]
    assert "SCHEMA_CONSTRAINT_REQUIRED" in candidates[0]["reasons"]


def test_router_allows_supervised_schema_required_model_with_explicit_supervision_and_schema(session):
    profile = _tier1_profile("provider-tier2", "qwen3-coder:30b").model_copy(
        update={
            "execution_tier": "TIER_2_LOCAL_SUPERVISED_SCHEMA_CONSTRAINED",
            "status": ModelQualificationStatus.SUPERVISED_ONLY,
            "supervision_required": True,
            "unattended_eligible": False,
            "schema_constrained_required": True,
        }
    )
    provider = _profiled_provider("provider-tier2", profile)

    result = _router(session, [provider]).execute(
        _context(
            queue_item={
                "execution_supervision": RuntimeExecutionSupervision.SUPERVISED.value,
                "context_limits": {
                    "schema_constraint": {
                        "required_sections": ["SUMMARY"],
                    }
                },
            }
        )
    )

    assert result.outcome == "COMPLETED"


def test_router_failover_cannot_select_supervised_only_model_without_authorization(session):
    failing = SequencedProvider(
        "provider-fails",
        [{"status": "FAILED", "retryability": RuntimeRetryability.FAILOVERABLE, "failure_class": "PROVIDER_DOWN"}],
        reliability_score=90,
    )
    profile = _tier1_profile("provider-tier2", "qwen3-coder:30b").model_copy(
        update={
            "execution_tier": "TIER_2_LOCAL_SUPERVISED_SCHEMA_CONSTRAINED",
            "status": ModelQualificationStatus.SUPERVISED_ONLY,
            "supervision_required": True,
            "unattended_eligible": False,
        }
    )
    supervised_only = _profiled_provider("provider-tier2", profile)

    result = _router(session, [failing, supervised_only], max_failovers=1).execute(_context())

    assert result.outcome == "FAILED"
    assert result.provider_id == "provider-fails"
    assert supervised_only.calls == 0


def test_router_rejects_unprofiled_model_for_unattended(session):
    provider = SequencedProvider("provider-unprofiled", [{"status": "COMPLETED"}])

    result = _router(session, [provider]).execute(
        _context(queue_item={"unattended": True, "context_limits": {"deterministic_verification": True}})
    )

    assert result.outcome == "FAILED"
    candidates = _latest_audit(session, "MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED").event_metadata["candidates"]
    assert "MODEL_NOT_QUALIFIED_FOR_UNATTENDED" in candidates[0]["reasons"]



def test_router_rejects_evidence_sensitive_unattended_task_for_model_not_evidence_suitable(session):
    provider = _profiled_provider(
        "provider-tier1",
        _tier1_profile("provider-tier1", "qwen3:8b"),
    )

    result = _router(session, [provider]).execute(
        _context(
            title="Inspect backtest evidence",
            queue_item={
                "unattended": True,
                "task_complexity": "T1",
                "task_risk": "LOW",
                "isolation_mode": "ISOLATED_WORKTREE",
                "context_limits": {
                    "deterministic_verification": True,
                    "evidence_sensitive": True,
                    "evidence_reference_validation_required": True,
                },
            },
        )
    )

    assert result.outcome == "FAILED"
    assert provider.calls == 0
    candidates = _latest_audit(
        session,
        "MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED",
    ).event_metadata["candidates"]
    assert "MODEL_NOT_QUALIFIED_FOR_EVIDENCE_SENSITIVE_WORK" in candidates[0]["reasons"]


def test_router_requires_evidence_validation_for_evidence_capable_unattended_model(session):
    profile = _tier1_profile("provider-evidence", "evidence-model").model_copy(
        update={"evidence_sensitive_suitable": True}
    )
    provider = _profiled_provider("provider-evidence", profile)

    result = _router(session, [provider]).execute(
        _context(
            title="Inspect backtest evidence",
            queue_item={
                "unattended": True,
                "task_complexity": "T1",
                "task_risk": "LOW",
                "isolation_mode": "ISOLATED_WORKTREE",
                "context_limits": {
                    "deterministic_verification": True,
                    "evidence_sensitive": True,
                },
            },
        )
    )

    assert result.outcome == "FAILED"
    assert provider.calls == 0
    candidates = _latest_audit(
        session,
        "MODEL_EXECUTION_PROVIDER_CANDIDATES_EVALUATED",
    ).event_metadata["candidates"]
    assert "EVIDENCE_VALIDATION_REQUIRED" in candidates[0]["reasons"]


def test_router_allows_evidence_capable_unattended_model_with_validation(session):
    profile = _tier1_profile("provider-evidence", "evidence-model").model_copy(
        update={"evidence_sensitive_suitable": True}
    )
    provider = _profiled_provider("provider-evidence", profile)

    result = _router(session, [provider]).execute(
        _context(
            title="Inspect backtest evidence",
            queue_item={
                "unattended": True,
                "task_complexity": "T1",
                "task_risk": "LOW",
                "isolation_mode": "ISOLATED_WORKTREE",
                "context_limits": {
                    "deterministic_verification": True,
                    "evidence_sensitive": True,
                    "evidence_reference_validation_required": True,
                },
            },
        )
    )

    assert result.outcome == "COMPLETED"
    assert provider.calls == 1


def test_router_records_success_failure_and_timeout_attempts_in_model_executions(session):
    provider = SequencedProvider(
        "provider-observable",
        [
            {"status": "FAILED", "failure_class": "PROVIDER_TIMEOUT", "retryability": RuntimeRetryability.NON_RETRYABLE},
        ],
    )

    result = _router(session, [provider]).execute(_context())

    assert result.outcome == "FAILED"
    rows = session.scalars(select(ModelExecutionORM).order_by(ModelExecutionORM.created_at)).all()
    assert len(rows) == 1
    assert rows[0].request_id == result.execution_id
    assert rows[0].run_id == "LWORK_TEST"
    assert rows[0].task_id == "LTASK_TEST"
    assert rows[0].provider_id == "provider-observable"
    assert rows[0].model == "scripted-runtime-model"
    assert rows[0].status == "FAILED"
    assert rows[0].error_code == "PROVIDER_TIMEOUT"


def test_router_preview_uses_canonical_eligibility_without_execution_or_persistence(session, tmp_path):
    context_file = tmp_path / "README.md"
    context_file.write_text("bounded evidence\n", encoding="utf-8")
    provider = _profiled_provider("ollama-local", _qwen3_8b_support_profile("ollama-local"))
    provider.registration = provider.registration.model_copy(
        update={
            "metadata": {
                "explicit_allowed_workspace_roots": True,
                "read_only_worker_shape": {
                    "max_context_files": 3,
                    "max_context_bytes": 40_000,
                    "max_evidence_refs": 3,
                    "required_capabilities": ["inspection_reasoning"],
                },
            }
        }
    )
    context = _context(
        worktree_path=tmp_path,
        title="Review one bounded observability invariant",
        queue_item={
            "read_only": True,
            "unattended": True,
            "required_capabilities": ["inspection_reasoning"],
            "task_type": "inspection",
            "task_complexity": "T1",
            "task_risk": "LOW",
            "isolation_mode": "ISOLATED_WORKTREE",
            "execution_supervision": "UNSUPERVISED",
            "context_limits": {
                "deterministic_verification": True,
                "evidence_reference_validation_required": True,
                "read_only_context_paths": ["README.md"],
                "requested_evidence_refs": 1,
            },
        },
    )

    preview = ModelExecutionRouter(
        session,
        registry=RuntimeProviderRegistry([provider]),
    ).preview(context)

    assert [candidate.provider_id for candidate in preview["candidates"] if candidate.eligible] == ["ollama-local"]
    assert preview["worker_shape"].valid is True
    assert provider.calls == 0
    assert session.scalars(select(ModelExecutionORM)).all() == []
    assert _audit_count(session, "MODEL_EXECUTION_ROUTING_DECISION") == 0


def test_router_preview_rejects_evidence_sensitive_qwen_task(session, tmp_path):
    provider = _profiled_provider("ollama-local", _qwen3_8b_support_profile("ollama-local"))
    provider.registration = provider.registration.model_copy(
        update={"metadata": {"explicit_allowed_workspace_roots": True}}
    )
    context = _context(
        worktree_path=tmp_path,
        title="Inventory profitability evidence",
        queue_item={
            "read_only": True,
            "unattended": True,
            "required_capabilities": ["inspection_reasoning"],
            "task_type": "inspection",
            "task_complexity": "T1",
            "task_risk": "LOW",
            "isolation_mode": "ISOLATED_WORKTREE",
            "execution_supervision": "UNSUPERVISED",
            "context_limits": {
                "deterministic_verification": True,
                "evidence_reference_validation_required": True,
            },
        },
    )

    preview = ModelExecutionRouter(session, registry=RuntimeProviderRegistry([provider])).preview(context)

    assert preview["candidates"][0].eligible is False
    assert preview["candidates"][0].reasons == ["MODEL_NOT_QUALIFIED_FOR_EVIDENCE_SENSITIVE_WORK"]
    assert session.scalars(select(ModelExecutionORM)).all() == []


def test_router_preview_mixed_backlog_reports_eligible_and_ineligible_states(session, tmp_path):
    provider = _profiled_provider("ollama-local", _qwen3_8b_support_profile("ollama-local"))
    provider.registration = provider.registration.model_copy(
        update={"metadata": {"explicit_allowed_workspace_roots": True}}
    )
    router = ModelExecutionRouter(session, registry=RuntimeProviderRegistry([provider]))
    narrow = _context(
        worktree_path=tmp_path,
        title="Review one bounded invariant",
        queue_item={
            "read_only": True,
            "unattended": True,
            "required_capabilities": ["inspection_reasoning"],
            "task_type": "inspection",
            "task_complexity": "T1",
            "task_risk": "LOW",
            "isolation_mode": "ISOLATED_WORKTREE",
            "execution_supervision": "UNSUPERVISED",
            "context_limits": {"deterministic_verification": True, "evidence_reference_validation_required": True},
        },
    )
    evidence = narrow.model_copy(update={"title": "Review profitability evidence"})

    narrow_preview = router.preview(narrow)
    evidence_preview = router.preview(evidence)

    assert any(candidate.eligible for candidate in narrow_preview["candidates"])
    assert not any(candidate.eligible for candidate in evidence_preview["candidates"])
    assert evidence_preview["candidates"][0].reasons == ["MODEL_NOT_QUALIFIED_FOR_EVIDENCE_SENSITIVE_WORK"]


def test_router_preview_zero_provider_is_not_launchable(session, tmp_path):
    provider = ScriptedExecutionAdapter(provider_id="wrong-capability", capabilities=["code_modification"])
    router = ModelExecutionRouter(session, registry=RuntimeProviderRegistry([provider]))
    context = _context(
        worktree_path=tmp_path,
        title="Review one bounded invariant",
        queue_item={
            "read_only": True,
            "unattended": True,
            "required_capabilities": ["inspection_reasoning"],
            "task_type": "inspection",
            "task_complexity": "T1",
            "task_risk": "LOW",
            "isolation_mode": "ISOLATED_WORKTREE",
            "execution_supervision": "UNSUPERVISED",
            "context_limits": {"deterministic_verification": True, "evidence_reference_validation_required": True},
        },
    )

    preview = router.preview(context)

    assert not any(candidate.eligible for candidate in preview["candidates"])
    assert "missing capabilities" in preview["candidates"][0].reasons[0]
    assert session.scalars(select(ModelExecutionORM)).all() == []


@pytest.mark.parametrize(
    ("paths", "requested_refs", "expected_reason"),
    [
        (["a", "b", "c", "d"], 1, "WORKER_CONTEXT_FILE_LIMIT_EXCEEDED"),
        (["large"], 1, "WORKER_CONTEXT_BYTE_LIMIT_EXCEEDED"),
        (["a"], 4, "WORKER_EVIDENCE_REFERENCE_LIMIT_EXCEEDED"),
    ],
)
def test_router_preview_enforces_provider_worker_shape(session, tmp_path, paths, requested_refs, expected_reason):
    for path in paths:
        (tmp_path / path).write_text("x" * (40_001 if path == "large" else 1), encoding="utf-8")
    provider = _profiled_provider("ollama-local", _qwen3_8b_support_profile("ollama-local"))
    provider.registration = provider.registration.model_copy(
        update={
            "metadata": {
                "explicit_allowed_workspace_roots": True,
                "read_only_worker_shape": {
                    "max_context_files": 3,
                    "max_context_bytes": 40_000,
                    "max_evidence_refs": 3,
                    "required_capabilities": ["inspection_reasoning"],
                },
            }
        }
    )
    context = _context(
        worktree_path=tmp_path,
        title="Review one bounded invariant",
        queue_item={
            "read_only": True,
            "unattended": True,
            "required_capabilities": ["inspection_reasoning"],
            "task_type": "inspection",
            "task_complexity": "T1",
            "task_risk": "LOW",
            "isolation_mode": "ISOLATED_WORKTREE",
            "execution_supervision": "UNSUPERVISED",
            "context_limits": {
                "deterministic_verification": True,
                "evidence_reference_validation_required": True,
                "read_only_context_paths": paths,
                "requested_evidence_refs": requested_refs,
            },
        },
    )

    preview = ModelExecutionRouter(session, registry=RuntimeProviderRegistry([provider])).preview(context)

    assert preview["worker_shape"].valid is False
    assert expected_reason in preview["worker_shape"].reasons
    assert session.scalars(select(ModelExecutionORM)).all() == []


def _router(session, providers, *, max_provider_retries: int = 0, max_failovers: int = 0) -> ModelExecutionRouter:
    _ensure_default_plan_freeze(session)
    return ModelExecutionRouter(
        session,
        registry=RuntimeProviderRegistry(providers),
        max_provider_retries=max_provider_retries,
        max_failovers=max_failovers,
    )


def _ensure_default_plan_freeze(session) -> None:
    if session.get(PlanFreezeORM, "LFREEZE_TEST") is not None:
        return

    session.add(
        PlanFreezeORM(
            id="LFREEZE_TEST",
            plan_id="LPLAN_TEST",
            task_id="LTASK_TEST",
            project_id="PROJECT_A",
            repository_state_id=None,
            repository_snapshot_ids=[],
            evidence_ids=[],
            commit_sha=None,
            planning_mode="CURRENT_STATE_PLANNING",
            evaluation_version="phase-1.30-router-test",
            plan_payload={
                "affected_files": [
                    {"path": "provider-proof.txt"},
                    {"path": "readiness.md"},
                    {"path": "protocol.md"},
                ],
                "deterministic_acceptance_checks": [
                    {
                        "type": "exact_file_content",
                        "path": "provider-proof.txt",
                        "expected_text": "provider proof\n",
                    }
                ],
            },
            frozen_by="SYSTEM",
        )
    )
    session.flush()


def _tier1_profile(provider_id: str, model_id: str) -> ModelCapabilityProfile:
    return ModelCapabilityProfile(
        provider_id=provider_id,
        model_id=model_id,
        execution_tier="LOCAL_TIER_1",
        is_local=True,
        supported_task_classes=["engineering"],
        supported_capabilities=["code_modification", "documentation_update", "inspection_reasoning"],
        supports_mutation=True,
        evidence_sensitive_suitable=False,
        schema_constrained_required=False,
        deterministic_verification_required=True,
        supervision_required=False,
        unattended_eligible=True,
        unattended_mutation_eligible=True,
        max_task_complexity="T1",
        default_timeout_seconds=60,
        max_timeout_seconds=120,
        status=ModelQualificationStatus.QUALIFIED_WITH_CONSTRAINTS,
    )


def _qwen3_8b_support_profile(provider_id: str) -> ModelCapabilityProfile:
    return _tier1_profile(provider_id, "qwen3:8b").model_copy(
        update={
            "supported_task_classes": ["engineering", "inspection", "reasoning"],
            "unattended_mutation_eligible": False,
            "policy_notes": [
                "Useful local Tier-1 support model.",
                "Not qualified for unattended code mutation.",
            ],
        }
    )


def _profiled_provider(
    provider_id: str,
    profile: ModelCapabilityProfile,
    *,
    explicit_allowed_workspace_roots: bool = True,
) -> "SequencedProvider":
    provider = SequencedProvider(provider_id, [{"status": "COMPLETED"}])
    provider.registration = provider.registration.model_copy(
        update={
            "capabilities": profile.supported_capabilities,
            "supported_task_classes": profile.supported_task_classes,
            "supports_code_modification": profile.supports_mutation,
            "metadata": {"explicit_allowed_workspace_roots": explicit_allowed_workspace_roots},
            "models": [
                RuntimeProviderModel(
                    model_id=profile.model_id,
                    capabilities=profile.supported_capabilities,
                    capability_profile=profile,
                )
            ],
        }
    )
    return provider


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
