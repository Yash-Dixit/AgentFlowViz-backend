from typing import Any

from pydantic import BaseModel, Field


class AgentCreate(BaseModel):
    name: str
    role: str
    system_prompt: str
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    schedule: str = "manual"
    skills: list[str] = Field(default_factory=list)
    memory_enabled: bool = True
    interaction_rules: str = "Ask for clarification when input is ambiguous."
    guardrails: str = "Be concise, factual, and avoid unsafe instructions."
    limits: dict[str, Any] = Field(
        default_factory=lambda: {
            "max_steps": 4,
            "max_tool_calls": 3,
            "max_output_tokens": 220,
            "context_window": "long",
        }
    )


class AgentUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    tools: list[str] | None = None
    channels: list[str] | None = None
    schedule: str | None = None
    skills: list[str] | None = None
    memory_enabled: bool | None = None
    interaction_rules: str | None = None
    guardrails: str | None = None
    limits: dict[str, Any] | None = None


class DemoRunCreate(BaseModel):
    message: str
    template_name: str = "smart_task_router"


class WorkflowTemplateCreate(BaseModel):
    name: str
    description: str
    graph: dict = Field(default_factory=dict)
    is_active: bool = True


class WorkflowTemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    graph: dict | None = None
    is_active: bool | None = None
