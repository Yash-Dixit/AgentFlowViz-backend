from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.database import get_session
from app.schemas import AgentCreate, AgentUpdate
from app.services import agents as agent_service


router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
def list_agents(session: Session = Depends(get_session)) -> list[dict]:
    return agent_service.list_agents(session)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_agent(payload: AgentCreate, session: Session = Depends(get_session)) -> dict:
    return agent_service.create_agent(payload, session)


@router.get("/{agent_id}")
def get_agent(agent_id: int, session: Session = Depends(get_session)) -> dict:
    return agent_service.get_agent(agent_id, session)


@router.patch("/{agent_id}")
def update_agent(agent_id: int, payload: AgentUpdate, session: Session = Depends(get_session)) -> dict:
    return agent_service.update_agent(agent_id, payload, session)


@router.delete("/{agent_id}")
def delete_agent(agent_id: int, session: Session = Depends(get_session)) -> dict:
    return agent_service.delete_agent(agent_id, session)
