"""Add RSS RAG item index.

Revision ID: 0009_rss_rag
Revises: 0008_agent_message_queue
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "0009_rss_rag"
down_revision = "0008_agent_message_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rssitem",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("feed_url", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("lead_score", sa.Integer(), nullable=False),
        sa.Column("tags_json", sa.String(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("reported", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rssitem_source", "rssitem", ["source"], unique=False)
    op.create_index("ix_rssitem_published_at", "rssitem", ["published_at"], unique=False)
    op.create_index("ix_rssitem_fetched_at", "rssitem", ["fetched_at"], unique=False)
    op.create_index("ix_rssitem_reported", "rssitem", ["reported"], unique=False)
    op.create_index("ix_rssitem_content_hash", "rssitem", ["content_hash"], unique=True)
    op.create_index(
        "ix_rssitem_embedding_hnsw",
        "rssitem",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.execute(
        """
        UPDATE agent
        SET tools_json = '["local_research", "rss_rag_retriever"]',
            skills_json = '["research", "tool_execution", "rss_rag"]',
            limits_json = '{"max_steps": 4, "max_tool_calls": 2, "max_output_tokens": 140}'
        WHERE name = 'Research Specialist'
        """
    )


def downgrade() -> None:
    op.drop_index("ix_rssitem_embedding_hnsw", table_name="rssitem")
    op.drop_index("ix_rssitem_content_hash", table_name="rssitem")
    op.drop_index("ix_rssitem_reported", table_name="rssitem")
    op.drop_index("ix_rssitem_fetched_at", table_name="rssitem")
    op.drop_index("ix_rssitem_published_at", table_name="rssitem")
    op.drop_index("ix_rssitem_source", table_name="rssitem")
    op.drop_table("rssitem")
