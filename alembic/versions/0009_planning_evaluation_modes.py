from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0009_planning_evaluation_modes"
down_revision = "0008_pilot_evaluation_infrastructure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "engineering_plan_evaluations",
        sa.Column("supersedes_evaluation_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "engineering_plan_evaluations",
        sa.Column(
            "evaluation_mode",
            sa.String(length=64),
            nullable=False,
            server_default="PLAN_VS_IMPLEMENTATION",
        ),
    )
    op.create_index(
        "ix_engineering_plan_evaluations_supersedes_evaluation_id",
        "engineering_plan_evaluations",
        ["supersedes_evaluation_id"],
    )
    op.create_index(
        "ix_engineering_plan_evaluations_evaluation_mode",
        "engineering_plan_evaluations",
        ["evaluation_mode"],
    )


def downgrade() -> None:
    op.drop_index("ix_engineering_plan_evaluations_evaluation_mode", table_name="engineering_plan_evaluations")
    op.drop_index("ix_engineering_plan_evaluations_supersedes_evaluation_id", table_name="engineering_plan_evaluations")
    op.drop_column("engineering_plan_evaluations", "evaluation_mode")
    op.drop_column("engineering_plan_evaluations", "supersedes_evaluation_id")
