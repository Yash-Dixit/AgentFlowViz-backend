"""Add media attachments and context profiles.

Revision ID: 0011_media_context_profiles
Revises: 0010_rss_response_grounding
Create Date: 2026-05-15
"""

import json

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "0011_media_context_profiles"
down_revision = "0010_rss_response_grounding"
branch_labels = None
depends_on = None


IMAGE_DOCUMENT_ROUTER_GRAPH = {
    "nodes": [
        "Media Intake",
        "Media Orchestrator",
        "Image Analyst",
        "Document Analyst",
        "Media Response Writer",
    ],
    "edges": [
        {"source": "Media Intake", "target": "Media Orchestrator", "condition": "always"},
        {"source": "Media Orchestrator", "target": "Image Analyst", "condition": "image_request"},
        {"source": "Media Orchestrator", "target": "Document Analyst", "condition": "document_request"},
        {"source": "Image Analyst", "target": "Media Response Writer", "condition": "image_done"},
        {"source": "Document Analyst", "target": "Media Response Writer", "condition": "document_done"},
        {
            "source": "Media Response Writer",
            "target": "Media Orchestrator",
            "condition": "needs_more_context",
            "feedback_loop": True,
        },
    ],
    "positions": {
        "Media Intake": {"x": 0, "y": 180},
        "Media Orchestrator": {"x": 260, "y": 180},
        "Image Analyst": {"x": 540, "y": 100},
        "Document Analyst": {"x": 540, "y": 260},
        "Media Response Writer": {"x": 840, "y": 180},
    },
}


MEDIA_AGENTS = [
    {
        "name": "Media Intake",
        "role": "Intake Agent",
        "system_prompt": "Normalize the media request and identify uploaded images, documents, and desired output.",
        "model": "qwen3.5:9b",
        "tools_json": '["attachment_context_reader"]',
        "channels_json": '["streamlit"]',
        "skills_json": '["intake", "channel_adapter", "attachment_context"]',
        "interaction_rules": "Keep the handoff short and do not answer the request yet.",
        "guardrails": "Be concise, factual, and avoid unsafe instructions.",
        "limits_json": '{"max_steps": 2, "max_tool_calls": 1, "max_output_tokens": 110, "context_window": "long"}',
    },
    {
        "name": "Media Orchestrator",
        "role": "Routing Agent",
        "system_prompt": (
            "Decide which specialist should inspect the upload. Choose exactly one route: "
            "image_request or document_request. Include a short reason."
        ),
        "model": "qwen3.5:9b",
        "tools_json": "[]",
        "channels_json": "[]",
        "skills_json": '["planning", "task_decomposition", "attachment_context"]',
        "interaction_rules": "Route to one media branch only, then let the response writer finish.",
        "guardrails": "Be concise, factual, and avoid unsafe instructions.",
        "limits_json": '{"max_steps": 2, "max_tool_calls": 0, "max_output_tokens": 120, "context_window": "long"}',
    },
    {
        "name": "Image Analyst",
        "role": "Image Analysis Agent",
        "system_prompt": (
            "Analyze image attachment context. Identify visible content, text, layout, uncertainties, "
            "and the most useful answer for the user's request."
        ),
        "model": "qwen3.5:9b",
        "tools_json": '["attachment_context_reader"]',
        "channels_json": "[]",
        "skills_json": '["vision", "tool_execution", "attachment_context"]',
        "interaction_rules": "Use stored attachment context and clearly state uncertainty.",
        "guardrails": "Be concise, factual, and avoid unsafe instructions.",
        "limits_json": '{"max_steps": 4, "max_tool_calls": 2, "max_output_tokens": 180, "context_window": "large"}',
    },
    {
        "name": "Document Analyst",
        "role": "Document Analysis Agent",
        "system_prompt": (
            "Analyze document attachment context. Use extracted text when available, summarize key points, "
            "and flag missing or unreadable content."
        ),
        "model": "qwen3.5:9b",
        "tools_json": '["attachment_context_reader"]',
        "channels_json": "[]",
        "skills_json": '["document_analysis", "tool_execution", "attachment_context"]',
        "interaction_rules": "Use extracted text first and mention when extraction was incomplete.",
        "guardrails": "Be concise, factual, and avoid unsafe instructions.",
        "limits_json": '{"max_steps": 4, "max_tool_calls": 2, "max_output_tokens": 180, "context_window": "large"}',
    },
    {
        "name": "Media Response Writer",
        "role": "Response Agent",
        "system_prompt": (
            "Write the final answer from the media analyst's output. Reference filenames when helpful, "
            "avoid internal workflow details, and finish naturally."
        ),
        "model": "gpt-oss:20b",
        "tools_json": "[]",
        "channels_json": '["streamlit"]',
        "skills_json": '["summarization", "final_response", "attachment_context"]',
        "interaction_rules": "Write for a human user and hide internal logs or routing decisions.",
        "guardrails": "Be concise, factual, and avoid unsafe instructions.",
        "limits_json": '{"max_steps": 4, "max_tool_calls": 1, "max_output_tokens": 420, "context_window": "large"}',
    },
]


