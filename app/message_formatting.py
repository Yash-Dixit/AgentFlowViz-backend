import html
import re


def _format_inline(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    return escaped


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_table_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells if cell.strip())


def _format_table(table_lines: list[str]) -> list[str]:
    rows = [_table_cells(line) for line in table_lines]
    rows = [row for row in rows if row and not _is_table_separator(row)]
    if len(rows) < 2:
        return [f"<pre>{html.escape(chr(10).join(table_lines), quote=False)}</pre>"]

    headers = rows[0]
    formatted: list[str] = []
    for row in rows[1:]:
        cells = row + [""] * max(0, len(headers) - len(row))
        title = cells[0].strip()
        if not title and not any(cell.strip() for cell in cells[1:]):
            continue

        if formatted:
            formatted.append("")
        if title:
            formatted.append(_format_inline(title))

        for header, cell in zip(headers[1:], cells[1:]):
            if cell.strip():
                formatted.append(f"<b>{_format_inline(header)}</b>: {_format_inline(cell)}")

    return formatted or [f"<pre>{html.escape(chr(10).join(table_lines), quote=False)}</pre>"]


def format_telegram_html(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n")
    output: list[str] = []
    table_lines: list[str] = []

    def flush_table() -> None:
        if not table_lines:
            return
        output.extend(_format_table(table_lines))
        table_lines.clear()

    for line in lines:
        stripped = line.strip()
        if _is_table_line(line):
            table_lines.append(line)
            continue

        flush_table()

        if not stripped:
            if output and output[-1] != "":
                output.append("")
            continue

        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            output.append(f"<b>{_format_inline(heading)}</b>")
            continue

        if stripped.startswith(("- ", "* ")):
            output.append(f"- {_format_inline(stripped[2:].strip())}")
            continue

        numbered = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if numbered:
            output.append(f"{numbered.group(1)}. {_format_inline(numbered.group(2))}")
            continue

        output.append(_format_inline(line))

    flush_table()
    return "\n".join(output).strip()


def split_message(text: str, max_length: int = 3500) -> list[str]:
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    for line in text.splitlines():
        for wrapped_line in _wrap_long_line(line, max_length):
            line_length = len(wrapped_line) + 1
            if current and current_length + line_length > max_length:
                chunks.append("\n".join(current).strip())
                current = []
                current_length = 0
            current.append(wrapped_line)
            current_length += line_length

    if current:
        chunks.append("\n".join(current).strip())

    return [chunk for chunk in chunks if chunk]


def _wrap_long_line(line: str, max_length: int) -> list[str]:
    if len(line) <= max_length:
        return [line]

    wrapped: list[str] = []
    remaining = line
    while len(remaining) > max_length:
        split_at = remaining.rfind(" ", 0, max_length)
        if split_at < max_length // 2:
            split_at = max_length
        wrapped.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    if remaining:
        wrapped.append(remaining)
    return wrapped
