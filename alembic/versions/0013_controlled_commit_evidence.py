from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013_controlled_commit_evidence"
down_revision = "0012_deterministic_acceptance_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "controlled_commits",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("repository_id", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("plan_freeze_id", sa.String(length=32), nullable=False),
        sa.Column("baseline_commit_sha", sa.String(length=64), nullable=False),
        sa.Column("parent_commit_sha", sa.String(length=64), nullable=False),
        sa.Column("resulting_commit_sha", sa.String(length=64), nullable=False),
        sa.Column("authorized_paths", sa.JSON(), nullable=False),
        sa.Column("actual_changed_paths", sa.JSON(), nullable=False),
        sa.Column("path_content_hashes", sa.JSON(), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("commit_message", sa.Text(), nullable=False),
        sa.Column("deterministic_acceptance_evidence", sa.JSON(), nullable=False),
        sa.Column("authority_level", sa.String(length=8), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("result", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_freeze_id"], ["plan_freezes.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["repository_id"], ["repository_registrations.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["persistent_workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "plan_freeze_id", "status", name="uq_controlled_commit_workflow_freeze_status"),
    )
    op.create_index(op.f("ix_controlled_commits_project_id"), "controlled_commits", ["project_id"], unique=False)
    op.create_index(op.f("ix_controlled_commits_repository_id"), "controlled_commits", ["repository_id"], unique=False)
    op.create_index(op.f("ix_controlled_commits_resulting_commit_sha"), "controlled_commits", ["resulting_commit_sha"], unique=False)
    op.create_index(op.f("ix_controlled_commits_status"), "controlled_commits", ["status"], unique=False)
    op.create_index(op.f("ix_controlled_commits_task_id"), "controlled_commits", ["task_id"], unique=False)
    op.create_index(op.f("ix_controlled_commits_workflow_id"), "controlled_commits", ["workflow_id"], unique=False)
    op.create_index(op.f("ix_controlled_commits_plan_freeze_id"), "controlled_commits", ["plan_freeze_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_controlled_commits_plan_freeze_id"), table_name="controlled_commits")
    op.drop_index(op.f("ix_controlled_commits_workflow_id"), table_name="controlled_commits")
    op.drop_index(op.f("ix_controlled_commits_task_id"), table_name="controlled_commits")
    op.drop_index(op.f("ix_controlled_commits_status"), table_name="controlled_commits")
    op.drop_index(op.f("ix_controlled_commits_resulting_commit_sha"), table_name="controlled_commits")
    op.drop_index(op.f("ix_controlled_commits_repository_id"), table_name="controlled_commits")
    op.drop_index(op.f("ix_controlled_commits_project_id"), table_name="controlled_commits")
    op.drop_table("controlled_commits")
