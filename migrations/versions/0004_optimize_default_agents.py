"""Optimize default agent models and token limits.

Revision ID: 0004_optimize_default_agents
Revises: 0003_conversation_memory
Create Date: 2026-05-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_optimize_default_agents"
down_revision = "0003_conversation_memory"
branch_labels = None
depends_on = None


FAST_MODEL = "qwen3.5:9b"
QUALITY_MODEL = "gpt-oss:20b"


DEFAULT_AGENT_UPDATES = [
    ("Telegram Intake", FAST_MODEL, '{"max_steps": 2, "max_tool_calls": 0, "max_output_tokens": 90}'),
    ("Planner", FAST_MODEL, '{"max_steps": 3, "max_tool_calls": 0, "max_output_tokens": 120}'),
    ("Researcher", FAST_MODEL, '{"max_steps": 4, "max_tool_calls": 3, "max_output_tokens": 140}'),
    ("Writer", QUALITY_MODEL, '{"max_steps": 4, "max_tool_calls": 1, "max_output_tokens": 260}'),
    ("Orchestrator", FAST_MODEL, '{"max_steps": 2, "max_tool_calls": 0, "max_output_tokens": 110}'),
    ("Research Specialist", FAST_MODEL, '{"max_steps": 4, "max_tool_calls": 2, "max_output_tokens": 140}'),
    ("Math Specialist", FAST_MODEL, '{"max_steps": 3, "max_tool_calls": 2, "max_output_tokens": 120}'),
    ("Support Specialist", FAST_MODEL, '{"max_steps": 4, "max_tool_calls": 2, "max_output_tokens": 140}'),
    ("Response Synthesizer", QUALITY_MODEL, '{"max_steps": 4, "max_tool_calls": 1, "max_output_tokens": 260}'),
]


SUPPORT_AGENT_INSERTS = [
    {
        "name": "Intake",
        "role": "Intake Agent",
        "system_prompt": "Normalize the support request and pass only the key facts forward.",
        "model": FAST_MODEL,
        "tools_json": "[]",
        "channels_json": '["telegram"]',
        "skills_json": '["intake", "channel_adapter"]',
        "interaction_rules": "Keep the handoff short and do not answer the request yet.",
        "guardrails": "Be concise, factual, and avoid unsafe instructions.",
        "limits_json": '{"max_steps": 2, "max_tool_calls": 0, "max_output_tokens": 90}',
    },
    {
        "name": "Classifier",
        "role": "Support Classifier",
        "system_prompt": "Classify the support request as known_issue or high_risk and explain briefly.",
        "model": FAST_MODEL,
        "tools_json": "[]",
        "channels_json": "[]",
        "skills_json": '["support_triage"]',
        "interaction_rules": "Choose the smallest useful classification and keep it concise.",
        "guardrails": "Be concise, factual, and avoid unsafe instructions.",
        "limits_json": '{"max_steps": 2, "max_tool_calls": 0, "max_output_tokens": 100}',
    },
    {
        "name": "Knowledge Agent",
        "role": "Knowledge Agent",
        "system_prompt": "Use local support knowledge and summarize the likely answer.",
        "model": FAST_MODEL,
        "tools_json": '["support_knowledge_lookup"]',
        "channels_json": "[]",
        "skills_json": '["support_triage", "tool_execution"]',
        "interaction_rules": "Ask for clarification when input is ambiguous.",
        "guardrails": "Be concise, factual, and avoid unsafe instructions.",
        "limits_json": '{"max_steps": 3, "max_tool_calls": 2, "max_output_tokens": 130}',
    },
    {
        "name": "Reply Agent",
        "role": "Response Agent",
        "system_prompt": "Write a concise support reply suitable for Telegram.",
        "model": QUALITY_MODEL,
        "tools_json": "[]",
        "channels_json": '["telegram"]',
        "skills_json": '["summarization", "final_response", "support_triage"]',
        "interaction_rules": "Ask for clarification when input is ambiguous.",
        "guardrails": "Be concise, factual, and avoid unsafe instructions.",
        "limits_json": '{"max_steps": 4, "max_tool_calls": 1, "max_output_tokens": 240}',
    },
    {
        "name": "Escalation Agent",
        "role": "Escalation Agent",
        "system_prompt": "Draft a calm escalation response with the safest next action.",
        "model": QUALITY_MODEL,
        "tools_json": "[]",
        "channels_json": '["telegram"]',
        "skills_json": '["support_triage", "final_response"]',
        "interaction_rules": "Ask for clarification when input is ambiguous.",
        "guardrails": "Be calm, factual, and avoid legal or security overclaims.",
        "limits_json": '{"max_steps": 4, "max_tool_calls": 1, "max_output_tokens": 220}',
    },
]


def upgrade() -> None:
    connection = op.get_bind()
    for name, model, limits_json in DEFAULT_AGENT_UPDATES:
        connection.execute(
            sa.text(
                """
                UPDATE agent
                SET model = :model,
                    limits_json = :limits_json,
                    updated_at = CURRENT_TIMESTAMP
                WHERE name = :name
                """
            ),
            {"name": name, "model": model, "limits_json": limits_json},
        )

    for agent in SUPPORT_AGENT_INSERTS:
        connection.execute(
            sa.text(
                """
                INSERT INTO agent (
                    name, role, system_prompt, model, tools_json, channels_json, schedule,
                    skills_json, memory_enabled, interaction_rules, guardrails, limits_json,
                    created_at, updated_at
                )
                SELECT
                    CAST(:name AS VARCHAR),
                    CAST(:role AS VARCHAR),
                    CAST(:system_prompt AS VARCHAR),
                    CAST(:model AS VARCHAR),
                    CAST(:tools_json AS VARCHAR),
                    CAST(:channels_json AS VARCHAR),
                    'manual',
                    CAST(:skills_json AS VARCHAR),
                    TRUE,
                    CAST(:interaction_rules AS VARCHAR),
                    CAST(:guardrails AS VARCHAR),
                    CAST(:limits_json AS VARCHAR),
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                WHERE NOT EXISTS (
                    SELECT 1 FROM agent WHERE name = CAST(:name AS VARCHAR)
                )
                """
            ),
            agent,
        )


def downgrade() -> None:
    return None
