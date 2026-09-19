"""Integration test for Shift 10N — Multi-Day Travel Productivity Feed Simulation."""

from pathlib import Path
import pytest
import sqlite3

from lucius.portfolio.manifest import (
    build_travel_mission_manifest,
    calculate_travel_metrics,
    replenish_from_canonical_roadmap,
    sort_manifest_items,
)


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).parent.parent.parent.resolve()


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "shift_10n_simulation.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS work_package_executions (
            work_package_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            status TEXT NOT NULL,
            execution_time_seconds REAL NOT NULL,
            completed_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_shift_10n_accelerated_feed_simulation(repo_root: Path, temp_db: Path):
    """Simulates multi-day backlog consumption on an isolated temporary SQLite database."""
    manifest = build_travel_mission_manifest(repo_root)
    assert manifest.total_packages_count > 0

    sorted_packages = sort_manifest_items(manifest.work_packages)
    eligible_packages = [pkg for pkg in sorted_packages if pkg.current_state in ("READY", "AUTO_PREPARABLE")]

    conn = sqlite3.connect(str(temp_db))
    completed_ids: list[str] = []
    blocked_ids: list[str] = []
    useful_work_seconds = 0.0
    idle_sleep_seconds = 0.0
    scheduler_cycles = 0

    # Cycle 1: Execute top 3 eligible packages
    for pkg in eligible_packages[:3]:
        scheduler_cycles += 1
        work_time = 1800.0  # 30 minutes simulated execution
        useful_work_seconds += work_time
        completed_ids.append(pkg.work_package_id)

        conn.execute(
            "INSERT INTO work_package_executions VALUES (?, ?, 'COMPLETED', ?, '2026-09-19T02:00:00Z')",
            (pkg.work_package_id, pkg.project_id, work_time),
        )
    conn.commit()

    # Verify 3 completions in DB
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM work_package_executions WHERE status = 'COMPLETED'")
    assert cursor.fetchone()[0] == 3

    # Cycle 2: Attempt duplicate execution (idempotency check)
    scheduler_cycles += 1
    dup_pkg = eligible_packages[0]
    cursor.execute("SELECT status FROM work_package_executions WHERE work_package_id = ?", (dup_pkg.work_package_id,))
    existing_status = cursor.fetchone()
    assert existing_status is not None and existing_status[0] == "COMPLETED"
    # Idempotent skip — no extra DB insert, no extra completed_ids

    # Cycle 3: Encounter blocked decision package
    scheduler_cycles += 1
    blocked_pkgs = [pkg for pkg in sorted_packages if pkg.current_state == "BLOCKED_DECISION"]
    if blocked_pkgs:
        target_blocked = blocked_pkgs[0]
        blocked_ids.append(target_blocked.work_package_id)
        assert target_blocked.blocked_behavior == "SKIP_AND_CONTINUE_NEXT_PROJECT"

    # Cycle 4: Replenishment test (ensure no synthetic tasks)
    scheduler_cycles += 1
    replenished = replenish_from_canonical_roadmap(manifest, repo_root, allow_synthetic=False)
    assert replenished.total_packages_count == manifest.total_packages_count

    # Cycle 5: Backoff idle sleep cycle
    scheduler_cycles += 1
    idle_sleep_seconds += 300.0  # 5 minutes backoff sleep

    conn.close()

    # Compute final metrics
    metrics = calculate_travel_metrics(
        manifest=manifest,
        completed_ids=completed_ids,
        blocked_ids=blocked_ids,
        useful_work_seconds=useful_work_seconds,
        idle_sleep_seconds=idle_sleep_seconds,
        total_scheduler_cycles=scheduler_cycles,
    )

    assert metrics["unique_useful_completions"] == 3
    assert metrics["useful_work_seconds"] == 5400.0
    assert metrics["idle_sleep_seconds"] == 300.0
    assert metrics["total_scheduler_cycles"] == 7
    assert metrics["effective_work_ratio"] > 0.90
