from __future__ import annotations

from typing import Any

from nexus.core.callbacks.callback_registry_service import dispatch_callback_action
from nexus.core.state_manager import HostStateManager


def parse_inline_action(query_data: str) -> tuple[str, str, str | None] | None:
    parts = query_data.split("_", 1)
    if len(parts) < 2:
        return None
    action = parts[0]
    payload = parts[1]

    issue_num = payload
    project_hint: str | None = None
    if "|" in payload:
        raw_issue, raw_project = payload.split("|", 1)
        issue_num = raw_issue.strip()
        project_hint = raw_project.strip() or None
    return action, issue_num.lstrip("#"), project_hint


async def handle_merge_queue_inline_action(
    ctx: Any, *, action: str, issue_num: str, project_hint: str
) -> bool:
    try:
        queue = HostStateManager.load_merge_queue()
        if not isinstance(queue, dict):
            queue = {}

        changed = 0
        for pr_url, item in list(queue.items()):
            if not isinstance(item, dict):
                continue
            if str(item.get("issue") or "") != str(issue_num):
                continue
            if str(item.get("project") or "") != str(project_hint):
                continue

            status = str(item.get("status") or "").strip().lower()
            review_mode = str(item.get("review_mode") or "manual").strip().lower()

            target_status: str | None = None
            if action == "mqapprove":
                if status == "pending_manual_review":
                    target_status = "pending_auto_merge"
            elif action == "mqretry":
                if status in {"blocked", "failed"}:
                    target_status = (
                        "pending_auto_merge" if review_mode == "auto" else "pending_manual_review"
                    )
            elif action == "mqmerge":
                if status in {"pending_manual_review", "blocked", "failed"}:
                    target_status = "pending_auto_merge"

            if not target_status:
                continue

            updated = HostStateManager.update_merge_candidate(
                pr_url,
                status=target_status,
                last_error="",
                manual_override=(action in {"mqapprove", "mqmerge"}),
            )
            if updated is not None:
                changed += 1

        if changed == 0:
            await ctx.edit_message_text(
                f"ℹ️ No merge-queue entries updated for issue #{issue_num} ({project_hint})."
            )
            return True

        async def _approve_text() -> None:
            await ctx.edit_message_text(
                f"✅ Merge approved for {changed} PR(s) on issue #{issue_num}.\n\n"
                "Queue worker will process them automatically."
            )

        async def _retry_text() -> None:
            await ctx.edit_message_text(
                f"🔄 Merge retry queued for {changed} PR(s) on issue #{issue_num}."
            )

        async def _default_text() -> None:
            await ctx.edit_message_text(
                f"🚀 Merge requested for {changed} PR(s) on issue #{issue_num}.\n\n"
                "Queue worker will attempt merge on the next cycle."
            )

        await dispatch_callback_action(
            action=action,
            handlers={
                "mqapprove": _approve_text,
                "mqretry": _retry_text,
            },
            default_handler=_default_text,
        )
    except Exception as exc:
        await ctx.edit_message_text(f"❌ Merge queue action failed: {exc}")
    return True
