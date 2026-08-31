from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from lucius.domain.enums import RepositoryAccessMode, SnapshotMode
from lucius.repositories.base import RepositoryAdapter
from lucius.repositories.errors import LuciusRepositoryError, RepositoryErrorCode
from lucius.repositories.hashing import hash_file, looks_binary, manifest_identity, sha256_bytes
from lucius.repositories.inspection import (
    classify_config,
    classify_document,
    classify_file_type,
    classify_test,
    configs_from_paths,
    detect_language,
    detect_technologies,
    docs_from_paths,
    is_entrypoint,
    is_excluded,
    is_sensitive_path,
    tests_from_paths,
)
from lucius.repositories.schemas import (
    FileRecord,
    GitState,
    ManifestSummary,
    RepositoryIdentity,
    RepositoryManifest,
    RepositoryProfile,
    SnapshotResult,
    WorkspaceContext,
)

DEFAULT_MAX_TEXT_FILE_SIZE = 2 * 1024 * 1024


class LocalGitRepositoryAdapter(RepositoryAdapter):
    def __init__(
        self,
        location: str | Path,
        workspace_context: WorkspaceContext,
        *,
        max_text_file_size: int = DEFAULT_MAX_TEXT_FILE_SIZE,
    ):
        if workspace_context.access_mode != RepositoryAccessMode.READ_ONLY:
            raise LuciusRepositoryError(RepositoryErrorCode.PERMISSION_DENIED, "Phase 1 only supports READ_ONLY repository access")
        self.location = Path(location)
        self.workspace_context = workspace_context
        self.max_text_file_size = max_text_file_size
        self._root: Path | None = None

    @property
    def root(self) -> Path:
        if self._root is None:
            self.validate()
        assert self._root is not None
        return self._root

    def validate(self) -> None:
        if not self.location.exists():
            raise LuciusRepositoryError(RepositoryErrorCode.REPOSITORY_NOT_FOUND, str(self.location))
        resolved_location = self.location.resolve()
        self._ensure_allowed(resolved_location)
        root_text = self._git(["rev-parse", "--show-toplevel"], cwd=resolved_location).strip()
        if not root_text:
            raise LuciusRepositoryError(RepositoryErrorCode.NOT_A_GIT_REPOSITORY, str(resolved_location))
        root = Path(root_text).resolve()
        self._ensure_allowed(root)
        self._root = root

    def get_identity(self) -> RepositoryIdentity:
        self.validate()
        remotes = self._remotes()
        default_branch = self.get_git_state().branch
        canonical_remote = None
        if "origin" in remotes and remotes["origin"]:
            canonical_remote = remotes["origin"][0]
        return RepositoryIdentity(root=str(self.root), canonical_remote=canonical_remote, default_branch=default_branch)

    def get_git_state(self) -> GitState:
        self.validate()
        if self.workspace_context.repository_ref:
            commit_sha = self._git(["rev-parse", f"{self.workspace_context.repository_ref}^{{commit}}"]).strip()
            branch_result = self._git_result(["name-rev", "--name-only", "--no-undefined", commit_sha])
            branch = branch_result.stdout.strip() if branch_result.returncode == 0 else self.workspace_context.repository_ref
            return GitState(
                root=str(self.root),
                branch=branch,
                commit_sha=commit_sha,
                detached_head=False,
                is_dirty=False,
                remotes=self._remotes(),
                warnings=[{"code": "HISTORICAL_STATE", "ref": self.workspace_context.repository_ref}],
            )
        commit_sha = self._git(["rev-parse", "HEAD"]).strip()
        branch_result = self._git_result(["symbolic-ref", "--quiet", "--short", "HEAD"])
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
        status_lines = [line for line in self._git(["status", "--porcelain"]).splitlines() if line]
        dirty = self._parse_status(status_lines)
        warnings = self._repository_state_warnings(branch is None, dirty["conflicted"])
        return GitState(
            root=str(self.root),
            branch=branch,
            commit_sha=commit_sha,
            detached_head=branch is None,
            is_dirty=any(dirty.values()),
            modified=dirty["modified"],
            added=dirty["added"],
            deleted=dirty["deleted"],
            renamed=dirty["renamed"],
            untracked=dirty["untracked"],
            conflicted=dirty["conflicted"],
            remotes=self._remotes(),
            warnings=warnings,
        )

    def list_files(self, *, include_untracked: bool = True) -> list[str]:
        self.validate()
        if self.workspace_context.repository_ref:
            output = self._git(["ls-tree", "-r", "--name-only", "-z", self.workspace_context.repository_ref])
            return sorted(path for path in output.split("\0") if path and not is_excluded(path))
        files = set(self._git_z(["ls-files"]))
        if include_untracked:
            files.update(self._git_z(["ls-files", "--others", "--exclude-standard"]))
        return sorted(path for path in files if path and not is_excluded(path))

    def read_file(self, relative_path: str) -> str:
        if self.workspace_context.repository_ref:
            rel = self._validate_relative_path(relative_path)
            if is_sensitive_path(rel):
                raise LuciusRepositoryError(RepositoryErrorCode.PERMISSION_DENIED, f"Sensitive file content is not readable: {rel}")
            result = self._git_result(["show", f"{self.workspace_context.repository_ref}:{rel}"])
            if result.returncode != 0:
                raise LuciusRepositoryError(RepositoryErrorCode.REPOSITORY_NOT_FOUND, rel)
            data = result.stdout.encode("utf-8")
            if len(data) > self.max_text_file_size:
                raise LuciusRepositoryError(RepositoryErrorCode.FILE_TOO_LARGE, rel)
            return result.stdout
        path = self._resolve_repo_file(relative_path)
        rel = self._relative(path)
        if is_sensitive_path(rel):
            raise LuciusRepositoryError(RepositoryErrorCode.PERMISSION_DENIED, f"Sensitive file content is not readable: {rel}")
        size = path.stat().st_size
        if size > self.max_text_file_size:
            raise LuciusRepositoryError(RepositoryErrorCode.FILE_TOO_LARGE, rel)
        if looks_binary(path):
            raise LuciusRepositoryError(RepositoryErrorCode.BINARY_FILE, rel)
        return path.read_text(encoding="utf-8")

    def discover_documents(self) -> list[dict[str, Any]]:
        return [row.model_dump() for row in docs_from_paths(self.list_files())]

    def discover_tests(self) -> list[dict[str, Any]]:
        return [row.model_dump() for row in tests_from_paths(self.list_files())]

    def discover_technologies(self) -> dict[str, Any]:
        profile = RepositoryProfile(technologies=detect_technologies(self.list_files(), self.read_file))
        return profile.model_dump(mode="json")

    def build_snapshot(self, mode: SnapshotMode = SnapshotMode.STANDARD) -> SnapshotResult:
        if mode == SnapshotMode.DEEP:
            mode = SnapshotMode.STANDARD
        git_state = self.get_git_state()
        files = self.list_files(include_untracked=True)
        documentation_map = docs_from_paths(files)
        test_map = tests_from_paths(files)
        configuration_map = configs_from_paths(files)
        file_records = [self._file_record(path, mode) for path in files]
        manifest_hash = manifest_identity([record.model_dump(mode="json") for record in file_records])
        manifest = RepositoryManifest(files=file_records, manifest_hash=manifest_hash)
        technologies = [] if mode == SnapshotMode.FAST else detect_technologies(files, self.read_file)
        profile = RepositoryProfile(technologies=technologies, configuration=configuration_map)
        warnings = list(git_state.warnings)
        if git_state.is_dirty:
            warnings.append({"code": "DIRTY_REPOSITORY", "message": "Snapshot includes uncommitted repository state."})
        return SnapshotResult(
            status="CREATED",
            reused_existing_snapshot=False,
            mode=mode,
            git_state=git_state,
            manifest_summary=ManifestSummary(
                file_count=len(file_records),
                document_count=len(documentation_map),
                test_count=len(test_map),
                config_count=len(configuration_map),
                manifest_hash=manifest_hash,
            ),
            repository_profile=profile,
            documentation_map=documentation_map,
            test_map=test_map,
            configuration_map=configuration_map,
            warnings=warnings,
            manifest=manifest,
        )

    def _file_record(self, relative_path: str, mode: SnapshotMode) -> FileRecord:
        if self.workspace_context.repository_ref:
            return self._historical_file_record(relative_path, mode)
        path = self._resolve_repo_file(relative_path)
        rel = self._relative(path)
        size = path.stat().st_size
        is_sensitive = is_sensitive_path(rel)
        file_type = classify_file_type(rel)
        is_binary = file_type == "binary"
        content_hash = None
        if not is_sensitive and mode != SnapshotMode.FAST and size <= self.max_text_file_size:
            is_binary = looks_binary(path)
            if not is_binary:
                content_hash = hash_file(path)
        elif not is_sensitive and size <= self.max_text_file_size:
            is_binary = looks_binary(path)
        return FileRecord(
            path=rel,
            size_bytes=size,
            file_type=file_type,
            language=detect_language(rel),
            content_hash=content_hash,
            git_status=self._git_status_for(rel),
            is_documentation=classify_document(rel) is not None,
            is_test=classify_test(rel) is not None,
            is_config=classify_config(rel) is not None,
            is_entrypoint=is_entrypoint(rel),
            is_sensitive=is_sensitive,
            is_binary=is_binary,
        )

    def _historical_file_record(self, relative_path: str, mode: SnapshotMode) -> FileRecord:
        rel = self._validate_relative_path(relative_path)
        is_sensitive = is_sensitive_path(rel)
        file_type = classify_file_type(rel)
        result = self._git_result(["show", f"{self.workspace_context.repository_ref}:{rel}"])
        if result.returncode != 0:
            raise LuciusRepositoryError(RepositoryErrorCode.REPOSITORY_NOT_FOUND, rel)
        data = result.stdout.encode("utf-8")
        is_binary = b"\0" in data[:8192]
        content_hash = None
        if not is_sensitive and mode != SnapshotMode.FAST and len(data) <= self.max_text_file_size and not is_binary:
            content_hash = sha256_bytes(data)
        return FileRecord(
            path=rel,
            size_bytes=len(data),
            file_type=file_type,
            language=detect_language(rel),
            content_hash=content_hash,
            git_status="tracked",
            is_documentation=classify_document(rel) is not None,
            is_test=classify_test(rel) is not None,
            is_config=classify_config(rel) is not None,
            is_entrypoint=is_entrypoint(rel),
            is_sensitive=is_sensitive,
            is_binary=is_binary,
        )

    def _resolve_repo_file(self, relative_path: str) -> Path:
        pure = PurePosixPath(self._validate_relative_path(relative_path))
        candidate = (self.root / Path(*pure.parts)).resolve()
        self._ensure_inside(candidate, self.root, RepositoryErrorCode.UNSAFE_PATH)
        if not candidate.exists() or not candidate.is_file():
            raise LuciusRepositoryError(RepositoryErrorCode.REPOSITORY_NOT_FOUND, relative_path)
        self._ensure_allowed(candidate)
        return candidate

    def _validate_relative_path(self, relative_path: str) -> str:
        pure = PurePosixPath(relative_path)
        if pure.is_absolute() or ".." in pure.parts:
            raise LuciusRepositoryError(RepositoryErrorCode.UNSAFE_PATH, relative_path)
        return pure.as_posix()

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _ensure_allowed(self, path: Path) -> None:
        roots = [root.resolve() for root in self.workspace_context.allowed_roots]
        if not any(_is_relative_to(path, root) for root in roots):
            raise LuciusRepositoryError(RepositoryErrorCode.REPOSITORY_OUTSIDE_WORKSPACE, str(path))

    def _ensure_inside(self, path: Path, root: Path, code: RepositoryErrorCode) -> None:
        if not _is_relative_to(path, root):
            raise LuciusRepositoryError(code, str(path))

    def _git(self, args: list[str], cwd: Path | None = None) -> str:
        result = self._git_result(args, cwd=cwd)
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "not a git repository" in stderr.lower():
                raise LuciusRepositoryError(RepositoryErrorCode.NOT_A_GIT_REPOSITORY, stderr)
            raise LuciusRepositoryError(RepositoryErrorCode.GIT_COMMAND_FAILED, stderr or "git command failed")
        return result.stdout

    def _git_result(self, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        working_dir = cwd or self.root
        return subprocess.run(
            ["git", *args],
            cwd=working_dir,
            capture_output=True,
            check=False,
            text=True,
        )

    def _git_z(self, args: list[str]) -> list[str]:
        output = self._git([*args, "-z"])
        return [item for item in output.split("\0") if item]

    def _remotes(self) -> dict[str, list[str]]:
        remotes: dict[str, list[str]] = {}
        output = self._git(["remote", "-v"])
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                remotes.setdefault(parts[0], [])
                if parts[1] not in remotes[parts[0]]:
                    remotes[parts[0]].append(parts[1])
        return remotes

    def _parse_status(self, lines: list[str]) -> dict[str, list[str]]:
        dirty = {"modified": [], "added": [], "deleted": [], "renamed": [], "untracked": [], "conflicted": []}
        conflict_codes = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
        for line in lines:
            code = line[:2]
            path = _porcelain_path(line)
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if code == "??":
                dirty["untracked"].append(path)
            if code in conflict_codes or "U" in code:
                dirty["conflicted"].append(path)
            if "M" in code:
                dirty["modified"].append(path)
            if "A" in code:
                dirty["added"].append(path)
            if "D" in code:
                dirty["deleted"].append(path)
            if "R" in code:
                dirty["renamed"].append(path)
        return {key: sorted(set(values)) for key, values in dirty.items()}

    def _git_status_for(self, relative_path: str) -> str:
        state = self.get_git_state()
        if relative_path in state.untracked:
            return "untracked"
        if relative_path in state.conflicted:
            return "conflicted"
        if relative_path in state.modified:
            return "modified"
        if relative_path in state.added:
            return "added"
        if relative_path in state.deleted:
            return "deleted"
        if relative_path in state.renamed:
            return "renamed"
        return "tracked"

    def _repository_state_warnings(self, detached: bool, conflicted: list[str]) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        if detached:
            warnings.append({"code": "DETACHED_HEAD", "message": "Repository is in detached HEAD state."})
        if conflicted:
            warnings.append({"code": "MERGE_CONFLICT", "paths": conflicted})
        for filename, code in {
            "rebase-merge": "REBASE_IN_PROGRESS",
            "rebase-apply": "REBASE_IN_PROGRESS",
            "CHERRY_PICK_HEAD": "CHERRY_PICK_IN_PROGRESS",
        }.items():
            result = self._git_result(["rev-parse", "--git-path", filename])
            if result.returncode == 0 and Path(result.stdout.strip()).exists():
                warnings.append({"code": code})
        return warnings


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _porcelain_path(line: str) -> str:
    return line[3:] if len(line) > 2 and line[2] == " " else line[2:].lstrip()
