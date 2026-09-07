from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from lucius.persistence.database import create_all, create_sqlite_engine, make_session_factory
from lucius.persistence.orm import BenchmarkResultORM, IdCounterORM, utc_now
from lucius.persistence.repositories import next_id
from lucius.persistence.store_lock import canonical_store_write_lock


def test_two_simultaneous_id_allocations_are_unique(tmp_path: Path):
    database = tmp_path / "store.sqlite"
    _create_store(database)
    start = tmp_path / "start"

    script = _worker_script(
        """
        from lucius.persistence.database import create_sqlite_engine, make_session_factory
        from lucius.persistence.repositories import next_id

        db, start = sys.argv[1], pathlib.Path(sys.argv[2])
        wait_for_start(start)
        engine = create_sqlite_engine(db)
        factory = make_session_factory(engine)
        with factory() as session:
            public_id = next_id(session, "benchmark")
            session.commit()
            print(public_id)
        """
    )
    processes = [_spawn(script, database, start) for _ in range(2)]
    start.touch()
    ids = [_finish(process) for process in processes]

    assert sorted(ids) == ["LBENCH_000001", "LBENCH_000002"]
    assert _integrity(database) == ("ok", [])


def test_concurrent_canonical_cli_repository_state_captures_are_serialized(tmp_path: Path):
    database = tmp_path / "store.sqlite"
    repos = [_make_git_repo(tmp_path / f"repo-{index}", f"repo-{index}") for index in range(3)]
    start = tmp_path / "start"

    processes = [
        _spawn_cli(
            database,
            start,
            "inspect-repository-state",
            str(repo),
            "--strict-canonical",
        )
        for repo in repos
    ]
    start.touch()
    observations = [json.loads(_finish(process)) for process in processes]
    ids = [item["id"] for item in observations]

    assert len(ids) == len(set(ids)) == 3
    assert sorted(ids) == ["LRSTATE_000001", "LRSTATE_000002", "LRSTATE_000003"]
    assert {item["repository_path"] for item in observations} == {str(repo) for repo in repos}
    assert all(item["classification"] == "CANONICAL_CLEAN" for item in observations)
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT id, repository_path, head_commit, manifest_hash FROM repository_state_observations ORDER BY id"
        ).fetchall()
    assert [row[0] for row in rows] == sorted(ids)
    assert {row[1] for row in rows} == {str(repo) for repo in repos}
    assert all(row[2] and row[3] for row in rows)
    assert _integrity(database) == ("ok", [])


def test_direct_service_concurrent_repository_state_captures_keep_distinct_identity(tmp_path: Path):
    database = tmp_path / "store.sqlite"
    _create_store(database)
    repos = [_make_git_repo(tmp_path / f"service-repo-{index}", f"service-repo-{index}") for index in range(3)]
    start = tmp_path / "start"
    script = _worker_script(
        """
        from lucius.domain.enums import Actor
        from lucius.persistence.database import create_sqlite_engine, make_session_factory
        from lucius.pilots.repository_state import RepositoryStateService
        from lucius.repositories.schemas import WorkspaceContext

        db, repo, start = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
        wait_for_start(start)
        engine = create_sqlite_engine(db)
        factory = make_session_factory(engine)
        with factory() as session:
            workspace = WorkspaceContext(workspace_id="concurrency-test", allowed_roots=[repo.parent])
            result = RepositoryStateService(session).inspect(
                repository_path=repo,
                workspace_context=workspace,
                strict_canonical=True,
                actor=Actor.LUCIUS,
            )
            session.commit()
            print(result.model_dump_json())
        """
    )
    processes = [_spawn(script, database, repo, start) for repo in repos]
    start.touch()
    observations = [json.loads(_finish(process)) for process in processes]

    assert len({item["id"] for item in observations}) == 3
    assert {item["repository_path"] for item in observations} == {str(repo) for repo in repos}
    assert _integrity(database) == ("ok", [])


