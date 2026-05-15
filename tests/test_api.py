import os
from datetime import datetime, timedelta, timezone

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://agentflowviz:agentflowviz@localhost:5433/agentflowviz_test",
)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["FAKE_LLM"] = "true"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["RSS_SCHEDULER_ENABLED"] = "false"
os.environ["MINIO_ENABLED"] = "false"

import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlmodel import Session, select
from sqlmodel import SQLModel

from app.config import Settings
from app.database import engine
from app.main import app
from app.message_formatting import format_telegram_html, split_message
from app.models import (
    Agent,
    AgentMessageQueue,
    Attachment,
    AttachmentContext,
    ConversationMemory,
    LogEvent,
    RSSItem,
    Run,
    SemanticCache,
    WorkflowTemplate,
    utc_now,
)
from app.ollama_service import ModelReply, OllamaService
from app.runtime import graph as runtime_graph
from app.runtime.completion import build_completion_prompt, check_completion, close_incomplete_output, merge_completion
from app.runtime.context import ContextBuilder, estimate_tokens, truncate_to_tokens
from app.runtime.graph import output_token_limit
from app.runtime.routing import choose_edge
from app.runtime.rss_rag import (
    ParsedRSSItem,
    format_rss_answer_from_context,
    parse_rss_items,
    parse_rendered_rss_items,
    retrieve_rss_context,
    rss_content_hash,
    score_lead_signal,
    store_rss_item,
)
from app.runtime.templates import fallback_graph, workflow_context, workflow_plan
from app.runtime.tools import calculator_output, prioritized_tools, run_tools
from app.runtime.utils import loads_json
from app.seed import LEGACY_AGENT_NAMES, seed_defaults
from app.telegram_bot import TelegramBotRunner


def setup_module() -> None:
    ensure_test_database()
    SQLModel.metadata.drop_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    command.upgrade(alembic_config, "head")


def ensure_test_database() -> None:
    url = make_url(TEST_DATABASE_URL)
    database_name = url.database
    if not database_name or "test" not in database_name:
        raise RuntimeError("TEST_DATABASE_URL must point to a dedicated database with 'test' in its name.")

    maintenance_url = url.set(database="postgres").render_as_string(hide_password=False)
    maintenance_url = maintenance_url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(maintenance_url, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (database_name,),
        ).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))


