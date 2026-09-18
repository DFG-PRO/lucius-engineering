from __future__ import annotations

from pathlib import Path
import pytest
from sqlalchemy.orm import Session

from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.runtime.feeder import DarwinBacklogFeeder, NormalizedTaskEnvelope
from lucius.runtime.ollama import (
    OllamaExecutionProvider,
    _ProviderBlocked,
    _extract_relevant_bounded_context,
    _verify_read_only_evidence,
)
from lucius.runtime.schemas import ModelQualificationStatus, RuntimeExecutionRequest


def test_A_relevant_small_provenance_document_passes(tmp_path: Path):
    """Test A: Relevant small provenance document (<10KB) is used directly and passes verification."""
    workspace = tmp_path / "darwin"
    workspace.mkdir(parents=True)
    doc_file = workspace / "docs" / "small_spec.md"
    doc_file.parent.mkdir(parents=True)
    doc_content = "# Small Spec\n\nThis is a small canonical specification for testing."
    doc_file.write_text(doc_content)

    provider = OllamaExecutionProvider(
        provider_id="ollama-local",
        model="qwen3:8b",
        allowed_workspace_roots=[workspace],
    )

    context = provider._read_only_file_context(
        workspace,
        ["docs/small_spec.md"],
        task_intent="Small spec testing",
    )
    assert len(context) == 1
    assert context[0]["path"] == "docs/small_spec.md"
    assert context[0]["content"] == doc_content

    # Verify evidence references from this context
    evidence_refs = [
        {
            "path": "docs/small_spec.md",
            "exact_fragment": "This is a small canonical specification for testing.",
        }
    ]
    verified = _verify_read_only_evidence(workspace, context, evidence_refs)
    assert len(verified) == 1
    assert verified[0]["result"] == "PASS"


def test_B_relevant_large_provenance_document_derives_bounded_context(tmp_path: Path):
    """Test B: Relevant large provenance document (>10KB) derives bounded context from THAT SAME SOURCE file."""
    workspace = tmp_path / "darwin"
    workspace.mkdir(parents=True)
    doc_file = workspace / "docs" / "large_spec.md"
    doc_file.parent.mkdir(parents=True)

    # Build a 15KB file with sections
    preamble = "# Large Spec Header\n\nPreamble text.\n\n"
    section_fp = "## Section FP Labs\n\nFP Labs technical consulting and external automation services.\n\n"
    padding = "## Other Section\n\n" + ("Filler line for size padding.\n" * 400)
    full_text = preamble + section_fp + padding
    doc_file.write_text(full_text)

    provider = OllamaExecutionProvider(
        provider_id="ollama-local",
        model="qwen3:8b",
        allowed_workspace_roots=[workspace],
    )

    context = provider._read_only_file_context(
        workspace,
        ["docs/large_spec.md"],
        task_intent="FP Labs external automation services",
        max_file_bytes=5_000,
    )
    assert len(context) == 1
    assert context[0]["path"] == "docs/large_spec.md"
    # Extracted content must be smaller than 5000 bytes and contain the FP Labs section from the SAME file
    assert len(context[0]["content"].encode("utf-8")) <= 5_000
    assert "FP Labs technical consulting" in context[0]["content"]

    # Verify evidence from extracted context passes verification against original path
    evidence_refs = [
        {
            "path": "docs/large_spec.md",
            "exact_fragment": "FP Labs technical consulting and external automation services.",
        }
    ]
    verified = _verify_read_only_evidence(workspace, context, evidence_refs)
    assert len(verified) == 1
    assert verified[0]["result"] == "PASS"


def test_C_large_document_with_no_safe_extraction_fails_closed(tmp_path: Path):
    """Test C: Large document that cannot produce a safe bounded extraction fails closed (CONTEXT_CAPABILITY_MISMATCH)."""
    with pytest.raises(_ProviderBlocked) as exc_info:
        _extract_relevant_bounded_context("X" * 100, task_intent="some intent", max_bytes=0)
    assert exc_info.value.failure_class == "CONTEXT_CAPABILITY_MISMATCH"


