from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.database import get_session
from app.schemas import WorkflowTemplateCreate, WorkflowTemplateUpdate
from app.services import workflows as workflow_service


router = APIRouter(prefix="/workflows/templates", tags=["workflows"])


@router.get("")
def list_templates(session: Session = Depends(get_session)) -> list[dict]:
    return workflow_service.list_templates(session)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_template(payload: WorkflowTemplateCreate, session: Session = Depends(get_session)) -> dict:
    return workflow_service.create_template(payload, session)


@router.post("/seed")
def seed_templates(session: Session = Depends(get_session)) -> dict:
    return workflow_service.seed_templates(session)


@router.patch("/{template_id}")
def update_template(
    template_id: int,
    payload: WorkflowTemplateUpdate,
    session: Session = Depends(get_session),
) -> dict:
    return workflow_service.update_template(template_id, payload, session)


@router.delete("/{template_id}")
def delete_template(template_id: int, session: Session = Depends(get_session)) -> dict:
    return workflow_service.delete_template(template_id, session)
