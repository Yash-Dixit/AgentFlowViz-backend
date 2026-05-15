from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile, status
from sqlmodel import Session

from app.database import get_session
from app.schemas import DemoRunCreate
from app.services import runs as run_service


router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("")
def list_runs(session: Session = Depends(get_session)) -> list[dict]:
    return run_service.list_runs(session)


@router.post("/demo", status_code=status.HTTP_202_ACCEPTED)
def start_demo_run(
    payload: DemoRunCreate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
) -> dict:
    return run_service.start_demo_run(payload, background_tasks, session)


@router.post("/demo/attachments", status_code=status.HTTP_202_ACCEPTED)
def start_demo_run_with_attachments(
    background_tasks: BackgroundTasks,
    message: str = Form(""),
    template_name: str = Form("image_document_router"),
    files: list[UploadFile] | None = File(default=None),
    session: Session = Depends(get_session),
) -> dict:
    return run_service.start_demo_run_with_attachments(
        message=message,
        template_name=template_name,
        uploads=files or [],
        background_tasks=background_tasks,
        session=session,
    )


@router.get("/{run_id}")
def get_run(run_id: int, session: Session = Depends(get_session)) -> dict:
    return run_service.get_run(run_id, session)


@router.get("/{run_id}/metrics")
def get_run_metrics(run_id: int, session: Session = Depends(get_session)) -> dict:
    return run_service.get_run_metrics(run_id, session)


@router.get("/{run_id}/messages")
def get_run_messages(run_id: int, session: Session = Depends(get_session)) -> list[dict]:
    return run_service.get_run_messages(run_id, session)


@router.get("/{run_id}/logs")
def get_run_logs(run_id: int, session: Session = Depends(get_session)) -> list[dict]:
    return run_service.get_run_logs(run_id, session)


@router.get("/{run_id}/tool-calls")
def get_run_tool_calls(run_id: int, session: Session = Depends(get_session)) -> list[dict]:
    return run_service.get_run_tool_calls(run_id, session)


@router.get("/{run_id}/attachments")
def get_run_attachments(run_id: int, session: Session = Depends(get_session)) -> list[dict]:
    return run_service.get_run_attachment_rows(run_id, session)
