"""Track semantic cache token savings.

Revision ID: 0005_cache_token_savings
Revises: 0004_optimize_default_agents
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_cache_token_savings"
down_revision = "0004_optimize_default_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("semanticcache", sa.Column("source_run_id", sa.Integer(), nullable=True))
    op.add_column("semanticcache", sa.Column("model_calls", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("semanticcache", sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("semanticcache", sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_semanticcache_source_run_id", "semanticcache", ["source_run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_semanticcache_source_run_id", table_name="semanticcache")
    op.drop_column("semanticcache", "output_tokens")
    op.drop_column("semanticcache", "prompt_tokens")
    op.drop_column("semanticcache", "model_calls")
    op.drop_column("semanticcache", "source_run_id")
