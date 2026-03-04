"""Common text formatting and normalization logic for Nexus Bot."""

import logging
import re

logger = logging.getLogger(__name__)


def clip_message_text(text: str, limit: int, suffix: str = "\n\n[truncated]") -> str:
    """
    Ensure outgoing text respects character limits of the target platform.
    
    Args:
        text: The text to potentially truncate.
        limit: The maximum character count allowed.
        suffix: The string to append if truncation occurs.
    """
    if not text or len(text) <= limit:
        return text
    
    budget = max(0, limit - len(suffix))
    clipped = text[:budget].rstrip()
    
    logger.debug(
        "Clipped message from %d to %d chars (limit=%d)", 
        len(text), len(clipped) + len(suffix), limit
    )
    return f"{clipped}{suffix}"


def flatten_markdown_table(text: str) -> str:
    """
    Converts GFM tables into a flattened key-value list format.
    
    Useful for chat platforms like Telegram or Discord that do not natively 
    render Markdown tables properly.
    """
    def _is_table_separator(line: str) -> bool:
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        return bool(parts) and all(p and re.fullmatch(r"[:\- ]+", p) for p in parts)

    def _parse_table_row(line: str) -> list[str]:
        return [p.strip() for p in line.strip().strip("|").split("|")]

    def _table_to_list(block: list[str]) -> list[str]:
        if len(block) < 2:
            return block
        headers = _parse_table_row(block[0])
        rows = [_parse_table_row(row) for row in block[2:]]
        if not headers or not rows:
            return block
        
        converted: list[str] = []
        for row in rows:
            pairs = []
            for idx, value in enumerate(row):
                header = headers[idx] if idx < len(headers) else f"col{idx + 1}"
                if header and value:
                    pairs.append(f"{header}: {value}")
                elif value:
                    pairs.append(value)
            if pairs:
                converted.append(f"- {' | '.join(pairs)}")
        return converted or block

    text = text.replace("\r\n", "\n")
    out_lines: list[str] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if i + 1 < len(lines) and "|" in line and _is_table_separator(lines[i + 1]):
            table_block = [line, lines[i+1]]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                table_block.append(lines[i])
                i += 1
            out_lines.extend(_table_to_list(table_block))
            continue
        out_lines.append(line)
        i += 1
    
    return "\n".join(out_lines)


def normalize_markdown_headers(text: str) -> str:
    """Converts # level headers to bold text for platforms with limited MD support."""
    out_lines = []
    for line in text.split("\n"):
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if match:
            out_lines.append(f"*{match.group(1)}*")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)
