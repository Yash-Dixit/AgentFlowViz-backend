from dataclasses import dataclass
import re


COMPLETION_SYSTEM_PROMPT = (
    "You complete clipped assistant replies. Return only the missing continuation, "
    "finish naturally, and do not repeat the draft."
)
TERMINAL_CHARS = ".!?)]}\"'`"
MIN_REPAIR_CHARS = 80
DANGLING_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "because",
    "but",
    "by",
    "for",
    "from",
    "if",
    "in",
    "into",
    "of",
    "or",
    "so",
    "the",
    "to",
    "with",
}


@dataclass(frozen=True)
class CompletionCheck:
    incomplete: bool
    reason: str = ""


def check_completion(text: str) -> CompletionCheck:
    stripped = text.strip()
    if not stripped:
        return CompletionCheck(True, "empty_output")

    if stripped.count("```") % 2:
        return CompletionCheck(True, "open_code_fence")

    last_line = stripped.splitlines()[-1].strip()
    if re.fullmatch(r"([-*]|\d+[.)])", last_line):
        return CompletionCheck(True, "dangling_list_marker")

    if stripped.endswith(("...", "…")):
        return CompletionCheck(True, "ellipsis")

    if stripped[-1] in ":,;":
        return CompletionCheck(True, "dangling_punctuation")

    words = re.findall(r"[A-Za-z]+", stripped)
    if words and words[-1].lower() in DANGLING_WORDS:
        return CompletionCheck(True, "dangling_word")

    if len(stripped) >= MIN_REPAIR_CHARS and stripped[-1] not in TERMINAL_CHARS:
        return CompletionCheck(True, "missing_terminal_punctuation")

    return CompletionCheck(False)


def build_completion_prompt(original_request: str, draft_answer: str) -> str:
    return (
        "Original user request:\n"
        f"{_head(original_request, 1200)}\n\n"
        "Draft answer that may have been clipped:\n"
        f"{_tail(draft_answer, 1800)}\n\n"
        "Continue only from where the draft stopped. Do not restart the answer, "
        "do not add workflow metadata, and finish within four short sentences."
    )


def merge_completion(draft_answer: str, continuation: str) -> str:
    draft = draft_answer.rstrip()
    extra = _strip_unhelpful_prefix(continuation.strip())
    if not extra:
        return close_incomplete_output(draft)

    if extra.startswith(draft):
        return close_incomplete_output(extra)

    extra = _remove_overlap(draft, extra)
    if not extra:
        return close_incomplete_output(draft)

    separator = "" if _joins_without_space(draft, extra) else " "
    return close_incomplete_output(f"{draft}{separator}{extra}")


def close_incomplete_output(text: str) -> str:
    closed = text.strip()
    if not closed:
        return ""

    if closed.count("```") % 2:
        closed = f"{closed}\n```"

    closed = closed.rstrip(" ,;:")
    if closed and closed[-1] not in TERMINAL_CHARS:
        closed = f"{closed}."

    return closed


def _head(text: str, max_chars: int) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    return stripped[:max_chars].rstrip() + "\n...[truncated]"


def _tail(text: str, max_chars: int) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    return "[earlier text omitted]\n" + stripped[-max_chars:].lstrip()


def _strip_unhelpful_prefix(text: str) -> str:
    patterns = [
        r"^continuation:\s*",
        r"^missing continuation:\s*",
        r"^final continuation:\s*",
    ]
    cleaned = text.strip()
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _remove_overlap(draft: str, continuation: str) -> str:
    max_overlap = min(160, len(draft), len(continuation))
    draft_lower = draft.lower()
    continuation_lower = continuation.lower()
    for size in range(max_overlap, 12, -1):
        if draft_lower[-size:] == continuation_lower[:size]:
            return continuation[size:].lstrip()
    return continuation


def _joins_without_space(draft: str, continuation: str) -> bool:
    if not draft or not continuation:
        return True
    if draft.endswith(("\n", " ", "-", "/", "(", "[", "{")):
        return True
    return continuation[0] in ".,;:!?)]}"
