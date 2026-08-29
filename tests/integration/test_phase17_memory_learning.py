from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from lucius.domain.enums import (
    Actor,
    AllowedAction,
    AuthorityLevel,
    EvidenceStatus,
    KnowledgeFirewallClassification,
    KnowledgeScope,
    LearningCandidateStatus,
    LearningCandidateType,
    MemorySourceType,
    MemoryType,
    ProjectType,
    SanitizationStatus,
    ValidationStatus,
)
from lucius.evidence.service import EvidenceService
from lucius.learning.service import LearningService
from lucius.memory.schemas import FailureExperience
from lucius.memory.service import MemoryService
from lucius.persistence.orm import AuditEventORM, EvidenceReferenceORM, MemoryEntryORM
from lucius.persistence.repositories import RepositorySnapshotService
from lucius.projects.service import ProjectRegistryService
from lucius.retrieval.service import RetrievalService
from lucius.tasks.service import TaskService

from tests.conftest import run_git


def _task_fixture(session, git_repo: Path | None = None, workspace=None):
    registry = ProjectRegistryService(session)
    project = registry.register_project("Memory Project")
    repository = snapshot = None
    if git_repo is not None:
        (git_repo / "src").mkdir(exist_ok=True)
        (git_repo / "src" / "snapshot.py").write_text("manifest_hash = 'stable'\n", encoding="utf-8")
        run_git(git_repo, "add", ".")
        run_git(git_repo, "commit", "-m", "memory evidence")
        repository = registry.attach_repository(project_id=project.id, name="Repo", location=git_repo, workspace_context=workspace)
        snapshot = RepositorySnapshotService(session).inspect_repository(repository_id=repository.id, workspace_context=workspace)
    service = TaskService(session)
    task = service.create_task(
        project_id=project.id,
        title="Memory task",
        objective="Record deterministic manifest_hash memory",
        authority_level=AuthorityLevel.L1,
        created_by=Actor.CODEX,
    )
    service.create_or_update_contract(
        task_id=task.id,
        objective="Record deterministic manifest_hash memory",
        acceptance_criteria=["manifest_hash evidence exists"],
        repository_ids=[repository.id] if repository else [],
        allowed_actions=[AllowedAction.READ_REPOSITORY],
        authority_level=AuthorityLevel.L1,
    )
    service.mark_ready(task.id)
    run = service.start_run(task.id, snapshot_id=snapshot.snapshot_id if snapshot else None, actor=Actor.CODEX)
    return project, repository, snapshot, task, run


def _evidence_fixture(session, git_repo: Path, workspace):
    project, repository, snapshot, task, run = _task_fixture(session, git_repo, workspace)
    package = RetrievalService(session).retrieve_for_task(
        task_id=task.id,
        task_run_id=run.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["manifest_hash"],
        max_results=1,
    )
    return project, repository, snapshot, task, run, package.evidence_ids[0]


def test_create_project_dfg_and_candidate_memory(session):
    project = ProjectRegistryService(session).register_project("Memory Scope")
    service = MemoryService(session)
    project_memory = service.create_memory(
        memory_type=MemoryType.PROJECT,
        scope=KnowledgeScope.PROJECT,
        project_id=project.id,
        statement="Project uses deterministic repository snapshots.",
        confidence=0.7,
        source_reference="docs/phases/PHASE_1_4B_REPOSITORY_CORE_IMPLEMENTATION.md",
        validation_status=ValidationStatus.OBSERVED,
        created_by=Actor.CODEX,
    )
    dfg_memory = service.create_memory(
        memory_type=MemoryType.PROCEDURAL,
        scope=KnowledgeScope.DFG,
        statement="DFG projects require documentation checkpoints.",
        confidence=0.8,
        source_reference="docs/foundation/DOCUMENTATION_POLICY.md",
        validation_status=ValidationStatus.CANDIDATE,
    )
    assert project_memory.id == "LMEM_000001"
    assert project_memory.scope == KnowledgeScope.PROJECT.value
    assert dfg_memory.scope == KnowledgeScope.DFG.value
    assert dfg_memory.validation_status == ValidationStatus.CANDIDATE.value


def test_project_scope_requires_project_id_and_confidence_range(session):
    service = MemoryService(session)
    with pytest.raises(ValueError):
        service.create_memory(
            memory_type=MemoryType.PROJECT,
            scope=KnowledgeScope.PROJECT,
            statement="Missing project.",
            confidence=0.5,
        )
    with pytest.raises(ValueError):
        service.create_memory(
            memory_type=MemoryType.SEMANTIC,
            scope=KnowledgeScope.DFG,
            statement="Bad confidence.",
            confidence=1.5,
        )


