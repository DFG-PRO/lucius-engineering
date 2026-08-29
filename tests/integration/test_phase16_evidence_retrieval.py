from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from lucius.domain.enums import Actor, AllowedAction, AuthorityLevel, EvidenceStatus, RetrievalWarningCode, SourceType
from lucius.evidence.service import EvidenceService
from lucius.persistence.orm import AuditEventORM, EvidenceReferenceORM, RepositorySnapshotORM, TaskRunORM
from lucius.persistence.repositories import RepositorySnapshotService
from lucius.projects.service import ProjectRegistryService
from lucius.repositories.errors import LuciusRepositoryError, RepositoryErrorCode
from lucius.repositories.local_git import LocalGitRepositoryAdapter
from lucius.retrieval.discovery import discover_candidates
from lucius.retrieval.query import derive_query_terms
from lucius.retrieval.ranking import WEIGHTS, rank_candidates
from lucius.retrieval.schemas import RetrievalRequest
from lucius.retrieval.service import RetrievalService
from lucius.tasks.service import TaskService

from tests.conftest import run_git


def _add_retrieval_files(repo: Path) -> None:
    (repo / "src" / "lucius" / "repositories").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "lucius" / "repositories" / "hashing.py").write_text(
        "def manifest_hash(files):\n    return 'manifest_hash:' + str(len(files))\n",
        encoding="utf-8",
    )
    (repo / "src" / "lucius" / "repositories" / "local_git.py").write_text(
        "class RepositorySnapshot:\n    def build_snapshot(self):\n        return 'snapshot reuse manifest identity'\n",
        encoding="utf-8",
    )
    (repo / "docs" / "adr").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "adr" / "ADR-001.md").write_text(
        "# Snapshot Reuse\n\nRepositorySnapshot reuse preserves manifest_hash identity.\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_snapshot_reuse.py").write_text(
        "def test_snapshot_reuse():\n    assert 'manifest_hash'\n",
        encoding="utf-8",
    )
    (repo / "package.json").write_text('{"scripts":{"test":"vitest"},"devDependencies":{"vitest":"latest"}}\n', encoding="utf-8")
    (repo / "notes.txt").write_text("gardening calendar unrelated prose\n", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "add retrieval files")


def _retrieval_fixture(session, git_repo: Path, workspace):
    _add_retrieval_files(git_repo)
    registry = ProjectRegistryService(session)
    project = registry.register_project("Retrieval Project")
    repository = registry.attach_repository(project_id=project.id, name="Repo", location=git_repo, workspace_context=workspace)
    snapshot = RepositorySnapshotService(session).inspect_repository(repository_id=repository.id, workspace_context=workspace)
    task_service = TaskService(session)
    task = task_service.create_task(
        project_id=project.id,
        title="Change snapshot reuse behavior",
        objective="Modify repository snapshot reuse logic while preserving deterministic manifest identity.",
        authority_level=AuthorityLevel.L1,
        created_by=Actor.CODEX,
    )
    task_service.create_or_update_contract(
        task_id=task.id,
        objective="Change snapshot reuse behavior with manifest_hash and RepositorySnapshot evidence.",
        acceptance_criteria=[
            {"id": "AC-001", "statement": "RepositorySnapshot reuse behavior is changed", "status": "PENDING"},
            {"id": "AC-002", "statement": "manifest_hash identity remains deterministic", "status": "PENDING"},
            {"id": "AC-003", "statement": "quantum banana evidence does not exist", "status": "PENDING"},
        ],
        repository_ids=[repository.id],
        allowed_actions=[AllowedAction.READ_REPOSITORY, AllowedAction.READ_DOCUMENTATION],
        authority_level=AuthorityLevel.L1,
        actor=Actor.CODEX,
    )
    task_service.mark_ready(task.id, actor=Actor.CODEX)
    run = task_service.start_run(task.id, snapshot_id=snapshot.snapshot_id, actor=Actor.CODEX)
    return project, repository, snapshot, task, run


