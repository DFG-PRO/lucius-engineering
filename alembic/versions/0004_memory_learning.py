from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_memory_learning"
down_revision = "0003_evidence_retrieval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_entries",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("organization_id", sa.String(length=255), nullable=True),
        sa.Column("workspace_id", sa.String(length=255), nullable=True),
        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=True),
        sa.Column("source_project_id", sa.String(length=32), nullable=True),
        sa.Column("source_task_id", sa.String(length=32), nullable=True),
        sa.Column("source_run_id", sa.String(length=32), nullable=True),
        sa.Column("source_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("context_tags", sa.JSON(), nullable=False),
        sa.Column("technology_tags", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", sa.String(length=32), nullable=True),
        sa.Column("superseded_by_id", sa.String(length=32), nullable=True),
        sa.Column("supersession_reason", sa.Text(), nullable=True),
        sa.Column("requires_revalidation", sa.Boolean(), nullable=False),
        sa.Column("revalidation_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in [
        "project_id",
        "organization_id",
        "workspace_id",
        "memory_type",
        "scope",
        "source_project_id",
        "source_task_id",
        "source_run_id",
        "validation_status",
        "supersedes_id",
        "superseded_by_id",
    ]:
        op.create_index(f"ix_memory_entries_{column}", "memory_entries", [column])

    op.create_table(
        "learning_candidates",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("task_id", sa.String(length=32), nullable=True),
        sa.Column("candidate_type", sa.String(length=64), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("source_memory_ids", sa.JSON(), nullable=False),
        sa.Column("source_document_refs", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("proposed_scope", sa.String(length=32), nullable=False),
        sa.Column("sanitization_status", sa.String(length=32), nullable=False),
        sa.Column("source_classification", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_notes", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["task_runs.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ["project_id", "run_id", "task_id", "candidate_type", "status", "proposed_scope"]:
        op.create_index(f"ix_learning_candidates_{column}", "learning_candidates", [column])


def downgrade() -> None:
    for column in ["project_id", "run_id", "task_id", "candidate_type", "status", "proposed_scope"]:
        op.drop_index(f"ix_learning_candidates_{column}", table_name="learning_candidates")
    op.drop_table("learning_candidates")
    for column in [
        "project_id",
        "organization_id",
        "workspace_id",
        "memory_type",
        "scope",
        "source_project_id",
        "source_task_id",
        "source_run_id",
        "validation_status",
        "supersedes_id",
        "superseded_by_id",
    ]:
        op.drop_index(f"ix_memory_entries_{column}", table_name="memory_entries")
    op.drop_table("memory_entries")

