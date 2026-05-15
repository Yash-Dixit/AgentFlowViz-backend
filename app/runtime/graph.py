import re

from langgraph.graph import END, StateGraph

from app.config import get_settings
from app.ollama_service import ollama_service
from app.runtime.agents import agent_config
from app.runtime.completion import (
    COMPLETION_SYSTEM_PROMPT,
    build_completion_prompt,
    check_completion,
    merge_completion,
)
from app.runtime.context import ContextBuilder
from app.runtime.persistence import (
    add_log,
    add_message,
    deliver_agent_messages,
    enqueue_agent_message,
    record_model_log,
)
from app.runtime.routing import choose_edge_for_run
from app.runtime.rss_rag import format_rss_answer_from_context
from app.runtime.state import DemoState
from app.runtime.tools import run_tools


FINAL_ROLE_TERMS = ["response", "writer", "reply", "synthesizer"]


def is_final_output_agent(config: dict, has_outgoing: bool) -> bool:
    role = config.get("role", "").lower()
    skills = " ".join(str(skill).lower() for skill in config.get("skills", []))
    return not has_outgoing or any(term in role for term in FINAL_ROLE_TERMS) or "final_response" in skills


def output_token_limit(config: dict, has_outgoing: bool) -> int:
    settings = get_settings()
    limits = config.get("limits", {})
    is_final_output = is_final_output_agent(config, has_outgoing)
    configured = int(limits.get("max_output_tokens") or 0)
    if configured:
        configured = min(configured, settings.final_response_max_output_tokens)
        if is_final_output:
            return max(settings.final_response_min_output_tokens, configured)
        return max(64, configured)

    if is_final_output:
        return settings.final_response_default_output_tokens
    return 120


def incoming_context(node_name: str, outputs: dict[str, str], edges: list[dict]) -> str:
    incoming = [
        edge
        for edge in edges
        if edge["target"] == node_name and edge["source"] in outputs and not edge["feedback_loop"]
    ]
    if not incoming:
        return ""
    return "\n\n".join(
        f"From {edge['source']} when {edge['condition']}:\n{outputs[edge['source']]}" for edge in incoming
    )


def delivered_context(run_id: int, node_name: str) -> str:
    delivered = deliver_agent_messages(run_id, node_name)
    if not delivered:
        return ""
    return "\n\n".join(
        f"From {message['sender']} when {message['condition']}:\n{message['content']}"
        for message in delivered
    )