def test_validated_memory_requires_provenance_and_global_policy(session, git_repo: Path, workspace):
    project, *_rest, evidence_id = _evidence_fixture(session, git_repo, workspace)
    service = MemoryService(session)
    with pytest.raises(ValueError):
        service.create_memory(
            memory_type=MemoryType.SEMANTIC,
            scope=KnowledgeScope.DFG,
            statement="Validated without provenance.",
            confidence=0.9,
            validation_status=ValidationStatus.VALIDATED,
        )
    global_memory = service.create_memory(
        memory_type=MemoryType.SEMANTIC,
        scope=KnowledgeScope.GLOBAL,
        statement="Validated global memory requires evidence.",
        confidence=0.8,
        validation_status=ValidationStatus.VALIDATED,
        source_evidence_ids=[evidence_id],
        created_by=Actor.HUMAN,
    )
    assert global_memory.validation_status == ValidationStatus.VALIDATED.value


def test_observed_memory_allowed_with_limited_provenance(session):
    memory = MemoryService(session).create_memory(
        memory_type=MemoryType.EPISODIC,
        scope=KnowledgeScope.SESSION,
        statement="An observed session event occurred.",
        confidence=0.4,
        validation_status=ValidationStatus.OBSERVED,
    )
    assert memory.validation_status == ValidationStatus.OBSERVED.value


def test_memory_retrieval_filters_and_ranking(session):
    project = ProjectRegistryService(session).register_project("Retrieval Memory")
    service = MemoryService(session)
    project_validated = service.create_memory(
        memory_type=MemoryType.PROJECT,
        scope=KnowledgeScope.PROJECT,
        project_id=project.id,
        statement="manifest_hash is project-specific snapshot identity.",
        confidence=0.9,
        validation_status=ValidationStatus.VALIDATED,
        source_reference="LEVID_000001",
        context_tags=["snapshots"],
        technology_tags=["python"],
    )
    dfg_candidate = service.create_memory(
        memory_type=MemoryType.SEMANTIC,
        scope=KnowledgeScope.DFG,
        statement="manifest_hash can identify deterministic manifests.",
        confidence=0.6,
        validation_status=ValidationStatus.CANDIDATE,
        source_reference="LEVID_000002",
        technology_tags=["python"],
    )
    service.create_memory(
        memory_type=MemoryType.PROCEDURAL,
        scope=KnowledgeScope.DFG,
        statement="Run tests before documentation.",
        confidence=0.7,
        source_reference="policy",
    )
    assert service.list_memory(project_id=project.id)[0].id == project_validated.id
    assert len(service.retrieve_memory(memory_type=MemoryType.PROJECT).matches) == 1
    assert len(service.retrieve_memory(validation_status=ValidationStatus.CANDIDATE).matches) == 1
    matches = service.retrieve_memory(project_id=project.id, query_terms=["manifest_hash"]).matches
    assert [match.memory_id for match in matches][:2] == [project_validated.id, dfg_candidate.id]
    assert matches[0].score > matches[1].score
    assert service.retrieve_memory(project_id=project.id, technology_tags=["python"]).total >= 2
    assert service.retrieve_memory(project_id=project.id, context_tags=["snapshots"]).matches[0].memory_id == project_validated.id


def test_superseded_deprecated_contradicted_excluded_and_deterministic_ordering(session):
    service = MemoryService(session)
    first = service.create_memory(
        memory_type=MemoryType.SEMANTIC,
        scope=KnowledgeScope.DFG,
        statement="Alpha memory manifest_hash.",
        confidence=0.7,
        source_reference="source",
    )
    second = service.create_memory(
        memory_type=MemoryType.SEMANTIC,
        scope=KnowledgeScope.DFG,
        statement="Beta memory manifest_hash.",
        confidence=0.7,
        source_reference="source",
    )
    deprecated = service.create_memory(
        memory_type=MemoryType.SEMANTIC,
        scope=KnowledgeScope.DFG,
        statement="Deprecated memory manifest_hash.",
        confidence=0.7,
        source_reference="source",
    )
    contradicted = service.create_memory(
        memory_type=MemoryType.SEMANTIC,
        scope=KnowledgeScope.DFG,
        statement="Contradicted memory manifest_hash.",
        confidence=0.7,
        source_reference="source",
    )
    service.supersede_memory(first.id, second.id, reason="Newer fact")
    service.deprecate_memory(deprecated.id, reason="Do not use")
    service.contradict_memory(contradicted.id, reason="Evidence against")
    matches = service.retrieve_memory(query_terms=["manifest_hash"]).matches
    assert [match.memory_id for match in matches] == [second.id]
    assert first.validation_status == ValidationStatus.SUPERSEDED.value
    assert first.superseded_by_id == second.id
    assert second.supersedes_id == first.id
    with pytest.raises(ValueError):
        service.supersede_memory(second.id, first.id, reason="cycle")


