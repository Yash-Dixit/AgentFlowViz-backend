from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET

import httpx
from sqlalchemy import func, text
from sqlmodel import Session, select

from app.config import get_settings
from app.database import engine
from app.models import RSSItem, Run, utc_now
from app.ollama_service import ollama_service
from app.runtime.persistence import add_log
from app.runtime.semantic_cache import vector_literal


RSS_INGESTION_TEMPLATE = "rss_rag_ingestion"
NEWS_INTENT_TERMS = ["news", "latest", "top 3", "top three", "update", "rss", "lead", "leads"]
LEAD_KEYWORDS = {
    "hiring": 3,
    "support": 3,
    "customer": 3,
    "sales": 2,
    "automation": 3,
    "workflow": 2,
    "agent": 3,
    "ai": 2,
    "startup": 2,
    "launch": 2,
    "funding": 2,
    "raise": 2,
    "series": 2,
    "inbox": 2,
    "operations": 2,
}


@dataclass(frozen=True)
class ParsedRSSItem:
    source: str
    feed_url: str
    title: str
    url: str
    summary: str
    published_at: datetime | None


@dataclass(frozen=True)
class IngestionResult:
    feeds_scanned: int
    items_scanned: int
    new_items: int
    skipped_existing: int
    errors: list[str]


def should_use_rss_rag(user_message: str) -> bool:
    lowered = user_message.lower()
    return any(term in lowered for term in NEWS_INTENT_TERMS)


def fetch_and_store_rss_items(run_id: int | None = None) -> IngestionResult:
    settings = get_settings()
    feed_urls = settings.rss_feed_url_list
    errors: list[str] = []
    items_scanned = 0
    new_items = 0
    skipped_existing = 0

    for feed_url in feed_urls:
        if new_items >= settings.rss_max_new_items_per_run:
            break

        try:
            response = httpx.get(
                feed_url,
                headers={"User-Agent": "AgentFlowViz RSS RAG/1.0"},
                timeout=12,
                follow_redirects=True,
                trust_env=False,
            )
            response.raise_for_status()
            parsed_items = parse_rss_items(response.text, feed_url)[: settings.rss_max_items_per_feed]
        except Exception as exc:
            errors.append(f"{feed_url}: {exc}")
            continue

        for item in parsed_items:
            if new_items >= settings.rss_max_new_items_per_run:
                break

            items_scanned += 1
            stored = store_rss_item(item, max_chars=settings.rss_item_max_chars)
            if stored:
                new_items += 1
            else:
                skipped_existing += 1

    result = IngestionResult(
        feeds_scanned=len(feed_urls),
        items_scanned=items_scanned,
        new_items=new_items,
        skipped_existing=skipped_existing,
        errors=errors,
    )
    if run_id:
        add_log(
            run_id,
            "rss_ingestion_finished",
            {
                "feeds_scanned": result.feeds_scanned,
                "items_scanned": result.items_scanned,
                "new_items": result.new_items,
                "skipped_existing": result.skipped_existing,
                "errors": result.errors,
            },
            level="warning" if result.errors else "info",
        )
    return result


def parse_rss_items(xml_text: str, feed_url: str) -> list[ParsedRSSItem]:
    root = ET.fromstring(xml_text)
    root_name = _local_name(root.tag)
    if root_name == "rss":
        return _parse_rss(root, feed_url)
    if root_name == "feed":
        return _parse_atom(root, feed_url)
    return []


def store_rss_item(item: ParsedRSSItem, *, max_chars: int) -> bool:
    title = _compact_text(item.title, 240)
    summary = _compact_text(item.summary, max_chars)
    url = item.url.strip() or item.feed_url
    content_hash = rss_content_hash(url, title)

    with Session(engine) as session:
        exists = session.exec(select(RSSItem.id).where(RSSItem.content_hash == content_hash)).first()
        if exists:
            return False

    embedding_text = f"{title}\n{summary}"
    embedding = ollama_service.embed_text(embedding_text)
    if not embedding:
        return False

    score, tags = score_lead_signal(title, summary)
    with Session(engine) as session:
        session.add(
            RSSItem(
                source=item.source,
                feed_url=item.feed_url,
                title=title,
                url=url,
                summary=summary,
                content_hash=content_hash,
                lead_score=score,
                tags_json=json.dumps(tags),
                embedding=embedding,
                published_at=item.published_at,
            )
        )
        session.commit()
    return True