def node_id(index: int, node_name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", node_name).strip("_").lower() or "agent"
    return f"step_{index}_{slug}"


def run_agent(
    *,
    run_id: int,
    agent_name: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    think: bool | str | None,
    num_predict: int,
    num_ctx: int,
) -> str:
    reply = ollama_service.chat(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        think=think,
        num_predict=num_predict,
        num_ctx=num_ctx,
    )
    record_model_log(run_id, agent_name, reply)
    return reply.content


def repair_final_output_if_needed(
    *,
    run_id: int,
    agent_name: str,
    model: str,
    user_message: str,
    content: str,
    think: bool | str | None,
    num_ctx: int,
) -> str:
    check = check_completion(content)
    if not check.incomplete:
        return content

    settings = get_settings()
    continuation = run_agent(
        run_id=run_id,
        agent_name=f"{agent_name} Completion Guard",
        model=model,
        system_prompt=COMPLETION_SYSTEM_PROMPT,
        user_prompt=build_completion_prompt(user_message, content),
        think=think,
        num_predict=settings.final_response_repair_tokens,
        num_ctx=num_ctx,
    )
    repaired = merge_completion(content, continuation)
    final_check = check_completion(repaired)
    add_log(
        run_id,
        "final_output_repaired",
        {
            "agent": agent_name,
            "reason": check.reason,
            "draft_length": len(content),
            "continuation_length": len(continuation),
            "final_length": len(repaired),
            "still_looked_incomplete": final_check.incomplete,
        },
    )
    return repaired


def make_graph_node(node_name: str, node_names: list[str], edges: list[dict]):
    def run_node(state: DemoState) -> DemoState:
        run_id = state["run_id"]
        template_name = state["template_name"]
        user_message = state["user_message"]
        outputs = dict(state.get("outputs", {}))
        route_choices = dict(state.get("route_choices", {}))
        config = agent_config(node_name, template_name)
        prior_context = delivered_context(run_id, node_name) or incoming_context(node_name, outputs, edges)
        outgoing = [
            edge
            for edge in edges
            if edge["source"] == node_name and edge["target"] in node_names and not edge["feedback_loop"]
        ]
        conversation_memory = state.get("conversation_memory", "").strip() if config["memory_enabled"] else ""

        if not outputs:
            add_message(run_id, "Telegram User", node_name, user_message, "external")

        tool_context = run_tools(run_id, config, user_message, prior_context)
        num_predict = output_token_limit(config, bool(outgoing))
        limits = config.get("limits", {})
        built_context = ContextBuilder().build(
            system_prompt=config["system_prompt"],
            workflow_context=state["workflow_context"],
            node_name=node_name,
            role=config["role"],
            interaction_rules=config["interaction_rules"],
            guardrails=config["guardrails"],
            user_message=user_message,
            prior_context=prior_context,
            tool_context=tool_context,
            conversation_memory=conversation_memory,
            output_token_limit=num_predict,
            context_window=limits.get("context_window") or limits.get("context_profile"),
        )
        add_log(run_id, "context_window", built_context.log_details(node_name))
        think: bool | str | None = False if config["model"] == get_settings().ollama_fast_model else "low"
        grounded_rss_answer = (
            format_rss_answer_from_context(user_message, tool_context)
            if is_final_output_agent(config, bool(outgoing))
            else ""
        )
        if grounded_rss_answer:
            content = grounded_rss_answer
            add_log(
                run_id,
                "rss_final_answer_grounded",
                {"agent": node_name, "final_length": len(content)},
            )
        else:
            content = run_agent(
                run_id=run_id,
                agent_name=node_name,
                model=config["model"],
                system_prompt=config["system_prompt"],
                user_prompt=built_context.user_prompt,
                think=think,
                num_predict=num_predict,
                num_ctx=built_context.num_ctx,
            )
        if not outgoing and not grounded_rss_answer:
            content = repair_final_output_if_needed(
                run_id=run_id,
                agent_name=node_name,
                model=config["model"],
                user_message=user_message,
                content=content,
                think=think,
                num_ctx=built_context.num_ctx,
            )

        outputs[node_name] = content
        if outgoing:
            chosen_edge = outgoing[0]
            if len(outgoing) > 1:
                chosen_edge = choose_edge_for_run(run_id, node_name, outgoing, user_message, content)
                route_choices[node_name] = chosen_edge["target"]
            enqueue_agent_message(
                run_id,
                node_name,
                chosen_edge["target"],
                content,
                chosen_edge["condition"],
            )
        else:
            add_message(run_id, node_name, "Telegram User", content, "external")

        return {"outputs": outputs, "route_choices": route_choices, "final": content}

    return run_node


def route_from(node_name: str, node_id_by_name: dict[str, str], outgoing: list[dict]):
    fallback = node_id_by_name[outgoing[0]["target"]]

    def route(state: DemoState) -> str:
        target_name = state.get("route_choices", {}).get(node_name, "")
        return node_id_by_name.get(target_name, fallback)

    return route


def build_demo_graph(node_names: list[str], edges: list[dict], entry_node: str):
    graph = StateGraph(DemoState)
    node_id_by_name = {
        node_name: node_id(index, node_name)
        for index, node_name in enumerate(node_names)
    }

    for node_name, graph_node_id in node_id_by_name.items():
        graph.add_node(graph_node_id, make_graph_node(node_name, node_names, edges))

    graph.set_entry_point(node_id_by_name[entry_node])

    for node_name, graph_node_id in node_id_by_name.items():
        outgoing = [
            edge
            for edge in edges
            if edge["source"] == node_name and edge["target"] in node_id_by_name and not edge["feedback_loop"]
        ]
        if not outgoing:
            graph.add_edge(graph_node_id, END)
        elif len(outgoing) == 1:
            graph.add_edge(graph_node_id, node_id_by_name[outgoing[0]["target"]])
        else:
            graph.add_conditional_edges(
                graph_node_id,
                route_from(node_name, node_id_by_name, outgoing),
                {node_id_by_name[edge["target"]]: node_id_by_name[edge["target"]] for edge in outgoing},
            )

    return graph.compile()
