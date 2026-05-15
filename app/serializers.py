import json
from datetime import datetime
from typing import Any

from app.models import Agent, Attachment, AttachmentContext, LogEvent, Message, Run, ToolCall, WorkflowTemplate


def _loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def agent_to_dict(agent: Agent) -> dict[str, Any]:
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "system_prompt": agent.system_prompt,
        "model": agent.model,
        "tools": _loads(agent.tools_json, []),
        "channels": _loads(agent.channels_json, []),
        "schedule": agent.schedule,
        "skills": _loads(agent.skills_json, []),
        "memory_enabled": agent.memory_enabled,
        "interaction_rules": agent.interaction_rules,
        "guardrails": agent.guardrails,
        "limits": _loads(agent.limits_json, {}),
        "created_at": _dt(agent.created_at),
        "updated_at": _dt(agent.updated_at),
    }


def template_to_dict(template: WorkflowTemplate) -> dict[str, Any]:
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "graph": _loads(template.graph_json, {}),
        "is_active": template.is_active,
        "created_at": _dt(template.created_at),
    }


def run_to_dict(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "template_name": run.template_name,
        "source_channel": run.source_channel,
        "telegram_user_id": run.telegram_user_id,
        "telegram_chat_id": run.telegram_chat_id,
        "status": run.status,
        "input_message": run.input_message,
        "final_output": run.final_output,
        "started_at": _dt(run.started_at),
        "completed_at": _dt(run.completed_at),
        "error": run.error,
    }


def message_to_dict(message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "run_id": message.run_id,
        "sender": message.sender,
        "recipient": message.recipient,
        "content": message.content,
        "message_type": message.message_type,
        "created_at": _dt(message.created_at),
    }


def log_to_dict(log: LogEvent) -> dict[str, Any]:
    return {
        "id": log.id,
        "run_id": log.run_id,
        "level": log.level,
        "event": log.event,
        "details": _loads(log.details_json, {}),
        "created_at": _dt(log.created_at),
    }


def tool_call_to_dict(tool_call: ToolCall) -> dict[str, Any]:
    return {
        "id": tool_call.id,
        "run_id": tool_call.run_id,
        "agent_name": tool_call.agent_name,
        "tool_name": tool_call.tool_name,
        "input_text": tool_call.input_text,
        "output_text": tool_call.output_text,
        "created_at": _dt(tool_call.created_at),
    }


def attachment_to_dict(attachment: Attachment) -> dict[str, Any]:
    return {
        "id": attachment.id,
        "run_id": attachment.run_id,
        "source_channel": attachment.source_channel,
        "bucket": attachment.bucket,
        "object_key": attachment.object_key,
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "file_size": attachment.file_size,
        "checksum_sha256": attachment.checksum_sha256,
        "caption": attachment.caption,
        "storage_status": attachment.storage_status,
        "created_at": _dt(attachment.created_at),
    }


def attachment_context_to_dict(context: AttachmentContext) -> dict[str, Any]:
    return {
        "id": context.id,
        "attachment_id": context.attachment_id,
        "run_id": context.run_id,
        "context_type": context.context_type,
        "model": context.model,
        "summary": context.summary,
        "extracted_text": context.extracted_text,
        "metadata": _loads(context.metadata_json, {}),
        "created_at": _dt(context.created_at),
    }