def test_retrieval_request_construction_and_query_derivation(session, git_repo: Path, workspace):
    project, repository, snapshot, task, run = _retrieval_fixture(session, git_repo, workspace)
    request = RetrievalService(session).build_request(
        task_id=task.id,
        task_run_id=run.id,
        snapshot_ids=[snapshot.snapshot_id],
        explicit_terms=["ProjectRegistryService", "manifest_hash", "manifest_hash"],
    )
    assert request.project_id == project.id
    assert request.repository_ids == [repository.id]
    assert request.snapshot_ids == [snapshot.snapshot_id]
    assert "projectregistryservice" in request.query_terms
    assert "manifest_hash" in request.query_terms
    assert request.query_terms.count("manifest_hash") == 1


def test_technical_identifier_and_duplicate_term_handling():
    terms = derive_query_terms(
        task_title="Use RepositorySnapshot ProjectRegistryService",
        task_objective="Preserve manifest_hash manifest_hash buildSnapshot",
        contract_objective="",
        acceptance_criteria=[],
        explicit_terms=["src/lucius/repositories/hashing.py"],
    )
    assert "repositorysnapshot" in terms
    assert "projectregistryservice" in terms
    assert "manifest_hash" in terms
    assert "src/lucius/repositories/hashing.py" in terms
    assert terms.count("manifest_hash") == 1


def test_ranking_signals_and_deterministic_tie_breaking(session, git_repo: Path, workspace):
    project, repository, snapshot, task, _run = _retrieval_fixture(session, git_repo, workspace)
    request = RetrievalService(session).build_request(
        task_id=task.id,
        snapshot_ids=[snapshot.snapshot_id],
        explicit_terms=["hashing.py", "repositories", "manifest_hash"],
    )
    adapter = LocalGitRepositoryAdapter(repository.location, workspace)
    candidates, warnings = discover_candidates(
        request=request,
        repository_id=repository.id,
        snapshot_id=snapshot.snapshot_id,
        manifest_files=session.get(RepositorySnapshotORM, snapshot.snapshot_id).manifest["files"],
        adapter=adapter,
    )
    ranked = rank_candidates(candidates, request)
    assert warnings == []
    assert ranked[0].path == "src/lucius/repositories/hashing.py"
    assert any("exact path/name match" in reason or "basename match" in reason for reason in ranked[0].match_reasons)
    assert any("path token match" in reason for reason in ranked[0].match_reasons)
    assert any("technical identifier match" in reason for reason in ranked[0].match_reasons)
    tied = sorted([item.path for item in ranked if item.score == ranked[-1].score])
    assert [item.path for item in ranked if item.score == ranked[-1].score] == tied
    assert WEIGHTS["technical_identifier"] > WEIGHTS["content_match"]


def test_source_type_discovery(session, git_repo: Path, workspace):
    _project, repository, snapshot, task, _run = _retrieval_fixture(session, git_repo, workspace)
    package = RetrievalService(session).retrieve_for_task(
        task_id=task.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["ADR-001.md", "test_snapshot_reuse.py", "package.json"],
        max_results=10,
    )
    source_types = {source.path: source.source_type for source in package.ranked_sources}
    assert source_types["docs/adr/ADR-001.md"] == SourceType.DOCUMENTATION
    assert source_types["tests/test_snapshot_reuse.py"] == SourceType.TEST
    assert source_types["package.json"] == SourceType.CONFIG


def test_context_budgets_and_snippet_bounds(session, git_repo: Path, workspace):
    _project, repository, snapshot, task, _run = _retrieval_fixture(session, git_repo, workspace)
    package = RetrievalService(session).retrieve_for_task(
        task_id=task.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["manifest_hash", "RepositorySnapshot", "snapshot"],
        max_results=2,
        max_total_bytes=80,
        max_snippet_bytes=32,
    )
    assert package.total_sources <= 2
    assert package.total_bytes <= 80
    assert all(len((source.snippet or "").encode("utf-8")) <= 32 for source in package.ranked_sources)
    assert any(warning["code"] == RetrievalWarningCode.CONTEXT_BUDGET_REACHED.value for warning in package.warnings)


