from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.repositories.worktree_hygiene import WorktreeHygieneService
from lucius.runtime.continuation import (
    BoundedContinuationService,
    ContinuationSessionResult,
    SessionBudget,
)
from lucius.runtime.feeder import DEFAULT_DARWIN_ROOT, DarwinBacklogFeeder

logger = logging.getLogger(__name__)

DEFAULT_LUCIUS_ROOT = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Lucius Engineering/lucius-engineering")
DEFAULT_BILLY_ROOT = Path("/Volumes/BLACKBOX/2 CODE PROJECTS/Billy Production Engine/billy-production-engine")
DEFAULT_DB_PATH = DEFAULT_LUCIUS_ROOT / ".lucius" / "state.db"


def get_default_session(db_path: Path | str = DEFAULT_DB_PATH) -> Session:
    engine = create_sqlite_engine(db_path)
    create_all(engine)
    factory = make_session_factory(engine)
    return factory()


class PreflightReport(BaseModel):
    all_passed: bool
    repository_checks: dict[str, bool] = Field(default_factory=dict)
    free_disk_gb: float = 0.0
    disk_ok: bool = False
    worktree_count: int = 0
    worktree_budget_ok: bool = False
    errors: list[str] = Field(default_factory=list)


class TravelLauncher:
    """Canonical single-command launcher for Lucius Travel Mode.

    Executes preflight verification, configures bounded session budgets,
    wires approved dynamic task feeders, and orchestrates cross-project execution.
    """

    def __init__(
        self,
        lucius_root: Path | str = DEFAULT_LUCIUS_ROOT,
        darwin_root: Path | str = DEFAULT_DARWIN_ROOT,
        billy_root: Path | str = DEFAULT_BILLY_ROOT,
        min_disk_free_gb: float = 5.0,
        max_worktree_limit: int = 150,
    ):
        self.lucius_root = Path(lucius_root)
        self.darwin_root = Path(darwin_root)
        self.billy_root = Path(billy_root)
        self.min_disk_free_gb = min_disk_free_gb
        self.max_worktree_limit = max_worktree_limit

    def preflight(self) -> PreflightReport:
        """Runs preflight checks on repositories, disk space, and worktree capacity."""
        repo_checks = {
            "lucius": (self.lucius_root / ".git").exists(),
            "darwin": (self.darwin_root / ".git").exists(),
            "billy": (self.billy_root / ".git").exists(),
        }

        errors = []
        for name, ok in repo_checks.items():
            if not ok:
                errors.append(f"Repository missing or invalid git root: {name}")

        # Disk check
        stat = shutil.disk_usage(self.lucius_root if self.lucius_root.exists() else Path.cwd())
        free_gb = stat.free / (1024**3)
        disk_ok = free_gb >= self.min_disk_free_gb
        if not disk_ok:
            errors.append(f"Insufficient free disk space: {free_gb:.1f} GB (minimum required: {self.min_disk_free_gb} GB)")

        # Worktree check
        scanner = WorktreeHygieneService(self.lucius_root)
        report = scanner.scan()
        worktree_count = report.total_scanned
        worktree_budget_ok = worktree_count <= self.max_worktree_limit
        if not worktree_budget_ok:
            errors.append(f"Worktree count ({worktree_count}) exceeds safe budget limit ({self.max_worktree_limit})")

        all_passed = len(errors) == 0 and all(repo_checks.values()) and disk_ok and worktree_budget_ok
        return PreflightReport(
            all_passed=all_passed,
            repository_checks=repo_checks,
            free_disk_gb=round(free_gb, 2),
            disk_ok=disk_ok,
            worktree_count=worktree_count,
            worktree_budget_ok=worktree_budget_ok,
            errors=errors,
        )

    def launch(
        self,
        session: Session,
        *,
        hours: float = 4.0,
        max_cycles: int = 25,
        workflow_ids: list[str] | None = None,
        stop_on_block: bool = False,
    ) -> ContinuationSessionResult:
        """Executes a bounded travel-mode continuation session."""
        preflight_res = self.preflight()
        if not preflight_res.all_passed:
            raise RuntimeError(f"Preflight checks failed: {preflight_res.errors}")

        budget = SessionBudget(
            max_cycles=max_cycles,
            max_wall_seconds=hours * 3600.0,
            max_consecutive_failures=3,
        )

        feeder = DarwinBacklogFeeder(darwin_root=self.darwin_root)
        continuation_svc = BoundedContinuationService(
            session=session,
            feeder=feeder,
        )

        return continuation_svc.run_session(
            budget=budget,
            workflow_ids=workflow_ids,
            stop_on_block=stop_on_block,
        )


def main() -> None:
    """CLI entrypoint for lucius travel mode launcher."""
    parser = argparse.ArgumentParser(description="Lucius Travel Mode Launcher")
    parser.add_argument("--hours", type=float, default=4.0, help="Maximum session duration in hours (default: 4.0)")
    parser.add_argument("--max-cycles", type=int, default=25, help="Maximum task execution cycles (default: 25)")
    parser.add_argument("--preflight-only", action="store_true", help="Run preflight checks only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    launcher = TravelLauncher()
    preflight = launcher.preflight()

    print("=== LUCIUS TRAVEL MODE PREFLIGHT ===")
    print(f"Repositories OK: {preflight.repository_checks}")
    print(f"Free Disk: {preflight.free_disk_gb} GB (OK: {preflight.disk_ok})")
    print(f"Worktrees: {preflight.worktree_count} (OK: {preflight.worktree_budget_ok})")
    print(f"Overall Preflight Passed: {preflight.all_passed}")

    if not preflight.all_passed:
        print(f"Preflight Errors: {preflight.errors}", file=sys.stderr)
        sys.exit(1)

    if args.preflight_only:
        print("Preflight completed successfully. Exiting (--preflight-only specified).")
        sys.exit(0)

    print(f"\nLaunching Travel Session: Budget = {args.hours} hours ({args.max_cycles} max cycles)...")
    with get_default_session() as session:
        result = launcher.launch(session, hours=args.hours, max_cycles=args.max_cycles)
        print("\n=== SESSION EXECUTION SUMMARY ===")
        print(f"Session Status:      {result.status}")
        print(f"Stop Reason:         {result.stop_reason}")
        print(f"Tasks Selected:      {result.tasks_selected}")
        print(f"Tasks Completed:     {result.tasks_completed}")
        print(f"Tasks Blocked:       {result.tasks_blocked}")
        print(f"Tasks Fed:           {result.tasks_fed}")
        print(f"Cycles Executed:     {result.cycles_executed}")
        print(f"Wall Clock Time:     {result.wall_clock_elapsed_seconds:.2f}s")
        print(f"Project Switches:    {result.project_switches}")


if __name__ == "__main__":
    main()
