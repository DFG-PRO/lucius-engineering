from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_evidence_retrieval"
down_revision = "0002_project_task_contracts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_references",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("repository_id", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("task_run_id", sa.String(length=32), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("claim", sa.Text(), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("match_reasons", sa.JSON(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["repository_id"], ["repository_registrations.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["repository_snapshots.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["task_run_id"], ["task_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_references_project_id", "evidence_references", ["project_id"])
    op.create_index("ix_evidence_references_repository_id", "evidence_references", ["repository_id"])
    op.create_index("ix_evidence_references_snapshot_id", "evidence_references", ["snapshot_id"])
    op.create_index("ix_evidence_references_task_id", "evidence_references", ["task_id"])
    op.create_index("ix_evidence_references_task_run_id", "evidence_references", ["task_run_id"])


def downgrade() -> None:
    op.drop_index("ix_evidence_references_task_run_id", table_name="evidence_references")
    op.drop_index("ix_evidence_references_task_id", table_name="evidence_references")
    op.drop_index("ix_evidence_references_snapshot_id", table_name="evidence_references")
    op.drop_index("ix_evidence_references_repository_id", table_name="evidence_references")
    op.drop_index("ix_evidence_references_project_id", table_name="evidence_references")
    op.drop_table("evidence_references")

