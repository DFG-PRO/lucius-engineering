from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_deterministic_acceptance_checks"
down_revision = "0011_extended_orchestration_plan_payload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "engineering_plans",
        sa.Column(
            "deterministic_acceptance_checks",
            sa.JSON(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("engineering_plans", "deterministic_acceptance_checks")
