import re

from app.runtime.persistence import add_log


def edge_score(edge: dict, user_message: str, node_output: str) -> int:
    del node_output
    haystack = user_message.lower()
    condition = edge["condition"].lower()
    target = edge["target"].lower()
    score = 0

    terms = set(re.split(r"[^a-z0-9]+", f"{condition} {target}".lower()))
    score += sum(2 for term in terms if term and term in haystack)

    math_words = ["calculate", "math", "number", "sum", "total", "percent", "percentage", "average", "cost"]
    support_words = ["support", "bug", "error", "login", "password", "reset", "customer", "issue", "broken"]
    image_words = ["image", "photo", "picture", "screenshot", "diagram", "visual", "scan", "png", "jpg", "jpeg"]
    document_words = ["document", "doc", "pdf", "file", "attachment", "text", "csv", "report", "transcript", "read"]
    research_words = [
        "research",
        "explain",
        "compare",
        "summarize",
        "report",
        "why",
        "what",
        "how",
        "latest",
        "news",
        "lead",
        "leads",
        "rss",
        "updates",
    ]
    escalation_words = ["urgent", "security", "privacy", "legal", "angry", "refund", "breach", "risk"]

    has_arithmetic_expression = bool(
        re.search(
            r"\b\d+(?:\.\d+)?\s*(?:%|percent|plus|minus|times|x|\*|/|\+|-|of)\s*\d*",
            haystack,
        )
    )
    has_latest_research_intent = any(
        term in haystack for term in ["latest", "news", "lead", "leads", "rss", "updates"]
    ) or bool(re.search(r"\b(top|best|latest)\s+\d+\b", haystack))
    has_math_intent = has_arithmetic_expression or any(word in haystack for word in math_words)
    has_research_intent = has_latest_research_intent or any(word in haystack for word in research_words)
    if ("math" in condition or "calc" in condition or "math" in target) and (
        has_math_intent and not has_latest_research_intent
    ):
        score += 20
    if ("support" in condition or "known_issue" in condition or "support" in target) and any(
        word in haystack for word in support_words
    ):
        score += 18
    if ("image" in condition or "image" in target) and any(word in haystack for word in image_words):
        score += 24
    if ("document" in condition or "document" in target or "doc" in condition) and any(
        word in haystack for word in document_words
    ):
        score += 24
    if ("high_risk" in condition or "escalation" in target) and any(word in haystack for word in escalation_words):
        score += 22
    if ("research" in condition or "research" in target) and has_research_intent:
        score += 24 if has_latest_research_intent else 14
    if condition == "always":
        score += 1

    return score


def choose_edge(outgoing: list[dict], user_message: str, node_output: str) -> dict:
    scored_edges = [(edge, edge_score(edge, user_message, node_output)) for edge in outgoing]
    chosen, score = max(scored_edges, key=lambda item: item[1])
    if score == 0:
        chosen = outgoing[0]
    return chosen


def choose_edge_for_run(
    run_id: int,
    node_name: str,
    outgoing: list[dict],
    user_message: str,
    node_output: str,
) -> dict:
    chosen = choose_edge(outgoing, user_message, node_output)
    add_log(
        run_id,
        "route_decision",
        {
            "agent": node_name,
            "target": chosen["target"],
            "condition": chosen["condition"],
            "candidates": [
                {"target": edge["target"], "condition": edge["condition"]}
                for edge in outgoing
            ],
        },
    )
    return chosen
