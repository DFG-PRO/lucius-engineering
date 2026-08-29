from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_model_gateway"
down_revision = "0004_memory_learning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_providers",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("supports_local", sa.Boolean(), nullable=False),
        sa.Column("default_timeout", sa.Integer(), nullable=True),
        sa.Column("secret_reference_name", sa.String(length=255), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_model_providers_status", "model_providers", ["status"])

    op.create_table(
        "model_profiles",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("privacy_class", sa.String(length=32), nullable=False),
        sa.Column("cost_class", sa.String(length=32), nullable=False),
        sa.Column("latency_class", sa.String(length=32), nullable=False),
        sa.Column("quality_score", sa.Integer(), nullable=False),
        sa.Column("supports_structured_output", sa.Boolean(), nullable=False),
        sa.Column("supports_tool_use", sa.Boolean(), nullable=False),
        sa.Column("supports_large_context", sa.Boolean(), nullable=False),
        sa.Column("supports_code", sa.Boolean(), nullable=False),
        sa.Column("supports_reasoning", sa.Boolean(), nullable=False),
        sa.Column("max_context_tokens", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("input_cost_per_million", sa.Float(), nullable=True),
        sa.Column("output_cost_per_million", sa.Float(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["model_providers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_name", name="uq_model_profile_provider_model"),
    )
    op.create_index("ix_model_profiles_provider_id", "model_profiles", ["provider_id"])
    op.create_index("ix_model_profiles_status", "model_profiles", ["status"])

    op.create_table(
        "model_executions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("task_id", sa.String(length=32), nullable=True),
        sa.Column("run_id", sa.String(length=32), nullable=True),
        sa.Column("provider_id", sa.String(length=32), nullable=True),
        sa.Column("provider", sa.String(length=255), nullable=True),
        sa.Column("model_profile_id", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("privacy_class", sa.String(length=32), nullable=False),
        sa.Column("routing_decision", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("cost_source", sa.String(length=32), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in [
        "request_id",
        "project_id",
        "task_id",
        "run_id",
        "provider_id",
        "model_profile_id",
        "status",
    ]:
        op.create_index(f"ix_model_executions_{column}", "model_executions", [column])


def downgrade() -> None:
    for column in [
        "request_id",
        "project_id",
        "task_id",
        "run_id",
        "provider_id",
        "model_profile_id",
        "status",
    ]:
        op.drop_index(f"ix_model_executions_{column}", table_name="model_executions")
    op.drop_table("model_executions")
    op.drop_index("ix_model_profiles_status", table_name="model_profiles")
    op.drop_index("ix_model_profiles_provider_id", table_name="model_profiles")
    op.drop_table("model_profiles")
    op.drop_index("ix_model_providers_status", table_name="model_providers")
    op.drop_table("model_providers")
