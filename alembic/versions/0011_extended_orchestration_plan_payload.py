from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0011_extended_orchestration_plan_payload"
down_revision = "0010_persistent_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "engineering_plans",
        sa.Column(
            "orchestration_contract_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "engineering_plans",
        sa.Column("orchestration_contract", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "engineering_plans",
        sa.Column("adversarial_probes", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("engineering_plans", "adversarial_probes")
    op.drop_column("engineering_plans", "orchestration_contract")
    op.drop_column("engineering_plans", "orchestration_contract_required")
