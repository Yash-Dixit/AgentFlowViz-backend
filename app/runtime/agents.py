from typing import Any

from sqlmodel import Session, select

from app.config import get_settings
from app.database import engine
from app.models import Agent
from app.runtime.utils import loads_json


FAST_OUTPUT_TOKENS = 120
FINAL_OUTPUT_TOKENS = 420


def agent_record(node_name: str) -> Agent | None:
    with Session(engine) as session:
        return session.exec(select(Agent).where(Agent.name == node_name)).first()


def default_output_tokens(node_name: str) -> int:
    lowered = node_name.lower()
    final_terms = ["writer", "reply", "response", "synthesizer", "escalation"]
    return FINAL_OUTPUT_TOKENS if any(term in lowered for term in final_terms) else FAST_OUTPUT_TOKENS


def default_model_for_node(node_name: str) -> str:
    settings = get_settings()
    lowered = node_name.lower()
    final_terms = ["writer", "reply", "response", "synthesizer", "escalation"]
    if any(term in lowered for term in final_terms):
        return settings.ollama_default_model
    return settings.ollama_fast_model


def fallback_agent(node_name: str, template_name: str) -> dict[str, Any]:
    lowered = node_name.lower()
    model = default_model_for_node(node_name)
    tools: list[str] = []

    if "research" in lowered:
        tools = ["local_research"]
    if "math" in lowered or "calculator" in lowered:
        tools = ["calculator"]
    if "knowledge" in lowered or template_name in {"support_triage", "support_resolution_router"}:
        tools = ["support_knowledge_lookup"] if "knowledge" in lowered else tools
    if "support" in lowered:
        tools = ["support_knowledge_lookup"]
    if "image" in lowered or "document" in lowered or "media" in lowered:
        tools = ["attachment_context_reader"]

    return {
        "name": node_name,
        "role": f"{node_name} Agent",
        "system_prompt": f"You are {node_name}. Complete your workflow step clearly and pass useful context forward.",
        "model": model,
        "tools": tools,
        "channels": ["telegram"] if "telegram" in lowered or "reply" in lowered or "writer" in lowered else [],
        "skills": [],
        "memory_enabled": True,
        "interaction_rules": "Use prior agent messages and keep handoffs concise.",
        "guardrails": "Be concise, factual, and safe.",
        "limits": {
            "max_steps": 4,
            "max_tool_calls": 3,
            "max_output_tokens": default_output_tokens(node_name),
            "context_window": "long",
        },
    }


def agent_config(node_name: str, template_name: str) -> dict[str, Any]:
    agent = agent_record(node_name)
    if not agent:
        return fallback_agent(node_name, template_name)

    return {
        "name": agent.name,
        "role": agent.role,
        "system_prompt": agent.system_prompt,
        "model": agent.model,
        "tools": loads_json(agent.tools_json, []),
        "channels": loads_json(agent.channels_json, []),
        "skills": loads_json(agent.skills_json, []),
        "memory_enabled": agent.memory_enabled,
        "interaction_rules": agent.interaction_rules,
        "guardrails": agent.guardrails,
        "limits": loads_json(agent.limits_json, {"max_steps": 4, "max_tool_calls": 3}),
    }
