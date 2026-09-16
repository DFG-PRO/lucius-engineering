from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from lucius.persistence.orm import PersistentWorkflowORM, ProjectORM, TaskORM
from lucius.pilots import cli
from lucius.pilots.provisioning import (
    ReadOnlyBacklogProvisioningError,
    provision_read_only_backlog,
)



def _run_git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _setup(tmp_path: Path, task_count: int = 1) -> tuple[dict, str]:
    repository = tmp_path / "darwin"
    workspace = tmp_path / "controlled" / "overnight01"
    repository.mkdir(parents=True)
    _run_git(repository, "init")
    _run_git(repository, "config", "user.email", "lucius@example.test")
    _run_git(repository, "config", "user.name", "Lucius Tests")
    _run_git(repository, "checkout", "-b", "main")
    (repository / "README.md").write_text("# Darwin\n", encoding="utf-8")
    (repository / "pyproject.toml").write_text("[project]\nname = 'darwin'\n", encoding="utf-8")
    (repository / ".gitignore").write_text("invalid.bin\nlarge.txt\n", encoding="utf-8")
    _run_git(repository, "add", "README.md", "pyproject.toml", ".gitignore")
    _run_git(repository, "commit", "-m", "initial")
    shutil.copytree(repository, workspace)
    head = _run_git(repository, "rev-parse", "HEAD")
    tasks = []
    for index in range(task_count):
        tasks.append(
            {
                "key": f"task-{index + 1}",
                "title": f"Inspection {index + 1}",
                "objective": f"Inspect bounded repository evidence {index + 1}.",
                "context_paths": ["README.md", "pyproject.toml"],
                "acceptance_criteria": ["Report only evidence grounded in supplied files."],
                "read_only": True,
                "mutation_allowed": False,
                "unattended": True,
                "task_complexity": "T1",
                "task_risk": "LOW",
                "isolation_mode": "ISOLATED_WORKTREE",
                "execution_supervision": "UNSUPERVISED",
                "deterministic_verification": True,
                "evidence_reference_validation_required": True,
                "required_capabilities": ["inspection_reasoning"],
            }
        )
    return (
        {
            "provisioning_id": "test-overnight01",
            "project": {"name": "Test Darwin Overnight01", "slug": "test-darwin-overnight01"},
            "repository": {
                "name": "Darwin",
                "path": str(repository),
                "frozen_head": head,
                "workspace": str(workspace),
                "allowed_workspace_root": str(workspace.parent),
            },
            "tasks": tasks,
        },
        head,
    )


def test_successfully_provisions_multiple_independent_tasks(session, tmp_path: Path):
    specification, _ = _setup(tmp_path, task_count=3)

    result = provision_read_only_backlog(session, specification)

    assert len(result) == 3
    workflows = session.scalars(select(PersistentWorkflowORM)).all()
    assert len(workflows) == 3
    assert all(workflow.plan_id and workflow.plan_freeze_id for workflow in workflows)
    assert all(workflow.task_backlog[0]["state"] == "READY" for workflow in workflows)
    assert all(workflow.task_backlog[0]["read_only"] is True for workflow in workflows)
    assert all(workflow.task_backlog[0]["mutation_allowed"] is False for workflow in workflows)
    assert all(task.status == "READY" for task in session.scalars(select(TaskORM)).all())


def test_cli_provisions_from_declarative_spec(tmp_path: Path, monkeypatch, capsys):
    specification, _ = _setup(tmp_path, task_count=2)
    spec_path = tmp_path / "spec.json"
    database_path = tmp_path / "lucius.sqlite"
    spec_path.write_text(json.dumps(specification), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "lucius",
            "--database",
            str(database_path),
            "provision-read-only-backlog",
            "--spec",
            str(spec_path),
        ],
    )

    cli.main()

    output = json.loads(capsys.readouterr().out)
    assert output["result"] == "PROVISIONED"
    assert len(output["workflows"]) == 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda spec, head: spec["repository"].update(frozen_head="0" * 40), "frozen_head"),
        (lambda spec, head: spec["repository"].update(workspace=str(Path(spec["repository"]["workspace"]).parent.parent / "outside")), "workspace"),
        (lambda spec, head: _run_git(Path(spec["repository"]["workspace"]), "clean", "-fd") or (Path(spec["repository"]["workspace"]) / "dirty.txt").write_text("dirty\n"), "dirty"),
        (lambda spec, head: spec["tasks"][0].update(context_paths=["missing.md"]), "Context"),
        (lambda spec, head: spec["tasks"][0].update(context_paths=["../README.md"]), "regular file"),
        (lambda spec, head: spec["tasks"][0].update(read_only=False), "read_only"),
        (lambda spec, head: spec["tasks"][0].update(deterministic_verification=False), "deterministic"),
    ],
)
def test_safety_failures_leave_no_rows(session, tmp_path: Path, change, message: str):
    specification, head = _setup(tmp_path)
    change(specification, head)

    with pytest.raises(ReadOnlyBacklogProvisioningError, match=message):
        provision_read_only_backlog(session, specification)

    assert session.scalars(select(ProjectORM)).all() == []
    assert session.scalars(select(PersistentWorkflowORM)).all() == []


def test_invalid_utf8_and_oversized_context_fail_closed(session, tmp_path: Path):
    specification, _ = _setup(tmp_path)
    workspace = Path(specification["repository"]["workspace"])
    (workspace / "invalid.bin").write_bytes(b"\xff")
    specification["tasks"][0]["context_paths"] = ["invalid.bin"]
    with pytest.raises(ReadOnlyBacklogProvisioningError, match="UTF-8"):
        provision_read_only_backlog(session, specification)

    specification, _ = _setup(tmp_path / "large")
    workspace = Path(specification["repository"]["workspace"])
    (workspace / "large.txt").write_text("x" * 40_001, encoding="utf-8")
    specification["tasks"][0]["context_paths"] = ["large.txt"]
    with pytest.raises(ReadOnlyBacklogProvisioningError, match="total bytes"):
        provision_read_only_backlog(session, specification)


def test_duplicate_provisioning_is_rejected(session, tmp_path: Path):
    specification, _ = _setup(tmp_path)
    provision_read_only_backlog(session, specification)

    with pytest.raises(ReadOnlyBacklogProvisioningError, match="already exists"):
        provision_read_only_backlog(session, copy.deepcopy(specification))


def test_historical_workflow_identifier_cannot_be_reused(session, tmp_path: Path):
    specification, _ = _setup(tmp_path)
    specification["provisioning_id"] = "reuse-LWORK_000153"

    with pytest.raises(ReadOnlyBacklogProvisioningError, match="Historical"):
        provision_read_only_backlog(session, specification)


def test_workspace_authorization_fields_and_policy_flags_are_explicit(session, tmp_path: Path):
    specification, _ = _setup(tmp_path)
    specification["repository"].pop("allowed_workspace_root")
    with pytest.raises(ReadOnlyBacklogProvisioningError, match="explicitly required"):
        provision_read_only_backlog(session, specification)

    specification, _ = _setup(tmp_path / "policy")
    specification["tasks"][0].pop("execution_supervision")
    with pytest.raises(ReadOnlyBacklogProvisioningError, match="explicitly declare"):
        provision_read_only_backlog(session, specification)