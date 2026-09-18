from __future__ import annotations

from pathlib import Path
import subprocess
import pytest

from lucius.repositories.worktree_hygiene import WorktreeClassification, WorktreeHygieneService


def test_worktree_hygiene_scans_main_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "README.md").write_text("# Repo", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo, check=True)

    service = WorktreeHygieneService(repo_root=repo)
    report = service.scan()

    assert report.total_scanned == 1
    assert report.active_count == 1
    assert report.records[0].is_main_root is True
    assert report.records[0].classification == WorktreeClassification.ACTIVE


def test_worktree_hygiene_classifies_clean_and_dirty(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "README.md").write_text("# Repo", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo, check=True)

    # Add clean worktree
    wt_clean = tmp_path / "wt_clean"
    subprocess.run(["git", "worktree", "add", str(wt_clean), "-b", "clean-branch"], cwd=repo, check=True, capture_output=True)

    # Add dirty worktree
    wt_dirty = tmp_path / "wt_dirty"
    subprocess.run(["git", "worktree", "add", str(wt_dirty), "-b", "dirty-branch"], cwd=repo, check=True, capture_output=True)
    (wt_dirty / "uncommitted.txt").write_text("dirty content", encoding="utf-8")

    service = WorktreeHygieneService(repo_root=repo)
    report = service.scan()

    assert report.total_scanned == 3
    assert report.active_count == 1
    assert report.clean_prunable_count == 1
    assert report.dirty_count == 1

    clean_record = [r for r in report.records if r.path == str(wt_clean.resolve())][0]
    assert clean_record.classification == WorktreeClassification.CLEAN_PRUNABLE

    dirty_record = [r for r in report.records if r.path == str(wt_dirty.resolve())][0]
    assert dirty_record.classification == WorktreeClassification.DIRTY

    # Test default dry_run prune leaves worktrees intact
    dry_prune_report = service.prune_safe_candidates(dry_run=True)
    assert dry_prune_report.dry_run is True
    assert len(dry_prune_report.pruned_paths) == 0
    assert wt_clean.exists()