def test_revalidation_flags_and_stale_missing_evidence(session, git_repo: Path, workspace):
    project, _repository, _snapshot, task, run, evidence_id = _evidence_fixture(session, git_repo, workspace)
    service = MemoryService(session)
    memory = service.create_memory(
        memory_type=MemoryType.SEMANTIC,
        scope=KnowledgeScope.PROJECT,
        project_id=project.id,
        statement="manifest_hash source is current.",
        confidence=0.8,
        validation_status=ValidationStatus.VALIDATED,
        source_type=MemorySourceType.EVIDENCE_REFERENCE,
        source_task_id=task.id,
        source_run_id=run.id,
        source_evidence_ids=[evidence_id],
    )
    service.mark_requires_revalidation(memory.id, reason="Repository evidence changed")
    assert memory.requires_revalidation is True
    service.clear_revalidation_flag(memory.id)
    assert memory.requires_revalidation is False
    evidence = session.get(EvidenceReferenceORM, evidence_id)
    assert evidence is not None
    (git_repo / evidence.path).write_text("changed\n", encoding="utf-8")
    service.propagate_evidence_staleness(memory.id, {evidence.repository_id: workspace})
    assert memory.requires_revalidation is True
    assert "stale or missing" in memory.revalidation_reason
    (git_repo / evidence.path).unlink()
    assert EvidenceService(session).check_status(evidence_id, workspace).status == EvidenceStatus.MISSING


def test_one_stale_source_with_other_valid_evidence_marks_revalidation_but_preserves_status(session, git_repo: Path, workspace):
    project, _repo, _snapshot, _task, _run, evidence_id = _evidence_fixture(session, git_repo, workspace)
    second_memory = MemoryService(session).create_memory(
        memory_type=MemoryType.SEMANTIC,
        scope=KnowledgeScope.PROJECT,
        project_id=project.id,
        statement="Stable source reference.",
        confidence=0.7,
        validation_status=ValidationStatus.VALIDATED,
        source_evidence_ids=[evidence_id, "LEVID_STABLE"],
    )
    evidence = session.get(EvidenceReferenceORM, evidence_id)
    assert evidence is not None
    (git_repo / evidence.path).write_text("changed\n", encoding="utf-8")
    MemoryService(session).propagate_evidence_staleness(second_memory.id, {evidence.repository_id: workspace})
    assert second_memory.requires_revalidation is True
    assert second_memory.validation_status == ValidationStatus.VALIDATED.value


def test_learning_candidate_lifecycle_and_provenance(session):
    service = LearningService(session)
    with pytest.raises(ValueError):
        service.create_candidate(
            candidate_type=LearningCandidateType.PATTERN,
            statement="No provenance.",
            proposed_scope=KnowledgeScope.DFG,
        )
    candidate = service.create_candidate(
        candidate_type=LearningCandidateType.PATTERN,
        statement="Repository snapshots reuse unchanged state.",
        evidence_ids=["LEVID_000001"],
        proposed_scope=KnowledgeScope.PROJECT,
        confidence=0.7,
        actor=Actor.CODEX,
    )
    assert candidate.id == "LLEARN_000001"
    assert candidate.status == LearningCandidateStatus.PENDING.value
    service.mark_needs_more_evidence(candidate.id, reason="One source is not enough")
    assert candidate.status == LearningCandidateStatus.NEEDS_MORE_EVIDENCE.value
    assert (
        session.scalar(
            select(AuditEventORM).where(AuditEventORM.event_type == "LEARNING_CANDIDATE_NEEDS_MORE_EVIDENCE")
        )
        is not None
    )
    service.validate_candidate(candidate.id, notes="Fixture validation")
    assert candidate.status == LearningCandidateStatus.VALIDATED.value
    rejected = service.create_candidate(
        candidate_type=LearningCandidateType.OPTIMIZATION,
        statement="Use faster hashing.",
        evidence_ids=["LEVID_000002"],
    )
    service.reject_candidate(rejected.id, reason="Insufficient benefit")
    assert rejected.status == LearningCandidateStatus.REJECTED.value