def test_evidence_reference_persisted_with_provenance_hash_and_reasons(session, git_repo: Path, workspace):
    project, repository, snapshot, task, run = _retrieval_fixture(session, git_repo, workspace)
    package = RetrievalService(session).retrieve_for_task(
        task_id=task.id,
        task_run_id=run.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["manifest_hash"],
        actor=Actor.CODEX,
    )
    evidence = session.get(EvidenceReferenceORM, package.evidence_ids[0])
    assert evidence.project_id == project.id
    assert evidence.repository_id == repository.id
    assert evidence.snapshot_id == snapshot.snapshot_id
    assert evidence.task_id == task.id
    assert evidence.task_run_id == run.id
    assert len(evidence.content_hash) == 64
    assert evidence.match_reasons
    assert "manifest_hash" in package.ranked_sources[0].matched_terms or any(
        "manifest_hash" in reason for reason in evidence.match_reasons
    )


def test_evidence_current_stale_and_missing_status(session, git_repo: Path, workspace):
    _project, repository, snapshot, task, _run = _retrieval_fixture(session, git_repo, workspace)
    package = RetrievalService(session).retrieve_for_task(
        task_id=task.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["manifest_hash"],
        max_results=1,
    )
    evidence_id = package.evidence_ids[0]
    service = EvidenceService(session)
    assert service.check_status(evidence_id, workspace).status == EvidenceStatus.CURRENT
    evidence = session.get(EvidenceReferenceORM, evidence_id)
    (git_repo / evidence.path).write_text("changed content\n", encoding="utf-8")
    assert service.check_status(evidence_id, workspace).status == EvidenceStatus.STALE
    (git_repo / evidence.path).unlink()
    assert service.check_status(evidence_id, workspace).status == EvidenceStatus.MISSING


def test_stale_snapshot_detected(session, git_repo: Path, workspace):
    _project, repository, snapshot, task, _run = _retrieval_fixture(session, git_repo, workspace)
    (git_repo / "src" / "lucius" / "repositories" / "hashing.py").write_text("changed\n", encoding="utf-8")
    package = RetrievalService(session).retrieve_for_task(
        task_id=task.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["manifest_hash"],
    )
    assert package.evidence_ids == []
    assert any(warning["code"] == RetrievalWarningCode.SNAPSHOT_STALE.value for warning in package.warnings)
    assert session.scalar(select(AuditEventORM).where(AuditEventORM.event_type == "SNAPSHOT_STALE_DETECTED")) is not None


def test_sensitive_private_key_binary_and_oversized_content_excluded(session, git_repo: Path, workspace):
    (git_repo / ".env").write_text("SECRET_TOKEN=never evidence\n", encoding="utf-8")
    (git_repo / "private_key.pem").write_text("PRIVATE KEY never evidence\n", encoding="utf-8")
    (git_repo / "binary.bin").write_bytes(b"\x00manifest_hash")
    (git_repo / "large.txt").write_text("manifest_hash\n" + ("x" * (2 * 1024 * 1024 + 1)), encoding="utf-8")
    run_git(git_repo, "add", ".")
    run_git(git_repo, "commit", "-m", "add excluded files")
    project, repository, snapshot, task, _run = _retrieval_fixture(session, git_repo, workspace)
    package = RetrievalService(session).retrieve_for_task(
        task_id=task.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["SECRET_TOKEN", "PRIVATE KEY", "manifest_hash"],
        max_results=20,
    )
    snippets = "\n".join(source.snippet or "" for source in package.ranked_sources)
    paths = {source.path for source in package.ranked_sources}
    assert "SECRET_TOKEN" not in snippets
    assert "PRIVATE KEY" not in snippets
    assert ".env" not in paths
    assert "private_key.pem" not in paths
    assert "binary.bin" not in paths
    assert "large.txt" not in paths
    warning_codes = {warning["code"] for warning in package.warnings}
    assert RetrievalWarningCode.SOURCE_SKIPPED_SENSITIVE.value in warning_codes
    assert RetrievalWarningCode.SOURCE_SKIPPED_BINARY.value in warning_codes
    assert RetrievalWarningCode.SOURCE_SKIPPED_LARGE.value in warning_codes