def test_D_unrelated_small_document_is_not_substituted(tmp_path: Path):
    """Test D: Feeder must NOT substitute an unrelated small document when provenance_refs has no small file."""
    darwin_root = tmp_path / "darwin"
    darwin_root.mkdir(parents=True)
    # Create large monetization doc
    large_doc = darwin_root / "docs" / "runtime" / "monetization-opportunity-portfolio.md"
    large_doc.parent.mkdir(parents=True)
    large_doc.write_text("Header\n" + ("Content fill line\n" * 500))

    # Create small unrelated doc
    unrelated_doc = darwin_root / "docs" / "runtime" / "narrative-research-synthesis.md"
    unrelated_doc.write_text("Unrelated narrative content.")

    raw_item = {
        "item_id": "RBACK-MON-001",
        "title": "FP Labs External Services Commercialization",
        "status": "READY",
        "priority": "P0_NOW",
        "objective": "FP Labs commercialization objective.",
        "provenance_refs": ["docs/runtime/monetization-opportunity-portfolio.md"],
        "dependencies": [],
        "blocked_by": [],
    }

    feeder = DarwinBacklogFeeder(darwin_root=darwin_root, custom_items=[raw_item])
    eligible = feeder.discover_eligible_tasks()
    assert len(eligible) == 1

    # Verify that valid_context_paths in feeder queue_item contains ONLY provenance_refs
    # and does NOT include the unrelated narrative-research-synthesis.md document.
    engine = create_sqlite_engine(tmp_path / "feeder_test.sqlite")
    create_all(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        res = feeder.feed_into_queue(session, max_items=1)
        assert len(res) == 1
        # Retrieve workflow and check queue_item context_limits
        from lucius.persistence.orm import PersistentWorkflowORM
        wf = session.get(PersistentWorkflowORM, res[0]["workflow_id"])
        item = wf.task_backlog[0]
        context_paths = item.get("context_limits", {}).get("read_only_context_paths", [])
        assert context_paths == ["docs/runtime/monetization-opportunity-portfolio.md"]
        assert "docs/runtime/narrative-research-synthesis.md" not in context_paths


def test_E_evidence_copied_from_task_intent_absent_from_source_fails(tmp_path: Path):
    """Test E: Evidence fragment copied from Task intent but absent from authorized source context must FAIL."""
    workspace = tmp_path / "darwin"
    workspace.mkdir(parents=True)
    doc_file = workspace / "docs" / "actual_source.md"
    doc_file.parent.mkdir(parents=True)
    doc_file.write_text("# Actual Source\n\nOnly real source content appears here.")

    read_only_context = [
        {
            "path": "docs/actual_source.md",
            "content": "# Actual Source\n\nOnly real source content appears here.",
        }
    ]

    # Model attempts to return a fragment from Task intent ("Fabricated Task Intent Fragment")
    fake_evidence_refs = [
        {
            "path": "docs/actual_source.md",
            "exact_fragment": "Fabricated Task Intent Fragment That Does Not Exist In File",
        }
    ]

    with pytest.raises(_ProviderBlocked) as exc_info:
        _verify_read_only_evidence(workspace, read_only_context, fake_evidence_refs)

    assert exc_info.value.failure_class == "DETERMINISTIC_READ_ONLY_VERIFICATION_FAILED"


def test_F_qwen3_8b_qualification_constraints_unchanged():
    """Test F: Existing qwen3:8b qualification constraints remain unchanged."""
    provider = OllamaExecutionProvider(
        provider_id="ollama-local",
        model="qwen3:8b",
    )
    reg = provider.registration
    assert reg.cost_class == "LOCAL_FREE"
    assert reg.supports_code_modification is True

    profile = reg.models[0].capability_profile
    assert profile.execution_tier == "LOCAL_TIER_1"
    assert profile.supervision_required is False
    assert profile.unattended_eligible is True
    assert profile.unattended_mutation_eligible is False
    assert profile.status == ModelQualificationStatus.QUALIFIED_WITH_CONSTRAINTS