def test_documentation_derived_candidate_not_auto_promoted(session):
    project = ProjectRegistryService(session).register_project("Doc Learning")
    candidate = LearningService(session).create_from_documentation(
        project_id=project.id,
        source_document_ref="docs/phases/PHASE_1_6_EVIDENCE_RETRIEVAL.md#ranking",
        statement="Retrieval ranking uses explicit lexical weights.",
        candidate_type=LearningCandidateType.PROCEDURE,
        proposed_scope=KnowledgeScope.PROJECT,
        actor=Actor.CODEX,
    )
    assert candidate.status == LearningCandidateStatus.PENDING.value
    assert session.scalars(select(MemoryEntryORM)).all() == []


def test_task_experience_may_produce_no_learning_candidate(session):
    project, _repo, _snapshot, task, run = _task_fixture(session)
    result = MemoryService(session).capture_task_experience(
        task_id=task.id,
        run_id=run.id,
        outcome="SUCCEEDED",
        tests="passed",
        summary="No reusable learning.",
        actor=Actor.CODEX,
    )
    assert result.outcome == "NO_LEARNING_CANDIDATE"
    assert result.memory.scope == KnowledgeScope.PROJECT.value
    assert result.learning_candidates == []


def test_failure_memory_creates_failure_pattern_without_global_promotion(session):
    project, _repo, _snapshot, task, run = _task_fixture(session)
    result = MemoryService(session).capture_failure_memory(
        FailureExperience(
            what_failed="Alembic loaded a sidecar file",
            context="Migration verification",
            error_summary="Null bytes in sidecar",
            attempted_approaches=["rerun migration"],
            root_cause="macOS sidecar file",
            final_fix="remove sidecar before migration",
            test_result="passed",
            regression_result="passed",
            task_id=task.id,
            run_id=run.id,
            evidence_ids=["LEVID_000001"],
        ),
        actor=Actor.CODEX,
    )
    candidate = result.learning_candidates[0]
    assert result.memory.memory_type == MemoryType.EPISODIC.value
    assert candidate.candidate_type == LearningCandidateType.FAILURE_PATTERN.value
    assert candidate.proposed_scope == KnowledgeScope.PROJECT.value


def test_human_correction_creates_correction_candidate_and_audit(session):
    candidate = LearningService(session).create_human_correction(
        project_id=None,
        statement="Confidence is not validation.",
        reason="Human correction",
        source_memory_ids=["LMEM_000001"],
        proposed_scope=KnowledgeScope.DFG,
    )
    assert candidate.candidate_type == LearningCandidateType.KNOWLEDGE_CORRECTION.value
    assert session.scalar(select(AuditEventORM).where(AuditEventORM.event_type == "HUMAN_CORRECTION_CAPTURED")) is not None


def test_knowledge_firewall_blocks_client_global_and_unsanitized_promotion(session, git_repo: Path, workspace):
    registry = ProjectRegistryService(session)
    client_project = registry.register_project("Client Memory", project_type=ProjectType.CLIENT)
    with pytest.raises(ValueError):
        MemoryService(session).create_memory(
            memory_type=MemoryType.SEMANTIC,
            scope=KnowledgeScope.GLOBAL,
            project_id=client_project.id,
            statement="Client implementation detail.",
            confidence=0.9,
            validation_status=ValidationStatus.VALIDATED,
            source_evidence_ids=["LEVID_000001"],
        )
    candidate = LearningService(session).create_candidate(
        candidate_type=LearningCandidateType.PATTERN,
        statement="Client pattern needs sanitization.",
        project_id=client_project.id,
        evidence_ids=["LEVID_000001"],
        proposed_scope=KnowledgeScope.GLOBAL,
    )
    assert candidate.source_classification == KnowledgeFirewallClassification.PRIVATE.value
    assert candidate.sanitization_status == SanitizationStatus.REQUIRED.value
    LearningService(session).validate_candidate(candidate.id)
    with pytest.raises(ValueError):
        LearningService(session).promote_candidate(candidate.id, target_scope=KnowledgeScope.GLOBAL, actor=Actor.HUMAN)


