"""Initial schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("system_prompt", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("tools_json", sa.String(), nullable=False),
        sa.Column("channels_json", sa.String(), nullable=False),
        sa.Column("schedule", sa.String(), nullable=False),
        sa.Column("skills_json", sa.String(), nullable=False),
        sa.Column("memory_enabled", sa.Boolean(), nullable=False),
        sa.Column("interaction_rules", sa.String(), nullable=False),
        sa.Column("guardrails", sa.String(), nullable=False),
        sa.Column("limits_json", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_name", "agent", ["name"], unique=False)

    op.create_table(
        "workflowtemplate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("graph_json", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflowtemplate_name", "workflowtemplate", ["name"], unique=True)

    op.create_table(
        "run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("input_message", sa.String(), nullable=False),
        sa.Column("final_output", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_status", "run", ["status"], unique=False)
    op.create_index("ix_run_template_name", "run", ["template_name"], unique=False)

    op.create_table(
        "message",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("sender", sa.String(), nullable=False),
        sa.Column("recipient", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("message_type", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_message_run_id", "message", ["run_id"], unique=False)

    op.create_table(
        "logevent",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("details_json", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_logevent_run_id", "logevent", ["run_id"], unique=False)

    op.create_table(
        "toolcall",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("agent_name", sa.String(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("input_text", sa.String(), nullable=False),
        sa.Column("output_text", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_toolcall_run_id", "toolcall", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_toolcall_run_id", table_name="toolcall")
    op.drop_table("toolcall")

    op.drop_index("ix_logevent_run_id", table_name="logevent")
    op.drop_table("logevent")

    op.drop_index("ix_message_run_id", table_name="message")
    op.drop_table("message")

    op.drop_index("ix_run_template_name", table_name="run")
    op.drop_index("ix_run_status", table_name="run")
    op.drop_table("run")

    op.drop_index("ix_workflowtemplate_name", table_name="workflowtemplate")
    op.drop_table("workflowtemplate")

    op.drop_index("ix_agent_name", table_name="agent")
    op.drop_table("agent")