def test_agent_creation() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/agents",
            json={
                "name": "QA Agent",
                "role": "Tester",
                "system_prompt": "Check if the workflow behaves correctly.",
                "tools": ["local_research"],
                "channels": [],
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "QA Agent"
        assert body["tools"] == ["local_research"]


def test_agent_update_delete_and_not_found_paths() -> None:
    with TestClient(app) as client:
        create_response = client.post(
            "/agents",
            json={
                "name": "Configurable Agent",
                "role": "Worker",
                "system_prompt": "Do useful work.",
                "tools": ["calculator"],
                "channels": ["streamlit"],
            },
        )
        assert create_response.status_code == 201
        agent_id = create_response.json()["id"]

        update_response = client.patch(
            f"/agents/{agent_id}",
            json={
                "name": "Updated Configurable Agent",
                "tools": ["local_research", "calculator"],
                "channels": ["telegram"],
                "schedule": "hourly",
                "skills": ["research"],
                "memory_enabled": False,
                "interaction_rules": "Ask one crisp follow-up if needed.",
                "guardrails": "Stay factual.",
                "limits": {"max_steps": 2, "max_tool_calls": 1},
            },
        )
        assert update_response.status_code == 200
        updated = update_response.json()
        assert updated["name"] == "Updated Configurable Agent"
        assert updated["tools"] == ["local_research", "calculator"]
        assert updated["channels"] == ["telegram"]
        assert updated["schedule"] == "hourly"
        assert updated["skills"] == ["research"]
        assert updated["memory_enabled"] is False
        assert updated["limits"] == {"max_steps": 2, "max_tool_calls": 1}

        assert client.get(f"/agents/{agent_id}").status_code == 200
        assert client.delete(f"/agents/{agent_id}").json() == {"deleted": True, "id": agent_id}
        assert client.get(f"/agents/{agent_id}").status_code == 404
        assert client.patch("/agents/999999", json={"name": "Missing"}).status_code == 404


def test_seeded_agents_are_optimized_for_fast_workflows() -> None:
    with Session(engine) as session:
        seed_defaults(session)
        orchestrator = session.exec(select(Agent).where(Agent.name == "Orchestrator")).first()
        media_router = session.exec(select(Agent).where(Agent.name == "Media Orchestrator")).first()
        media_writer = session.exec(select(Agent).where(Agent.name == "Media Response Writer")).first()
        document_analyst = session.exec(select(Agent).where(Agent.name == "Document Analyst")).first()
        response_synthesizer = session.exec(select(Agent).where(Agent.name == "Response Synthesizer")).first()
        legacy_agents = session.exec(select(Agent).where(Agent.name.in_(LEGACY_AGENT_NAMES))).all()

    assert orchestrator is not None
    assert media_router is not None
    assert media_writer is not None
    assert document_analyst is not None
    assert orchestrator.model == "qwen3.5:9b"
    assert media_router.model == "qwen3.5:9b"
    assert media_writer.model == "gpt-oss:20b"
    assert loads_json(media_router.limits_json, {})["max_output_tokens"] <= 120
    assert loads_json(document_analyst.limits_json, {})["context_window"] == "large"
    assert loads_json(media_writer.limits_json, {})["max_output_tokens"] >= 220
    assert response_synthesizer is not None
    assert "rss_rag_retriever" in loads_json(response_synthesizer.tools_json, [])
    assert legacy_agents == []


def test_context_builder_truncates_low_priority_sections() -> None:
    settings = Settings(
        ollama_context_window_tokens=1400,
        context_window_overhead_tokens=100,
        fake_llm=True,
    )
    builder = ContextBuilder(settings)
    built = builder.build(
        system_prompt="System prompt.",
        workflow_context="workflow detail " * 500,
        node_name="Context Agent",
        role="Worker",
        interaction_rules="Be useful.",
        guardrails="Be safe.",
        user_message="This exact user request must remain visible.",
        prior_context="prior agent output " * 500,
        tool_context="tool output " * 500,
        conversation_memory="memory detail " * 500,
        output_token_limit=200,
    )

    assert built.num_ctx == 1400
    assert built.estimated_input_tokens <= built.input_token_budget
    assert "This exact user request must remain visible." in built.user_prompt
    assert "workflow_context" in built.truncated_sections
    assert "tool_results" in built.truncated_sections


def test_context_builder_preserves_required_sections() -> None:
    settings = Settings(
        ollama_context_window_tokens=1024,
        context_window_overhead_tokens=120,
        fake_llm=True,
    )
    built = ContextBuilder(settings).build(
        system_prompt="System prompt.",
        workflow_context="workflow detail " * 100,
        node_name="Required Agent",
        role="Worker",
        interaction_rules="Follow the rules.",
        guardrails="Stay safe.",
        user_message="Remember this important request.",
        prior_context="",
        tool_context="",
        conversation_memory="User prefers short replies.",
        output_token_limit=220,
    )

    assert "Current agent: Required Agent" in built.user_prompt
    assert "You have about 220 output tokens" in built.user_prompt
    assert "Remember this important request." in built.user_prompt
    assert "User prefers short replies." in built.user_prompt
    assert estimate_tokens(built.user_prompt) <= built.input_token_budget
    assert truncate_to_tokens("abcd" * 100, 10)[1] is True


def test_context_builder_uses_named_context_windows() -> None:
    settings = Settings(
        ollama_context_window_tokens=2048,
        ollama_context_window_short_tokens=4096,
        ollama_context_window_long_tokens=8192,
        ollama_context_window_large_tokens=16384,
        fake_llm=True,
    )
    built = ContextBuilder(settings).build(
        system_prompt="System prompt.",
        workflow_context="workflow",
        node_name="Large Context Agent",
        role="Worker",
        interaction_rules="Be useful.",
        guardrails="Be safe.",
        user_message="Use the larger context window.",
        prior_context="prior context " * 1000,
        tool_context="tool context " * 1000,
        conversation_memory="memory context " * 1000,
        output_token_limit=220,
        context_window="large",
    )

    assert built.num_ctx == 16384
    assert built.estimated_input_tokens <= built.input_token_budget


def test_final_output_limits_and_completion_helpers() -> None:
    final_config = {
        "role": "Response Agent",
        "skills": ["final_response"],
        "limits": {"max_output_tokens": 120},
    }
    intermediate_config = {
        "role": "Routing Agent",
        "skills": [],
        "limits": {"max_output_tokens": 90},
    }

    assert output_token_limit(final_config, has_outgoing=False) >= 360
    assert output_token_limit(intermediate_config, has_outgoing=True) == 90

    draft = "Here are the next steps:\n1. Check the workflow budget and"
    assert check_completion(draft).incomplete is True

    repaired = merge_completion(draft, "then finish the reply cleanly.")
    assert repaired.endswith(".")
    assert check_completion(repaired).incomplete is False

    assert close_incomplete_output("```python\nprint(1)").endswith("```")
    prompt = build_completion_prompt("x" * 1500, "y" * 1900)
    assert "[truncated]" in prompt
    assert "[earlier text omitted]" in prompt


def test_final_output_repair_appends_guard_continuation(monkeypatch) -> None:
    def fake_chat(**kwargs) -> ModelReply:
        return ModelReply(
            model=kwargs["model"],
            content="and close with a natural final sentence.",
            prompt_tokens=10,
            output_tokens=8,
            total_duration_ms=1.0,
            eval_duration_ms=1.0,
            tokens_per_second=8.0,
        )

    monkeypatch.setattr(runtime_graph.ollama_service, "chat", fake_chat)

    repaired = runtime_graph.repair_final_output_if_needed(
        run_id=999001,
        agent_name="Response Synthesizer",
        model="gpt-oss:20b",
        user_message="Explain local AI orchestration.",
        content="The short version is that local orchestration routes work to specialists and",
        think=False,
        num_ctx=2048,
    )

    assert "natural final sentence." in repaired
    assert check_completion(repaired).incomplete is False


def test_rss_rag_parses_scores_stores_and_retrieves_items() -> None:
    rss_xml = """
    <rss version="2.0">
      <channel>
        <title>Demo Startup Feed</title>
        <item>
          <title>AI support startup is hiring customer operations leads</title>
          <link>https://example.com/rss-lead-1</link>
          <description>Launches workflow automation for support inboxes.</description>
          <pubDate>Thu, 14 May 2026 10:00:00 GMT</pubDate>
        </item>
      </channel>
    </rss>
    """
    parsed = parse_rss_items(rss_xml, "https://example.com/rss")
    score, tags = score_lead_signal(parsed[0].title, parsed[0].summary)

    stored = store_rss_item(parsed[0], max_chars=500)
    stored_again = store_rss_item(parsed[0], max_chars=500)

    with Session(engine) as session:
        run = Run(template_name="rss_unit_test", input_message="top 3 leads", status="running")
        session.add(run)
        session.commit()
        session.refresh(run)
        item = session.exec(select(RSSItem).where(RSSItem.url == "https://example.com/rss-lead-1")).first()

    context = retrieve_rss_context(run.id, "top 3 leads for support automation outreach")

    assert parsed[0].source == "Demo Startup Feed"
    assert score >= 8
    assert {"ai", "support", "hiring", "customer", "automation"} & set(tags)
    assert stored is True
    assert stored_again is False
    assert item is not None
    assert item.content_hash == rss_content_hash(item.url, item.title)
    assert "RSS RAG Results" in context
    assert "AI support startup" in context


def test_rss_rag_parses_atom_items() -> None:
    atom_xml = """
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Demo Atom Feed</title>
      <entry>
        <title>New AI workflow paper</title>
        <link href="https://example.com/atom-1" />
        <summary>Agent workflow orchestration research.</summary>
        <updated>2026-05-14T11:00:00Z</updated>
      </entry>
    </feed>
    """

    parsed = parse_rss_items(atom_xml, "https://example.com/atom")

    assert len(parsed) == 1
    assert parsed[0].source == "Demo Atom Feed"
    assert parsed[0].url == "https://example.com/atom-1"
    assert parsed[0].published_at == datetime(2026, 5, 14, 11, 0, tzinfo=timezone.utc)


def test_rss_tool_is_prioritized_for_latest_requests(monkeypatch) -> None:
    def fake_retrieve(run_id: int, user_message: str) -> str:
        return "RSS RAG Results:\n1. Verified feed item\nURL: https://example.com/feed-item"

    monkeypatch.setattr("app.runtime.tools.retrieve_rss_context", fake_retrieve)
    config = {
        "name": "Response Synthesizer",
        "tools": ["report_writer", "rss_rag_retriever"],
        "limits": {"max_tool_calls": 1},
    }
    with Session(engine) as session:
        run = Run(template_name="rss_tool_priority_test", input_message="top 3 latest AI news", status="running")
        session.add(run)
        session.commit()
        session.refresh(run)
        assert run.id is not None
        run_id = run.id

    output = run_tools(run_id, config, "Top 3 latest AI news", "")

    assert prioritized_tools(config["tools"], "Top 3 latest AI news")[0] == "rss_rag_retriever"
    assert output.startswith("rss_rag_retriever:")
    assert "report_writer:" not in output


def test_rss_final_answer_uses_only_rendered_items() -> None:
    tool_context = """
    rss_rag_retriever: RSS RAG Results:
    Last indexed: 2026-05-14T18:00:00+00:00
    Freshness: default freshness window

    1. First RSS item
    Source: Hacker News
    Published: 2026-05-14T17:00:00+00:00
    Lead score: 5/10
    Tags: ai
    URL: https://example.com/first
    Summary: First summary.

    2. Second RSS item
    Source: arXiv cs.AI
    Published: 2026-05-14T16:00:00+00:00
    Lead score: 2/10
    Tags: research
    URL: https://example.com/second
    Summary: Second summary.
    """

    items = parse_rendered_rss_items(tool_context)
    answer = format_rss_answer_from_context("Top 1 latest AI news", tool_context)

    assert len(items) == 2
    assert "First RSS item" in answer
    assert "https://example.com/first" in answer
    assert "Second RSS item" not in answer
    assert "https://example.com/second" not in answer
    assert answer.endswith("Only indexed RSS items are included.")


def test_workflow_template_update_and_seed_endpoint() -> None:
    with TestClient(app) as client:
        create_response = client.post(
            "/workflows/templates",
            json={
                "name": "coverage_template",
                "description": "Template used by coverage tests.",
                "graph": {"nodes": ["A", "B"], "edges": [{"source": "A", "target": "B"}]},
            },
        )
        assert create_response.status_code == 201
        created = create_response.json()
        template_id = created["id"]
        assert created["is_active"] is True

        graph = {
            "nodes": ["A", "B", "C"],
            "edges": [
                {"source": "A", "target": "B", "condition": "ready"},
                {"source": "B", "target": "C", "condition": "done"},
                {"source": "C", "target": "B", "condition": "revise", "feedback_loop": True},
            ],
        }
        update_response = client.patch(
            f"/workflows/templates/{template_id}",
            json={"description": "Updated coverage template.", "graph": graph, "is_active": False},
        )
        assert update_response.status_code == 200
        updated = update_response.json()
        assert updated["description"] == "Updated coverage template."
        assert updated["is_active"] is False
        assert updated["graph"]["edges"][2]["feedback_loop"] is True

        assert client.patch("/workflows/templates/999999", json={"description": "Missing"}).status_code == 404
        delete_response = client.delete(f"/workflows/templates/{template_id}")
        assert delete_response.status_code == 200
        assert delete_response.json() == {"deleted": True, "id": template_id}
        assert client.delete("/workflows/templates/999999").status_code == 404

        assert client.post("/workflows/templates/seed").json() == {"seeded": True}
        template_names = {template["name"] for template in client.get("/workflows/templates").json()}
        assert {"smart_task_router", "image_document_router"}.issubset(template_names)
        assert not {
            "research_to_report",
            "support_triage",
            "orchestrated_task_router",
            "support_resolution_router",
        } & template_names


def test_disabled_workflow_cannot_start_demo_run() -> None:
    with TestClient(app) as client:
        templates = client.get("/workflows/templates").json()
        template = next(template for template in templates if template["name"] == "smart_task_router")
        template_id = template["id"]

        disabled_response = client.patch(f"/workflows/templates/{template_id}", json={"is_active": False})
        assert disabled_response.status_code == 200
        assert disabled_response.json()["is_active"] is False

        run_response = client.post(
            "/runs/demo",
            json={"message": "This disabled workflow should not run.", "template_name": "smart_task_router"},
        )
        assert run_response.status_code == 400
        assert run_response.json()["detail"] == "Workflow template is disabled"

        reenabled_response = client.patch(f"/workflows/templates/{template_id}", json={"is_active": True})
        assert reenabled_response.status_code == 200
        assert reenabled_response.json()["is_active"] is True


def test_startup_seed_does_not_restore_deleted_workflow_templates() -> None:
    with Session(engine) as session:
        template = session.exec(select(WorkflowTemplate).where(WorkflowTemplate.name == "smart_task_router")).first()
        assert template is not None
        session.delete(template)
        session.commit()

        seed_defaults(session, include_templates=False)
        template_after_startup_seed = session.exec(
            select(WorkflowTemplate).where(WorkflowTemplate.name == "smart_task_router")
        ).first()

    assert template_after_startup_seed is None

    with Session(engine) as session:
        seed_defaults(session)


def test_demo_workflow_persists_messages_and_logs() -> None:
    with TestClient(app) as client:
        response = client.post("/runs/demo", json={"message": "Create a tiny report about local AI agents."})
        assert response.status_code == 202
        run_id = response.json()["id"]

        run = client.get(f"/runs/{run_id}").json()
        assert run["status"] == "completed"
        assert run["final_output"]

        messages = client.get(f"/runs/{run_id}/messages").json()
        logs = client.get(f"/runs/{run_id}/logs").json()
        tool_calls = client.get(f"/runs/{run_id}/tool-calls").json()
        metrics = client.get(f"/runs/{run_id}/metrics").json()

        assert len(messages) >= 4
        assert any(message["sender"] == "Response Synthesizer" for message in messages)
        assert any(log["event"] == "run_completed" for log in logs)
        assert any(log["event"] == "context_window" for log in logs)
        assert any(log["event"] == "agent_message_enqueued" for log in logs)
        assert any(log["event"] == "agent_messages_delivered" for log in logs)
        assert metrics["inference_power_watts"] > 0
        assert metrics["estimated_energy_kwh"] >= 0
        assert tool_calls[0]["tool_name"] == "local_research"

        with Session(engine) as session:
            queued_messages = session.exec(
                select(AgentMessageQueue).where(AgentMessageQueue.run_id == run_id)
            ).all()
        assert queued_messages
        assert all(message.status == "delivered" for message in queued_messages)


def test_image_document_router_persists_document_context() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/runs/demo/attachments",
            data={
                "message": "Please summarize this document.",
                "template_name": "image_document_router",
            },
            files=[("files", ("notes.txt", b"Project Alpha needs a Friday launch checklist.", "text/plain"))],
        )
        assert response.status_code == 202
        run_id = response.json()["id"]

        run = client.get(f"/runs/{run_id}").json()
        logs = client.get(f"/runs/{run_id}/logs").json()
        tool_calls = client.get(f"/runs/{run_id}/tool-calls").json()
        attachments = client.get(f"/runs/{run_id}/attachments").json()
        route_logs = [log for log in logs if log["event"] == "route_decision"]

        assert run["status"] == "completed"
        assert run["template_name"] == "image_document_router"
        assert route_logs[0]["details"]["agent"] == "Media Orchestrator"
        assert route_logs[0]["details"]["target"] == "Document Analyst"
        assert any(call["tool_name"] == "attachment_context_reader" for call in tool_calls)
        assert attachments[0]["context"]["context_type"] == "document"
        assert "Project Alpha" in attachments[0]["context"]["extracted_text"]

        with Session(engine) as session:
            attachment = session.exec(select(Attachment).where(Attachment.run_id == run_id)).first()
            context = session.exec(select(AttachmentContext).where(AttachmentContext.run_id == run_id)).first()

        assert attachment is not None
        assert attachment.storage_status == "metadata_only"
        assert context is not None
        assert len(context.embedding) == 1024


