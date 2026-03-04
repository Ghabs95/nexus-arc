"""Visualize command handler — renders a Mermaid workflow diagram for an issue."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nexus.core.interactive.context import InteractiveContext

from nexus.core.config import NEXUS_CORE_STORAGE_DIR
from nexus.core.mermaid_render_service import build_mermaid_diagram
from nexus.core.integrations.workflow_state_factory import get_workflow_state
from nexus.core.state_manager import HostStateManager
from nexus.core.utils.log_utils import log_unauthorized_access


@dataclass
class VisualizeHandlerDeps:
    logger: logging.Logger
    allowed_user_ids: list[int]
    prompt_project_selection: Callable[..., Awaitable[None]]
    ensure_project_issue: Callable[..., Awaitable[tuple[str | None, str | None, list[str]]]]


async def visualize_handler(
    ctx: InteractiveContext,
    deps: VisualizeHandlerDeps,
) -> None:
    """Handle /visualize [project_key] [issue#] — send a Mermaid workflow diagram."""
    deps.logger.info("Visualize requested by user: %s", ctx.user_id)
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(deps.logger, ctx.user_id)
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "visualize")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "visualize")
    if not project_key:
        return

    msg_id = await ctx.reply_text(f"🎨 Generating workflow diagram for issue #{issue_num}...")

    # Load workflow steps from the workflow JSON file
    workflow_id = HostStateManager.get_workflow_id_for_issue(issue_num)
    if not workflow_id:
        workflow_id = get_workflow_state().get_workflow_id(issue_num)
    steps: list[dict[str, Any]] = []
    workflow_state = "unknown"

    if workflow_id:
        workflow_file = os.path.join(NEXUS_CORE_STORAGE_DIR, "workflows", f"{workflow_id}.json")
        if os.path.exists(workflow_file):
            try:
                with open(workflow_file, encoding="utf-8") as fh:
                    payload = json.load(fh)
                steps = payload.get("steps", [])
                workflow_state = str(payload.get("state", "unknown"))
            except Exception as exc:
                deps.logger.warning(
                    "visualize: failed to read workflow file for #%s: %s", issue_num, exc
                )

    if not steps:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"⚠️ No workflow steps found for issue #{issue_num}. Has a workflow been started?",
        )
        return

    diagram_text = build_mermaid_diagram(steps, issue_num)
    caption = f"📊 Workflow diagram — Issue #{issue_num} · state: {workflow_state}"

    # For Discord and Telegram, we fallback to sending Mermaid markdown
    fallback_text = f"{caption}\n\n```mermaid\n{diagram_text}\n```"
    await ctx.edit_message_text(
        message_id=msg_id,
        text=fallback_text,
    )
