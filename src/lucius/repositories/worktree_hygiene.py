from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import logging
from pathlib import Path
import subprocess
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from lucius.domain.enums import PersistentWorkflowState
from lucius.persistence.orm import PersistentWorkflowORM

logger = logging.getLogger(__name__)


class WorktreeClassification(StrEnum):
    ACTIVE = "ACTIVE"
    DIRTY = "DIRTY"
    REFERENCED_BY_OPEN_WORKFLOW = "REFERENCED_BY_OPEN_WORKFLOW"
    CLEAN_PRUNABLE = "CLEAN_PRUNABLE"
    UNKNOWN = "UNKNOWN"


@dataclass
class WorktreeRecord:
    path: str
    head_sha: str
    branch: str | None = None
    is_main_root: bool = False
    classification: WorktreeClassification = WorktreeClassification.UNKNOWN
    classification_reason: str = ""
    tracked_modified: int = 0
    untracked_files: int = 0


@dataclass
class WorktreeHygieneReport:
    total_scanned: int = 0
    active_count: int = 0
    dirty_count: int = 0
    referenced_open_workflow_count: int = 0
    clean_prunable_count: int = 0
    unknown_count: int = 0
    records: list[WorktreeRecord] = field(default_factory=list)
    pruned_paths: list[str] = field(default_factory=list)
    dry_run: bool = True


class WorktreeHygieneService:
    """Safely audits, classifies, and manages ephemeral git worktrees."""

    def __init__(self, repo_root: Path | str, session: Session | None = None):
        self.repo_root = Path(repo_root).resolve()
        self.session = session

    def scan(self) -> WorktreeHygieneReport:
        report = WorktreeHygieneReport(dry_run=True)
        raw_worktrees = self._list_worktrees_porcelain()
        active_db_paths = self._get_active_workflow_worktree_paths()

        for wt in raw_worktrees:
            record = self._classify_worktree(wt, active_db_paths)
            report.records.append(record)
            report.total_scanned += 1

            if record.classification == WorktreeClassification.ACTIVE:
                report.active_count += 1
            elif record.classification == WorktreeClassification.DIRTY:
                report.dirty_count += 1
            elif record.classification == WorktreeClassification.REFERENCED_BY_OPEN_WORKFLOW:
                report.referenced_open_workflow_count += 1
            elif record.classification == WorktreeClassification.CLEAN_PRUNABLE:
                report.clean_prunable_count += 1
            elif record.classification == WorktreeClassification.UNKNOWN:
                report.unknown_count += 1

        return report

    def prune_safe_candidates(self, *, dry_run: bool = True) -> WorktreeHygieneReport:
        report = self.scan()
        report.dry_run = dry_run

        if dry_run:
            logger.info("Dry-run enabled: skipping actual removal of %d safe candidates", report.clean_prunable_count)
            return report

        for record in report.records:
            if record.classification == WorktreeClassification.CLEAN_PRUNABLE:
                success = self._remove_worktree(record.path)
                if success:
                    report.pruned_paths.append(record.path)

        # Run git worktree prune to clean metadata
        subprocess.run(["git", "worktree", "prune"], cwd=self.repo_root, check=False, capture_output=True)
        return report

    def _classify_worktree(self, wt_info: dict[str, Any], active_db_paths: set[str]) -> WorktreeRecord:
        path_str = wt_info.get("worktree", "")
        head_sha = wt_info.get("HEAD", "")
        branch = wt_info.get("branch")
        path = Path(path_str).resolve()

        record = WorktreeRecord(
            path=str(path),
            head_sha=head_sha,
            branch=branch,
        )

        if path == self.repo_root:
            record.is_main_root = True
            record.classification = WorktreeClassification.ACTIVE
            record.classification_reason = "Canonical main repository checkout"
            return record

        if not path.exists():
            record.classification = WorktreeClassification.UNKNOWN
            record.classification_reason = "Worktree path does not exist on disk"
            return record

        # Check if dirty
        is_dirty, modified, untracked = self._inspect_git_status(path)
        record.tracked_modified = modified
        record.untracked_files = untracked

        if is_dirty:
            record.classification = WorktreeClassification.DIRTY
            record.classification_reason = f"Uncommitted changes present ({modified} modified, {untracked} untracked)"
            return record

        # Check if referenced by active SQLite workflow
        if str(path) in active_db_paths:
            record.classification = WorktreeClassification.REFERENCED_BY_OPEN_WORKFLOW
            record.classification_reason = "Referenced by an active persistent workflow in database"
            return record

        # Clean and unreferenced
        record.classification = WorktreeClassification.CLEAN_PRUNABLE
        record.classification_reason = "Clean worktree with no uncommitted changes or open workflows"
        return record

    def _list_worktrees_porcelain(self) -> list[dict[str, Any]]:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.error("git worktree list failed: %s", result.stderr)
            return []

        worktrees: list[dict[str, Any]] = []
        current: dict[str, Any] = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                if current:
                    worktrees.append(current)
                    current = {}
                continue
            if " " in line:
                key, val = line.split(" ", 1)
                current[key] = val
            elif line == "bare":
                current["bare"] = True
            elif line == "detached":
                current["detached"] = True
        if current:
            worktrees.append(current)
        return worktrees

    def _inspect_git_status(self, path: Path) -> tuple[bool, int, int]:
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=path,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return (True, 0, 0)
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            untracked = sum(1 for line in lines if line.startswith("??"))
            modified = len(lines) - untracked
            return (len(lines) > 0, modified, untracked)
        except Exception:
            return (True, 0, 0)

    def _remove_worktree(self, path: str) -> bool:
        try:
            res = subprocess.run(
                ["git", "worktree", "remove", path],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            return res.returncode == 0
        except Exception:
            return False

    def _get_active_workflow_worktree_paths(self) -> set[str]:
        if self.session is None:
            return set()
        active_states = [
            PersistentWorkflowState.PENDING.value,
            PersistentWorkflowState.RUNNING.value,
            PersistentWorkflowState.BLOCKED.value,
        ]
        workflows = self.session.scalars(
            select(PersistentWorkflowORM).where(PersistentWorkflowORM.workflow_state.in_(active_states))
        ).all()
        return {str(Path(wf.worktree_path).resolve()) for wf in workflows if wf.worktree_path}
