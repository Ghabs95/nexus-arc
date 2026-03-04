"""Mermaid diagram rendering service for the /visualize command."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger(__name__)

# Status → background fill colour (GitHub dark-mode palette)
_STATUS_COLOURS: dict[str, str] = {
    "complete": "#3fb950",
    "completed": "#3fb950",
    "running": "#d29922",
    "pending": "#21262d",
    "failed": "#f85149",
    "error": "#f85149",
    "skipped": "#8b949e",
    "paused": "#58a6ff",
}

_MMDC_TIMEOUT = 15  # seconds


def build_mermaid_diagram(steps: list[dict[str, Any]], issue_num: str) -> str:
    """Convert workflow steps list to a Mermaid flowchart string."""
    total = len(steps)
    lines: list[str] = ["flowchart TD", f'  I["Issue #{issue_num}"]']
    style_lines: list[str] = []

    prev_node = "I"
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        node_id = f"S{idx + 1}"
        step_num = idx + 1
        raw_name = str(step.get("name", "unknown"))
        agent = step.get("agent") or {}
        if isinstance(agent, dict):
            agent_name = str(agent.get("name") or agent.get("display_name") or "").strip()
        else:
            agent_name = str(agent).strip()
        agent_name = agent_name.replace('"', "'")
        raw_status = str(step.get("status", "pending")).strip().lower()

        status_icon = {
            "complete": "✅",
            "completed": "✅",
            "running": "▶️",
            "pending": "⏳",
            "failed": "❌",
            "error": "❌",
            "skipped": "⏭️",
            "paused": "⏸️",
        }.get(raw_status, "❓")

        label_parts = [f"{step_num}/{total}"]
        if raw_name and raw_name != "unknown":
            label_parts.append(raw_name)
        if agent_name:
            label_parts.append(agent_name)
        label_parts.append(f"{status_icon} {raw_status}")
        label = "\\n".join(label_parts)

        lines.append(f'  {prev_node} --> {node_id}(["{label}"])')

        colour = _STATUS_COLOURS.get(raw_status)
        if colour:
            text_colour = (
                "#000" if raw_status in {"running", "complete", "completed"} else "#cdd9e5"
            )
            style_lines.append(f"  style {node_id} fill:{colour},color:{text_colour}")

        prev_node = node_id

    lines.extend(style_lines)
    return "\n".join(lines)


async def render_mermaid_to_png(diagram_text: str) -> bytes | None:
    """Render a Mermaid diagram string to PNG bytes using mmdc CLI.

    Returns None if mmdc is unavailable or rendering fails; callers should
    fall back to sending the raw diagram as a code block.
    """
    tmp_in: str | None = None
    tmp_out: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".mmd", delete=False, encoding="utf-8"
        ) as f_in:
            f_in.write(diagram_text)
            tmp_in = f_in.name

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f_out:
            tmp_out = f_out.name

        proc = await asyncio.create_subprocess_exec(
            "mmdc",
            "-i",
            tmp_in,
            "-o",
            tmp_out,
            "-t",
            "dark",
            "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=_MMDC_TIMEOUT)
        except TimeoutError:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=1)
            except TimeoutError:
                logger.warning("mmdc process did not exit promptly after kill()")
            logger.warning("mmdc timed out rendering Mermaid diagram")
            return None

        if proc.returncode != 0:
            logger.warning("mmdc exited with code %s", proc.returncode)
            return None

        if not os.path.exists(tmp_out) or os.path.getsize(tmp_out) == 0:
            logger.warning("mmdc produced empty/missing output file")
            return None

        with open(tmp_out, "rb") as fh:
            return fh.read()

    except FileNotFoundError:
        logger.info("mmdc not found; falling back to text diagram")
        return None
    except Exception as exc:
        logger.warning("Unexpected error rendering Mermaid diagram: %s", exc)
        return None
    finally:
        for tmp in (tmp_in, tmp_out):
            if tmp and os.path.exists(tmp):
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
