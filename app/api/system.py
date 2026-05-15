from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from app.config import get_settings
from app.database import get_session
from app.models import Agent, Attachment, AttachmentContext, ConversationMemory, RSSItem, SemanticCache, WorkflowTemplate
from app.ollama_service import ollama_service
from app.runtime.rss_rag import rss_status
from app.runtime.rss_scheduler import rss_scheduler
from app.telegram_bot import telegram_bot_runner


router = APIRouter(tags=["system"])


@router.get("/")
def root() -> dict:
    settings = get_settings()
    return {"name": settings.app_name, "status": "running"}


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    settings = get_settings()
    agent_count = len(session.exec(select(Agent)).all())
    template_count = len(session.exec(select(WorkflowTemplate)).all())
    active_template_count = len(
        session.exec(select(WorkflowTemplate).where(WorkflowTemplate.is_active.is_(True))).all()
    )
    cache_count = len(session.exec(select(SemanticCache)).all())
    rss_item_count = session.exec(select(func.count(RSSItem.id))).one()
    cache_hit_count = session.exec(select(func.coalesce(func.sum(SemanticCache.hit_count), 0))).one()
    attachment_count = session.exec(select(func.count(Attachment.id))).one()
    attachment_context_count = session.exec(select(func.count(AttachmentContext.id))).one()
    saved_model_calls = session.exec(
        select(func.coalesce(func.sum(SemanticCache.hit_count * SemanticCache.model_calls), 0))
    ).one()
    saved_prompt_tokens = session.exec(
        select(func.coalesce(func.sum(SemanticCache.hit_count * SemanticCache.prompt_tokens), 0))
    ).one()
    saved_output_tokens = session.exec(
        select(func.coalesce(func.sum(SemanticCache.hit_count * SemanticCache.output_tokens), 0))
    ).one()
    memory_count = len(session.exec(select(ConversationMemory)).all())
    return {
        "status": "ok",
        "database": "ok",
        "agents": agent_count,
        "templates": template_count,
        "active_templates": active_template_count,
        "ollama": ollama_service.list_models(),
        "defaults": {
            "chat_model": settings.ollama_default_model,
            "fast_model": settings.ollama_fast_model,
            "embedding_model": settings.ollama_embedding_model,
        },
        "semantic_cache": {
            "enabled": settings.semantic_cache_enabled,
            "entries": cache_count,
            "similarity_threshold": settings.semantic_cache_similarity_threshold,
            "ttl_hours": settings.semantic_cache_ttl_hours,
            "embedding_dimensions": settings.semantic_cache_embedding_dimensions,
            "index": "hnsw_cosine",
            "hits": int(cache_hit_count or 0),
            "saved_model_calls": int(saved_model_calls or 0),
            "saved_prompt_tokens": int(saved_prompt_tokens or 0),
            "saved_output_tokens": int(saved_output_tokens or 0),
            "saved_total_tokens": int((saved_prompt_tokens or 0) + (saved_output_tokens or 0)),
        },
        "conversation_memory": {
            "entries": memory_count,
            "recent_runs": settings.conversation_memory_recent_runs,
            "summary_max_chars": settings.conversation_memory_summary_max_chars,
            "recent_max_chars": settings.conversation_memory_recent_max_chars,
        },
        "context_window": {
            "ollama_num_ctx": settings.ollama_context_window_tokens,
            "profiles": {
                "short": settings.ollama_context_window_short_tokens,
                "long": settings.ollama_context_window_long_tokens,
                "large": settings.ollama_context_window_large_tokens,
            },
            "overhead_tokens": settings.context_window_overhead_tokens,
            "reduction_order": list(settings.context_reduction_order),
        },
        "attachments": {
            "object_store": "minio",
            "enabled": settings.minio_enabled,
            "bucket": settings.minio_bucket,
            "items": int(attachment_count or 0),
            "contexts": int(attachment_context_count or 0),
            "context_index": "hnsw_cosine",
            "max_upload_mb": settings.attachment_max_upload_mb,
        },
        "final_output_guard": {
            "min_output_tokens": settings.final_response_min_output_tokens,
            "default_output_tokens": settings.final_response_default_output_tokens,
            "max_output_tokens": settings.final_response_max_output_tokens,
            "repair_tokens": settings.final_response_repair_tokens,
        },
        "local_energy_cost": {
            "inference_power_watts": settings.inference_power_watts,
            "electricity_cost_per_kwh_usd": settings.electricity_cost_per_kwh_usd,
            "basis": "Estimated from Ollama model_response total_duration_ms.",
        },
        "rss_rag": {
            **rss_status(),
            "item_count": int(rss_item_count or 0),
            "scheduler": rss_scheduler.status(),
        },
        "telegram": telegram_bot_runner.status(),
    }


@router.get("/models")
def models() -> dict:
    return ollama_service.list_models()
