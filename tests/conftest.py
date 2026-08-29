from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.repositories.schemas import WorkspaceContext


@pytest.fixture()
def session():
    engine = create_sqlite_engine()
    create_all(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        yield session


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def make_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init")
    run_git(path, "config", "user.email", "lucius@example.test")
    run_git(path, "config", "user.name", "Lucius Tests")
    run_git(path, "checkout", "-b", "main")
    (path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    (path / "pyproject.toml").write_text(
        "[project]\nname = \"sample\"\ndependencies = [\"SQLAlchemy\", \"pytest\"]\n",
        encoding="utf-8",
    )
    (path / "tests").mkdir()
    (path / "tests" / "test_sample.py").write_text("def test_sample():\n    assert True\n", encoding="utf-8")
    run_git(path, "add", "README.md", "pyproject.toml", "tests/test_sample.py")
    run_git(path, "commit", "-m", "initial")
    return path


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    return make_git_repo(tmp_path / "workspace" / "repo")


@pytest.fixture()
def workspace(git_repo: Path) -> WorkspaceContext:
    return WorkspaceContext(workspace_id="test", allowed_roots=[git_repo.parent])