def test_custom_template_uses_saved_graph_and_agent_config() -> None:
    with TestClient(app) as client:
        for payload in [
            {
                "name": "Custom Intake",
                "role": "Intake",
                "system_prompt": "Custom Intake captures the request.",
                "model": "qwen3.5:9b",
                "tools": [],
                "channels": ["streamlit"],
            },
            {
                "name": "Custom Researcher",
                "role": "Research",
                "system_prompt": "Custom Researcher uses local tools.",
                "model": "qwen3.5:9b",
                "tools": ["local_research"],
                "channels": [],
            },
            {
                "name": "Custom Writer",
                "role": "Writer",
                "system_prompt": "Custom Writer drafts the final answer.",
                "model": "gpt-oss:20b",
                "tools": [],
                "channels": ["telegram"],
            },
        ]:
            assert client.post("/agents", json=payload).status_code == 201

        template_response = client.post(
            "/workflows/templates",
            json={
                "name": "custom_graph_test",
                "description": "A custom graph should drive runtime execution.",
                "graph": {
                    "nodes": ["Custom Intake", "Custom Researcher", "Custom Writer"],
                    "edges": [
                        {"source": "Custom Intake", "target": "Custom Researcher", "condition": "ready"},
                        {"source": "Custom Researcher", "target": "Custom Writer", "condition": "researched"},
                        {
                            "source": "Custom Writer",
                            "target": "Custom Researcher",
                            "condition": "revise",
                            "feedback_loop": True,
                        },
                    ],
                },
            },
        )
        assert template_response.status_code == 201

        run_response = client.post(
            "/runs/demo",
            json={"message": "Use the custom graph.", "template_name": "custom_graph_test"},
        )
        assert run_response.status_code == 202
        run_id = run_response.json()["id"]

        run = client.get(f"/runs/{run_id}").json()
        messages = client.get(f"/runs/{run_id}/messages").json()
        tool_calls = client.get(f"/runs/{run_id}/tool-calls").json()
        metrics = client.get(f"/runs/{run_id}/metrics").json()

        assert run["status"] == "completed"
        assert any(message["sender"] == "Custom Intake" for message in messages)
        assert any(message["sender"] == "Custom Writer" for message in messages)
        assert tool_calls[0]["agent_name"] == "Custom Researcher"
        assert tool_calls[0]["tool_name"] == "local_research"
        assert metrics["model_calls"] >= 3