def retrieve_rss_context(run_id: int, user_message: str) -> str:
    if not should_use_rss_rag(user_message):
        return "RSS RAG skipped because the request did not ask for latest news, RSS updates, or leads."

    settings = get_settings()
    try:
        query_embedding = ollama_service.embed_text(user_message)
    except Exception as exc:
        add_log(run_id, "rss_rag_error", {"stage": "embed_query", "error": str(exc)}, level="warning")
        return "RSS RAG could not embed the query."

    if not query_embedding:
        return "RSS RAG could not produce a query embedding."

    rows = _query_rss_items(
        query_embedding,
        candidates=settings.rss_retrieval_candidates,
        freshness_hours=settings.rss_freshness_hours,
    )
    expanded = False
    if len(rows) < settings.rss_retrieval_limit:
        rows = _query_rss_items(query_embedding, candidates=settings.rss_retrieval_candidates, freshness_hours=None)
        expanded = True

    ranked = rank_rss_rows(rows, user_message)[: settings.rss_retrieval_limit]
    context = render_rss_context(ranked, expanded=expanded)
    if len(context) > settings.rss_context_max_chars:
        context = context[: settings.rss_context_max_chars].rstrip() + "\n...[RSS context capped]"

    add_log(
        run_id,
        "rss_rag_retrieval",
        {
            "query": user_message[:160],
            "results": len(ranked),
            "freshness_hours": settings.rss_freshness_hours,
            "expanded_window": expanded,
            "candidate_count": len(rows),
        },
    )
    return context


def rank_rss_rows(rows: list[dict], user_message: str) -> list[dict]:
    wants_leads = "lead" in user_message.lower() or "outreach" in user_message.lower()
    now = utc_now()
    ranked = []
    for row in rows:
        published_at = _as_utc(row.get("published_at") or row.get("fetched_at") or now)
        age_hours = max(0.0, (now - published_at).total_seconds() / 3600)
        recency_score = max(0.0, 1.0 - min(age_hours, 168) / 168)
        semantic_score = max(0.0, min(float(row.get("similarity") or 0), 1.0))
        lead_score = max(0.0, min(float(row.get("lead_score") or 0) / 10, 1.0))
        if wants_leads:
            final_score = semantic_score * 0.55 + lead_score * 0.25 + recency_score * 0.20
        else:
            final_score = semantic_score * 0.65 + recency_score * 0.35
        ranked.append({**row, "final_score": final_score, "recency_score": recency_score})
    return sorted(ranked, key=lambda item: item["final_score"], reverse=True)


def render_rss_context(items: list[dict], *, expanded: bool) -> str:
    latest = latest_rss_item_time()
    freshness_note = "expanded beyond the default window" if expanded else "default freshness window"
    lines = [
        "RSS RAG Results:",
        "Grounding rule: use only the titles, sources, dates, URLs, and summaries listed below.",
        "Do not invent RSS items, publishers, links, or publication dates.",
        f"Last indexed: {_format_dt(latest)}",
        f"Freshness: {freshness_note}",
    ]
    if not items:
        lines.append("No indexed RSS items matched. Ask again after the scheduler ingests feeds.")
        return "\n".join(lines)

    for index, item in enumerate(items, 1):
        tags = _loads_list(item.get("tags_json", "[]"))
        lines.extend(
            [
                "",
                f"{index}. {item['title']}",
                f"Source: {item['source']}",
                f"Published: {_format_dt(item.get('published_at') or item.get('fetched_at'))}",
                f"Lead score: {item.get('lead_score', 0)}/10",
                f"Tags: {', '.join(tags) if tags else 'none'}",
                f"URL: {item['url']}",
                f"Summary: {item.get('summary') or 'No summary provided by feed.'}",
            ]
        )
    return "\n".join(lines)


