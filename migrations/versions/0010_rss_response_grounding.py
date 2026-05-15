"""Ground final RSS responses on retrieved feed items.

Revision ID: 0010_rss_response_grounding
Revises: 0009_rss_rag
Create Date: 2026-05-14
"""

from alembic import op


revision = "0010_rss_response_grounding"
down_revision = "0009_rss_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE agent
        SET system_prompt = 'Write the final response using the chosen specialist''s output. If RSS RAG context is available, use only the listed titles, sources, dates, and URLs. Be concise, clear, and finish naturally.',
            tools_json = '["report_writer", "rss_rag_retriever"]',
            limits_json = '{"max_steps": 4, "max_tool_calls": 2, "max_output_tokens": 420}'
        WHERE name = 'Response Synthesizer'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE agent
        SET system_prompt = 'Write the final response using the chosen specialist''s output. Be concise, clear, and finish naturally.',
            tools_json = '["report_writer"]',
            limits_json = '{"max_steps": 4, "max_tool_calls": 1, "max_output_tokens": 420}'
        WHERE name = 'Response Synthesizer'
        """
    )
