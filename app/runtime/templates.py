from typing import Any

from sqlmodel import Session, select

from app.database import engine
from app.models import WorkflowTemplate
from app.runtime.utils import loads_json


def fallback_graph(template_name: str) -> dict[str, Any]:
    if template_name in {"smart_task_router", "orchestrated_task_router"}:
        return {
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
        }

    if template_name == "image_document_router":
        return {
            "nodes": [
                "Media Intake",
                "Media Orchestrator",
                "Image Analyst",
                "Document Analyst",
                "Media Response Writer",
            ],
            "edges": [
                {"source": "Media Intake", "target": "Media Orchestrator", "condition": "always"},
                {"source": "Media Orchestrator", "target": "Image Analyst", "condition": "image_request"},
                {"source": "Media Orchestrator", "target": "Document Analyst", "condition": "document_request"},
                {"source": "Image Analyst", "target": "Media Response Writer", "condition": "image_done"},
                {"source": "Document Analyst", "target": "Media Response Writer", "condition": "document_done"},
                {
                    "source": "Media Response Writer",
                    "target": "Media Orchestrator",
                    "condition": "needs_more_context",
                    "feedback_loop": True,
                },
            ],
        }

    return fallback_graph("smart_task_router")


def load_template(template_name: str) -> tuple[str, dict[str, Any]]:
    with Session(engine) as session:
        template = session.exec(select(WorkflowTemplate).where(WorkflowTemplate.name == template_name)).first()
        if not template:
            return template_name, fallback_graph(template_name)
        return template.description, loads_json(template.graph_json, fallback_graph(template_name))


def normalize_edges(edges: list[Any]) -> list[dict[str, Any]]:
    normalized = []
    for edge in edges:
        if isinstance(edge, dict):
            normalized.append(
                {
                    "source": edge.get("source", ""),
                    "target": edge.get("target", ""),
                    "condition": edge.get("condition") or "always",
                    "feedback_loop": bool(edge.get("feedback_loop", False)),
                }
            )
        elif isinstance(edge, (list, tuple)) and len(edge) >= 2:
            normalized.append(
                {
                    "source": edge[0],
                    "target": edge[1],
                    "condition": "always",
                    "feedback_loop": False,
                }
            )
    return [edge for edge in normalized if edge["source"] and edge["target"]]


def workflow_plan(graph: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]], str]:
    nodes = [str(node) for node in graph.get("nodes", []) if str(node).strip()]
    edges = normalize_edges(graph.get("edges", []))
    runnable_edges = [edge for edge in edges if not edge["feedback_loop"]]

    if not nodes:
        nodes = fallback_graph("smart_task_router")["nodes"]

    incoming = {edge["target"] for edge in runnable_edges}
    entry = next((node for node in nodes if node not in incoming), nodes[0])
    return nodes, edges, entry


def workflow_context(template_name: str, description: str, nodes: list[str], edges: list[dict[str, Any]]) -> str:
    edge_lines = [
        f"- {edge['source']} -> {edge['target']} when {edge['condition']}"
        + (" (feedback loop)" if edge["feedback_loop"] else "")
        for edge in edges
    ]
    return (
        f"Workflow: {template_name}\n"
        f"Description: {description}\n"
        f"Agents: {', '.join(nodes)}\n"
        f"Edges:\n" + "\n".join(edge_lines)
    )