def test_rollback_and_crash_equivalent_do_not_corrupt_counter(tmp_path: Path):
    database = tmp_path / "store.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        allocated = next_id(session, "benchmark")
        session.rollback()
    with factory() as session:
        assert next_id(session, "benchmark") == allocated
        session.commit()

    crash_script = _worker_script(
        """
        from lucius.persistence.database import create_sqlite_engine, make_session_factory
        from lucius.persistence.repositories import next_id

        db = sys.argv[1]
        engine = create_sqlite_engine(db)
        factory = make_session_factory(engine)
        with factory() as session:
            print(next_id(session, "benchmark"), flush=True)
            os._exit(19)
        """
    )
    process = subprocess.run(
        [sys.executable, "-c", crash_script, str(database)],
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 19
    crashed_id = process.stdout.strip()
    with factory() as session:
        replacement_id = next_id(session, "benchmark")
        session.commit()
    assert replacement_id == crashed_id
    assert _integrity(database) == ("ok", [])


def test_stale_counter_with_concurrent_writers_repairs_to_persisted_high_id(tmp_path: Path):
    database = tmp_path / "store.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        session.add(IdCounterORM(entity="benchmark", next_number=1))
        session.add(
            BenchmarkResultORM(
                id="LBENCH_000777",
                suite_name="manual",
                suite_version=1,
                benchmark_version="manual",
                git_head=None,
                target_dirty=False,
                status="PASSED",
                total_cases=1,
                passed=1,
                failed=0,
                skipped=0,
                duration_ms=1,
                deterministic_metrics={},
                environment_metadata={},
                captured_at=utc_now(),
            )
        )
        session.commit()

    start = tmp_path / "start"
    script = _worker_script(
        """
        from lucius.persistence.database import create_sqlite_engine, make_session_factory
        from lucius.persistence.repositories import next_id

        db, start = sys.argv[1], pathlib.Path(sys.argv[2])
        wait_for_start(start)
        engine = create_sqlite_engine(db)
        factory = make_session_factory(engine)
        with factory() as session:
            public_id = next_id(session, "benchmark")
            session.commit()
            print(public_id)
        """
    )
    processes = [_spawn(script, database, start) for _ in range(2)]
    start.touch()
    ids = sorted(_finish(process) for process in processes)
    assert ids == ["LBENCH_000778", "LBENCH_000779"]


def test_cli_writer_waits_for_single_dispatcher_store_lock(tmp_path: Path):
    database = tmp_path / "store.sqlite"
    repo = _make_git_repo(tmp_path / "repo", "repo")
    start = tmp_path / "start"

    with canonical_store_write_lock(database, timeout_seconds=1.0):
        process = _spawn_cli(
            database,
            start,
            "inspect-repository-state",
            str(repo),
            "--strict-canonical",
        )
        start.touch()
        time.sleep(0.2)
        assert process.poll() is None
    observation = json.loads(_finish(process))

    assert observation["id"] == "LRSTATE_000001"
    assert observation["repository_path"] == str(repo)
    assert _integrity(database) == ("ok", [])


def test_sqlite_busy_contention_waits_and_allocates_after_writer_releases(tmp_path: Path):
    database = tmp_path / "store.sqlite"
    _create_store(database)
    start = tmp_path / "start"
    script = _worker_script(
        """
        from lucius.persistence.database import create_sqlite_engine, make_session_factory
        from lucius.persistence.repositories import next_id

        db, start = sys.argv[1], pathlib.Path(sys.argv[2])
        wait_for_start(start)
        engine = create_sqlite_engine(db)
        factory = make_session_factory(engine)
        with factory() as session:
            public_id = next_id(session, "benchmark")
            session.commit()
            print(public_id)
        """
    )

    holder = sqlite3.connect(database, timeout=10, isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        process = _spawn(script, database, start)
        start.touch()
        time.sleep(0.2)
        assert process.poll() is None
        holder.commit()
        assert _finish(process) == "LBENCH_000001"
    finally:
        holder.close()
    assert _integrity(database) == ("ok", [])


def test_ordinary_sequential_id_operation_is_unchanged(tmp_path: Path):
    database = tmp_path / "store.sqlite"
    engine = create_sqlite_engine(database)
    create_all(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        assert next_id(session, "benchmark") == "LBENCH_000001"
        assert next_id(session, "benchmark") == "LBENCH_000002"
        session.commit()


def _create_store(database: Path) -> None:
    engine = create_sqlite_engine(database)
    create_all(engine)


def _worker_script(body: str) -> str:
    return (
        "from __future__ import annotations\n"
        "import os, pathlib, sys, time\n"
        "def wait_for_start(path):\n"
        "    deadline = time.monotonic() + 5\n"
        "    while not path.exists():\n"
        "        if time.monotonic() > deadline:\n"
        "            raise SystemExit('start barrier timed out')\n"
        "        time.sleep(0.01)\n"
        + textwrap.dedent(body)
    )


def _spawn(script: str, *args: object) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", script, *(str(arg) for arg in args)],
        env=_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _spawn_cli(database: Path, start: Path, *cli_args: str) -> subprocess.Popen[str]:
    script = _worker_script(
        """
        db, start, cli_args = sys.argv[1], pathlib.Path(sys.argv[2]), sys.argv[3:]
        wait_for_start(start)
        os.execv(sys.executable, [sys.executable, "-m", "lucius.pilots.cli", "--database", db, *cli_args])
        """
    )
    return _spawn(script, database, start, *cli_args)


def _finish(process: subprocess.Popen[str]) -> str:
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, stderr or stdout
    return stdout.strip()


def _env() -> dict[str, str]:
    env = os.environ.copy()
    src = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = f"{src}:{env.get('PYTHONPATH', '')}"
    return env


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _make_git_repo(path: Path, name: str) -> Path:
    path.mkdir(parents=True)
    _run_git(path, "init")
    _run_git(path, "config", "user.email", "lucius@example.test")
    _run_git(path, "config", "user.name", "Lucius Tests")
    _run_git(path, "checkout", "-b", "main")
    (path / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    (path / "pyproject.toml").write_text("[project]\nname = \"sample\"\n", encoding="utf-8")
    (path / "tests").mkdir()
    (path / "tests" / "test_sample.py").write_text("def test_sample():\n    assert True\n", encoding="utf-8")
    _run_git(path, "add", "README.md", "pyproject.toml", "tests/test_sample.py")
    _run_git(path, "commit", "-m", "initial")
    return path


def _integrity(database: Path) -> tuple[str, list[tuple]]:
    with sqlite3.connect(database) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        fk = connection.execute("PRAGMA foreign_key_check").fetchall()
    return integrity, fk
