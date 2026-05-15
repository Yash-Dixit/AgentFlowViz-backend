import json

from fastapi import HTTPException
from sqlmodel import Session, select

from app.config import get_settings
from app.models import Agent, utc_now
from app.runtime.semantic_cache import clear_semantic_cache
from app.schemas import AgentCreate, AgentUpdate
from app.serializers import agent_to_dict


def list_agents(session: Session) -> list[dict]:
    agents = session.exec(select(Agent).order_by(Agent.id)).all()
    return [agent_to_dict(agent) for agent in agents]


def create_agent(payload: AgentCreate, session: Session) -> dict:
    settings = get_settings()
    agent = Agent(
        name=payload.name,
        role=payload.role,
        system_prompt=payload.system_prompt,
        model=payload.model or settings.ollama_default_model,
        tools_json=json.dumps(payload.tools),
        channels_json=json.dumps(payload.channels),
        schedule=payload.schedule,
        skills_json=json.dumps(payload.skills),
        memory_enabled=payload.memory_enabled,
        interaction_rules=payload.interaction_rules,
        guardrails=payload.guardrails,
        limits_json=json.dumps(payload.limits),
    )
    session.add(agent)
    session.commit()
    session.refresh(agent)
    clear_semantic_cache()
    return agent_to_dict(agent)


def get_agent(agent_id: int, session: Session) -> dict:
    agent = session.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent_to_dict(agent)


def update_agent(agent_id: int, payload: AgentUpdate, session: Session) -> dict:
    agent = session.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    data = payload.model_dump(exclude_unset=True)
    if "tools" in data:
        agent.tools_json = json.dumps(data.pop("tools"))
    if "channels" in data:
        agent.channels_json = json.dumps(data.pop("channels"))
    if "skills" in data:
        agent.skills_json = json.dumps(data.pop("skills"))
    if "limits" in data:
        agent.limits_json = json.dumps(data.pop("limits"))
    for key, value in data.items():
        setattr(agent, key, value)
    agent.updated_at = utc_now()
    session.add(agent)
    session.commit()
    session.refresh(agent)
    clear_semantic_cache()
    return agent_to_dict(agent)


def delete_agent(agent_id: int, session: Session) -> dict:
    agent = session.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    session.delete(agent)
    session.commit()
    clear_semantic_cache()
    return {"deleted": True, "id": agent_id}
