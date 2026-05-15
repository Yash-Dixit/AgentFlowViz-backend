"""Keep two end-user workflow templates.

Revision ID: 0006_two_orchestrated_workflows
Revises: 0005_cache_token_savings
Create Date: 2026-05-14
"""

import json

from alembic import op
import sqlalchemy as sa


revision = "0006_two_orchestrated_workflows"
down_revision = "0005_cache_token_savings"
branch_labels = None
depends_on = None


SMART_TASK_ROUTER_GRAPH = {
    "nodes": [
        "Telegram Intake",
        "Orchestrator",
        "Research Specialist",
        "Math Specialist",
        "Support Specialist",
        "Response Synthesizer",
    ],
    "edges": [
        {"source": "Telegram Intake", "target": "Orchestrator", "condition": "always"},
        {"source": "Orchestrator", "target": "Research Specialist", "condition": "research_request"},
        {"source": "Orchestrator", "target": "Math Specialist", "condition": "math_request"},
        {"source": "Orchestrator", "target": "Support Specialist", "condition": "support_request"},
        {"source": "Research Specialist", "target": "Response Synthesizer", "condition": "research_done"},
        {"source": "Math Specialist", "target": "Response Synthesizer", "condition": "calculation_done"},
        {"source": "Support Specialist", "target": "Response Synthesizer", "condition": "support_done"},
        {
            "source": "Response Synthesizer",
            "target": "Orchestrator",
            "condition": "needs_reroute",
            "feedback_loop": True,
        },
    ],
    "positions": {
        "Telegram Intake": {"x": 0, "y": 180},
        "Orchestrator": {"x": 260, "y": 180},
        "Research Specialist": {"x": 540, "y": 40},
        "Math Specialist": {"x": 540, "y": 180},
        "Support Specialist": {"x": 540, "y": 320},
        "Response Synthesizer": {"x": 840, "y": 180},
    },
}


SUPPORT_RESOLUTION_ROUTER_GRAPH = {
    "nodes": ["Intake", "Support Orchestrator", "Knowledge Agent", "Escalation Agent", "Reply Agent"],
    "edges": [
        {"source": "Intake", "target": "Support Orchestrator", "condition": "always"},
        {"source": "Support Orchestrator", "target": "Knowledge Agent", "condition": "known_issue"},
        {"source": "Support Orchestrator", "target": "Escalation Agent", "condition": "high_risk"},
        {"source": "Knowledge Agent", "target": "Reply Agent", "condition": "answer_found"},
        {"source": "Escalation Agent", "target": "Reply Agent", "condition": "escalation_prepared"},
        {
            "source": "Reply Agent",
            "target": "Support Orchestrator",
            "condition": "user_unsatisfied",
            "feedback_loop": True,
        },
    ],
    "positions": {
        "Intake": {"x": 0, "y": 180},
        "Support Orchestrator": {"x": 260, "y": 180},
        "Knowledge Agent": {"x": 540, "y": 100},
        "Escalation Agent": {"x": 540, "y": 260},
        "Reply Agent": {"x": 840, "y": 180},
    },
}


SUPPORT_ORCHESTRATOR_AGENT = {
    "name": "Support Orchestrator",
    "role": "Routing Agent",
    "system_prompt": (
        "Decide which support branch should handle the request. Choose exactly one route: "
        "known_issue or high_risk. Include a short reason."
    ),
    "model": "qwen3.5:9b",
    "tools_json": "[]",
    "channels_json": "[]",
    "skills_json": '["support_triage"]',
    "interaction_rules": "Route to one support branch only, then let the reply agent finish.",
    "guardrails": "Be concise, factual, and avoid unsafe instructions.",
    "limits_json": '{"max_steps": 2, "max_tool_calls": 0, "max_output_tokens": 100}',
}


def upsert_template(connection, old_name: str, new_name: str, description: str, graph: dict) -> None:
    graph_json = json.dumps(graph)
    old_id = connection.execute(
        sa.text("SELECT id FROM workflowtemplate WHERE name = :old_name"),
        {"old_name": old_name},
    ).scalar()

    if old_id:
        connection.execute(
            sa.text("DELETE FROM workflowtemplate WHERE name = :new_name AND id <> :old_id"),
            {"new_name": new_name, "old_id": old_id},
        )
        connection.execute(
            sa.text(
                """
                UPDATE workflowtemplate
                SET name = :new_name,
                    description = :description,
                    graph_json = :graph_json
                WHERE id = :old_id
                """
            ),
            {
                "old_id": old_id,
                "new_name": new_name,
                "description": description,
                "graph_json": graph_json,
            },
        )
        return

    existing_id = connection.execute(
        sa.text("SELECT id FROM workflowtemplate WHERE name = :new_name"),
        {"new_name": new_name},
    ).scalar()
    if existing_id:
        connection.execute(
            sa.text(
                """
                UPDATE workflowtemplate
                SET description = :description,
                    graph_json = :graph_json
                WHERE id = :existing_id
                """
            ),
            {"existing_id": existing_id, "description": description, "graph_json": graph_json},
        )
        return

    connection.execute(
        sa.text(
            """
            INSERT INTO workflowtemplate (name, description, graph_json, created_at)
            VALUES (:new_name, :description, :graph_json, CURRENT_TIMESTAMP)
            """
        ),
        {"new_name": new_name, "description": description, "graph_json": graph_json},
    )


def ensure_support_orchestrator_agent(connection) -> None:
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
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            WHERE NOT EXISTS (
                SELECT 1 FROM agent WHERE name = CAST(:name AS VARCHAR)
            )
            """
        ),
        SUPPORT_ORCHESTRATOR_AGENT,
    )


def upgrade() -> None:
    connection = op.get_bind()
    ensure_support_orchestrator_agent(connection)
    connection.execute(
        sa.text(
            """
            UPDATE agent
            SET system_prompt = :system_prompt,
                updated_at = CURRENT_TIMESTAMP
            WHERE name = 'Response Synthesizer'
            """
        ),
        {
            "system_prompt": (
                "Write the final response using the chosen specialist's output. Be concise and clear."
            )
        },
    )
    upsert_template(
        connection,
        "orchestrated_task_router",
        "smart_task_router",
        (
            "General-purpose orchestrator that routes each prompt to research, math, or support, "
            "then synthesizes one final answer."
        ),
        SMART_TASK_ROUTER_GRAPH,
    )
    upsert_template(
        connection,
        "support_triage",
        "support_resolution_router",
        (
            "Support-focused orchestrator that routes customer issues to a knowledge answer or escalation, "
            "then drafts a clean final reply."
        ),
        SUPPORT_RESOLUTION_ROUTER_GRAPH,
    )
    connection.execute(
        sa.text(
            """
            DELETE FROM workflowtemplate
            WHERE name IN ('research_to_report', 'support_triage', 'orchestrated_task_router')
            """
        )
    )


def downgrade() -> None:
    return None