def test_orchestrated_router_selects_one_specialist_branch() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/runs/demo",
            json={
                "message": "Calculate 12 plus 30 and explain the result.",
                "template_name": "smart_task_router",
            },
        )
        assert response.status_code == 202
        run_id = response.json()["id"]

        run = client.get(f"/runs/{run_id}").json()
        messages = client.get(f"/runs/{run_id}/messages").json()
        logs = client.get(f"/runs/{run_id}/logs").json()
        tool_calls = client.get(f"/runs/{run_id}/tool-calls").json()

        route_logs = [log for log in logs if log["event"] == "route_decision"]
        assert run["status"] == "completed"
        assert route_logs[0]["details"]["agent"] == "Orchestrator"
        assert route_logs[0]["details"]["target"] == "Math Specialist"
        assert any(
            message["sender"] == "Orchestrator" and message["recipient"] == "Math Specialist"
            for message in messages
        )
        assert not any(
            message["sender"] == "Orchestrator" and message["recipient"] == "Research Specialist"
            for message in messages
        )
        assert not any(
            message["sender"] == "Orchestrator" and message["recipient"] == "Support Specialist"
            for message in messages
        )
        assert any(call["agent_name"] == "Math Specialist" and call["tool_name"] == "calculator" for call in tool_calls)
        assert any(message["sender"] == "Response Synthesizer" for message in messages)


