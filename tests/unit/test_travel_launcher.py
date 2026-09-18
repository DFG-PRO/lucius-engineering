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

    reg_file = lucius_root / "src" / "lucius" / "projects" / "dfg_canonical_registry.json"
    reg_file.parent.mkdir(parents=True)
    real_reg = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering/src/lucius/projects/dfg_canonical_registry.json")
    reg_file.write_text(real_reg.read_text())

    backlog = darwin_root / "src" / "darwin" / "backlog" / "master_backlog.py"
    backlog.parent.mkdir(parents=True)
    backlog.write_text("CANONICAL_MASTER_BACKLOG_ITEMS = []")

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
    assert report.registry_ok is True
    assert report.feeder_available is True
    assert report.disk_ok is True
    assert len(report.errors) == 0


def test_travel_launcher_preflight_on_canonical_repositories():
    launcher = TravelLauncher()
    report = launcher.preflight()
    assert report.all_passed is True
    assert report.registry_ok is True
    assert report.feeder_available is True
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


def test_travel_launcher_launch_wiring_scripted_provider(tmp_path: Path):
    """Regression test: verify TravelLauncher.launch() directly constructs the runtime loop
    with ModelExecutionRouter properly wired, without raising TypeError.
    """
    lucius_root = tmp_path / "lucius"
    darwin_root = tmp_path / "darwin"
    billy_root = tmp_path / "billy"

    for r in (lucius_root, darwin_root, billy_root):
        (r / ".git").mkdir(parents=True)

    reg_file = lucius_root / "src" / "lucius" / "projects" / "dfg_canonical_registry.json"
    reg_file.parent.mkdir(parents=True)
    real_reg = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering/src/lucius/projects/dfg_canonical_registry.json")
    reg_file.write_text(real_reg.read_text())

    backlog = darwin_root / "src" / "darwin" / "backlog" / "master_backlog.py"
    backlog.parent.mkdir(parents=True)
    backlog.write_text("CANONICAL_MASTER_BACKLOG_ITEMS = []")

    launcher = TravelLauncher(
        lucius_root=lucius_root,
        darwin_root=darwin_root,
        billy_root=billy_root,
        min_disk_free_gb=0.01,
        max_worktree_limit=200,
    )

    engine = create_sqlite_engine(tmp_path / "test_launch.sqlite")
    create_all(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        result = launcher.launch(
            session,
            hours=0.01,
            max_cycles=1,
            provider_type="scripted",
        )
        assert result.status == "IDLE"
        assert result.stop_reason == "IDLE_NO_ELIGIBLE_WORK"
        assert result.cycles_attempted == 1


def test_travel_launcher_launch_smoke_mode(tmp_path: Path):
    """Verify smoke mode runs 1 cycle, bypasses feeder, and constructs Ollama provider cleanly."""
    launcher = TravelLauncher()
    engine = create_sqlite_engine(tmp_path / "smoke.sqlite")
    create_all(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        result = launcher.launch(session, smoke=True, provider_type="ollama")
        assert result.status == "IDLE"
        assert result.stop_reason == "IDLE_NO_ELIGIBLE_WORK"
        assert result.cycles_attempted == 1
        assert result.tasks_fed == 0


def test_travel_launcher_cli_smoke_main(monkeypatch, tmp_path: Path, capsys):
    """Test CLI main() with --mode travel --smoke --provider scripted."""
    from lucius.runtime import launcher as launcher_mod

    db_path = tmp_path / "cli_smoke.sqlite"
    test_args = [
        "lucius.runtime.launcher",
        "--mode", "travel",
        "--smoke",
        "--provider", "scripted",
        "--db-path", str(db_path),
    ]
    monkeypatch.setattr("sys.argv", test_args)

    launcher_mod.main()
    captured = capsys.readouterr()
    assert "LUCIUS TRAVEL MODE PREFLIGHT" in captured.out
    assert "Overall Preflight Passed: True" in captured.out
    assert "Launching Travel Session (travel): SMOKE TEST" in captured.out
    assert "SESSION EXECUTION SUMMARY" in captured.out
    assert "Session Status:      IDLE" in captured.out
    assert "Cycles Attempted:    1" in captured.out
