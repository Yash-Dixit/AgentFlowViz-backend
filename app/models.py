from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Column, Index
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Agent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    role: str
    system_prompt: str
    model: str
    tools_json: str = "[]"
    channels_json: str = "[]"
    schedule: str = "manual"
    skills_json: str = "[]"
    memory_enabled: bool = True
    interaction_rules: str = "Ask for clarification when input is ambiguous."
    guardrails: str = "Be concise, factual, and avoid unsafe instructions."
    limits_json: str = '{"max_steps": 4, "max_tool_calls": 3}'
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WorkflowTemplate(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    description: str
    graph_json: str
    is_active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=utc_now)


class Run(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    template_name: str = Field(index=True)
    source_channel: str = Field(default="streamlit", index=True)
    telegram_user_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, index=True, nullable=True),
    )
    telegram_chat_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, index=True, nullable=True),
    )
    status: str = Field(default="queued", index=True)
    input_message: str
    final_output: str = ""
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    error: str = ""


class Message(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    sender: str
    recipient: str
    content: str
    message_type: str = "agent"
    created_at: datetime = Field(default_factory=utc_now)


class AgentMessageQueue(SQLModel, table=True):
    __table_args__ = (
        Index("ix_agentmessagequeue_run_recipient_status", "run_id", "recipient", "status"),
    )

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    sender: str = Field(index=True)
    recipient: str = Field(index=True)
    content: str
    condition: str = "always"
    status: str = Field(default="queued", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    delivered_at: datetime | None = None


class LogEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    level: str = "info"
    event: str
    details_json: str = "{}"
    created_at: datetime = Field(default_factory=utc_now)


class ToolCall(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    agent_name: str
    tool_name: str
    input_text: str
    output_text: str
    created_at: datetime = Field(default_factory=utc_now)


class Attachment(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    source_channel: str = Field(default="streamlit", index=True)
    bucket: str
    object_key: str
    filename: str
    mime_type: str
    file_size: int = Field(sa_column=Column(BigInteger, nullable=False))
    checksum_sha256: str = Field(index=True)
    caption: str = ""
    storage_status: str = Field(default="stored", index=True)
    created_at: datetime = Field(default_factory=utc_now)


class AttachmentContext(SQLModel, table=True):
    __table_args__ = (
        Index("ix_attachmentcontext_run_context_type", "run_id", "context_type"),
        Index(
            "ix_attachmentcontext_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    attachment_id: int = Field(index=True)
    run_id: int = Field(index=True)
    context_type: str = Field(index=True)
    model: str = ""
    summary: str
    extracted_text: str = ""
    metadata_json: str = "{}"
    embedding: list[float] | None = Field(default=None, sa_column=Column(Vector(1024), nullable=True))
    created_at: datetime = Field(default_factory=utc_now)


class SemanticCache(SQLModel, table=True):
    __table_args__ = (
        Index("ix_semanticcache_expires_at", "expires_at"),
        Index(
            "ix_semanticcache_embedding_hnsw",
            "prompt_embedding",
            postgresql_using="hnsw",
            postgresql_ops={"prompt_embedding": "vector_cosine_ops"},
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    template_name: str = Field(index=True)
    prompt: str
    prompt_embedding: list[float] = Field(sa_column=Column(Vector(1024), nullable=False))
    final_output: str
    model: str
    source_run_id: int | None = Field(default=None, index=True)
    model_calls: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    similarity_threshold: float = 0.92
    hit_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime


class RSSItem(SQLModel, table=True):
    __table_args__ = (
        Index("ix_rssitem_content_hash", "content_hash", unique=True),
        Index("ix_rssitem_source", "source"),
        Index("ix_rssitem_published_at", "published_at"),
        Index("ix_rssitem_fetched_at", "fetched_at"),
        Index("ix_rssitem_reported", "reported"),
        Index(
            "ix_rssitem_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    source: str
    feed_url: str
    title: str
    url: str
    summary: str = ""
    content_hash: str
    lead_score: int = 0
    tags_json: str = "[]"
    embedding: list[float] = Field(sa_column=Column(Vector(1024), nullable=False))
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=utc_now)
    reported: bool = False


class ConversationMemory(SQLModel, table=True):
    __table_args__ = (
        Index("ix_conversationmemory_channel_user", "channel", "user_id", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)
    channel: str = Field(index=True)
    user_id: str = Field(index=True)
    chat_id: str = ""
    summary: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