def test_semantic_cache_reuses_repeated_workflow_result() -> None:
    prompt = "Explain durable agent orchestration for reusable cache coverage."
    with TestClient(app) as client:
        first_response = client.post(
            "/runs/demo",
            json={"message": prompt, "template_name": "smart_task_router"},
        )
        assert first_response.status_code == 202
        first_run_id = first_response.json()["id"]
        first_run = client.get(f"/runs/{first_run_id}").json()
        first_logs = client.get(f"/runs/{first_run_id}/logs").json()
        first_metrics = client.get(f"/runs/{first_run_id}/metrics").json()

        second_response = client.post(
            "/runs/demo",
            json={"message": prompt, "template_name": "smart_task_router"},
        )
        assert second_response.status_code == 202
        second_run_id = second_response.json()["id"]
        second_run = client.get(f"/runs/{second_run_id}").json()
        second_logs = client.get(f"/runs/{second_run_id}/logs").json()
        second_messages = client.get(f"/runs/{second_run_id}/messages").json()
        second_metrics = client.get(f"/runs/{second_run_id}/metrics").json()

        assert first_run["status"] == "completed"
        assert second_run["status"] == "completed"
        assert first_run["final_output"] == second_run["final_output"]
        assert any(log["event"] == "semantic_cache_store" for log in first_logs)
        assert any(log["event"] == "semantic_cache_hit" for log in second_logs)
        assert any(message["sender"] == "Semantic Cache" for message in second_messages)
        assert first_metrics["model_calls"] >= 1
        assert second_metrics["model_calls"] == 0
        assert second_metrics["semantic_cache_hits"] == 1
        assert second_metrics["saved_model_calls"] == first_metrics["model_calls"]
        assert second_metrics["saved_total_tokens"] == first_metrics["total_tokens"]

        health = client.get("/health").json()
        assert health["semantic_cache"]["hits"] >= 1
        assert health["semantic_cache"]["saved_model_calls"] >= first_metrics["model_calls"]
        assert health["semantic_cache"]["saved_total_tokens"] >= first_metrics["total_tokens"]


