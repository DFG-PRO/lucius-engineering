from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0010_persistent_workflows"
down_revision = "0009_planning_evaluation_modes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persistent_workflows",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("repository_id", sa.String(length=32), nullable=True),
        sa.Column("repository_snapshot_id", sa.String(length=32), nullable=True),
        sa.Column("task_id", sa.String(length=32), nullable=True),
        sa.Column("plan_id", sa.String(length=32), nullable=True),
        sa.Column("plan_freeze_id", sa.String(length=32), nullable=True),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("expected_main_head", sa.String(length=64), nullable=False),
        sa.Column("isolated_branch", sa.String(length=255), nullable=False),
        sa.Column("worktree_path", sa.Text(), nullable=False),
        sa.Column("workflow_state", sa.String(length=64), nullable=False),
        sa.Column("authority_tier", sa.String(length=64), nullable=False),
        sa.Column("task_backlog", sa.JSON(), nullable=False),
        sa.Column("dependency_graph", sa.JSON(), nullable=False),
        sa.Column("active_task_id", sa.String(length=32), nullable=True),
        sa.Column("completed_task_ids", sa.JSON(), nullable=False),
        sa.Column("pending_task_ids", sa.JSON(), nullable=False),
        sa.Column("decisions", sa.JSON(), nullable=False),
        sa.Column("deviations", sa.JSON(), nullable=False),
        sa.Column("repair_counters", sa.JSON(), nullable=False),
        sa.Column("targeted_test_evidence", sa.JSON(), nullable=False),
        sa.Column("full_test_status", sa.JSON(), nullable=False),
        sa.Column("checkpoint_history", sa.JSON(), nullable=False),
        sa.Column("pending_human_approvals", sa.JSON(), nullable=False),
        sa.Column("resume_requirements", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_freeze_id"], ["plan_freezes.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["engineering_plans.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["repository_id"], ["repository_registrations.id"]),
        sa.ForeignKeyConstraint(["repository_snapshot_id"], ["repository_snapshots.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_persistent_workflows_project_id", "persistent_workflows", ["project_id"])
    op.create_index("ix_persistent_workflows_repository_id", "persistent_workflows", ["repository_id"])
    op.create_index("ix_persistent_workflows_repository_snapshot_id", "persistent_workflows", ["repository_snapshot_id"])
    op.create_index("ix_persistent_workflows_task_id", "persistent_workflows", ["task_id"])
    op.create_index("ix_persistent_workflows_plan_id", "persistent_workflows", ["plan_id"])
    op.create_index("ix_persistent_workflows_plan_freeze_id", "persistent_workflows", ["plan_freeze_id"])
    op.create_index("ix_persistent_workflows_workflow_state", "persistent_workflows", ["workflow_state"])
    op.create_index("ix_persistent_workflows_active_task_id", "persistent_workflows", ["active_task_id"])

    op.create_table(
        "persistent_workflow_checkpoints",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("checkpoint_state", sa.String(length=64), nullable=False),
        sa.Column("implementation_head", sa.String(length=64), nullable=False),
        sa.Column("expected_main_head", sa.String(length=64), nullable=False),
        sa.Column("branch", sa.String(length=255), nullable=False),
        sa.Column("worktree_path", sa.Text(), nullable=False),
        sa.Column("active_task_id", sa.String(length=32), nullable=True),
        sa.Column("completed_task_ids", sa.JSON(), nullable=False),
        sa.Column("pending_task_ids", sa.JSON(), nullable=False),
        sa.Column("dependency_graph", sa.JSON(), nullable=False),
        sa.Column("decisions", sa.JSON(), nullable=False),
        sa.Column("deviations", sa.JSON(), nullable=False),
        sa.Column("repair_counters", sa.JSON(), nullable=False),
        sa.Column("latest_test_results", sa.JSON(), nullable=False),
        sa.Column("known_warnings", sa.JSON(), nullable=False),
        sa.Column("authority_tier", sa.String(length=64), nullable=False),
        sa.Column("pending_human_approvals", sa.JSON(), nullable=False),
        sa.Column("resume_conditions", sa.JSON(), nullable=False),
        sa.Column("recommended_next_action", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["persistent_workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_persistent_workflow_checkpoints_workflow_id", "persistent_workflow_checkpoints", ["workflow_id"])
    op.create_index("ix_persistent_workflow_checkpoints_checkpoint_state", "persistent_workflow_checkpoints", ["checkpoint_state"])
    op.create_index("ix_persistent_workflow_checkpoints_active_task_id", "persistent_workflow_checkpoints", ["active_task_id"])

    op.create_table(
        "resume_validations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("workflow_id", sa.String(length=32), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=32), nullable=False),
        sa.Column("result", sa.String(length=64), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("next_eligible_task_id", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["checkpoint_id"], ["persistent_workflow_checkpoints.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["persistent_workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resume_validations_workflow_id", "resume_validations", ["workflow_id"])
    op.create_index("ix_resume_validations_checkpoint_id", "resume_validations", ["checkpoint_id"])
    op.create_index("ix_resume_validations_result", "resume_validations", ["result"])
    op.create_index("ix_resume_validations_next_eligible_task_id", "resume_validations", ["next_eligible_task_id"])


def downgrade() -> None:
    op.drop_index("ix_resume_validations_next_eligible_task_id", table_name="resume_validations")
    op.drop_index("ix_resume_validations_result", table_name="resume_validations")
    op.drop_index("ix_resume_validations_checkpoint_id", table_name="resume_validations")
    op.drop_index("ix_resume_validations_workflow_id", table_name="resume_validations")
    op.drop_table("resume_validations")
    op.drop_index("ix_persistent_workflow_checkpoints_active_task_id", table_name="persistent_workflow_checkpoints")
    op.drop_index("ix_persistent_workflow_checkpoints_checkpoint_state", table_name="persistent_workflow_checkpoints")
    op.drop_index("ix_persistent_workflow_checkpoints_workflow_id", table_name="persistent_workflow_checkpoints")
    op.drop_table("persistent_workflow_checkpoints")
    op.drop_index("ix_persistent_workflows_active_task_id", table_name="persistent_workflows")
    op.drop_index("ix_persistent_workflows_workflow_state", table_name="persistent_workflows")
    op.drop_index("ix_persistent_workflows_plan_freeze_id", table_name="persistent_workflows")
    op.drop_index("ix_persistent_workflows_plan_id", table_name="persistent_workflows")
    op.drop_index("ix_persistent_workflows_task_id", table_name="persistent_workflows")
    op.drop_index("ix_persistent_workflows_repository_snapshot_id", table_name="persistent_workflows")
    op.drop_index("ix_persistent_workflows_repository_id", table_name="persistent_workflows")
    op.drop_index("ix_persistent_workflows_project_id", table_name="persistent_workflows")
    op.drop_table("persistent_workflows")