def test_authorized_explicit_promotion_succeeds_and_autonomous_global_promotion_blocked(session, git_repo: Path, workspace):
    _project, _repo, _snapshot, _task, _run, evidence_id = _evidence_fixture(session, git_repo, workspace)
    service = LearningService(session)
    candidate = service.create_candidate(
        candidate_type=LearningCandidateType.PATTERN,
        statement="Repository snapshots use deterministic SHA-256 manifest identity and reuse unchanged state.",
        evidence_ids=[evidence_id],
        proposed_scope=KnowledgeScope.DFG,
        sanitization_status=SanitizationStatus.NOT_REQUIRED,
        actor=Actor.CODEX,
    )
    service.validate_candidate(candidate.id)
    with pytest.raises(ValueError):
        service.promote_candidate(candidate.id, target_scope=KnowledgeScope.GLOBAL, actor=Actor.LUCIUS)
    memory = service.promote_candidate(candidate.id, target_scope=KnowledgeScope.GLOBAL, actor=Actor.HUMAN)
    assert memory.scope == KnowledgeScope.GLOBAL.value
    assert memory.source_reference == candidate.id
    assert memory.source_evidence_ids == [evidence_id]


def test_memory_retrieval_does_not_return_evidence_reference(session):
    memory = MemoryService(session).create_memory(
        memory_type=MemoryType.SEMANTIC,
        scope=KnowledgeScope.DFG,
        statement="Memory is context, not current evidence.",
        confidence=0.6,
        source_reference="ADR-024",
    )
    result = MemoryService(session).retrieve_memory(query_terms=["context"])
    assert result.matches[0].memory_id == memory.id
    assert not isinstance(result.matches[0], EvidenceService)


def test_audit_memory_and_learning_events(session):
    memory = MemoryService(session).create_memory(
        memory_type=MemoryType.SEMANTIC,
        scope=KnowledgeScope.DFG,
        statement="Audit memory events.",
        confidence=0.6,
        source_reference="source",
    )
    MemoryService(session).mark_requires_revalidation(memory.id, reason="check")
    MemoryService(session).clear_revalidation_flag(memory.id)
    candidate = LearningService(session).create_candidate(
        candidate_type=LearningCandidateType.PATTERN,
        statement="Audit learning events.",
        evidence_ids=["LEVID_000001"],
    )
    LearningService(session).validate_candidate(candidate.id)
    events = {event.event_type for event in session.scalars(select(AuditEventORM)).all()}
    assert {
        "MEMORY_CREATED",
        "MEMORY_REVALIDATION_REQUIRED",
        "MEMORY_REVALIDATION_CLEARED",
        "LEARNING_CANDIDATE_CREATED",
        "LEARNING_CANDIDATE_VALIDATED",
    }.issubset(events)


def test_internal_learning_and_failure_fixtures_are_project_scoped(session):
    project = ProjectRegistryService(session).register_project("Internal Fixture")
    memory = MemoryService(session).create_memory(
        memory_type=MemoryType.PROJECT,
        scope=KnowledgeScope.PROJECT,
        project_id=project.id,
        statement="Repository snapshots use deterministic SHA-256 manifest identity and reuse unchanged state.",
        confidence=0.75,
        validation_status=ValidationStatus.CANDIDATE,
        source_reference="Phase 1.4B documentation fixture",
        created_by=Actor.CODEX,
    )
    candidate = LearningService(session).create_candidate(
        candidate_type=LearningCandidateType.PATTERN,
        statement=memory.statement,
        project_id=project.id,
        source_memory_ids=[memory.id],
        proposed_scope=KnowledgeScope.PROJECT,
        actor=Actor.CODEX,
    )
    assert memory.scope == KnowledgeScope.PROJECT.value
    assert candidate.proposed_scope == KnowledgeScope.PROJECT.value
    assert candidate.proposed_scope != KnowledgeScope.GLOBAL.value


def test_alembic_0003_to_head_and_clean_head(tmp_path: Path):
    for sidecar in Path("alembic").glob("**/._*"):
        sidecar.unlink()
    from_0003 = tmp_path / "from_0003.sqlite3"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{from_0003}")
    command.upgrade(cfg, "0003_evidence_retrieval")
    command.upgrade(cfg, "head")

    clean = tmp_path / "clean.sqlite3"
    clean_cfg = Config("alembic.ini")
    clean_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{clean}")
    command.upgrade(clean_cfg, "head")
    assert from_0003.exists()
    assert clean.exists()
