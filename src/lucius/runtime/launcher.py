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


import json
import urllib.error
import urllib.request

from lucius.projects.registry_schema import DFGProjectRegistry
from lucius.projects.regression_guard import RegressionGuardValidator

class PreflightReport(BaseModel):
    all_passed: bool
    repository_checks: dict[str, bool] = Field(default_factory=dict)
    free_disk_gb: float = 0.0
    disk_ok: bool = False
    worktree_count: int = 0
    worktree_budget_ok: bool = False
    registry_ok: bool = False
    ollama_online: bool = False
    ollama_models: list[str] = Field(default_factory=list)
    feeder_available: bool = False
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
        ollama_url: str = "http://localhost:11434/api/tags",
    ):
        self.lucius_root = Path(lucius_root)
        self.darwin_root = Path(darwin_root)
        self.billy_root = Path(billy_root)
        self.min_disk_free_gb = min_disk_free_gb
        self.max_worktree_limit = max_worktree_limit
        self.ollama_url = ollama_url

    def preflight(self) -> PreflightReport:
        """Runs preflight checks on repositories, registry, providers, disk, and worktree capacity."""
        repo_checks = {
            "lucius": (self.lucius_root / ".git").exists(),
            "darwin": (self.darwin_root / ".git").exists(),
            "billy": (self.billy_root / ".git").exists(),
        }

        errors = []
        for name, ok in repo_checks.items():
            if not ok:
                errors.append(f"Repository missing or invalid git root: {name}")

        # Canonical Registry check
        registry_path = self.lucius_root / "src" / "lucius" / "projects" / "dfg_canonical_registry.json"
        registry_ok = False
        if registry_path.exists():
            try:
                reg = DFGProjectRegistry.load_json(registry_path)
                core_projects = ["lucius-engineering", "darwin-research-engine", "billy-production-engine"]
                if all(reg.get_project(cp) is not None for cp in core_projects):
                    registry_ok = True
                else:
                    errors.append(f"Canonical registry missing core project definitions in {registry_path}")
            except Exception as exc:
                errors.append(f"Failed to load canonical registry: {exc}")
        else:
            errors.append(f"Canonical registry file not found: {registry_path}")

        # Feeder check
        backlog_file = self.darwin_root / "src" / "darwin" / "backlog" / "master_backlog.py"
        feeder_available = backlog_file.exists()
        if not feeder_available:
            errors.append(f"Darwin master backlog file missing: {backlog_file}")

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

        # Provider (Ollama) check
        ollama_online = False
        ollama_models: list[str] = []
        try:
            req = urllib.request.Request(self.ollama_url, method="GET")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    payload = json.loads(resp.read().decode())
                    ollama_models = [m.get("name", "") for m in payload.get("models", [])]
                    ollama_online = True
        except Exception:
            # Non-blocking for pure Class A deterministic runs, but logged
            ollama_online = False

        all_passed = (
            len(errors) == 0
            and all(repo_checks.values())
            and disk_ok
            and worktree_budget_ok
            and registry_ok
            and feeder_available
        )
        return PreflightReport(
            all_passed=all_passed,
            repository_checks=repo_checks,
            free_disk_gb=round(free_gb, 2),
            disk_ok=disk_ok,
            worktree_count=worktree_count,
            worktree_budget_ok=worktree_budget_ok,
            registry_ok=registry_ok,
            ollama_online=ollama_online,
            ollama_models=ollama_models,
            feeder_available=feeder_available,
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
        runtime_service: Any = None,
        smoke: bool = False,
        provider_type: str = "ollama",
        provider_registry: Any = None,
    ) -> ContinuationSessionResult:
        """Executes a bounded travel-mode continuation session."""
        preflight_res = self.preflight()
        if not preflight_res.all_passed:
            raise RuntimeError(f"Preflight checks failed: {preflight_res.errors}")

        if smoke:
            budget = SessionBudget(
                max_cycles=1,
                max_wall_seconds=10.0,
                max_consecutive_failures=1,
            )
        else:
            budget = SessionBudget(
                max_cycles=max_cycles,
                max_wall_seconds=hours * 3600.0,
                max_consecutive_failures=3,
            )

        registry_path = self.lucius_root / "src" / "lucius" / "projects" / "dfg_canonical_registry.json"
        registry = DFGProjectRegistry.load_json(registry_path) if registry_path.exists() else None
        guard = RegressionGuardValidator(registry) if registry is not None else None

        feeder = (
            None
            if smoke
            else DarwinBacklogFeeder(
                darwin_root=self.darwin_root,
                registry=registry,
                regression_guard=guard,
            )
        )
        if runtime_service is None:
            from lucius.domain.enums import Actor
            from lucius.runtime.adapters import ScriptedExecutionAdapter, ScriptedRuntimePlanningAdapter
            from lucius.runtime.dispatcher import MultiProjectDispatcher
            from lucius.runtime.ollama import OllamaExecutionProvider
            from lucius.runtime.router import ModelExecutionRouter, RuntimeProviderRegistry
            from lucius.runtime.service import ExecutionRuntimeLoopService

            planning_adapter = ScriptedRuntimePlanningAdapter()
            if provider_registry is None:
                if provider_type == "scripted":
                    provider = ScriptedExecutionAdapter(provider_id="scripted-runtime-provider")
                else:
                    provider = OllamaExecutionProvider(
                        provider_id="ollama-local",
                        model="qwen3:8b",
                        allowed_workspace_roots=[self.lucius_root, self.darwin_root, self.billy_root],
                    )
                provider_registry = RuntimeProviderRegistry([provider])

            execution_router = ModelExecutionRouter(
                session,
                registry=provider_registry,
                actor=Actor.LUCIUS,
            )
            dispatcher = MultiProjectDispatcher(session)
            runtime_service = ExecutionRuntimeLoopService(
                session=session,
                planning_adapter=planning_adapter,
                execution_router=execution_router,
                dispatcher=dispatcher,
            )

        continuation_svc = BoundedContinuationService(
            session=session,
            runtime_service=runtime_service,
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
    parser.add_argument("--mode", choices=["travel"], default="travel", help="Operational mode (default: travel)")
    parser.add_argument("--hours", type=float, default=4.0, help="Maximum session duration in hours (default: 4.0)")
    parser.add_argument("--max-cycles", type=int, default=25, help="Maximum task execution cycles (default: 25)")
    parser.add_argument("--preflight-only", action="store_true", help="Run preflight checks only")
    parser.add_argument("--smoke", action="store_true", help="Run quick 1-cycle runtime smoke test without consuming backlog")
    parser.add_argument("--provider", choices=["ollama", "scripted"], default="ollama", help="Execution provider type (default: ollama)")
    parser.add_argument("--db-path", type=str, default=str(DEFAULT_DB_PATH), help="Path to state database")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    launcher = TravelLauncher()
    preflight = launcher.preflight()

    print("=== LUCIUS TRAVEL MODE PREFLIGHT ===")
    print(f"Repositories OK: {preflight.repository_checks}")
    print(f"Canonical Registry: {'OK' if preflight.registry_ok else 'MISSING'}")
    print(f"Darwin Feeder: {'AVAILABLE' if preflight.feeder_available else 'UNAVAILABLE'}")
    print(f"Free Disk: {preflight.free_disk_gb} GB (OK: {preflight.disk_ok})")
    print(f"Worktrees: {preflight.worktree_count} (OK: {preflight.worktree_budget_ok})")
    print(f"Provider (Ollama): {'ONLINE' if preflight.ollama_online else 'OFFLINE'} (Models: {preflight.ollama_models})")
    print(f"Overall Preflight Passed: {preflight.all_passed}")

    if not preflight.all_passed:
        print(f"Preflight Errors: {preflight.errors}", file=sys.stderr)
        sys.exit(1)

    if args.preflight_only:
        print("Preflight completed successfully. Exiting (--preflight-only specified).")
        sys.exit(0)

    mode_label = "SMOKE TEST" if args.smoke else f"Budget = {args.hours} hours ({args.max_cycles} max cycles)"
    print(f"\nLaunching Travel Session ({args.mode}): {mode_label} [provider: {args.provider}]...")
    with get_default_session(Path(args.db_path)) as session:
        try:
            result = launcher.launch(
                session,
                hours=args.hours,
                max_cycles=args.max_cycles,
                smoke=args.smoke,
                provider_type=args.provider,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        print("\n=== SESSION EXECUTION SUMMARY ===")
        print(f"Session Status:      {result.status}")
        print(f"Stop Reason:         {result.stop_reason}")
        print(f"Tasks Selected:      {result.tasks_selected}")
        print(f"Tasks Completed:     {result.tasks_completed}")
        print(f"Tasks Blocked:       {result.tasks_blocked}")
        print(f"Tasks Fed:           {result.tasks_fed}")
        print(f"Cycles Attempted:    {result.cycles_attempted}")
        print(f"Wall Clock Time:     {result.wall_clock_duration_seconds:.2f}s")
        print(f"Project Switches:    {result.project_switches}")

        if result.status == "FAILED":
            sys.exit(1)


if __name__ == "__main__":
    main()
