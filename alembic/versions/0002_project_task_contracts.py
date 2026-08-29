from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_project_task_contracts"
down_revision = "0001_repository_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("slug", sa.String(length=255), nullable=True))
    op.add_column("projects", sa.Column("organization", sa.String(length=255), nullable=True))
    op.add_column("projects", sa.Column("project_type", sa.String(length=32), nullable=False, server_default="DFG_INTERNAL"))
    op.add_column("projects", sa.Column("workspace_scope", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("documentation_policy", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("projects", sa.Column("default_authority_level", sa.String(length=8), nullable=False, server_default="L0"))
    op.execute("update projects set slug = lower(replace(name, ' ', '-')) where slug is null")
    with op.batch_alter_table("projects") as batch:
        batch.alter_column("slug", existing_type=sa.String(length=255), nullable=False)
    op.create_index("ix_projects_slug", "projects", ["slug"], unique=True)

    op.create_table(
        "project_repository_attachments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("repository_id", sa.String(length=32), nullable=False),
        sa.Column("attached_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attached_by", sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["repository_id"], ["repository_registrations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "repository_id", name="uq_project_repository_attachment"),
    )
    op.create_index("ix_project_repository_attachments_project_id", "project_repository_attachments", ["project_id"])
    op.create_index("ix_project_repository_attachments_repository_id", "project_repository_attachments", ["repository_id"])
    op.execute(
        """
        insert or ignore into project_repository_attachments
            (project_id, repository_id, attached_at, attached_by)
        select project_id, id, created_at, 'SYSTEM'
        from repository_registrations
        """
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=32), nullable=False),
        sa.Column("complexity", sa.String(length=8), nullable=False),
        sa.Column("authority_level", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("blocker_code", sa.String(length=64), nullable=True),
        sa.Column("blocker_message", sa.Text(), nullable=True),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])

    op.create_table(
        "task_contracts",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=False),
        sa.Column("constraints", sa.JSON(), nullable=False),
        sa.Column("repository_ids", sa.JSON(), nullable=False),
        sa.Column("allowed_actions", sa.JSON(), nullable=False),
        sa.Column("allowed_tools", sa.JSON(), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("authority_level", sa.String(length=8), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("documentation_required", sa.Boolean(), nullable=False),
        sa.Column("documentation_targets", sa.JSON(), nullable=False),
        sa.Column("stop_conditions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "version", name="uq_task_contract_version"),
    )
    op.create_index("ix_task_contracts_task_id", "task_contracts", ["task_id"])

    op.create_table(
        "task_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("model_provider", sa.String(length=255), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["snapshot_id"], ["repository_snapshots.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_runs_snapshot_id", "task_runs", ["snapshot_id"])
    op.create_index("ix_task_runs_task_id", "task_runs", ["task_id"])

    op.create_table(
        "documentation_completions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("targets_completed", sa.JSON(), nullable=False),
        sa.Column("evidence_references", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_by", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index("ix_documentation_completions_task_id", "documentation_completions", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_documentation_completions_task_id", table_name="documentation_completions")
    op.drop_table("documentation_completions")
    op.drop_index("ix_task_runs_task_id", table_name="task_runs")
    op.drop_index("ix_task_runs_snapshot_id", table_name="task_runs")
    op.drop_table("task_runs")
    op.drop_index("ix_task_contracts_task_id", table_name="task_contracts")
    op.drop_table("task_contracts")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_project_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_project_repository_attachments_repository_id", table_name="project_repository_attachments")
    op.drop_index("ix_project_repository_attachments_project_id", table_name="project_repository_attachments")
    op.drop_table("project_repository_attachments")
    op.drop_index("ix_projects_slug", table_name="projects")
    op.drop_column("projects", "default_authority_level")
    op.drop_column("projects", "documentation_policy")
    op.drop_column("projects", "workspace_scope")
    op.drop_column("projects", "project_type")
    op.drop_column("projects", "organization")
    op.drop_column("projects", "slug")
