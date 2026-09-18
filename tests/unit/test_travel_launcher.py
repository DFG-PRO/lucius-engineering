from __future__ import annotations

from pathlib import Path
import pytest
from sqlalchemy.orm import Session

from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.runtime.launcher import PreflightReport, TravelLauncher


def test_travel_launcher_preflight_validates_environment(tmp_path: Path):
    # Mock repos
    lucius_root = tmp_path / "lucius"
    darwin_root = tmp_path / "darwin"
    billy_root = tmp_path / "billy"

    for r in (lucius_root, darwin_root, billy_root):
        (r / ".git").mkdir(parents=True)

    launcher = TravelLauncher(
        lucius_root=lucius_root,
        darwin_root=darwin_root,
        billy_root=billy_root,
        min_disk_free_gb=0.01,
        max_worktree_limit=200,
    )

    report = launcher.preflight()
    assert report.all_passed is True
    assert report.repository_checks == {"lucius": True, "darwin": True, "billy": True}
    assert report.disk_ok is True
    assert len(report.errors) == 0


def test_travel_launcher_preflight_fails_on_missing_repository(tmp_path: Path):
    lucius_root = tmp_path / "lucius"
    (lucius_root / ".git").mkdir(parents=True)

    launcher = TravelLauncher(
        lucius_root=lucius_root,
        darwin_root=tmp_path / "non_existent_darwin",
        billy_root=tmp_path / "non_existent_billy",
        min_disk_free_gb=0.01,
        max_worktree_limit=200,
    )

    report = launcher.preflight()
    assert report.all_passed is False
    assert report.repository_checks["darwin"] is False
    assert report.repository_checks["billy"] is False
    assert any("Repository missing" in e for e in report.errors)


def test_travel_launcher_preflight_fails_on_insufficient_disk(tmp_path: Path):
    lucius_root = tmp_path / "lucius"
    darwin_root = tmp_path / "darwin"
    billy_root = tmp_path / "billy"

    for r in (lucius_root, darwin_root, billy_root):
        (r / ".git").mkdir(parents=True)

    launcher = TravelLauncher(
        lucius_root=lucius_root,
        darwin_root=darwin_root,
        billy_root=billy_root,
        min_disk_free_gb=999999.0,  # impossibly high requirement
        max_worktree_limit=200,
    )

    report = launcher.preflight()
    assert report.all_passed is False
    assert report.disk_ok is False
    assert any("Insufficient free disk space" in e for e in report.errors)
