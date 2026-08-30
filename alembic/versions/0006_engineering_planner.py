from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006_engineering_planner"
down_revision = "0005_model_gateway"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "engineering_plans",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("task_contract_id", sa.String(length=32), nullable=False),
        sa.Column("task_contract_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("required_authority_level", sa.String(length=8), nullable=False),
        sa.Column("repository_snapshot_ids", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("memory_ids", sa.JSON(), nullable=False),
        sa.Column("model_execution_ids", sa.JSON(), nullable=False),
        sa.Column("assumptions", sa.JSON(), nullable=False),
        sa.Column("unknowns", sa.JSON(), nullable=False),
        sa.Column("open_questions", sa.JSON(), nullable=False),
        sa.Column("affected_components", sa.JSON(), nullable=False),
        sa.Column("affected_files", sa.JSON(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("acceptance_coverage", sa.JSON(), nullable=False),
        sa.Column("test_strategy", sa.JSON(), nullable=False),
        sa.Column("documentation_requirements", sa.JSON(), nullable=False),
        sa.Column("rollback_considerations", sa.JSON(), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("risks", sa.JSON(), nullable=False),
        sa.Column("estimated_scope", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("validation_warnings", sa.JSON(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("planner_version", sa.String(length=32), nullable=False),
        sa.Column("supersedes_plan_id", sa.String(length=32), nullable=True),
        sa.Column("superseded_by_plan_id", sa.String(length=32), nullable=True),
        sa.Column("supersession_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in [
        "task_id",
        "run_id",
        "project_id",
        "status",
        "supersedes_plan_id",
        "superseded_by_plan_id",
    ]:
        op.create_index(f"ix_engineering_plans_{column}", "engineering_plans", [column])


def downgrade() -> None:
    for column in [
        "task_id",
        "run_id",
        "project_id",
        "status",
        "supersedes_plan_id",
        "superseded_by_plan_id",
    ]:
        op.drop_index(f"ix_engineering_plans_{column}", table_name="engineering_plans")
    op.drop_table("engineering_plans")