def upgrade() -> None:
    op.create_table(
        "attachment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("source_channel", sa.String(), nullable=False),
        sa.Column("bucket", sa.String(), nullable=False),
        sa.Column("object_key", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(), nullable=False),
        sa.Column("caption", sa.String(), nullable=False),
        sa.Column("storage_status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attachment_run_id", "attachment", ["run_id"], unique=False)
    op.create_index("ix_attachment_source_channel", "attachment", ["source_channel"], unique=False)
    op.create_index("ix_attachment_checksum_sha256", "attachment", ["checksum_sha256"], unique=False)
    op.create_index("ix_attachment_storage_status", "attachment", ["storage_status"], unique=False)

    op.create_table(
        "attachmentcontext",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attachment_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("context_type", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("extracted_text", sa.String(), nullable=False),
        sa.Column("metadata_json", sa.String(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_attachmentcontext_attachment_id", "attachmentcontext", ["attachment_id"], unique=False)
    op.create_index("ix_attachmentcontext_run_id", "attachmentcontext", ["run_id"], unique=False)
    op.create_index("ix_attachmentcontext_context_type", "attachmentcontext", ["context_type"], unique=False)
    op.create_index(
        "ix_attachmentcontext_run_context_type",
        "attachmentcontext",
        ["run_id", "context_type"],
        unique=False,
    )
    op.execute(
        "CREATE INDEX ix_attachmentcontext_embedding_hnsw "
        "ON attachmentcontext USING hnsw (embedding vector_cosine_ops)"
    )

    connection = op.get_bind()
    for agent in MEDIA_AGENTS:
        connection.execute(
            sa.text(
                """
                INSERT INTO agent (
                    name, role, system_prompt, model, tools_json, channels_json, schedule,
                    skills_json, memory_enabled, interaction_rules, guardrails, limits_json,
                    created_at, updated_at
                )
                SELECT
                    CAST(:name AS VARCHAR),
                    CAST(:role AS VARCHAR),
                    CAST(:system_prompt AS VARCHAR),
                    CAST(:model AS VARCHAR),
                    CAST(:tools_json AS VARCHAR),
                    CAST(:channels_json AS VARCHAR),
                    'manual',
                    CAST(:skills_json AS VARCHAR),
                    TRUE,
                    CAST(:interaction_rules AS VARCHAR),
                    CAST(:guardrails AS VARCHAR),
                    CAST(:limits_json AS VARCHAR),
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                WHERE NOT EXISTS (
                    SELECT 1 FROM agent WHERE name = CAST(:name AS VARCHAR)
                )
                """
            ),
            agent,
        )

    connection.execute(sa.text("DELETE FROM workflowtemplate WHERE name = 'support_resolution_router'"))
    graph_json = json.dumps(IMAGE_DOCUMENT_ROUTER_GRAPH)
    existing_id = connection.execute(
        sa.text("SELECT id FROM workflowtemplate WHERE name = 'image_document_router'")
    ).scalar()
    if existing_id:
        connection.execute(
            sa.text(
                """
                UPDATE workflowtemplate
                SET description = :description,
                    graph_json = :graph_json,
                    is_active = TRUE
                WHERE id = :existing_id
                """
            ),
            {
                "existing_id": existing_id,
                "description": (
                    "Media-focused orchestrator that routes uploaded images or documents to the right analyst, "
                    "then drafts one final answer."
                ),
                "graph_json": graph_json,
            },
        )
    else:
        connection.execute(
            sa.text(
                """
                INSERT INTO workflowtemplate (name, description, graph_json, is_active, created_at)
                VALUES (
                    'image_document_router',
                    :description,
                    :graph_json,
                    TRUE,
                    CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "description": (
                    "Media-focused orchestrator that routes uploaded images or documents to the right analyst, "
                    "then drafts one final answer."
                ),
                "graph_json": graph_json,
            },
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_attachmentcontext_embedding_hnsw")
    op.drop_index("ix_attachmentcontext_run_context_type", table_name="attachmentcontext")
    op.drop_index("ix_attachmentcontext_context_type", table_name="attachmentcontext")
    op.drop_index("ix_attachmentcontext_run_id", table_name="attachmentcontext")
    op.drop_index("ix_attachmentcontext_attachment_id", table_name="attachmentcontext")
    op.drop_table("attachmentcontext")

    op.drop_index("ix_attachment_storage_status", table_name="attachment")
    op.drop_index("ix_attachment_checksum_sha256", table_name="attachment")
    op.drop_index("ix_attachment_source_channel", table_name="attachment")
    op.drop_index("ix_attachment_run_id", table_name="attachment")
    op.drop_table("attachment")
