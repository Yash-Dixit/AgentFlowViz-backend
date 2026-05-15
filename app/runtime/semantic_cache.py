from dataclasses import dataclass
from datetime import timedelta
import re

from sqlalchemy import delete, text
from sqlmodel import Session, select

from app.config import get_settings
from app.database import engine
from app.models import LogEvent, SemanticCache, utc_now
from app.ollama_service import ollama_service
from app.runtime.persistence import add_log, add_message
from app.runtime.utils import loads_json


@dataclass
class CacheMatch:
    cache_id: int
    prompt: str
    final_output: str
    model: str
    similarity: float
    source_run_id: int | None
    saved_model_calls: int
    saved_prompt_tokens: int
    saved_output_tokens: int


def is_cacheable_prompt(user_message: str) -> bool:
    lowered = user_message.lower()
    volatile_terms = [
        "today",
        "latest",
        "current",
        "right now",
        "yesterday",
        "tomorrow",
        "weather",
        "price",
        "stock",
        "news",
        "status",
    ]
    if re.search(r"\d", user_message):
        return False
    return not any(term in lowered for term in volatile_terms)


def vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"


def find_cached_output(run_id: int, template_name: str, user_message: str) -> CacheMatch | None:
    settings = get_settings()
    if not settings.semantic_cache_enabled:
        add_log(run_id, "semantic_cache_skipped", {"reason": "disabled"})
        return None
    if not is_cacheable_prompt(user_message):
        add_log(run_id, "semantic_cache_skipped", {"reason": "uncacheable_prompt"})
        return None

    try:
        embedding = ollama_service.embed_text(user_message)
    except Exception as exc:
        add_log(run_id, "semantic_cache_error", {"stage": "embed_lookup", "error": str(exc)}, level="warning")
        return None

    if not embedding:
        add_log(run_id, "semantic_cache_skipped", {"reason": "empty_embedding"})
        return None

    now = utc_now()
    with engine.begin() as connection:
        row = connection.execute(
            text(
                """
                SELECT
                    id,
                    prompt,
                    final_output,
                    model,
                    source_run_id,
                    model_calls,
                    prompt_tokens,
                    output_tokens,
                    1 - (prompt_embedding <=> CAST(:embedding AS vector)) AS similarity
                FROM semanticcache
                WHERE template_name = :template_name
                  AND expires_at > :now
                ORDER BY prompt_embedding <=> CAST(:embedding AS vector)
                LIMIT 1
                """
            ),
            {
                "embedding": vector_literal(embedding),
                "template_name": template_name,
                "now": now,
            },
        ).mappings().first()

    if not row or float(row["similarity"]) < settings.semantic_cache_similarity_threshold:
        add_log(
            run_id,
            "semantic_cache_miss",
            {
                "template_name": template_name,
                "best_similarity": round(float(row["similarity"]), 4) if row else 0.0,
                "threshold": settings.semantic_cache_similarity_threshold,
            },
        )
        return None

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE semanticcache SET hit_count = hit_count + 1 WHERE id = :cache_id"),
            {"cache_id": row["id"]},
        )

    match = CacheMatch(
        cache_id=int(row["id"]),
        prompt=str(row["prompt"]),
        final_output=str(row["final_output"]),
        model=str(row["model"]),
        similarity=float(row["similarity"]),
        source_run_id=int(row["source_run_id"]) if row["source_run_id"] is not None else None,
        saved_model_calls=int(row["model_calls"] or 0),
        saved_prompt_tokens=int(row["prompt_tokens"] or 0),
        saved_output_tokens=int(row["output_tokens"] or 0),
    )
    add_log(
        run_id,
        "semantic_cache_hit",
        {
            "cache_id": match.cache_id,
            "template_name": template_name,
            "similarity": round(match.similarity, 4),
            "threshold": settings.semantic_cache_similarity_threshold,
            "source_run_id": match.source_run_id,
            "saved_model_calls": match.saved_model_calls,
            "saved_prompt_tokens": match.saved_prompt_tokens,
            "saved_output_tokens": match.saved_output_tokens,
            "saved_total_tokens": match.saved_prompt_tokens + match.saved_output_tokens,
        },
    )
    return match


def source_run_token_footprint(run_id: int) -> dict[str, int]:
    with Session(engine) as session:
        logs = session.exec(select(LogEvent).where(LogEvent.run_id == run_id)).all()

    model_calls = 0
    prompt_tokens = 0
    output_tokens = 0
    for log in logs:
        if log.event != "model_response":
            continue
        details = loads_json(log.details_json, {})
        model_calls += 1
        prompt_tokens += int(details.get("prompt_tokens") or 0)
        output_tokens += int(details.get("output_tokens") or 0)

    return {
        "model_calls": model_calls,
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
    }


def store_cached_output(run_id: int, template_name: str, user_message: str, final_output: str) -> None:
    settings = get_settings()
    if not settings.semantic_cache_enabled or not final_output.strip() or not is_cacheable_prompt(user_message):
        return

    try:
        embedding = ollama_service.embed_text(user_message)
    except Exception as exc:
        add_log(run_id, "semantic_cache_error", {"stage": "embed_store", "error": str(exc)}, level="warning")
        return

    if not embedding:
        return

    now = utc_now()
    expires_at = now + timedelta(hours=settings.semantic_cache_ttl_hours)
    footprint = source_run_token_footprint(run_id)
    with Session(engine) as session:
        session.exec(delete(SemanticCache).where(SemanticCache.expires_at <= now))
        session.add(
            SemanticCache(
                template_name=template_name,
                prompt=user_message,
                prompt_embedding=embedding,
                final_output=final_output,
                model=settings.ollama_default_model,
                source_run_id=run_id,
                model_calls=footprint["model_calls"],
                prompt_tokens=footprint["prompt_tokens"],
                output_tokens=footprint["output_tokens"],
                similarity_threshold=settings.semantic_cache_similarity_threshold,
                expires_at=expires_at,
            )
        )
        session.commit()

    add_log(
        run_id,
        "semantic_cache_store",
        {
            "template_name": template_name,
            "ttl_hours": settings.semantic_cache_ttl_hours,
            "embedding_model": settings.ollama_embedding_model,
            "source_run_id": run_id,
            "model_calls": footprint["model_calls"],
            "prompt_tokens": footprint["prompt_tokens"],
            "output_tokens": footprint["output_tokens"],
            "total_tokens": footprint["prompt_tokens"] + footprint["output_tokens"],
        },
    )


def clear_semantic_cache(template_name: str | None = None) -> int:
    with Session(engine) as session:
        statement = delete(SemanticCache)
        if template_name:
            statement = statement.where(SemanticCache.template_name == template_name)
        result = session.exec(statement)
        session.commit()
        return int(result.rowcount or 0)


def complete_run_from_cache(run_id: int, user_message: str, match: CacheMatch) -> None:
    add_message(run_id, "Telegram User", "Semantic Cache", user_message, "external")
    add_message(run_id, "Semantic Cache", "Telegram User", match.final_output, "cache")
