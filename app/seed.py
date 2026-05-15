import json

from sqlmodel import Session, select

from app.config import get_settings
from app.models import Agent, WorkflowTemplate


def limits(
    max_steps: int,
    max_tool_calls: int,
    max_output_tokens: int,
    context_window: str = "long",
) -> str:
    return json.dumps(
        {
            "max_steps": max_steps,
            "max_tool_calls": max_tool_calls,
            "max_output_tokens": max_output_tokens,
            "context_window": context_window,
        }
    )


DEPRECATED_TEMPLATE_NAMES = {
    "research_to_report",
    "support_triage",
    "orchestrated_task_router",
    "support_resolution_router",
}


LEGACY_AGENT_NAMES = {
    "Intake",
    "Classifier",
    "Knowledge Agent",
    "Reply Agent",
    "Escalation Agent",
    "Support Orchestrator",
    "Planner",
    "Researcher",
    "Writer",
}


def seed_defaults(session: Session, include_templates: bool = True) -> None:
    settings = get_settings()

    agents = [
        Agent(
            name="Telegram Intake",
            role="Intake Agent",
            system_prompt="Normalize the user's request and identify the target deliverable.",
            model=settings.ollama_fast_model,
            tools_json=json.dumps([]),
            channels_json=json.dumps(["telegram"]),
            schedule="manual",
            skills_json=json.dumps(["intake", "channel_adapter"]),
            interaction_rules="Accept requests from the configured Telegram allow-list only.",
            limits_json=limits(2, 0, 90),
        ),
        Agent(
            name="Media Intake",
            role="Intake Agent",
            system_prompt="Normalize the media request and identify uploaded images, documents, and desired output.",
            model=settings.ollama_fast_model,
            tools_json=json.dumps(["attachment_context_reader"]),
            channels_json=json.dumps(["streamlit"]),
            skills_json=json.dumps(["intake", "channel_adapter", "attachment_context"]),
            interaction_rules="Keep the handoff short and do not answer the request yet.",
            limits_json=limits(2, 1, 110, "long"),
        ),
        Agent(
            name="Media Orchestrator",
            role="Routing Agent",
            system_prompt=(
                "Decide which specialist should inspect the upload. Choose exactly one route: "
                "image_request or document_request. Include a short reason."
            ),
            model=settings.ollama_fast_model,
            tools_json=json.dumps([]),
            channels_json=json.dumps([]),
            skills_json=json.dumps(["planning", "task_decomposition", "attachment_context"]),
            interaction_rules="Route to one media branch only, then let the response writer finish.",
            limits_json=limits(2, 0, 120, "long"),
        ),
        Agent(
            name="Image Analyst",
            role="Image Analysis Agent",
            system_prompt=(
                "Analyze image attachment context. Identify visible content, text, layout, uncertainties, "
                "and the most useful answer for the user's request."
            ),
            model=settings.ollama_fast_model,
            tools_json=json.dumps(["attachment_context_reader"]),
            channels_json=json.dumps([]),
            skills_json=json.dumps(["vision", "tool_execution", "attachment_context"]),
            limits_json=limits(4, 2, 180, "large"),
        ),
        Agent(
            name="Document Analyst",
            role="Document Analysis Agent",
            system_prompt=(
                "Analyze document attachment context. Use extracted text when available, summarize key points, "
                "and flag missing or unreadable content."
            ),
            model=settings.ollama_fast_model,
            tools_json=json.dumps(["attachment_context_reader"]),
            channels_json=json.dumps([]),
            skills_json=json.dumps(["document_analysis", "tool_execution", "attachment_context"]),
            limits_json=limits(4, 2, 180, "large"),
        ),
        Agent(
            name="Media Response Writer",
            role="Response Agent",
            system_prompt=(
                "Write the final answer from the media analyst's output. Reference filenames when helpful, "
                "avoid internal workflow details, and finish naturally."
            ),
            model=settings.ollama_default_model,
            tools_json=json.dumps([]),
            channels_json=json.dumps(["streamlit"]),
            skills_json=json.dumps(["summarization", "final_response", "attachment_context"]),
            limits_json=limits(4, 1, 420, "large"),
        ),
        Agent(
            name="Orchestrator",
            role="Routing Agent",
            system_prompt=(
                "Decide which specialist should handle the request. Choose exactly one route: "
                "research_request, math_request, or support_request. Include a short reason."
            ),
            model=settings.ollama_fast_model,
            tools_json=json.dumps([]),
            channels_json=json.dumps(["telegram", "streamlit"]),
            skills_json=json.dumps(["planning", "task_decomposition"]),
            interaction_rules="Route to one specialist only, then let the response synthesizer finish.",
            limits_json=limits(2, 0, 110),
        ),
        Agent(
            name="Research Specialist",
            role="Research Specialist",
            system_prompt=(
                "Research the request and pass concise findings to the response synthesizer. "
                "For latest news, RSS updates, or top lead requests, use the indexed RSS RAG context."
            ),
            model=settings.ollama_fast_model,
            tools_json=json.dumps(["local_research", "rss_rag_retriever"]),
            channels_json=json.dumps([]),
            skills_json=json.dumps(["research", "tool_execution", "rss_rag"]),
            limits_json=limits(4, 2, 140),
        ),
        Agent(
            name="Math Specialist",
            role="Math Specialist",
            system_prompt="Solve numeric or calculation-heavy requests and explain the result simply.",
            model=settings.ollama_fast_model,
            tools_json=json.dumps(["calculator"]),
            channels_json=json.dumps([]),
            skills_json=json.dumps(["tool_execution"]),
            limits_json=limits(3, 2, 120),
        ),
        Agent(
            name="Support Specialist",
            role="Support Specialist",
            system_prompt="Classify support issues, use local support knowledge, and suggest a next action.",
            model=settings.ollama_fast_model,
            tools_json=json.dumps(["support_knowledge_lookup"]),
            channels_json=json.dumps([]),
            skills_json=json.dumps(["support_triage", "tool_execution"]),
            limits_json=limits(4, 2, 140),
        ),
        Agent(
            name="Response Synthesizer",
            role="Response Agent",
            system_prompt=(
                "Write the final response using the chosen specialist's output. "
                "If RSS RAG context is available, use only the listed titles, sources, dates, "
                "and URLs. Be concise, clear, and finish naturally."
            ),
            model=settings.ollama_default_model,
            tools_json=json.dumps(["report_writer", "rss_rag_retriever"]),
            channels_json=json.dumps(["telegram"]),
            skills_json=json.dumps(["summarization", "final_response"]),
            limits_json=limits(4, 2, 420, "large"),
        ),
    ]

    current_agent_names = {agent.name for agent in agents}

    for agent in agents:
        existing = session.exec(select(Agent).where(Agent.name == agent.name)).first()
        if not existing:
            session.add(agent)
        elif existing.skills_json == "[]":
            existing.schedule = agent.schedule
            existing.skills_json = agent.skills_json
            existing.interaction_rules = agent.interaction_rules
            existing.limits_json = agent.limits_json
            session.add(existing)

    if not include_templates:
        _prune_legacy_agents(session, current_agent_names)
        session.commit()
        return

    templates = [
        WorkflowTemplate(
            name="smart_task_router",
            description=(
                "General-purpose orchestrator that routes each prompt to research, math, or support, "
                "then synthesizes one final answer."
            ),
            graph_json=json.dumps(
                {
                    "nodes": [
                        "Telegram Intake",
                        "Orchestrator",
                        "Research Specialist",
                        "Math Specialist",
                        "Support Specialist",
                        "Response Synthesizer",
                    ],
                    "edges": [
                        {"source": "Telegram Intake", "target": "Orchestrator", "condition": "always"},
                        {
                            "source": "Orchestrator",
                            "target": "Research Specialist",
                            "condition": "research_request",
                        },
                        {"source": "Orchestrator", "target": "Math Specialist", "condition": "math_request"},
                        {"source": "Orchestrator", "target": "Support Specialist", "condition": "support_request"},
                        {
                            "source": "Research Specialist",
                            "target": "Response Synthesizer",
                            "condition": "research_done",
                        },
                        {
                            "source": "Math Specialist",
                            "target": "Response Synthesizer",
                            "condition": "calculation_done",
                        },
                        {
                            "source": "Support Specialist",
                            "target": "Response Synthesizer",
                            "condition": "support_done",
                        },
                        {
                            "source": "Response Synthesizer",
                            "target": "Orchestrator",
                            "condition": "needs_reroute",
                            "feedback_loop": True,
                        },
                    ],
                    "positions": {
                        "Telegram Intake": {"x": 0, "y": 180},
                        "Orchestrator": {"x": 260, "y": 180},
                        "Research Specialist": {"x": 540, "y": 40},
                        "Math Specialist": {"x": 540, "y": 180},
                        "Support Specialist": {"x": 540, "y": 320},
                        "Response Synthesizer": {"x": 840, "y": 180},
                    },
                }
            ),
        ),
        WorkflowTemplate(
            name="image_document_router",
            description=(
                "Media-focused orchestrator that routes uploaded images or documents to the right analyst, "
                "then drafts one final answer."
            ),
            graph_json=json.dumps(
                {
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
                        {
                            "source": "Media Orchestrator",
                            "target": "Document Analyst",
                            "condition": "document_request",
                        },
                        {"source": "Image Analyst", "target": "Media Response Writer", "condition": "image_done"},
                        {
                            "source": "Document Analyst",
                            "target": "Media Response Writer",
                            "condition": "document_done",
                        },
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
            ),
        ),
    ]

    deprecated_templates = session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.name.in_(DEPRECATED_TEMPLATE_NAMES))
    ).all()
    for template in deprecated_templates:
        session.delete(template)

    for template in templates:
        existing = session.exec(select(WorkflowTemplate).where(WorkflowTemplate.name == template.name)).first()
        if not existing:
            session.add(template)
        else:
            existing.description = template.description
            existing.graph_json = template.graph_json
            existing.is_active = True
            session.add(existing)

    _prune_legacy_agents(session, current_agent_names)
    session.commit()


def _prune_legacy_agents(session: Session, current_default_names: set[str]) -> None:
    referenced_names = _workflow_node_names(session)
    for agent_name in LEGACY_AGENT_NAMES:
        if agent_name in current_default_names or agent_name in referenced_names:
            continue
        existing = session.exec(select(Agent).where(Agent.name == agent_name)).first()
        if existing:
            session.delete(existing)


def _workflow_node_names(session: Session) -> set[str]:
    names: set[str] = set()
    templates = session.exec(select(WorkflowTemplate)).all()
    for template in templates:
        try:
            graph = json.loads(template.graph_json)
        except json.JSONDecodeError:
            continue
        names.update(str(node) for node in graph.get("nodes", []) if str(node).strip())
    return names
