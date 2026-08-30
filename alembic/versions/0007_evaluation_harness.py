from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007_evaluation_harness"
down_revision = "0006_engineering_planner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_suites",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("case_refs", sa.JSON(), nullable=False),
        sa.Column("scoring_policy", sa.JSON(), nullable=False),
        sa.Column("hard_gate_policy", sa.JSON(), nullable=False),
        sa.Column("baseline_run_id", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_evaluation_suite_name_version"),
    )
    op.create_index("ix_evaluation_suites_name", "evaluation_suites", ["name"])
    op.create_index("ix_evaluation_suites_baseline_run_id", "evaluation_suites", ["baseline_run_id"])

    op.create_table(
        "evaluation_cases",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("suite_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("difficulty", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["suite_id"], ["evaluation_suites.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("suite_id", "name", "version", name="uq_evaluation_case_suite_name_version"),
    )
    op.create_index("ix_evaluation_cases_suite_id", "evaluation_cases", ["suite_id"])
    op.create_index("ix_evaluation_cases_name", "evaluation_cases", ["name"])
    op.create_index("ix_evaluation_cases_target_type", "evaluation_cases", ["target_type"])

    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("suite_id", sa.String(length=32), nullable=False),
        sa.Column("suite_version", sa.Integer(), nullable=False),
        sa.Column("target_version", sa.String(length=64), nullable=True),
        sa.Column("target_commit_sha", sa.String(length=64), nullable=True),
        sa.Column("target_dirty", sa.Boolean(), nullable=False),
        sa.Column("planner_version", sa.String(length=32), nullable=True),
        sa.Column("model_provider", sa.String(length=255), nullable=True),
        sa.Column("model_profile", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("aggregate_score", sa.Float(), nullable=True),
        sa.Column("hard_gate_status", sa.String(length=32), nullable=False),
        sa.Column("release_decision", sa.String(length=64), nullable=True),
        sa.Column("baseline_run_id", sa.String(length=32), nullable=True),
        sa.Column("environment_metadata", sa.JSON(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("regressions", sa.JSON(), nullable=False),
        sa.Column("machine_report", sa.JSON(), nullable=False),
        sa.Column("markdown_report", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["suite_id"], ["evaluation_suites.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evaluation_runs_suite_id", "evaluation_runs", ["suite_id"])
    op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"])
    op.create_index("ix_evaluation_runs_baseline_run_id", "evaluation_runs", ["baseline_run_id"])

    op.create_table(
        "evaluation_case_results",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.String(length=32), nullable=False),
        sa.Column("case_id", sa.String(length=32), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metric_results", sa.JSON(), nullable=False),
        sa.Column("weighted_score", sa.Float(), nullable=False),
        sa.Column("hard_gate_passed", sa.Boolean(), nullable=False),
        sa.Column("hard_gate_failures", sa.JSON(), nullable=False),
        sa.Column("observations", sa.JSON(), nullable=False),
        sa.Column("regressions", sa.JSON(), nullable=False),
        sa.Column("actual_artifact_reference", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["evaluation_cases.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["evaluation_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evaluation_case_results_run_id", "evaluation_case_results", ["run_id"])
    op.create_index("ix_evaluation_case_results_case_id", "evaluation_case_results", ["case_id"])
    op.create_index("ix_evaluation_case_results_status", "evaluation_case_results", ["status"])


def downgrade() -> None:
    op.drop_index("ix_evaluation_case_results_status", table_name="evaluation_case_results")
    op.drop_index("ix_evaluation_case_results_case_id", table_name="evaluation_case_results")
    op.drop_index("ix_evaluation_case_results_run_id", table_name="evaluation_case_results")
    op.drop_table("evaluation_case_results")
    op.drop_index("ix_evaluation_runs_baseline_run_id", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_status", table_name="evaluation_runs")
    op.drop_index("ix_evaluation_runs_suite_id", table_name="evaluation_runs")
    op.drop_table("evaluation_runs")
    op.drop_index("ix_evaluation_cases_target_type", table_name="evaluation_cases")
    op.drop_index("ix_evaluation_cases_name", table_name="evaluation_cases")
    op.drop_index("ix_evaluation_cases_suite_id", table_name="evaluation_cases")
    op.drop_table("evaluation_cases")
    op.drop_index("ix_evaluation_suites_baseline_run_id", table_name="evaluation_suites")
    op.drop_index("ix_evaluation_suites_name", table_name="evaluation_suites")
    op.drop_table("evaluation_suites")