def test_retrieval_traversal_and_symlink_escape_are_blocked(git_repo: Path, workspace):
    outside = git_repo.parent / "outside.txt"
    outside.write_text("outside manifest_hash\n", encoding="utf-8")
    (git_repo / "escape.txt").symlink_to(outside)
    adapter = LocalGitRepositoryAdapter(git_repo, workspace)
    with pytest.raises(LuciusRepositoryError) as traversal:
        adapter.read_file("../outside.txt")
    assert traversal.value.code == RepositoryErrorCode.UNSAFE_PATH
    with pytest.raises(LuciusRepositoryError) as symlink:
        adapter.read_file("escape.txt")
    assert symlink.value.code == RepositoryErrorCode.UNSAFE_PATH


def test_dirty_repository_warning_preserved(session, git_repo: Path, workspace):
    _add_retrieval_files(git_repo)
    (git_repo / "dirty.txt").write_text("manifest_hash dirty\n", encoding="utf-8")
    registry = ProjectRegistryService(session)
    project = registry.register_project("Dirty Retrieval")
    repository = registry.attach_repository(project_id=project.id, name="Repo", location=git_repo, workspace_context=workspace)
    snapshot = RepositorySnapshotService(session).inspect_repository(repository_id=repository.id, workspace_context=workspace)
    task_service = TaskService(session)
    task = task_service.create_task(project_id=project.id, title="Dirty", objective="manifest_hash dirty", authority_level=AuthorityLevel.L1)
    task_service.create_or_update_contract(
        task_id=task.id,
        objective="manifest_hash dirty",
        acceptance_criteria=["manifest_hash dirty"],
        repository_ids=[repository.id],
        authority_level=AuthorityLevel.L1,
    )
    task_service.mark_ready(task.id)
    package = RetrievalService(session).retrieve_for_task(
        task_id=task.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["dirty.txt"],
    )
    assert any(warning["code"] == RetrievalWarningCode.REPOSITORY_DIRTY.value for warning in package.warnings)


def test_acceptance_criteria_coverage_and_empty_retrieval(session, git_repo: Path, workspace):
    _project, repository, snapshot, task, _run = _retrieval_fixture(session, git_repo, workspace)
    package = RetrievalService(session).retrieve_for_task(
        task_id=task.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["manifest_hash"],
    )
    coverage = {item.key: item.status for item in package.coverage_summary["acceptance_criteria"]}
    assert coverage["AC-001"] == "EVIDENCE_FOUND"
    assert coverage["AC-003"] == "NO_EVIDENCE_FOUND"

    empty = RetrievalService(session).retrieve_for_task(
        task_id=task.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["zzzz-no-match"],
        requested_source_types=[SourceType.GIT],
    )
    assert empty.evidence_ids == []
    assert any(warning["code"] == RetrievalWarningCode.NO_RELEVANT_EVIDENCE.value for warning in empty.warnings)


def test_audit_events_and_context_package_determinism(session, git_repo: Path, workspace):
    _project, repository, snapshot, task, run = _retrieval_fixture(session, git_repo, workspace)
    service = RetrievalService(session)
    first = service.retrieve_for_task(
        task_id=task.id,
        task_run_id=run.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["manifest_hash"],
    )
    second = service.retrieve_for_task(
        task_id=task.id,
        task_run_id=run.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["manifest_hash"],
    )
    assert first.id == second.id
    assert [source.path for source in first.ranked_sources] == [source.path for source in second.ranked_sources]
    event_types = {event.event_type for event in session.scalars(select(AuditEventORM)).all()}
    assert {"RETRIEVAL_STARTED", "RETRIEVAL_COMPLETED", "EVIDENCE_CAPTURED", "CONTEXT_PACKAGE_CREATED"}.issubset(event_types)


