"""Add Telegram conversation memory.

Revision ID: 0003_conversation_memory
Revises: 0002_semantic_cache
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_conversation_memory"
down_revision = "0002_semantic_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "run",
        sa.Column("source_channel", sa.String(), nullable=False, server_default="streamlit"),
    )
    op.add_column("run", sa.Column("telegram_user_id", sa.BigInteger(), nullable=True))
    op.add_column("run", sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_run_source_channel", "run", ["source_channel"], unique=False)
    op.create_index("ix_run_telegram_user_id", "run", ["telegram_user_id"], unique=False)
    op.create_index("ix_run_telegram_chat_id", "run", ["telegram_chat_id"], unique=False)

    op.create_table(
        "conversationmemory",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("chat_id", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversationmemory_channel", "conversationmemory", ["channel"], unique=False)
    op.create_index("ix_conversationmemory_user_id", "conversationmemory", ["user_id"], unique=False)
    op.create_index(
        "ix_conversationmemory_channel_user",
        "conversationmemory",
        ["channel", "user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_conversationmemory_channel_user", table_name="conversationmemory")
    op.drop_index("ix_conversationmemory_user_id", table_name="conversationmemory")
    op.drop_index("ix_conversationmemory_channel", table_name="conversationmemory")
    op.drop_table("conversationmemory")

    op.drop_index("ix_run_telegram_chat_id", table_name="run")
    op.drop_index("ix_run_telegram_user_id", table_name="run")
    op.drop_index("ix_run_source_channel", table_name="run")
    op.drop_column("run", "telegram_chat_id")
    op.drop_column("run", "telegram_user_id")
    op.drop_column("run", "source_channel")
