import json

from fastapi import HTTPException
from sqlmodel import Session, select

from app.models import WorkflowTemplate
from app.runtime.semantic_cache import clear_semantic_cache
from app.schemas import WorkflowTemplateCreate, WorkflowTemplateUpdate
from app.seed import seed_defaults
from app.serializers import template_to_dict


def list_templates(session: Session) -> list[dict]:
    templates = session.exec(select(WorkflowTemplate).order_by(WorkflowTemplate.id)).all()
    return [template_to_dict(template) for template in templates]


def create_template(payload: WorkflowTemplateCreate, session: Session) -> dict:
    existing = session.exec(select(WorkflowTemplate).where(WorkflowTemplate.name == payload.name)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Workflow template name already exists")

    template = WorkflowTemplate(
        name=payload.name,
        description=payload.description,
        graph_json=json.dumps(payload.graph),
        is_active=payload.is_active,
    )
    session.add(template)
    session.commit()
    session.refresh(template)
    clear_semantic_cache(template.name)
    return template_to_dict(template)


def update_template(template_id: int, payload: WorkflowTemplateUpdate, session: Session) -> dict:
    template = session.get(WorkflowTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Workflow template not found")

    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        duplicate = session.exec(select(WorkflowTemplate).where(WorkflowTemplate.name == data["name"])).first()
        if duplicate and duplicate.id != template_id:
            raise HTTPException(status_code=409, detail="Workflow template name already exists")

    if "graph" in data:
        template.graph_json = json.dumps(data.pop("graph"))
    for key, value in data.items():
        setattr(template, key, value)
    session.add(template)
    session.commit()
    session.refresh(template)
    clear_semantic_cache(template.name)
    return template_to_dict(template)


def delete_template(template_id: int, session: Session) -> dict:
    template = session.get(WorkflowTemplate, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Workflow template not found")

    session.delete(template)
    session.commit()
    clear_semantic_cache(template.name)
    return {"deleted": True, "id": template_id}


def seed_templates(session: Session) -> dict:
    seed_defaults(session)
    clear_semantic_cache()
    return {"seeded": True}
