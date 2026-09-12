from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class GitMutationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedFileContent:
    path: str
    absolute_path: Path
    content: str
    byte_count: int


def repository_root(path: str | Path) -> Path:
    root = _git(Path(path).resolve(), ["rev-parse", "--show-toplevel"]).strip()
    if not root:
        raise GitMutationError("NOT_A_GIT_REPOSITORY", f"Not a Git repository: {path}")
    return Path(root).resolve()


def current_head(repo: str | Path) -> str:
    return _git(repository_root(repo), ["rev-parse", "HEAD"]).strip()


def changed_paths(repo: str | Path) -> list[str]:
    root = repository_root(repo)
    commands = [
        ["git", "diff", "--name-only", "-z"],
        ["git", "diff", "--cached", "--name-only", "-z"],
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
    ]
    changed: set[str] = set()
    for command in commands:
        completed = subprocess.run(command, cwd=root, capture_output=True, check=False)
        if completed.returncode != 0:
            raise GitMutationError("GIT_STATUS_FAILED", _stderr(completed) or "Unable to inspect Git mutation state.")
        for raw_path in completed.stdout.split(b"\0"):
            if raw_path:
                changed.add(raw_path.decode("utf-8", errors="surrogateescape"))
    return sorted(changed)


def assert_clean(repo: str | Path) -> None:
    paths = changed_paths(repo)
    if paths:
        raise GitMutationError("REPOSITORY_DIRTY", "Repository is not clean: " + ", ".join(paths))


def validate_relative_repo_path(raw_path: object) -> str:
    if not isinstance(raw_path, str):
        raise GitMutationError("INVALID_REPOSITORY_PATH", "Repository path must be a string.")
    path = raw_path.strip()
    if (
        not path
        or path != raw_path
        or "\\" in path
        or path.startswith("/")
    ):
        raise GitMutationError("INVALID_REPOSITORY_PATH", f"Unsafe repository path: {raw_path!r}")
    pure = PurePosixPath(path)
    if pure.is_absolute() or "." in pure.parts or ".." in pure.parts:
        raise GitMutationError("INVALID_REPOSITORY_PATH", f"Unsafe repository path: {raw_path!r}")
    return pure.as_posix()


def validate_file_contents(
    repo: str | Path,
    files: list[dict],
    *,
    authorized_paths: set[str],
    max_file_bytes: int | None = None,
) -> list[ValidatedFileContent]:
    root = repository_root(repo)
    validated: list[ValidatedFileContent] = []
    seen: set[str] = set()
    if not files:
        raise GitMutationError("NO_FILE_CONTENTS", "No file contents were supplied for integration.")
    for item in files:
        if not isinstance(item, dict):
            raise GitMutationError("MALFORMED_FILE_CONTENT", "File content item must be an object.")
        path = validate_relative_repo_path(item.get("path"))
        if path in seen:
            raise GitMutationError("DUPLICATE_FILE_CONTENT", f"Duplicate file content path: {path}")
        seen.add(path)
        if path not in authorized_paths:
            raise GitMutationError("UNAUTHORIZED_FILE_CONTENT", f"Unauthorized file content path: {path}")
        content = item.get("content")
        if not isinstance(content, str):
            raise GitMutationError("MALFORMED_FILE_CONTENT", f"File content must be a string: {path}")
        encoded = content.encode("utf-8")
        if max_file_bytes is not None and len(encoded) > max_file_bytes:
            raise GitMutationError("FILE_CONTENT_TOO_LARGE", f"File content too large: {path}")
        target = (root / Path(*PurePosixPath(path).parts)).resolve()
        if not _is_relative_to(target, root):
            raise GitMutationError("INVALID_REPOSITORY_PATH", f"Path escapes repository: {path}")
        validated.append(ValidatedFileContent(path=path, absolute_path=target, content=content, byte_count=len(encoded)))
    return validated


def capture_utf8_file_contents(
    repo: str | Path,
    paths: list[str],
    *,
    authorized_paths: set[str],
) -> list[dict[str, str]]:
    root = repository_root(repo)
    captured: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_path in paths:
        path = validate_relative_repo_path(raw_path)
        if path in seen:
            raise GitMutationError("DUPLICATE_FILE_CONTENT", f"Duplicate file content path: {path}")
        seen.add(path)
        if path not in authorized_paths:
            raise GitMutationError("UNAUTHORIZED_FILE_CONTENT", f"Unauthorized file content path: {path}")
        target = (root / Path(*PurePosixPath(path).parts)).resolve()
        if not _is_relative_to(target, root):
            raise GitMutationError("INVALID_REPOSITORY_PATH", f"Path escapes repository: {path}")
        if not target.is_file():
            raise GitMutationError("CANDIDATE_FILE_NOT_REGULAR", f"Candidate file is not a regular file: {path}")
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise GitMutationError("CANDIDATE_FILE_NOT_UTF8", f"Candidate file is not valid UTF-8: {path}") from exc
        except OSError as exc:
            raise GitMutationError("CANDIDATE_FILE_READ_FAILED", f"Could not read candidate file {path}: {exc}") from exc
        captured.append({"path": path, "content": content})
    return captured


def write_validated_file_contents(files: list[ValidatedFileContent]) -> list[dict[str, object]]:
    written: list[dict[str, object]] = []
    for item in files:
        item.absolute_path.parent.mkdir(parents=True, exist_ok=True)
        item.absolute_path.write_text(item.content, encoding="utf-8")
        written.append({"path": item.path, "bytes": item.byte_count})
    return written


def restore_to_baseline(repo: str | Path, baseline_commit: str) -> None:
    root = repository_root(repo)
    resolved = _git(root, ["rev-parse", f"{baseline_commit}^{{commit}}"]).strip()
    if resolved != baseline_commit:
        raise GitMutationError("BASELINE_COMMIT_AMBIGUOUS", "Baseline commit must be an exact full commit SHA.")
    for command in (["git", "reset", "--hard", baseline_commit], ["git", "clean", "-fd"]):
        completed = subprocess.run(command, cwd=root, capture_output=True, check=False)
        if completed.returncode != 0:
            raise GitMutationError("RESTORE_FAILED", _stderr(completed) or "Unable to restore repository.")
    verify_restored(root, baseline_commit)


def verify_restored(repo: str | Path, baseline_commit: str) -> None:
    root = repository_root(repo)
    head = current_head(root)
    if head != baseline_commit:
        raise GitMutationError("RESTORE_HEAD_MISMATCH", f"Repository HEAD {head} does not match baseline {baseline_commit}.")
    assert_clean(root)


def _git(repo: Path, args: list[str]) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise GitMutationError("GIT_COMMAND_FAILED", completed.stderr.strip() or "git command failed")
    return completed.stdout


def _stderr(completed: subprocess.CompletedProcess[bytes]) -> str:
    return completed.stderr.decode("utf-8", errors="replace").strip()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
