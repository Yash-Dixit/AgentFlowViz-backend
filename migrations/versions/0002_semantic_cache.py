"""Add semantic cache.

Revision ID: 0002_semantic_cache
Revises: 0001_initial_schema
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "0002_semantic_cache"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "semanticcache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_name", sa.String(), nullable=False),
        sa.Column("prompt", sa.String(), nullable=False),
        sa.Column("prompt_embedding", Vector(1024), nullable=False),
        sa.Column("final_output", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("similarity_threshold", sa.Float(), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_semanticcache_template_name", "semanticcache", ["template_name"], unique=False)
    op.create_index("ix_semanticcache_expires_at", "semanticcache", ["expires_at"], unique=False)
    op.execute(
        "CREATE INDEX ix_semanticcache_embedding_hnsw "
        "ON semanticcache USING hnsw (prompt_embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_semanticcache_embedding_hnsw")
    op.drop_index("ix_semanticcache_expires_at", table_name="semanticcache")
    op.drop_index("ix_semanticcache_template_name", table_name="semanticcache")
    op.drop_table("semanticcache")