def test_agent_update_clears_semantic_cache() -> None:
    with TestClient(app) as client:
        create_response = client.post(
            "/agents",
            json={
                "name": "Cache Reset Agent",
                "role": "Worker",
                "system_prompt": "Original prompt.",
                "model": "qwen3.5:9b",
                "tools": [],
                "channels": ["streamlit"],
            },
        )
        assert create_response.status_code == 201
        agent_id = create_response.json()["id"]

        with Session(engine) as session:
            session.add(
                SemanticCache(
                    template_name="smart_task_router",
                    prompt="Reusable cached prompt.",
                    prompt_embedding=[0.001] * 1024,
                    final_output="Old cached answer.",
                    model="qwen3.5:9b",
                    expires_at=utc_now() + timedelta(hours=1),
                )
            )
            session.commit()
            assert session.exec(select(SemanticCache)).first() is not None

        update_response = client.patch(f"/agents/{agent_id}", json={"system_prompt": "Updated prompt."})
        assert update_response.status_code == 200

        with Session(engine) as session:
            assert session.exec(select(SemanticCache)).first() is None


def test_workflow_update_clears_template_semantic_cache() -> None:
    with TestClient(app) as client:
        templates = client.get("/workflows/templates").json()
        template = next(template for template in templates if template["name"] == "smart_task_router")

        with Session(engine) as session:
            session.add(
                SemanticCache(
                    template_name="smart_task_router",
                    prompt="Reusable workflow cached prompt.",
                    prompt_embedding=[0.001] * 1024,
                    final_output="Old workflow cached answer.",
                    model="qwen3.5:9b",
                    expires_at=utc_now() + timedelta(hours=1),
                )
            )
            session.commit()
            assert session.exec(select(SemanticCache)).first() is not None

        update_response = client.patch(
            f"/workflows/templates/{template['id']}",
            json={"description": template["description"]},
        )
        assert update_response.status_code == 200

        with Session(engine) as session:
            assert session.exec(select(SemanticCache)).first() is None


