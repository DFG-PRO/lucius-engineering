from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0008_pilot_evaluation_infrastructure"
down_revision = "0007_evaluation_harness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repository_state_observations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("repository_id", sa.String(length=32), nullable=True),
        sa.Column("repository_path", sa.Text(), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("head_commit", sa.String(length=64), nullable=False),
        sa.Column("remote", sa.Text(), nullable=True),
        sa.Column("classification", sa.String(length=64), nullable=False),
        sa.Column("tracked_modifications", sa.JSON(), nullable=False),
        sa.Column("staged_modifications", sa.JSON(), nullable=False),
        sa.Column("untracked_files", sa.JSON(), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["repository_id"], ["repository_registrations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_repository_state_observations_repository_id", "repository_state_observations", ["repository_id"])
    op.create_index("ix_repository_state_observations_classification", "repository_state_observations", ["classification"])

    op.create_table(
        "plan_freezes",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("plan_id", sa.String(length=32), nullable=False),
        sa.Column("task_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=False),
        sa.Column("repository_state_id", sa.String(length=32), nullable=True),
        sa.Column("repository_snapshot_ids", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("planning_mode", sa.String(length=64), nullable=False),
        sa.Column("evaluation_version", sa.String(length=32), nullable=False),
        sa.Column("plan_payload", sa.JSON(), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frozen_by", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["engineering_plans.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["repository_state_id"], ["repository_state_observations.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id"),
    )
    op.create_index("ix_plan_freezes_plan_id", "plan_freezes", ["plan_id"])
    op.create_index("ix_plan_freezes_task_id", "plan_freezes", ["task_id"])
    op.create_index("ix_plan_freezes_project_id", "plan_freezes", ["project_id"])
    op.create_index("ix_plan_freezes_repository_state_id", "plan_freezes", ["repository_state_id"])

    op.create_table(
        "engineering_plan_evaluations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("plan_freeze_id", sa.String(length=32), nullable=False),
        sa.Column("result", sa.String(length=64), nullable=False),
        sa.Column("aggregate_score", sa.Float(), nullable=True),
        sa.Column("dimensions", sa.JSON(), nullable=False),
        sa.Column("corrections", sa.JSON(), nullable=False),
        sa.Column("implementation_artifact", sa.JSON(), nullable=False),
        sa.Column("evaluator_version", sa.String(length=32), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_by", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["plan_freeze_id"], ["plan_freezes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_engineering_plan_evaluations_plan_freeze_id", "engineering_plan_evaluations", ["plan_freeze_id"])
    op.create_index("ix_engineering_plan_evaluations_result", "engineering_plan_evaluations", ["result"])

    op.create_table(
        "human_rubrics",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("plan_freeze_id", sa.String(length=32), nullable=True),
        sa.Column("pilot_record_id", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("evaluator", sa.String(length=255), nullable=True),
        sa.Column("scores", sa.JSON(), nullable=False),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_freeze_id"], ["plan_freezes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_human_rubrics_plan_freeze_id", "human_rubrics", ["plan_freeze_id"])
    op.create_index("ix_human_rubrics_pilot_record_id", "human_rubrics", ["pilot_record_id"])
    op.create_index("ix_human_rubrics_status", "human_rubrics", ["status"])

    op.create_table(
        "benchmark_results",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("suite_name", sa.String(length=255), nullable=False),
        sa.Column("suite_version", sa.Integer(), nullable=False),
        sa.Column("benchmark_version", sa.String(length=64), nullable=False),
        sa.Column("git_head", sa.String(length=64), nullable=True),
        sa.Column("target_dirty", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("deterministic_metrics", sa.JSON(), nullable=False),
        sa.Column("artifact_result_id", sa.String(length=32), nullable=True),
        sa.Column("evaluation_run_id", sa.String(length=32), nullable=True),
        sa.Column("environment_metadata", sa.JSON(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["evaluation_run_id"], ["evaluation_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_benchmark_results_suite_name", "benchmark_results", ["suite_name"])
    op.create_index("ix_benchmark_results_status", "benchmark_results", ["status"])
    op.create_index("ix_benchmark_results_artifact_result_id", "benchmark_results", ["artifact_result_id"])
    op.create_index("ix_benchmark_results_evaluation_run_id", "benchmark_results", ["evaluation_run_id"])

    op.create_table(
        "pilot_learning_candidates",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("pilot_record_id", sa.String(length=32), nullable=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pilot_learning_candidates_pilot_record_id", "pilot_learning_candidates", ["pilot_record_id"])
    op.create_index("ix_pilot_learning_candidates_status", "pilot_learning_candidates", ["status"])

    op.create_table(
        "pilot_evaluation_records",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("target_repository_id", sa.String(length=32), nullable=True),
        sa.Column("repository_snapshot_id", sa.String(length=32), nullable=True),
        sa.Column("repository_state_id", sa.String(length=32), nullable=True),
        sa.Column("task_id", sa.String(length=32), nullable=True),
        sa.Column("plan_id", sa.String(length=32), nullable=True),
        sa.Column("plan_freeze_id", sa.String(length=32), nullable=True),
        sa.Column("deterministic_evaluation_id", sa.String(length=32), nullable=True),
        sa.Column("human_rubric_id", sa.String(length=32), nullable=True),
        sa.Column("benchmark_before_id", sa.String(length=32), nullable=True),
        sa.Column("benchmark_after_id", sa.String(length=32), nullable=True),
        sa.Column("learning_candidate_ids", sa.JSON(), nullable=False),
        sa.Column("corrections", sa.JSON(), nullable=False),
        sa.Column("repository_integrity_result", sa.String(length=64), nullable=False),
        sa.Column("canonical_status", sa.String(length=64), nullable=False),
        sa.Column("autonomy_recommendation", sa.String(length=64), nullable=False),
        sa.Column("gate_result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["benchmark_after_id"], ["benchmark_results.id"]),
        sa.ForeignKeyConstraint(["benchmark_before_id"], ["benchmark_results.id"]),
        sa.ForeignKeyConstraint(["deterministic_evaluation_id"], ["engineering_plan_evaluations.id"]),
        sa.ForeignKeyConstraint(["human_rubric_id"], ["human_rubrics.id"]),
        sa.ForeignKeyConstraint(["plan_freeze_id"], ["plan_freezes.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["engineering_plans.id"]),
        sa.ForeignKeyConstraint(["repository_snapshot_id"], ["repository_snapshots.id"]),
        sa.ForeignKeyConstraint(["repository_state_id"], ["repository_state_observations.id"]),
        sa.ForeignKeyConstraint(["target_repository_id"], ["repository_registrations.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pilot_evaluation_records_target_repository_id", "pilot_evaluation_records", ["target_repository_id"])
    op.create_index("ix_pilot_evaluation_records_repository_snapshot_id", "pilot_evaluation_records", ["repository_snapshot_id"])
    op.create_index("ix_pilot_evaluation_records_repository_state_id", "pilot_evaluation_records", ["repository_state_id"])
    op.create_index("ix_pilot_evaluation_records_task_id", "pilot_evaluation_records", ["task_id"])
    op.create_index("ix_pilot_evaluation_records_plan_id", "pilot_evaluation_records", ["plan_id"])
    op.create_index("ix_pilot_evaluation_records_plan_freeze_id", "pilot_evaluation_records", ["plan_freeze_id"])
    op.create_index("ix_pilot_evaluation_records_deterministic_evaluation_id", "pilot_evaluation_records", ["deterministic_evaluation_id"])
    op.create_index("ix_pilot_evaluation_records_human_rubric_id", "pilot_evaluation_records", ["human_rubric_id"])
    op.create_index("ix_pilot_evaluation_records_benchmark_before_id", "pilot_evaluation_records", ["benchmark_before_id"])
    op.create_index("ix_pilot_evaluation_records_benchmark_after_id", "pilot_evaluation_records", ["benchmark_after_id"])
    op.create_index("ix_pilot_evaluation_records_canonical_status", "pilot_evaluation_records", ["canonical_status"])


def downgrade() -> None:
    op.drop_index("ix_pilot_evaluation_records_canonical_status", table_name="pilot_evaluation_records")
    op.drop_index("ix_pilot_evaluation_records_benchmark_after_id", table_name="pilot_evaluation_records")
    op.drop_index("ix_pilot_evaluation_records_benchmark_before_id", table_name="pilot_evaluation_records")
    op.drop_index("ix_pilot_evaluation_records_human_rubric_id", table_name="pilot_evaluation_records")
    op.drop_index("ix_pilot_evaluation_records_deterministic_evaluation_id", table_name="pilot_evaluation_records")
    op.drop_index("ix_pilot_evaluation_records_plan_freeze_id", table_name="pilot_evaluation_records")
    op.drop_index("ix_pilot_evaluation_records_plan_id", table_name="pilot_evaluation_records")
    op.drop_index("ix_pilot_evaluation_records_task_id", table_name="pilot_evaluation_records")
    op.drop_index("ix_pilot_evaluation_records_repository_state_id", table_name="pilot_evaluation_records")
    op.drop_index("ix_pilot_evaluation_records_repository_snapshot_id", table_name="pilot_evaluation_records")
    op.drop_index("ix_pilot_evaluation_records_target_repository_id", table_name="pilot_evaluation_records")
    op.drop_table("pilot_evaluation_records")
    op.drop_index("ix_pilot_learning_candidates_status", table_name="pilot_learning_candidates")
    op.drop_index("ix_pilot_learning_candidates_pilot_record_id", table_name="pilot_learning_candidates")
    op.drop_table("pilot_learning_candidates")
    op.drop_index("ix_benchmark_results_evaluation_run_id", table_name="benchmark_results")
    op.drop_index("ix_benchmark_results_artifact_result_id", table_name="benchmark_results")
    op.drop_index("ix_benchmark_results_status", table_name="benchmark_results")
    op.drop_index("ix_benchmark_results_suite_name", table_name="benchmark_results")
    op.drop_table("benchmark_results")
    op.drop_index("ix_human_rubrics_status", table_name="human_rubrics")
    op.drop_index("ix_human_rubrics_pilot_record_id", table_name="human_rubrics")
    op.drop_index("ix_human_rubrics_plan_freeze_id", table_name="human_rubrics")
    op.drop_table("human_rubrics")
    op.drop_index("ix_engineering_plan_evaluations_result", table_name="engineering_plan_evaluations")
    op.drop_index("ix_engineering_plan_evaluations_plan_freeze_id", table_name="engineering_plan_evaluations")
    op.drop_table("engineering_plan_evaluations")
    op.drop_index("ix_plan_freezes_repository_state_id", table_name="plan_freezes")
    op.drop_index("ix_plan_freezes_project_id", table_name="plan_freezes")
    op.drop_index("ix_plan_freezes_task_id", table_name="plan_freezes")
    op.drop_index("ix_plan_freezes_plan_id", table_name="plan_freezes")
    op.drop_table("plan_freezes")
    op.drop_index("ix_repository_state_observations_classification", table_name="repository_state_observations")
    op.drop_index("ix_repository_state_observations_repository_id", table_name="repository_state_observations")
    op.drop_table("repository_state_observations")
