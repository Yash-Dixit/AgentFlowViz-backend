from dataclasses import dataclass

from sqlmodel import Session, select

from app.config import Settings, get_settings
from app.database import engine
from app.models import ConversationMemory, Run, utc_now
from app.ollama_service import ollama_service
from app.runtime.persistence import add_log, record_model_log


@dataclass
class ConversationMemoryContext:
    text: str
    enabled: bool
    summary_available: bool
    recent_runs: int


def build_conversation_memory_context(run_id: int) -> ConversationMemoryContext:
    settings = get_settings()
    with Session(engine) as session:
        run = session.get(Run, run_id)
        if not _has_conversation_identity(run):
            return ConversationMemoryContext("", enabled=False, summary_available=False, recent_runs=0)

        memory = _get_memory(session, run)
        recent_runs = _recent_completed_runs(session, run, settings.conversation_memory_recent_runs)
        summary = memory.summary.strip() if memory and memory.summary else ""
        text = _format_memory_context(summary, recent_runs, settings)

    add_log(
        run_id,
        "conversation_memory_loaded",
        {
            "enabled": True,
            "summary_available": bool(summary),
            "recent_runs": len(recent_runs),
            "short_term_context": True,
            "long_term_summary": True,
        },
    )
    return ConversationMemoryContext(
        text=text,
        enabled=True,
        summary_available=bool(summary),
        recent_runs=len(recent_runs),
    )


def update_conversation_memory(run_id: int) -> None:
    settings = get_settings()
    with Session(engine) as session:
        run = session.get(Run, run_id)
        if not _has_conversation_identity(run) or not run.final_output.strip():
            return

        memory = _get_memory(session, run)
        existing_summary = memory.summary if memory else ""
        summary = _summarize_memory(run_id, existing_summary, run.input_message, run.final_output, settings)

        if memory:
            memory.summary = summary
            memory.chat_id = str(run.telegram_chat_id or "")
            memory.updated_at = utc_now()
        else:
            memory = ConversationMemory(
                channel=run.source_channel,
                user_id=str(run.telegram_user_id),
                chat_id=str(run.telegram_chat_id or ""),
                summary=summary,
            )
        session.add(memory)
        session.commit()

    add_log(
        run_id,
        "conversation_memory_updated",
        {
            "summary_chars": len(summary),
            "source_channel": "telegram",
        },
    )


def _has_conversation_identity(run: Run | None) -> bool:
    return bool(
        run
        and run.source_channel == "telegram"
        and run.telegram_user_id is not None
    )


def _get_memory(session: Session, run: Run) -> ConversationMemory | None:
    return session.exec(
        select(ConversationMemory).where(
            ConversationMemory.channel == run.source_channel,
            ConversationMemory.user_id == str(run.telegram_user_id),
        )
    ).first()


def _recent_completed_runs(session: Session, run: Run, limit: int) -> list[Run]:
    if not limit:
        return []
    recent = session.exec(
        select(Run)
        .where(
            Run.source_channel == run.source_channel,
            Run.telegram_user_id == run.telegram_user_id,
            Run.id != run.id,
            Run.status == "completed",
            Run.final_output != "",
        )
        .order_by(Run.id.desc())
        .limit(limit)
    ).all()
    return list(reversed(recent))


def _format_memory_context(summary: str, recent_runs: list[Run], settings: Settings) -> str:
    sections = []
    if summary:
        sections.append("Long-term conversation summary:\n" + _truncate(summary, settings.conversation_memory_summary_max_chars))

    if recent_runs:
        turns = []
        for run in recent_runs:
            turns.append(
                "User: "
                + _compact(run.input_message, 260)
                + "\nAssistant: "
                + _compact(run.final_output, 420)
            )
        recent_text = "\n\n".join(turns)
        sections.append("Recent short-term turns:\n" + _truncate(recent_text, settings.conversation_memory_recent_max_chars))

    return "\n\n".join(sections).strip()


def _summarize_memory(
    run_id: int,
    existing_summary: str,
    user_message: str,
    final_output: str,
    settings: Settings,
) -> str:
    system_prompt = (
        "You maintain a concise private memory summary for one Telegram user's ongoing conversation. "
        "Preserve stable facts, preferences, goals, constraints, and unresolved tasks. "
        "Ignore one-off wording, internal workflow details, logs, route decisions, and secrets."
    )
    user_prompt = (
        f"Existing memory summary:\n{existing_summary or 'No previous summary.'}\n\n"
        "Latest completed exchange:\n"
        f"User: {user_message}\n"
        f"Assistant: {final_output}\n\n"
        f"Return an updated memory summary under {settings.conversation_memory_summary_max_chars} characters. "
        "Return only the summary."
    )
    reply = ollama_service.chat(
        model=settings.ollama_fast_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        think=False,
        num_predict=220,
    )
    record_model_log(run_id, "Conversation Memory", reply)

    if reply.fallback:
        return _fallback_summary(existing_summary, user_message, final_output, settings.conversation_memory_summary_max_chars)

    summary = reply.content.strip()
    if not summary:
        return _fallback_summary(existing_summary, user_message, final_output, settings.conversation_memory_summary_max_chars)
    return _truncate(summary, settings.conversation_memory_summary_max_chars)


def _fallback_summary(existing_summary: str, user_message: str, final_output: str, max_chars: int) -> str:
    latest = (
        "Recent exchange - User: "
        + _compact(user_message, 260)
        + " | Assistant: "
        + _compact(final_output, 360)
    )
    summary = "\n".join(part for part in [existing_summary.strip(), latest] if part)
    return _truncate(summary, max_chars)


def _compact(text: str, max_chars: int) -> str:
    return _truncate(" ".join(text.split()), max_chars)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."
