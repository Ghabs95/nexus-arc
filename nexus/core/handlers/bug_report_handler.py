"""Handler for reporting bugs to GitHub."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nexus.core.interactive.context import InteractiveContext

logger = logging.getLogger(__name__)


async def handle_report_bug(
    ctx: InteractiveContext,
    issue_num: str,
    repo_key: str,
    get_direct_issue_plugin: Any,
):
    """Report a bug for a specific issue as a comment and a new issue if needed."""
    try:
        # 1. Notify user
        await ctx.edit_message_text(f"🐞 Reporting bug for issue #{issue_num}...")

        # 2. Get issue details for context
        plugin = get_direct_issue_plugin(repo_key)
        if not plugin:
            await ctx.edit_message_text(f"❌ Could not initialize issue plugin for {repo_key}")
            return

        issue = plugin.get_issue(issue_num, ["title", "body"])
        if not issue:
            await ctx.edit_message_text(f"❌ Could not find issue #{issue_num}")
            return

        title = issue.get("title", f"Issue #{issue_num}")

        # 3. Create bug report issue
        bug_title = f"[BUG REPORT] Issue #{issue_num}: {title}"
        bug_body = (
            f"User reported a bug while interacting with issue #{issue_num}.\n\n"
            f"**Original Issue:** {title}\n"
            f"**Reporter ID:** {ctx.user_id}\n\n"
            "**Context:**\n"
            "Automated bug report triggered via Nexus Bot 'Report Bug' button."
        )

        bug_issue_num = plugin.create_issue(
            title=bug_title,
            body=bug_body,
            labels=["bug", "nexus-bot"],
        )

        if not bug_issue_num:
            await ctx.edit_message_text(f"❌ Failed to create bug report on the issue tracker.")
            return

        # 4. Comment on the original issue
        plugin.add_comment(
            issue_num, f"🐞 User reported a bug for this issue. See bug report: {bug_issue_num}"
        )

        await ctx.edit_message_text(
            f"✅ Bug report created: {bug_issue_num}\n\n" f"Thank you for your feedback!"
        )

    except Exception as exc:
        logger.error("Error reporting bug: %s", exc, exc_info=True)
        await ctx.edit_message_text(f"❌ Error reporting bug: {exc}")