def test_semantic_cache_pgvector_extension_and_hnsw_index_exist() -> None:
    with engine.connect() as connection:
        extension = connection.execute(text("SELECT extname FROM pg_extension WHERE extname = 'vector'")).scalar_one()
        index_name = connection.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE tablename = 'semanticcache'
                  AND indexname = 'ix_semanticcache_embedding_hnsw'
                """
            )
        ).scalar_one()

    assert extension == "vector"
    assert index_name == "ix_semanticcache_embedding_hnsw"


def test_calculator_handles_percentage_requests() -> None:
    output = calculator_output("Calculate 18 percent of 240 and answer briefly.")

    assert "18% of 240" in output
    assert "43.2" in output


def test_runtime_helpers_route_and_describe_graphs() -> None:
    graph = {
        "nodes": ["Router", "Research", "Math"],
        "edges": [
            ["Router", "Research"],
            {"source": "Router", "target": "Math", "condition": "math_request"},
            {"source": "Math", "target": "Router", "condition": "revise", "feedback_loop": True},
            {"source": "", "target": "Ignored"},
        ],
    }

    nodes, edges, entry = workflow_plan(graph)
    chosen = choose_edge(edges[:2], "Please calculate 20 percent of 80.", "")
    top_three_route = choose_edge(
        [
            {"source": "Router", "target": "Research Specialist", "condition": "research_request"},
            {"source": "Router", "target": "Math Specialist", "condition": "math_request"},
        ],
        "Top 3 latest AI news from RSS.",
        "",
    )
    context = workflow_context("unit", "Unit workflow.", nodes, edges)

    assert nodes == ["Router", "Research", "Math"]
    assert entry == "Router"
    assert chosen["target"] == "Math"
    assert top_three_route["target"] == "Research Specialist"
    assert "Math -> Router when revise (feedback loop)" in context
    assert fallback_graph("image_document_router")["nodes"][0] == "Media Intake"
    assert loads_json("{bad json", {"fallback": True}) == {"fallback": True}


def test_ollama_service_fake_chat_and_model_listing() -> None:
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        ollama_host="0.0.0.0:11434",
        fake_llm=True,
    )
    service = OllamaService(settings)

    class FakeClient:
        def list(self) -> dict:
            return {"models": [{"model": "gpt-oss:20b"}, {"name": "qwen3.5:9b"}, {"model": ""}]}

    service.client = FakeClient()

    models = service.list_models()
    reply = service.chat(model="qwen3.5:9b", system_prompt="Tester. Verify behavior.", user_prompt="Hello")

    assert service.host == "http://127.0.0.1:11434"
    assert models == {"available": True, "models": ["gpt-oss:20b", "qwen3.5:9b"]}
    assert reply.fallback is True
    assert reply.prompt_tokens >= 1
    assert reply.output_tokens >= 1


def test_telegram_status_defaults_to_unconfigured() -> None:
    with TestClient(app) as client:
        response = client.get("/telegram/status")
        assert response.status_code == 200
        body = response.json()
    assert body["configured"] is False
    assert body["running"] is False


class FakeTelegramResponse:
    def __init__(self, payload: dict | None = None, content: bytes = b"") -> None:
        self._payload = payload or {}
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class FakeTelegramClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.gets: list[dict] = []

    def post(self, url: str, json: dict) -> FakeTelegramResponse:
        self.posts.append({"url": url, "json": json})
        return FakeTelegramResponse()

    def get(self, url: str, params: dict | None = None) -> FakeTelegramResponse:
        self.gets.append({"url": url, "params": params or {}})
        if url.endswith("/getFile"):
            return FakeTelegramResponse({"result": {"file_path": "documents/demo.pdf"}})
        return FakeTelegramResponse(content=b"%PDF-1.4 demo document bytes")


def test_telegram_bot_blocks_unauthorized_workflow_messages() -> None:
    runner = TelegramBotRunner(
        Settings(
            database_url=TEST_DATABASE_URL,
            telegram_bot_token="token",
            telegram_allowed_user_ids="42",
            fake_llm=True,
        )
    )
    client = FakeTelegramClient()

    runner._handle_update(
        client,
        {"message": {"chat": {"id": 100}, "from": {"id": 7}, "text": "Run this workflow"}},
    )

    assert len(client.posts) == 1
    payload = client.posts[0]["json"]
    assert payload["chat_id"] == 100
    assert "Not authorized" in payload["text"]


def test_telegram_callback_selects_existing_template() -> None:
    with Session(engine) as session:
        seed_defaults(session)

    runner = TelegramBotRunner(
        Settings(
            database_url=TEST_DATABASE_URL,
            telegram_bot_token="token",
            telegram_allowed_user_ids="42",
            fake_llm=True,
        )
    )
    client = FakeTelegramClient()

    runner._handle_update(
        client,
        {
            "callback_query": {
                "id": "callback-1",
                "from": {"id": 42},
                "message": {"chat": {"id": 100}},
                "data": "template:smart_task_router",
            }
        },
    )

    assert runner._selected_template_by_user[42] == "smart_task_router"
    assert client.posts[0]["url"].endswith("/answerCallbackQuery")
    assert "Selected Smart Task Router" in client.posts[1]["json"]["text"]


def test_telegram_ignores_disabled_templates() -> None:
    with Session(engine) as session:
        seed_defaults(session)
        template = session.exec(select(WorkflowTemplate).where(WorkflowTemplate.name == "smart_task_router")).first()
        assert template is not None
        template.is_active = False
        session.add(template)
        session.commit()

    runner = TelegramBotRunner(
        Settings(
            database_url=TEST_DATABASE_URL,
            telegram_bot_token="token",
            telegram_allowed_user_ids="42",
            fake_llm=True,
        )
    )
    client = FakeTelegramClient()

    runner._handle_update(
        client,
        {
            "callback_query": {
                "id": "callback-disabled",
                "from": {"id": 42},
                "message": {"chat": {"id": 100}},
                "data": "template:smart_task_router",
            }
        },
    )

    assert 42 not in runner._selected_template_by_user
    assert "not available anymore" in client.posts[1]["json"]["text"]

    with Session(engine) as session:
        template = session.exec(select(WorkflowTemplate).where(WorkflowTemplate.name == "smart_task_router")).first()
        assert template is not None
        template.is_active = True
        session.add(template)
        session.commit()


def test_telegram_normal_prompt_stays_natural_and_uses_typing_action() -> None:
    runner = TelegramBotRunner(
        Settings(
            database_url=TEST_DATABASE_URL,
            telegram_bot_token="token",
            telegram_allowed_user_ids="42",
            fake_llm=True,
        )
    )
    client = FakeTelegramClient()

    runner._handle_update(
        client,
        {
            "message": {
                "chat": {"id": 100},
                "from": {"id": 42},
                "text": "Explain friendly chatbot responses for cache-safe tests.",
            }
        },
    )

    text_payloads = [post["json"]["text"] for post in client.posts if "text" in post["json"]]
    action_payloads = [post["json"]["action"] for post in client.posts if "action" in post["json"]]
    combined_text = "\n".join(text_payloads)
    runner._wait_for_memory_updates()

    assert "typing" in action_payloads
    assert len(text_payloads) == 1
    assert "I'm on it" not in combined_text
    assert "Started AgentFlowViz" not in combined_text
    assert "Agents:" not in combined_text
    assert "Watch it live" not in combined_text
    assert "Streamlit Monitoring" not in combined_text
    assert "route_decision" not in combined_text


def test_telegram_document_caption_starts_media_workflow() -> None:
    runner = TelegramBotRunner(
        Settings(
            database_url=TEST_DATABASE_URL,
            telegram_bot_token="token",
            telegram_allowed_user_ids="42",
            fake_llm=True,
        )
    )
    client = FakeTelegramClient()

    runner._handle_update(
        client,
        {
            "message": {
                "chat": {"id": 100},
                "from": {"id": 42},
                "caption": "Can you give me a summary of this doc",
                "document": {
                    "file_id": "telegram-file-1",
                    "file_unique_id": "unique-file-1",
                    "file_name": "demo.pdf",
                    "mime_type": "application/pdf",
                    "file_size": 24,
                },
            }
        },
    )

    runner._wait_for_memory_updates()
    with Session(engine) as session:
        run = session.exec(
            select(Run)
            .where(Run.telegram_user_id == 42, Run.template_name == "image_document_router")
            .order_by(Run.id.desc())
        ).first()
        attachment = session.exec(select(Attachment).where(Attachment.run_id == run.id)).first() if run else None

    text_payloads = [post["json"]["text"] for post in client.posts if "text" in post["json"]]
    action_payloads = [post["json"].get("action") for post in client.posts if "action" in post["json"]]

    assert run is not None
    assert run.status == "completed"
    assert run.input_message == "Can you give me a summary of this doc"
    assert attachment is not None
    assert attachment.filename == "demo.pdf"
    assert attachment.source_channel == "telegram"
    assert any(get["url"].endswith("/getFile") for get in client.gets)
    assert any(action == "typing" for action in action_payloads)
    assert text_payloads
    assert "I hit a backend issue" not in "\n".join(text_payloads)


def test_telegram_run_accepts_large_telegram_ids() -> None:
    runner = TelegramBotRunner(
        Settings(
            database_url=TEST_DATABASE_URL,
            telegram_bot_token="token",
            telegram_allowed_user_ids="8830790840",
            fake_llm=True,
        )
    )

    run_id = runner._create_run(
        "Run the workflow for a large Telegram ID.",
        "smart_task_router",
        8830790840,
        8830790840,
    )

    with Session(engine) as session:
        run = session.get(Run, run_id)

    assert run is not None
    assert run.source_channel == "telegram"
    assert run.telegram_user_id == 8830790840
    assert run.telegram_chat_id == 8830790840


def test_telegram_memory_loads_recent_turns_and_updates_summary() -> None:
    runner = TelegramBotRunner(
        Settings(
            database_url=TEST_DATABASE_URL,
            telegram_bot_token="token",
            telegram_allowed_user_ids="77",
            fake_llm=True,
        )
    )
    client = FakeTelegramClient()

    runner._handle_update(
        client,
        {
            "message": {
                "chat": {"id": 700},
                "from": {"id": 77},
                "text": "Remember that my preferred demo topic is local agent memory.",
            }
        },
    )
    runner._wait_for_memory_updates()
    runner._handle_update(
        client,
        {
            "message": {
                "chat": {"id": 700},
                "from": {"id": 77},
                "text": "What demo topic did I mention earlier?",
            }
        },
    )
    runner._wait_for_memory_updates()

    with Session(engine) as session:
        runs = session.exec(
            select(Run)
            .where(Run.source_channel == "telegram", Run.telegram_user_id == 77)
            .order_by(Run.id)
        ).all()
        memory = session.exec(
            select(ConversationMemory).where(
                ConversationMemory.channel == "telegram",
                ConversationMemory.user_id == "77",
            )
        ).first()
        second_run_logs = session.exec(select(LogEvent).where(LogEvent.run_id == runs[-1].id)).all()

    loaded_memory_logs = [
        loads_json(log.details_json, {})
        for log in second_run_logs
        if log.event == "conversation_memory_loaded"
    ]
    cache_skip_logs = [
        loads_json(log.details_json, {})
        for log in second_run_logs
        if log.event == "semantic_cache_skipped"
    ]

    assert len(runs) >= 2
    assert runs[-1].telegram_chat_id == 700
    assert memory is not None
    assert "local agent memory" in memory.summary
    assert loaded_memory_logs[-1]["summary_available"] is True
    assert loaded_memory_logs[-1]["recent_runs"] >= 1
    assert any(log.get("reason") == "conversation_memory_enabled" for log in cache_skip_logs)


def test_telegram_formatter_handles_markdown_safely() -> None:
    formatted = format_telegram_html(
        "# Result\n\n**Strong point**\n\n| A | B |\n|---|---|\n| <x> | `code` |\n\n- item"
    )

    assert "<b>Result</b>" in formatted
    assert "<b>Strong point</b>" in formatted
    assert "<pre>" not in formatted
    assert "&lt;x&gt;" in formatted
    assert "<b>B</b>: <code>code</code>" in formatted
    assert "- item" in formatted


def test_telegram_formatter_turns_markdown_tables_into_readable_lists() -> None:
    formatted = format_telegram_html(
        "| Step | Action | Key Focus |\n"
        "| :--- | :--- | :--- |\n"
        "| **1. Verify Compliance** | Check category policies & IP rules. | Avoid suspensions. |\n"
        "| **2. Choose Niche** | Use research tools. | Target sustainable watches. |"
    )

    assert "|" not in formatted
    assert "<b>1. Verify Compliance</b>" in formatted
    assert "<b>Action</b>: Check category policies &amp; IP rules." in formatted
    assert "<b>Key Focus</b>: Avoid suspensions." in formatted
    assert "<b>2. Choose Niche</b>" in formatted


def test_telegram_formatter_splits_long_messages() -> None:
    long_message = "\n".join(f"line {index}" for index in range(100))

    chunks = split_message(long_message, max_length=80)

    assert len(chunks) > 1
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert chunks[0].startswith("line 0")
    assert chunks[-1].endswith("line 99")


def test_telegram_formatter_hard_wraps_single_long_line() -> None:
    long_message = "x" * 205

    chunks = split_message(long_message, max_length=80)

    assert len(chunks) == 3
    assert all(len(chunk) <= 80 for chunk in chunks)
    assert "".join(chunks) == long_message
