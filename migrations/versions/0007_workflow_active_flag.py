"""Add workflow active flag.

Revision ID: 0007_workflow_active_flag
Revises: 0006_two_orchestrated_workflows
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_workflow_active_flag"
down_revision = "0006_two_orchestrated_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflowtemplate",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_workflowtemplate_is_active", "workflowtemplate", ["is_active"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_workflowtemplate_is_active", table_name="workflowtemplate")
    op.drop_column("workflowtemplate", "is_active")