def test_multiple_repositories_scoped_and_unrelated_project_rejected(session, git_repo: Path, tmp_path: Path, workspace):
    _project, repository, snapshot, task, _run = _retrieval_fixture(session, git_repo, workspace)
    other_repo = tmp_path / "workspace" / "other"
    other_repo.mkdir(parents=True)
    run_git(other_repo, "init")
    run_git(other_repo, "config", "user.email", "lucius@example.test")
    run_git(other_repo, "config", "user.name", "Lucius Tests")
    run_git(other_repo, "checkout", "-b", "main")
    (other_repo / "README.md").write_text("manifest_hash other project\n", encoding="utf-8")
    run_git(other_repo, "add", "README.md")
    run_git(other_repo, "commit", "-m", "initial")
    other_workspace = type(workspace)(workspace_id="other", allowed_roots=[other_repo.parent])
    other_project = ProjectRegistryService(session).register_project("Other Retrieval Project")
    other_registration = ProjectRegistryService(session).attach_repository(
        project_id=other_project.id,
        name="Other",
        location=other_repo,
        workspace_context=other_workspace,
    )
    other_snapshot = RepositorySnapshotService(session).inspect_repository(
        repository_id=other_registration.id,
        workspace_context=other_workspace,
    )

    package = RetrievalService(session).retrieve_for_task(
        task_id=task.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["manifest_hash"],
    )
    assert {source.repository_id for source in package.ranked_sources} == {repository.id}
    with pytest.raises(ValueError):
        RetrievalService(session).retrieve_for_task(
            task_id=task.id,
            snapshot_ids=[other_snapshot.snapshot_id],
            workspace_contexts={other_registration.id: other_workspace},
            explicit_terms=["manifest_hash"],
        )


def test_git_evidence_source(session, git_repo: Path, workspace):
    _project, repository, snapshot, task, _run = _retrieval_fixture(session, git_repo, workspace)
    package = RetrievalService(session).retrieve_for_task(
        task_id=task.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["commit"],
        requested_source_types=[SourceType.GIT],
    )
    assert package.ranked_sources[0].source_type == SourceType.GIT
    assert snapshot.snapshot_id == package.ranked_sources[0].snapshot_id


def test_benchmark_snapshot_reuse_ranks_relevant_sources_above_unrelated(session, git_repo: Path, workspace):
    _project, repository, snapshot, task, _run = _retrieval_fixture(session, git_repo, workspace)
    package = RetrievalService(session).retrieve_for_task(
        task_id=task.id,
        snapshot_ids=[snapshot.snapshot_id],
        workspace_contexts={repository.id: workspace},
        explicit_terms=["manifest_hash", "build_snapshot", "RepositorySnapshot", "snapshot reuse"],
        max_results=10,
    )
    paths = [source.path for source in package.ranked_sources]
    assert "src/lucius/repositories/hashing.py" in paths[:3]
    assert "src/lucius/repositories/local_git.py" in paths[:4]
    assert "notes.txt" not in paths[:4]


def test_alembic_0002_to_head_and_clean_head(tmp_path: Path):
    for sidecar in Path("alembic").glob("**/._*"):
        sidecar.unlink()
    from_0002 = tmp_path / "from_0002.sqlite3"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{from_0002}")
    command.upgrade(cfg, "0002_project_task_contracts")
    command.upgrade(cfg, "head")

    clean = tmp_path / "clean.sqlite3"
    clean_cfg = Config("alembic.ini")
    clean_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{clean}")
    command.upgrade(clean_cfg, "head")

    assert from_0002.exists()
    assert clean.exists()
