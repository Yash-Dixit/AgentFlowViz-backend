"""Add persisted agent message queue.

Revision ID: 0008_agent_message_queue
Revises: 0007_workflow_active_flag
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_agent_message_queue"
down_revision = "0007_workflow_active_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agentmessagequeue",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("sender", sa.String(), nullable=False),
        sa.Column("recipient", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("condition", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agentmessagequeue_run_id", "agentmessagequeue", ["run_id"], unique=False)
    op.create_index("ix_agentmessagequeue_sender", "agentmessagequeue", ["sender"], unique=False)
    op.create_index("ix_agentmessagequeue_recipient", "agentmessagequeue", ["recipient"], unique=False)
    op.create_index("ix_agentmessagequeue_status", "agentmessagequeue", ["status"], unique=False)
    op.create_index(
        "ix_agentmessagequeue_run_recipient_status",
        "agentmessagequeue",
        ["run_id", "recipient", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agentmessagequeue_run_recipient_status", table_name="agentmessagequeue")
    op.drop_index("ix_agentmessagequeue_status", table_name="agentmessagequeue")
    op.drop_index("ix_agentmessagequeue_recipient", table_name="agentmessagequeue")
    op.drop_index("ix_agentmessagequeue_sender", table_name="agentmessagequeue")
    op.drop_index("ix_agentmessagequeue_run_id", table_name="agentmessagequeue")
    op.drop_table("agentmessagequeue")
