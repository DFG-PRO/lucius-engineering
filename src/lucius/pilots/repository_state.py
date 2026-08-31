from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from lucius.audit.service import AuditService
from lucius.domain.enums import Actor, RepositoryStateClassification
from lucius.persistence.orm import RepositoryStateObservationORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.repositories.local_git import LocalGitRepositoryAdapter
from lucius.repositories.schemas import WorkspaceContext
from lucius.pilots.schemas import RepositoryStateObservation


class RepositoryStateError(RuntimeError):
    pass


class RepositoryStateService:
    def __init__(self, session: Session):
        self.session = session
        self.audit = AuditService(session)

    def inspect(
        self,
        *,
        repository_path: str | Path,
        workspace_context: WorkspaceContext,
        repository_id: str | None = None,
        strict_canonical: bool = False,
        actor: Actor = Actor.SYSTEM,
    ) -> RepositoryStateObservation:
        root = Path(repository_path).resolve()
        branch = _git(root, "branch", "--show-current") or None
        head = _git(root, "rev-parse", "HEAD")
        remote = _origin_remote(root)
        staged, tracked, untracked = _status_groups(root)
        classification = _classification(staged, tracked, untracked)
        manifest_hash = None
        try:
            snapshot = LocalGitRepositoryAdapter(root, workspace_context).build_snapshot()
            manifest_hash = snapshot.manifest_summary.manifest_hash
        except Exception:
            manifest_hash = None
        observation = RepositoryStateObservation(
            id=next_id(self.session, "repository_state"),
            repository_id=repository_id,
            repository_path=str(root),
            branch=branch,
            head_commit=head,
            remote=remote,
            classification=classification,
            tracked_modifications=tracked,
            staged_modifications=staged,
            untracked_files=untracked,
            manifest_hash=manifest_hash,
            details={"strict_canonical": strict_canonical},
            observed_at=utc_now(),
        )
        if strict_canonical and not observation.canonical:
            self._persist(observation)
            self._audit(observation, "BLOCKED", actor)
            raise RepositoryStateError(f"Repository state is not canonical: {classification.value}")
        self._persist(observation)
        self._audit(observation, "SUCCESS", actor)
        return observation

    def compare_integrity(
        self,
        before: RepositoryStateObservation,
        after: RepositoryStateObservation,
    ) -> RepositoryStateClassification:
        if before.head_commit != after.head_commit:
            return RepositoryStateClassification.NON_CANONICAL_HEAD_CHANGED
        if (
            before.tracked_modifications != after.tracked_modifications
            or before.staged_modifications != after.staged_modifications
            or before.untracked_files != after.untracked_files
            or before.manifest_hash != after.manifest_hash
        ):
            return RepositoryStateClassification.NON_CANONICAL_EXTERNAL_MUTATION
        return before.classification

    def _persist(self, observation: RepositoryStateObservation) -> RepositoryStateObservationORM:
        row = RepositoryStateObservationORM(
            id=observation.id,
            repository_id=observation.repository_id,
            repository_path=observation.repository_path,
            branch=observation.branch,
            head_commit=observation.head_commit,
            remote=observation.remote,
            classification=observation.classification.value,
            tracked_modifications=observation.tracked_modifications,
            staged_modifications=observation.staged_modifications,
            untracked_files=observation.untracked_files,
            manifest_hash=observation.manifest_hash,
            details=observation.details,
            observed_at=observation.observed_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _audit(self, observation: RepositoryStateObservation, result: str, actor: Actor) -> None:
        self.audit.record(
            event_type="REPOSITORY_STATE_CLASSIFIED",
            actor=actor.value,
            repository_id=observation.repository_id,
            action="inspect_repository_state",
            result=result,
            metadata={
                "repository_state_id": observation.id,
                "classification": observation.classification.value,
                "head_commit": observation.head_commit,
                "manifest_hash": observation.manifest_hash,
            },
        )


def _classification(
    staged: list[str],
    tracked: list[str],
    untracked: list[str],
) -> RepositoryStateClassification:
    if untracked:
        return RepositoryStateClassification.NON_CANONICAL_UNTRACKED_STATE
    if staged or tracked:
        return RepositoryStateClassification.NON_CANONICAL_DIRTY
    return RepositoryStateClassification.CANONICAL_CLEAN


def _status_groups(root: Path) -> tuple[list[str], list[str], list[str]]:
    staged: list[str] = []
    tracked: list[str] = []
    untracked: list[str] = []
    for line in _git(root, "status", "--porcelain").splitlines():
        if not line:
            continue
        code = line[:2]
        path = _porcelain_path(line)
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if code == "??":
            untracked.append(path)
            continue
        if code[0] != " ":
            staged.append(path)
        if code[1] != " ":
            tracked.append(path)
    return sorted(set(staged)), sorted(set(tracked)), sorted(set(untracked))


def _origin_remote(root: Path) -> str | None:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() or None if result.returncode == 0 else None


def _porcelain_path(line: str) -> str:
    return line[3:] if len(line) > 2 and line[2] == " " else line[2:].lstrip()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RepositoryStateError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()
