from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from lucius.persistence.orm import (
    EngineeringPlanORM,
    PersistentWorkflowORM,
    PlanFreezeORM,
    ProjectORM,
    RepositoryRegistrationORM,
    TaskContractORM,
    TaskORM,
)
from lucius.pilots import cli
from lucius.pilots.controlled_mutation_provisioning import (
    ControlledMutationProvisioningError,
    provision_controlled_mutation,
)


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _setup(tmp_path: Path) -> tuple[dict, str]:
    repository = tmp_path / "darwin"
    worktrees = tmp_path / "controlled"
    workspace = worktrees / "portfolio01"

    repository.mkdir(parents=True)
    worktrees.mkdir(parents=True)

    _git(repository, "init")
    _git(repository, "config", "user.email", "lucius@example.test")
    _git(repository, "config", "user.name", "Lucius Tests")
    _git(repository, "checkout", "-b", "main")

    (repository / "src").mkdir()
    (repository / "tests").mkdir()
    (repository / "docs").mkdir()

    (repository / "src" / "portfolio.py").write_text(
        'VALUE = "baseline"\n',
        encoding="utf-8",
    )
    (repository / "tests" / "test_portfolio.py").write_text(
        "def test_placeholder():\n    assert True\n",
        encoding="utf-8",
    )
    (repository / "docs" / "portfolio.md").write_text(
        "# Portfolio\n",
        encoding="utf-8",
    )

    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "initial")

    baseline = _git(repository, "rev-parse", "HEAD")

    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "worktree",
            "add",
            "-b",
            "lucius/test/portfolio01",
            str(workspace),
            baseline,
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    paths = [
        "src/portfolio.py",
        "tests/test_portfolio.py",
        "docs/portfolio.md",
    ]

    checks = [
        {"type": "file_exists", "path": path}
        for path in paths
    ]

    specification = {
        "provisioning_id": "test-controlled-mutation",
        "project": {
            "name": "Test Darwin Controlled Mutation",
            "slug": "test-darwin-controlled-mutation",
        },
        "repository": {
            "name": "Darwin",
            "path": str(repository),
            "frozen_head": baseline,
            "workspace": str(workspace),
            "allowed_workspace_root": str(worktrees),
            "default_branch": "main",
        },
        "tasks": [
            {
                "key": "portfolio01",
                "title": "Build Portfolio 01",
                "objective": "Build bounded Portfolio 01 support.",
                "acceptance_criteria": [
                    "Authorized implementation, tests, and documentation exist."
                ],
                "read_only": False,
                "mutation_allowed": True,
                "unattended": False,
                "execution_supervision": "SUPERVISED",
                "deterministic_verification": True,
                "allowed_mutation_paths": paths,
                "deterministic_acceptance_checks": checks,
                "context_paths": paths,
            }
        ],
    }

    return specification, baseline


def test_provisions_l1_controlled_mutation_workflow(session, tmp_path: Path):
    specification, baseline = _setup(tmp_path)

    result = provision_controlled_mutation(session, specification)

    assert len(result) == 1

    workflow = session.scalar(select(PersistentWorkflowORM))
    task = session.scalar(select(TaskORM))
    contract = session.scalar(select(TaskContractORM))
    plan = session.scalar(select(EngineeringPlanORM))
    freeze = session.scalar(select(PlanFreezeORM))
    repository = session.scalar(select(RepositoryRegistrationORM))

    assert workflow is not None
    assert task is not None
    assert contract is not None
    assert plan is not None
    assert freeze is not None
    assert repository is not None

    assert workflow.workflow_state == "PLAN_READY"
    assert workflow.authority_tier == "L1"
    assert workflow.expected_main_head == baseline
    assert workflow.task_backlog[0]["mutation_allowed"] is True
    assert workflow.task_backlog[0]["read_only"] is False
    assert workflow.task_backlog[0]["unattended"] is False
    assert workflow.task_backlog[0]["execution_supervision"] == "SUPERVISED"

    assert task.authority_level == "L1"
    assert contract.authority_level == "L1"
    assert "WRITE_SOURCE" in contract.allowed_actions
    assert "WRITE_TESTS" in contract.allowed_actions
    assert "WRITE_DOCUMENTATION" in contract.allowed_actions
    assert "CREATE_COMMIT" not in contract.allowed_actions

    assert repository.access_mode == "CANONICAL_INTEGRATION"
    assert freeze.commit_sha == baseline

    frozen_paths = {
        item["path"]
        for item in freeze.plan_payload["affected_files"]
    }
    assert frozen_paths == {
        "src/portfolio.py",
        "tests/test_portfolio.py",
        "docs/portfolio.md",
    }


