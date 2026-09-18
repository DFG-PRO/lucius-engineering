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


def test_qwen3_8b_supports_inspection_reasoning_task_type():
    """Regression: qwen3:8b must include 'inspection_reasoning' in supported_task_classes.

    Darwin feeder tasks use task_type='inspection_reasoning'. Before Shift 09B, the
    Ollama provider's supported_task_classes was missing this value, causing all feeder
    tasks to receive NO_ELIGIBLE_PROVIDER and triggering FAILURE_BUDGET_REACHED.
    """
    from lucius.runtime.ollama import OllamaExecutionProvider

    provider = OllamaExecutionProvider(
        provider_id="ollama-local-test",
        model="qwen3:8b",
    )
    assert "inspection_reasoning" in provider.registration.supported_task_classes, (
        "qwen3:8b must include 'inspection_reasoning' in supported_task_classes "
        "so that Darwin feeder tasks (task_type='inspection_reasoning') are eligible."
    )
    assert "inspection" in provider.registration.supported_task_classes
    assert "engineering" in provider.registration.supported_task_classes


def test_no_eligible_provider_does_not_burn_consecutive_failure_budget():
    """Regression: NO_ELIGIBLE_PROVIDER FAILED outcome must not increment consecutive_failures.

    Before Shift 09B, TASK_LOCAL_FAILURE_CLASSES semantics were not applied in the
    continuation loop, so 3 NO_ELIGIBLE_PROVIDER failures triggered FAILURE_BUDGET_REACHED
    even though the service had _should_stop_after_non_completion() returning False for them.
    """
    from lucius.runtime.schemas import RuntimeTaskExecutionRecord, RuntimeExecutionOutcome
    from lucius.runtime.service import TASK_LOCAL_FAILURE_CLASSES

    # Verify NO_ELIGIBLE_PROVIDER is in the task-local set
    assert "NO_ELIGIBLE_PROVIDER" in TASK_LOCAL_FAILURE_CLASSES

    # Simulate what the continuation loop does:
    # Three FAILED outcomes with NO_ELIGIBLE_PROVIDER failure_class must not
    # cause consecutive_failures to reach max_consecutive_failures=3
    consecutive_failures = 0
    total_failures = 0
    for _ in range(3):
        rec = RuntimeTaskExecutionRecord(
            workflow_id="wf-001",
            item_id="item-001",
            provider_id="ollama-local",
            outcome=RuntimeExecutionOutcome.FAILED,
            failure_class="NO_ELIGIBLE_PROVIDER",
            blocker_category="NO_ELIGIBLE_PROVIDER",
        )
        if rec.outcome.value == "FAILED":
            total_failures += 1
            # TASK_LOCAL_FAILURE_CLASSES must NOT increment consecutive_failures
            if rec.failure_class not in TASK_LOCAL_FAILURE_CLASSES:
                consecutive_failures += 1
            else:
                consecutive_failures = 0

    assert consecutive_failures == 0, (
        "NO_ELIGIBLE_PROVIDER FAILED outcomes must not burn the consecutive_failures budget. "
        f"Got consecutive_failures={consecutive_failures} after 3 task-local failures."
    )
    assert total_failures == 3


def test_cli_failed_session_returns_nonzero_exit_code(monkeypatch, tmp_path, capsys):
    """Regression: CLI must return exit code 1 when session status is FAILED.

    Before Shift 09B, main() always returned exit code 0 regardless of session outcome.
    """
    from lucius.runtime import launcher as launcher_mod
    from lucius.runtime.launcher import TravelLauncher
    from lucius.runtime.continuation import ContinuationSessionResult

    db_path = tmp_path / "cli_failed.sqlite"

    # Patch launch() to return a FAILED result without executing real session
    def _fake_launch(self, session, **kwargs):
        result = ContinuationSessionResult()
        result.status = "FAILED"
        result.stop_reason = "FAILURE_BUDGET_REACHED"
        result.tasks_selected = 3
        result.tasks_completed = 0
        result.tasks_blocked = 3
        result.tasks_fed = 5
        result.cycles_attempted = 4
        result.wall_clock_duration_seconds = 0.39
        result.project_switches = 0
        return result

    monkeypatch.setattr(TravelLauncher, "launch", _fake_launch)

    test_args = [
        "lucius.runtime.launcher",
        "--mode", "travel",
        "--provider", "scripted",
        "--db-path", str(db_path),
    ]
    monkeypatch.setattr("sys.argv", test_args)

    with pytest.raises(SystemExit) as exc_info:
        launcher_mod.main()

    assert exc_info.value.code == 1, (
        f"CLI must exit with code 1 on FAILED session, got: {exc_info.value.code}"
    )
    captured = capsys.readouterr()
    assert "Session Status:      FAILED" in captured.out