def format_rss_answer_from_context(user_message: str, tool_context: str) -> str:
    if "RSS RAG Results:" not in tool_context:
        return ""

    items = parse_rendered_rss_items(tool_context)
    if not items:
        return ""

    limit = min(requested_item_limit(user_message, default=get_settings().rss_retrieval_limit), len(items))
    wants_leads = "lead" in user_message.lower() or "outreach" in user_message.lower()
    heading = "Top indexed RSS leads" if wants_leads else "Top latest indexed RSS items"
    latest = _context_value(tool_context, "Last indexed")
    freshness = _context_value(tool_context, "Freshness")

    lines = [f"{heading}:"]
    if latest:
        lines.append(f"Last indexed: {latest}")
    if freshness:
        lines.append(f"Freshness: {freshness}")

    for index, item in enumerate(items[:limit], 1):
        lines.extend(
            [
                "",
                f"{index}. {item['title']}",
                f"Source: {item.get('source', 'unknown')}",
                f"Published: {item.get('published', 'unknown')}",
                f"URL: {item.get('url', '')}",
            ]
        )
        if wants_leads:
            lines.append(f"Lead score: {item.get('lead_score', '0/10')}")
            tags = item.get("tags", "")
            if tags and tags != "none":
                lines.append(f"Tags: {tags}")
        summary = item.get("summary", "")
        if summary and summary.lower() != "comments":
            lines.append(f"Why it matters: {_compact_text(summary, 220)}")

    lines.extend(["", "Only indexed RSS items are included."])
    return "\n".join(lines).strip()