def test_cli_provisions_controlled_mutation(tmp_path: Path, monkeypatch, capsys):
    specification, _ = _setup(tmp_path)
    spec_path = tmp_path / "spec.json"
    database_path = tmp_path / "lucius.sqlite"

    spec_path.write_text(
        json.dumps(specification),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "lucius",
            "--database",
            str(database_path),
            "provision-controlled-mutation",
            "--spec",
            str(spec_path),
        ],
    )

    cli.main()

    payload = json.loads(capsys.readouterr().out)

    assert payload["result"] == "PROVISIONED"
    assert payload["mode"] == "CONTROLLED_MUTATION"
    assert len(payload["workflows"]) == 1


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda spec: spec["tasks"][0].update(mutation_allowed=False),
            "mutation_allowed",
        ),
        (
            lambda spec: spec["tasks"][0].update(read_only=True),
            "read_only",
        ),
        (
            lambda spec: spec["tasks"][0].update(unattended=True),
            "unattended",
        ),
        (
            lambda spec: spec["tasks"][0].update(
                execution_supervision="UNSUPERVISED"
            ),
            "execution_supervision",
        ),
        (
            lambda spec: spec["tasks"][0].update(
                deterministic_verification=False
            ),
            "deterministic_verification",
        ),
        (
            lambda spec: spec["tasks"][0].update(
                deterministic_acceptance_checks=[]
            ),
            "acceptance",
        ),
    ],
)
def test_policy_failures_leave_no_rows(
    session,
    tmp_path: Path,
    mutate,
    message: str,
):
    specification, _ = _setup(tmp_path)
    mutate(specification)

    with pytest.raises(
        ControlledMutationProvisioningError,
        match=message,
    ):
        provision_controlled_mutation(session, specification)

    assert session.scalars(select(ProjectORM)).all() == []
    assert session.scalars(select(PersistentWorkflowORM)).all() == []


def test_acceptance_must_cover_every_authorized_path(session, tmp_path: Path):
    specification, _ = _setup(tmp_path)

    specification["tasks"][0]["deterministic_acceptance_checks"] = [
        {
            "type": "file_exists",
            "path": "src/portfolio.py",
        }
    ]

    with pytest.raises(
        ControlledMutationProvisioningError,
        match="Every allowed mutation path",
    ):
        provision_controlled_mutation(session, specification)


def test_workspace_must_be_clean(session, tmp_path: Path):
    specification, _ = _setup(tmp_path)

    workspace = Path(specification["repository"]["workspace"])
    (workspace / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(
        ControlledMutationProvisioningError,
        match="dirty",
    ):
        provision_controlled_mutation(session, specification)


def test_canonical_baseline_must_match(session, tmp_path: Path):
    specification, _ = _setup(tmp_path)
    specification["repository"]["frozen_head"] = "0" * 40

    with pytest.raises(
        ControlledMutationProvisioningError,
        match="HEAD",
    ):
        provision_controlled_mutation(session, specification)


def test_reuses_existing_project_when_name_matches_but_slug_differs(
    session,
    tmp_path: Path,
):
    specification, _ = _setup(tmp_path)

    existing = ProjectORM(
        id="LPROJ_009999",
        name=specification["project"]["name"],
        slug="existing-darwin-project",
        organization=None,
        project_type="R_AND_D",
        status="ACTIVE",
        description="Existing canonical Darwin project.",
        workspace_scope=None,
        documentation_policy={},
        default_authority_level="L0",
        created_at=__import__(
            "lucius.persistence.orm",
            fromlist=["utc_now"],
        ).utc_now(),
        updated_at=__import__(
            "lucius.persistence.orm",
            fromlist=["utc_now"],
        ).utc_now(),
    )
    session.add(existing)
    session.flush()

    result = provision_controlled_mutation(
        session,
        specification,
    )

    assert len(result) == 1

    projects = session.scalars(
        select(ProjectORM)
    ).all()

    assert len(projects) == 1
    assert projects[0].id == existing.id
    assert projects[0].name == specification["project"]["name"]
    assert projects[0].slug == "existing-darwin-project"

    task = session.scalar(select(TaskORM))
    repository = session.scalar(
        select(RepositoryRegistrationORM)
    )

    assert task is not None
    assert repository is not None
    assert task.project_id == existing.id
    assert repository.project_id == existing.id



def test_duplicate_provisioning_is_rejected(session, tmp_path: Path):
    specification, _ = _setup(tmp_path)

    provision_controlled_mutation(session, specification)

    with pytest.raises(
        ControlledMutationProvisioningError,
        match="already exists",
    ):
        provision_controlled_mutation(
            session,
            copy.deepcopy(specification),
        )
