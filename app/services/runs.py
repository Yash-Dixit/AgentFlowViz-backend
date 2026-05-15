import json

from fastapi import BackgroundTasks, HTTPException
from sqlmodel import Session, select

from app.config import get_settings
from app.models import LogEvent, Message, Run, ToolCall, WorkflowTemplate
from app.runtime import execute_demo_run
from app.schemas import DemoRunCreate
from app.serializers import log_to_dict, message_to_dict, run_to_dict, tool_call_to_dict
from app.services.attachments import get_run_attachments, save_uploads_for_run


def list_runs(session: Session) -> list[dict]:
    runs = session.exec(select(Run).order_by(Run.id.desc())).all()
    return [run_to_dict(run) for run in runs]


def start_demo_run(payload: DemoRunCreate, background_tasks: BackgroundTasks, session: Session) -> dict:
    template = session.exec(select(WorkflowTemplate).where(WorkflowTemplate.name == payload.template_name)).first()
    if not template:
        raise HTTPException(status_code=404, detail="Workflow template not found")
    if not template.is_active:
        raise HTTPException(status_code=400, detail="Workflow template is disabled")

    run = Run(template_name=payload.template_name, input_message=payload.message, status="queued")
    session.add(run)
    session.commit()
    session.refresh(run)
    background_tasks.add_task(execute_demo_run, run.id, payload.message)
    return run_to_dict(run)


def start_demo_run_with_attachments(
    *,
    message: str,
    template_name: str,
    uploads: list,
    background_tasks: BackgroundTasks,
    session: Session,
) -> dict:
    template = session.exec(select(WorkflowTemplate).where(WorkflowTemplate.name == template_name)).first()
    if not template:
        raise HTTPException(status_code=404, detail="Workflow template not found")
    if not template.is_active:
        raise HTTPException(status_code=400, detail="Workflow template is disabled")
    if not uploads:
        raise HTTPException(status_code=400, detail="Upload at least one image or document.")

    prompt = message.strip() or f"Analyze the uploaded {_upload_kind(uploads)} and summarize the useful findings."
    run = Run(
        template_name=template_name,
        source_channel="streamlit",
        input_message=prompt,
        status="queued",
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    saved = save_uploads_for_run(
        session=session,
        run_id=int(run.id or 0),
        source_channel="streamlit",
        caption=prompt,
        uploads=uploads,
    )
    background_tasks.add_task(execute_demo_run, run.id, prompt)
    payload = run_to_dict(run)
    payload["attachments"] = saved
    return payload


def get_run(run_id: int, session: Session) -> dict:
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run_to_dict(run)


def get_run_metrics(run_id: int, session: Session) -> dict:
    settings = get_settings()
    messages = session.exec(select(Message).where(Message.run_id == run_id)).all()
    logs = session.exec(select(LogEvent).where(LogEvent.run_id == run_id)).all()
    tool_calls = session.exec(select(ToolCall).where(ToolCall.run_id == run_id)).all()

    prompt_tokens = 0
    output_tokens = 0
    model_calls = 0
    cache_hits = 0
    saved_model_calls = 0
    saved_prompt_tokens = 0
    saved_output_tokens = 0
    model_duration_ms = 0.0
    tokens_per_second_values: list[float] = []

    for log in logs:
        details = json.loads(log.details_json)
        if log.event == "model_response":
            prompt_tokens += int(details.get("prompt_tokens") or 0)
            output_tokens += int(details.get("output_tokens") or 0)
            model_calls += 1
            model_duration_ms += float(details.get("total_duration_ms") or 0)
            tokens_per_second = float(details.get("tokens_per_second") or 0)
            if tokens_per_second:
                tokens_per_second_values.append(tokens_per_second)
        elif log.event == "semantic_cache_hit":
            cache_hits += 1
            saved_model_calls += int(details.get("saved_model_calls") or 0)
            saved_prompt_tokens += int(details.get("saved_prompt_tokens") or 0)
            saved_output_tokens += int(details.get("saved_output_tokens") or 0)

    average_tokens_per_second = 0.0
    if tokens_per_second_values:
        average_tokens_per_second = round(sum(tokens_per_second_values) / len(tokens_per_second_values), 2)
    model_duration_seconds = model_duration_ms / 1000
    estimated_energy_kwh = (settings.inference_power_watts / 1000) * (model_duration_seconds / 3600)
    estimated_energy_cost_usd = estimated_energy_kwh * settings.electricity_cost_per_kwh_usd

    return {
        "run_id": run_id,
        "message_count": len(messages),
        "tool_call_count": len(tool_calls),
        "log_count": len(logs),
        "model_calls": model_calls,
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "total_tokens": prompt_tokens + output_tokens,
        "semantic_cache_hits": cache_hits,
        "saved_model_calls": saved_model_calls,
        "saved_prompt_tokens": saved_prompt_tokens,
        "saved_output_tokens": saved_output_tokens,
        "saved_total_tokens": saved_prompt_tokens + saved_output_tokens,
        "average_tokens_per_second": average_tokens_per_second,
        "model_duration_ms": round(model_duration_ms, 2),
        "inference_power_watts": settings.inference_power_watts,
        "electricity_cost_per_kwh_usd": settings.electricity_cost_per_kwh_usd,
        "estimated_energy_kwh": round(estimated_energy_kwh, 8),
        "estimated_energy_cost_usd": round(estimated_energy_cost_usd, 6),
        "local_cost_usd": round(estimated_energy_cost_usd, 6),
    }


def get_run_messages(run_id: int, session: Session) -> list[dict]:
    messages = session.exec(select(Message).where(Message.run_id == run_id).order_by(Message.id)).all()
    return [message_to_dict(message) for message in messages]


def get_run_logs(run_id: int, session: Session) -> list[dict]:
    logs = session.exec(select(LogEvent).where(LogEvent.run_id == run_id).order_by(LogEvent.id)).all()
    return [log_to_dict(log) for log in logs]


def get_run_tool_calls(run_id: int, session: Session) -> list[dict]:
    tool_calls = session.exec(select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.id)).all()
    return [tool_call_to_dict(tool_call) for tool_call in tool_calls]


def get_run_attachment_rows(run_id: int, session: Session) -> list[dict]:
    run = session.get(Run, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return get_run_attachments(session, run_id)


def _upload_kind(uploads: list) -> str:
    content_types = [(getattr(upload, "content_type", "") or "").lower() for upload in uploads]
    if content_types and all(content_type.startswith("image/") for content_type in content_types):
        return "image"
    if content_types and not any(content_type.startswith("image/") for content_type in content_types):
        return "document"
    return "image or document"
