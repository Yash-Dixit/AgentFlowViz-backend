import re
from typing import Any

from app.runtime.persistence import add_log, add_tool_call
from app.runtime.rss_rag import retrieve_rss_context, should_use_rss_rag
from app.services.attachments import build_attachment_context


def calculator_output(user_message: str) -> str:
    percent_match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+(-?\d+(?:\.\d+)?)",
        user_message,
        flags=re.IGNORECASE,
    )
    if percent_match:
        percentage = float(percent_match.group(1))
        base = float(percent_match.group(2))
        result = base * percentage / 100
        return f"Calculator evaluated {percentage:g}% of {base:g} as {result:g}."

    numbers = [float(match) for match in re.findall(r"-?\d+(?:\.\d+)?", user_message)]
    if numbers:
        return f"Calculator inspected numeric inputs and found their sum is {sum(numbers):g}."
    return "Calculator found no numeric expression that needed evaluation."


def tool_output(run_id: int, tool_name: str, user_message: str, prior_context: str) -> str:
    if tool_name == "attachment_context_reader":
        return build_attachment_context(run_id) or "No image or document attachment context is available for this run."
    if tool_name == "support_knowledge_lookup":
        return (
            "Support knowledge lookup searched the local workflow context and found likely issue "
            "classification, answer guidance, and escalation criteria."
        )
    if tool_name == "calculator":
        return calculator_output(user_message)
    if tool_name == "report_writer":
        return "Report writer prepared a concise structure with summary, findings, risks, and next actions."
    return (
        "Local research reviewed the user request and prior agent notes. "
        "It found useful findings, risks, and next actions for the next agent."
    )


def run_tools(run_id: int, config: dict[str, Any], user_message: str, prior_context: str) -> str:
    max_tool_calls = int(config.get("limits", {}).get("max_tool_calls", 3))
    tool_names = prioritized_tools(list(config.get("tools", [])), user_message)[:max_tool_calls]
    outputs = []
    for tool_name in tool_names:
        if tool_name == "rss_rag_retriever":
            output = retrieve_rss_context(run_id, user_message)
        else:
            output = tool_output(run_id, tool_name, user_message, prior_context)
        add_tool_call(run_id, config["name"], tool_name, prior_context or user_message, output)
        add_log(run_id, "tool_call", {"agent": config["name"], "tool": tool_name})
        outputs.append(f"{tool_name}: {output}")
    return "\n".join(outputs)


def prioritized_tools(tool_names: list[str], user_message: str) -> list[str]:
    if should_use_rss_rag(user_message) and "rss_rag_retriever" in tool_names:
        return ["rss_rag_retriever"] + [tool_name for tool_name in tool_names if tool_name != "rss_rag_retriever"]
    return tool_names