def parse_rendered_rss_items(tool_context: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in tool_context.splitlines():
        line = raw_line.strip()
        title_match = re.match(r"^\d+\.\s+(.+)$", line)
        if title_match:
            if current:
                items.append(current)
            current = {"title": title_match.group(1).strip()}
            continue
        if not current or ":" not in line:
            continue

        key, value = line.split(":", 1)
        field = key.strip().lower().replace(" ", "_")
        if field in {"source", "published", "lead_score", "tags", "url", "summary"}:
            current[field] = value.strip()

    if current:
        items.append(current)
    return [item for item in items if item.get("title") and item.get("url")]


def requested_item_limit(user_message: str, *, default: int) -> int:
    lowered = user_message.lower()
    match = re.search(r"\btop\s+(\d{1,2})\b", lowered)
    if match:
        return max(1, min(int(match.group(1)), 10))
    if "top three" in lowered:
        return 3
    return max(1, default)


def rss_status() -> dict:
    settings = get_settings()
    with Session(engine) as session:
        item_count = session.exec(select(func.count(RSSItem.id))).one()
        latest_item = session.exec(select(func.max(RSSItem.fetched_at))).one()
        latest_run = session.exec(
            select(Run)
            .where(Run.template_name == RSS_INGESTION_TEMPLATE)
            .order_by(Run.id.desc())
            .limit(1)
        ).first()

    return {
        "enabled": settings.rss_scheduler_enabled,
        "interval_minutes": settings.rss_crawl_interval_minutes,
        "feed_count": len(settings.rss_feed_url_list),
        "feeds": settings.rss_feed_url_list,
        "item_count": int(item_count or 0),
        "latest_item_fetched_at": _format_dt(latest_item),
        "freshness_hours": settings.rss_freshness_hours,
        "retrieval_limit": settings.rss_retrieval_limit,
        "latest_ingestion_run": {
            "id": latest_run.id,
            "status": latest_run.status,
            "completed_at": _format_dt(latest_run.completed_at),
            "final_output": latest_run.final_output,
        }
        if latest_run
        else None,
    }


def execute_rss_ingestion_run(run_id: int) -> None:
    with Session(engine) as session:
        run = session.get(Run, run_id)
        if not run:
            return
        run.status = "running"
        session.add(run)
        session.commit()

    add_log(run_id, "rss_ingestion_started", {"template_name": RSS_INGESTION_TEMPLATE})
    try:
        result = fetch_and_store_rss_items(run_id)
        final_output = (
            f"RSS ingestion scanned {result.items_scanned} item(s), stored {result.new_items} new item(s), "
            f"and skipped {result.skipped_existing} existing item(s)."
        )
        with Session(engine) as session:
            run = session.get(Run, run_id)
            if run:
                run.status = "completed"
                run.final_output = final_output
                run.completed_at = utc_now()
                session.add(run)
                session.commit()
        add_log(run_id, "run_completed", {"final_length": len(final_output), "rss_new_items": result.new_items})
    except Exception as exc:
        with Session(engine) as session:
            run = session.get(Run, run_id)
            if run:
                run.status = "failed"
                run.error = str(exc)
                run.completed_at = utc_now()
                session.add(run)
                session.commit()
        add_log(run_id, "run_failed", {"error": str(exc)}, level="error")


def create_rss_ingestion_run() -> int:
    with Session(engine) as session:
        run = Run(
            template_name=RSS_INGESTION_TEMPLATE,
            source_channel="scheduler",
            input_message="Scheduled RSS ingestion for latest news and lead RAG.",
            status="queued",
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return int(run.id)


def _query_rss_items(
    embedding: list[float],
    *,
    candidates: int,
    freshness_hours: int | None,
) -> list[dict]:
    params: dict[str, object] = {
        "embedding": vector_literal(embedding),
        "limit": candidates,
    }
    where_clause = ""
    if freshness_hours is not None:
        params["fresh_since"] = utc_now() - timedelta(hours=freshness_hours)
        where_clause = "WHERE COALESCE(published_at, fetched_at) >= :fresh_since"

    with engine.begin() as connection:
        rows = connection.execute(
            text(
                f"""
                SELECT
                    id,
                    source,
                    title,
                    url,
                    summary,
                    lead_score,
                    tags_json,
                    published_at,
                    fetched_at,
                    1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                FROM rssitem
                {where_clause}
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()

    return [dict(row) for row in rows]


def _parse_rss(root: ET.Element, feed_url: str) -> list[ParsedRSSItem]:
    channel = root.find("channel")
    if channel is None:
        return []
    source = _text(channel, "title") or feed_url
    items = []
    for item in channel.findall("item"):
        title = _text(item, "title")
        url = _text(item, "link") or _text(item, "guid")
        summary = _text(item, "description")
        published_at = parse_datetime(_text(item, "pubDate") or _text(item, "dc:date"))
        if title:
            items.append(
                ParsedRSSItem(
                    source=_compact_text(source, 120),
                    feed_url=feed_url,
                    title=title,
                    url=url,
                    summary=summary,
                    published_at=published_at,
                )
            )
    return items


def _parse_atom(root: ET.Element, feed_url: str) -> list[ParsedRSSItem]:
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    source = _text(root, "atom:title", namespace) or feed_url
    items = []
    for entry in root.findall("atom:entry", namespace):
        title = _text(entry, "atom:title", namespace)
        link = ""
        for link_node in entry.findall("atom:link", namespace):
            if link_node.attrib.get("href"):
                link = link_node.attrib["href"]
                break
        summary = _text(entry, "atom:summary", namespace) or _text(entry, "atom:content", namespace)
        published_at = parse_datetime(
            _text(entry, "atom:published", namespace) or _text(entry, "atom:updated", namespace)
        )
        if title:
            items.append(
                ParsedRSSItem(
                    source=_compact_text(source, 120),
                    feed_url=feed_url,
                    title=title,
                    url=link,
                    summary=summary,
                    published_at=published_at,
                )
            )
    return items


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except Exception:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def score_lead_signal(title: str, summary: str) -> tuple[int, list[str]]:
    text = f"{title} {summary}".lower()
    score = 0
    tags = []
    for keyword, weight in LEAD_KEYWORDS.items():
        if keyword in text:
            score += weight
            tags.append(keyword)
    return min(score, 10), tags[:8]


def latest_rss_item_time() -> datetime | None:
    with Session(engine) as session:
        return session.exec(select(func.max(RSSItem.fetched_at))).one()


def rss_content_hash(url: str, title: str) -> str:
    normalized = (url.strip().lower() or title.strip().lower()).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def _text(node: ET.Element, path: str, namespaces: dict[str, str] | None = None) -> str:
    found = node.find(path, namespaces or {})
    return _clean_html(found.text or "") if found is not None else ""


def _clean_html(text_value: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", text_value)
    return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()


def _compact_text(text_value: str, max_chars: int) -> str:
    cleaned = _clean_html(text_value)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "..."


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _loads_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _context_value(tool_context: str, label: str) -> str:
    prefix = f"{label}:"
    for raw_line in tool_context.splitlines():
        line = raw_line.strip()
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return ""


def _format_dt(value: datetime | None) -> str:
    return _as_utc(value).isoformat(timespec="seconds") if value else "not indexed yet"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
