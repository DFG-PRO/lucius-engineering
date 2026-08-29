from __future__ import annotations

from pathlib import Path

import pytest

from lucius.repositories.base import RepositoryAdapter
from lucius.repositories.errors import LuciusRepositoryError, RepositoryErrorCode
from lucius.repositories.hashing import manifest_identity
from lucius.repositories.local_git import LocalGitRepositoryAdapter
from lucius.repositories.schemas import WorkspaceContext


def test_valid_local_git_detection(git_repo: Path, workspace: WorkspaceContext):
    adapter = LocalGitRepositoryAdapter(git_repo, workspace)
    adapter.validate()
    assert adapter.get_identity().root == str(git_repo.resolve())


def test_non_git_path_rejection(tmp_path: Path):
    path = tmp_path / "workspace" / "not-git"
    path.mkdir(parents=True)
    workspace = WorkspaceContext(workspace_id="test", allowed_roots=[path.parent])
    with pytest.raises(LuciusRepositoryError) as error:
        LocalGitRepositoryAdapter(path, workspace).validate()
    assert error.value.code == RepositoryErrorCode.NOT_A_GIT_REPOSITORY


def test_repo_outside_allowed_workspace_rejection(git_repo: Path, tmp_path: Path):
    workspace = WorkspaceContext(workspace_id="test", allowed_roots=[tmp_path / "other"])
    with pytest.raises(LuciusRepositoryError) as error:
        LocalGitRepositoryAdapter(git_repo, workspace).validate()
    assert error.value.code == RepositoryErrorCode.REPOSITORY_OUTSIDE_WORKSPACE


def test_path_traversal_rejection(git_repo: Path, workspace: WorkspaceContext):
    adapter = LocalGitRepositoryAdapter(git_repo, workspace)
    with pytest.raises(LuciusRepositoryError) as error:
        adapter.read_file("../README.md")
    assert error.value.code == RepositoryErrorCode.UNSAFE_PATH


def test_symlink_escape_rejection(git_repo: Path, workspace: WorkspaceContext, tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (git_repo / "escape.txt").symlink_to(outside)
    adapter = LocalGitRepositoryAdapter(git_repo, workspace)
    with pytest.raises(LuciusRepositoryError) as error:
        adapter.read_file("escape.txt")
    assert error.value.code == RepositoryErrorCode.UNSAFE_PATH


def test_safe_text_file_reading(git_repo: Path, workspace: WorkspaceContext):
    adapter = LocalGitRepositoryAdapter(git_repo, workspace)
    assert adapter.read_file("README.md") == "# Test Repo\n"


def test_oversized_file_rejection(git_repo: Path, workspace: WorkspaceContext):
    (git_repo / "large.txt").write_text("x" * 20, encoding="utf-8")
    adapter = LocalGitRepositoryAdapter(git_repo, workspace, max_text_file_size=8)
    with pytest.raises(LuciusRepositoryError) as error:
        adapter.read_file("large.txt")
    assert error.value.code == RepositoryErrorCode.FILE_TOO_LARGE


def test_binary_file_rejection(git_repo: Path, workspace: WorkspaceContext):
    (git_repo / "binary.bin").write_bytes(b"\x00\x01\x02")
    adapter = LocalGitRepositoryAdapter(git_repo, workspace)
    with pytest.raises(LuciusRepositoryError) as error:
        adapter.read_file("binary.bin")
    assert error.value.code == RepositoryErrorCode.BINARY_FILE


def test_sensitive_file_content_not_read(git_repo: Path, workspace: WorkspaceContext):
    (git_repo / ".env").write_text("SECRET_TOKEN=do-not-read\n", encoding="utf-8")
    adapter = LocalGitRepositoryAdapter(git_repo, workspace)
    with pytest.raises(LuciusRepositoryError) as error:
        adapter.read_file(".env")
    assert error.value.code == RepositoryErrorCode.PERMISSION_DENIED
    snapshot = adapter.build_snapshot()
    env_record = next(record for record in snapshot.manifest.files if record.path == ".env")
    assert env_record.is_sensitive is True
    assert env_record.content_hash is None


def test_git_branch_and_commit_detection(git_repo: Path, workspace: WorkspaceContext):
    state = LocalGitRepositoryAdapter(git_repo, workspace).get_git_state()
    assert state.branch == "main"
    assert len(state.commit_sha) == 40


def test_dirty_modified_and_untracked_detection(git_repo: Path, workspace: WorkspaceContext):
    (git_repo / "README.md").write_text("# Changed\n", encoding="utf-8")
    (git_repo / "new.txt").write_text("new\n", encoding="utf-8")
    state = LocalGitRepositoryAdapter(git_repo, workspace).get_git_state()
    assert state.is_dirty is True
    assert "README.md" in state.modified
    assert "new.txt" in state.untracked


def test_document_test_and_technology_discovery(git_repo: Path, workspace: WorkspaceContext):
    adapter = LocalGitRepositoryAdapter(git_repo, workspace)
    docs = adapter.discover_documents()
    tests = adapter.discover_tests()
    technologies = adapter.discover_technologies()
    assert {"path": "README.md", "kind": "README"} in docs
    assert any(item["path"] == "tests/test_sample.py" for item in tests)
    detected = {item["name"] for item in technologies["technologies"]}
    assert {"Python", "SQLAlchemy", "Pytest"}.issubset(detected)


def test_manifest_hashing_determinism(git_repo: Path, workspace: WorkspaceContext):
    adapter = LocalGitRepositoryAdapter(git_repo, workspace)
    first = adapter.build_snapshot().manifest.manifest_hash
    second = adapter.build_snapshot().manifest.manifest_hash
    manual = manifest_identity([record.model_dump(mode="json") for record in adapter.build_snapshot().manifest.files])
    assert first == second == manual


def test_read_only_adapter_exposes_no_mutation_capability():
    forbidden = {"write_file", "delete", "checkout", "commit", "merge", "push", "deploy"}
    assert not forbidden.intersection(dir(RepositoryAdapter))
    assert not forbidden.intersection(dir(LocalGitRepositoryAdapter))

