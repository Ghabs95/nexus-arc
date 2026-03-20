"""Issue-related command handlers."""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from nexus.core.config import NEXUS_STORAGE_BACKEND
from nexus.core.runtime_mode import is_postgres_backend
from nexus.core.utils.log_utils import log_unauthorized_access

if TYPE_CHECKING:
    from nexus.core.interactive.context import InteractiveContext


@dataclass
class IssueHandlerDeps:
    logger: Any
    allowed_user_ids: list[int]
    base_dir: str
    default_repo: str
    project_config: dict[str, dict[str, Any]]
    prompt_project_selection: Callable[[InteractiveContext, str], Awaitable[None]]
    ensure_project_issue: Callable[
        [InteractiveContext, str], Awaitable[tuple[str | None, str | None, list[str]]]
    ]
    project_repo: Callable[[str], str]
    project_issue_url: Callable[[str, str], str]
    get_issue_details: Callable[..., dict[str, Any] | None]
    get_direct_issue_plugin: Callable[[str], Any]
    resolve_project_config_from_task: Callable[[str], tuple[str | None, dict[str, Any] | None]]
    invoke_ai_agent: Callable[..., tuple[int | None, str | None]]
    get_sop_tier: Callable[[str], tuple[str, Any, Any]]
    find_task_file_by_issue: Callable[[str], str | None]
    resolve_repo: Callable[[dict[str, Any] | None, str], str]
    build_issue_url: Callable[[str, str, dict[str, Any] | None], str]
    user_manager: Any
    save_tracked_issues: Callable[[dict[str, Any]], None]
    tracked_issues_ref: dict[str, Any]
    default_issue_url: Callable[[str], str]
    get_project_label: Callable[[str], str]
    track_short_projects: list[str]
    create_planning_task: Callable[..., Awaitable[dict[str, Any]]]
    requester_context_builder: Callable[[Any], dict[str, str]] | None = None


def _resolve_requester_nexus_id(ctx: InteractiveContext, deps: IssueHandlerDeps) -> str | None:
    try:
        platform = str(ctx.platform or "").strip().lower()
        user_id = str(ctx.user_id or "").strip()
        if not platform or not user_id:
            return None
        return str(deps.user_manager.resolve_nexus_id(platform, user_id) or "").strip() or None
    except Exception:
        return None


def _resolve_direct_issue_plugin(
    deps: IssueHandlerDeps,
    repo: str,
    requester_nexus_id: str | None,
):
    if requester_nexus_id:
        try:
            return deps.get_direct_issue_plugin(repo, requester_nexus_id=requester_nexus_id)
        except TypeError:
            return deps.get_direct_issue_plugin(repo)
    return deps.get_direct_issue_plugin(repo)


def _resolve_issue_details(
    deps: IssueHandlerDeps,
    issue_num: str,
    *,
    repo: str | None = None,
    requester_nexus_id: str | None = None,
):
    kwargs: dict[str, Any] = {}
    if repo is not None:
        kwargs["repo"] = repo
    if requester_nexus_id:
        kwargs["requester_nexus_id"] = requester_nexus_id
    try:
        return deps.get_issue_details(issue_num, **kwargs)
    except TypeError:
        kwargs.pop("requester_nexus_id", None)
        try:
            return deps.get_issue_details(issue_num, **kwargs)
        except TypeError:
            if repo is None:
                return deps.get_issue_details(issue_num)
            return deps.get_issue_details(issue_num, repo)


async def assign_handler(ctx: InteractiveContext, deps: IssueHandlerDeps) -> None:
    deps.logger.info(f"Assign triggered by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "assign")
        return

    project_key, issue_number, rest = await deps.ensure_project_issue(ctx, "assign")
    if not project_key:
        return

    repo = deps.project_repo(project_key)
    issue_url = deps.project_issue_url(project_key, issue_number)
    assignee = "@me"
    if rest:
        raw_assignee = rest[0]
        # Allow generic "agent" or "bot" to refer to the default AI implementation
        if raw_assignee.lower() in ("copilot", "agent", "bot", "ai"):
            assignee = os.getenv("AI_AGENT_USER", os.getenv("GITHUB_COPILOT_USER", "copilot"))
        else:
            assignee = raw_assignee

    msg_id = await ctx.reply_text(f"🔄 Assigning issue #{issue_number}...")

    try:
        plugin = _resolve_direct_issue_plugin(
            deps,
            repo,
            _resolve_requester_nexus_id(ctx, deps),
        )
        if not plugin or not plugin.add_assignee(issue_number, assignee):
            await ctx.edit_message_text(
                message_id=msg_id,
                text=f"❌ Failed to assign issue #{issue_number}",
            )
            return

        display_assignee = assignee
        if display_assignee == "@me":
            display_assignee = "you (@me)"
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"✅ Issue #{issue_number} assigned to {display_assignee}!\n\n{issue_url}",
        )
    except Exception as exc:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"❌ Failed to assign issue #{issue_number}\n\nError: {exc}",
        )


async def implement_handler(ctx: InteractiveContext, deps: IssueHandlerDeps) -> None:
    deps.logger.info(f"Implement requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "implement")
        return

    project_key, issue_number, _ = await deps.ensure_project_issue(ctx, "implement")
    if not project_key:
        return

    repo = deps.project_repo(project_key)
    issue_url = deps.project_issue_url(project_key, issue_number)

    msg_id = await ctx.reply_text(
        f"🔔 Requesting AI Agent implementation for issue #{issue_number}..."
    )

    try:
        plugin = _resolve_direct_issue_plugin(
            deps,
            repo,
            _resolve_requester_nexus_id(ctx, deps),
        )
        if not plugin:
            await ctx.edit_message_text(
                message_id=msg_id,
                text="❌ Failed to initialize Git issue plugin",
            )
            return

        plugin.ensure_label(
            "agent:requested",
            "E6E6FA",
            "Requested AI Agent implementation",
        )
        if not plugin.add_label(issue_number, "agent:requested"):
            await ctx.edit_message_text(
                message_id=msg_id,
                text=f"❌ Failed to request implementation for issue #{issue_number}.",
            )
            return

        comment = (
            "🤖 **Implementation Request** — AI implementation has been requested via chat command.\n\n"
            "Please review the issue and add the label `agent:approved` to start implementation.\n\n"
            f"Issue: {issue_url}"
        )

        if not plugin.add_comment(issue_number, comment):
            await ctx.edit_message_text(
                message_id=msg_id,
                text=f"❌ Failed to request implementation for issue #{issue_number}.",
            )
            return

        await ctx.edit_message_text(
            message_id=msg_id,
            text=(
                f"✅ Requested implementation for issue #{issue_number}. "
                f"The assigned agent will process this upon approval.\n\n{issue_url}"
            ),
        )
    except Exception as exc:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"❌ Failed to request implementation for issue #{issue_number}.\n\nError: {exc}",
        )


async def plan_handler(ctx: InteractiveContext, deps: IssueHandlerDeps) -> None:
    deps.logger.info(f"Plan requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    project_keys = [
        str(key).strip().lower()
        for key, value in (deps.project_config or {}).items()
        if isinstance(value, dict) and str(key).strip().lower() != "system_operations"
    ]

    if not ctx.args:
        usage = "Usage: /plan <project> <planning request>"
        if len(project_keys) == 1:
            usage = f"Usage: /plan <planning request>\nDefault project: {project_keys[0]}"
        await ctx.reply_text(usage)
        return

    request_tokens = [str(token or "").strip() for token in list(ctx.args or []) if str(token or "").strip()]
    project_key: str | None = None
    if request_tokens:
        first_arg = request_tokens[0].lower()
        if first_arg in project_keys:
            project_key = first_arg
            request_tokens = request_tokens[1:]
        elif len(project_keys) == 1:
            project_key = project_keys[0]

    request_text = " ".join(request_tokens).strip()
    if not project_key or not request_text:
        usage = "Usage: /plan <project> <planning request>"
        if len(project_keys) == 1:
            usage = f"Usage: /plan <planning request>\nDefault project: {project_keys[0]}"
        await ctx.reply_text(usage)
        return

    requester_context = None
    if callable(deps.requester_context_builder):
        try:
            requester_context = deps.requester_context_builder(int(str(ctx.user_id)))
        except Exception:
            requester_context = None
    if not isinstance(requester_context, dict):
        requester_context = {}

    trigger_message_id = ""
    if hasattr(ctx.raw_event, "message_id"):
        trigger_message_id = str(ctx.raw_event.message_id or "")
    elif hasattr(ctx.raw_event, "message") and hasattr(ctx.raw_event.message, "message_id"):
        trigger_message_id = str(ctx.raw_event.message.message_id or "")
    if not trigger_message_id:
        trigger_message_id = f"plan-{int(time.time() * 1000)}"

    result = await deps.create_planning_task(
        text=request_text,
        project_key=project_key,
        message_id=trigger_message_id,
        requester_context=requester_context,
    )
    await ctx.reply_text(result.get("message", "⚠️ Failed to queue planning task."))


async def prepare_handler(ctx: InteractiveContext, deps: IssueHandlerDeps) -> None:
    deps.logger.info(f"Prepare requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "prepare")
        return

    project_key, issue_number, _ = await deps.ensure_project_issue(ctx, "prepare")
    if not project_key:
        return

    repo = deps.project_repo(project_key)
    issue_url = deps.project_issue_url(project_key, issue_number)

    msg_id = await ctx.reply_text(f"🔧 Preparing issue #{issue_number} for AI implementation...")

    try:
        plugin = _resolve_direct_issue_plugin(
            deps,
            repo,
            _resolve_requester_nexus_id(ctx, deps),
        )
        if not plugin:
            await ctx.edit_message_text(
                message_id=msg_id,
                text="❌ Failed to initialize Git issue plugin",
            )
            return

        data = plugin.get_issue(issue_number, ["body", "title"])
        if not data:
            await ctx.edit_message_text(
                message_id=msg_id,
                text=f"❌ Failed to prepare issue #{issue_number}.",
            )
            return

        body = data.get("body", "")

        branch_match = re.search(r"Target Branch:\s*`([^`]+)`", body)
        taskfile_match = re.search(r"Task File:\s*`([^`]+)`", body)
        branch_name = branch_match.group(1) if branch_match else "<create-branch>"
        task_file = taskfile_match.group(1) if taskfile_match else None

        copilot_block = f"""
## AI Agent Instructions

- Follow existing repository style and tests.
- Create a branch: `{branch_name}` and open a PR against the appropriate base branch.
- Include unit tests or update existing tests when applicable.
- Keep changes minimal and focused; reference the task file if present.
"""

        if task_file:
            copilot_block += f"\n**Suggested files to modify:** `{task_file}`\n"

        copilot_block += (
            "\n**Acceptance Criteria**\n- Add concise acceptance criteria here (one per line).\n"
        )

        new_body = body + "\n\n---\n\n" + copilot_block

        if not plugin.update_issue_body(issue_number, new_body):
            await ctx.edit_message_text(
                message_id=msg_id,
                text=f"❌ Failed to prepare issue #{issue_number}.",
            )
            return

        await ctx.edit_message_text(
            message_id=msg_id,
            text=(
                f"✅ Prepared issue #{issue_number} for AI implementation. You can now add the "
                f"`agent:approved` label to start the agent.\n\n{issue_url}"
            ),
        )
    except Exception as exc:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"❌ Failed to prepare issue #{issue_number}.\n\nError: {exc}",
        )


async def track_handler(ctx: InteractiveContext, deps: IssueHandlerDeps) -> None:
    deps.logger.info(f"Track requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await ctx.reply_text(
            "⚠️ Usage:\n"
            "/track <issue#> - Track issue globally\n"
            "/track <project> <issue#> - Track issue per-project\n\n"
            f"Projects: {', '.join(deps.track_short_projects)}\n\n"
            "Examples:\n"
            "  /track 123\n"
            "  /track nxs 456"
        )
        return

    if len(ctx.args) >= 2:
        project = ctx.args[0].lower()
        issue_num = ctx.args[1].lstrip("#")

        if project not in deps.track_short_projects:
            await ctx.reply_text(
                f"❌ Invalid project '{project}'.\n"
                f"Valid projects: {', '.join(deps.track_short_projects)}"
            )
            return

        if not issue_num.isdigit():
            await ctx.reply_text("❌ Invalid issue number.")
            return

        user = deps.user_manager.get_or_create_user_by_identity(
            platform=ctx.platform,
            platform_user_id=str(ctx.user_id),
            username=f"user_{ctx.user_id}",
            first_name="User",
        )
        deps.user_manager.track_issue_by_nexus_id(
            nexus_id=user.nexus_id,
            project=project,
            issue_number=issue_num,
        )

        await ctx.reply_text(
            f"👁️ Now tracking {project.upper()} issue #{issue_num} for you\n\n"
            "Use /myissues to see all your tracked issues\n"
            f"Use /untrack {project} {issue_num} to stop tracking"
        )
    else:
        issue_num = ctx.args[0].lstrip("#")
        if not issue_num.isdigit():
            await ctx.reply_text("❌ Invalid issue number.")
            return

        details = _resolve_issue_details(
            deps,
            issue_num,
            requester_nexus_id=_resolve_requester_nexus_id(ctx, deps),
        )
        issue_title = details.get("title", "") if isinstance(details, dict) else ""

        deps.tracked_issues_ref[issue_num] = {
            "project": "global",
            "status": "active",
            "description": issue_title,
            "added_at": datetime.now().isoformat(),
            "last_seen_state": None,
            "last_seen_labels": [],
        }
        deps.save_tracked_issues(deps.tracked_issues_ref)

        if details:
            await ctx.reply_text(
                f"👁️ Now tracking issue #{issue_num} (global)\n\n"
                f"Title: {details.get('title', 'N/A')}\n"
                f"Status: {details.get('state', 'N/A')}\n"
                f"Labels: {', '.join([l['name'] for l in details.get('labels', [])])}\n\n"
                f"🔗 {deps.default_issue_url(issue_num)}\n\n"
                "💡 Tip: Use /track <project> <issue#> for per-project tracking"
            )
        else:
            await ctx.reply_text(
                "⚠️ Could not fetch issue details, but tracking started.\n\n"
                f"🔗 {deps.default_issue_url(issue_num)}"
            )


async def untrack_handler(ctx: InteractiveContext, deps: IssueHandlerDeps) -> None:
    deps.logger.info(f"Untrack requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "untrack")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "untrack")
    if not project_key:
        return

    nexus_id = deps.user_manager.resolve_nexus_id(ctx.platform, str(ctx.user_id))
    success = (
        deps.user_manager.untrack_issue_by_nexus_id(
            nexus_id=nexus_id,
            project=project_key,
            issue_number=issue_num,
        )
        if nexus_id
        else False
    )

    if success:
        await ctx.reply_text(
            f"✅ Stopped tracking {deps.get_project_label(project_key)} issue #{issue_num}"
        )
    else:
        await ctx.reply_text(
            f"❌ You weren't tracking {deps.get_project_label(project_key)} issue #{issue_num}"
        )


async def myissues_handler(ctx: InteractiveContext, deps: IssueHandlerDeps) -> None:
    deps.logger.info(f"My issues requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    nexus_id = deps.user_manager.resolve_nexus_id(ctx.platform, str(ctx.user_id))
    tracked = deps.user_manager.get_user_tracked_issues_by_nexus_id(nexus_id) if nexus_id else {}

    if not tracked:
        await ctx.reply_text(
            "📋 You're not tracking any issues yet.\n\n"
            "Use /track <project> <issue#> to start tracking.\n\n"
            "Examples:\n"
            "  /track prj 123\n"
            "  /track nxs 456"
        )
        return

    message = "📋 <b>Your Tracked Issues</b>\n\n"

    total_issues = 0
    for project, issues in sorted(tracked.items()):
        if issues:
            message += f"<b>{project.upper()}</b>\n"
            for issue_num in issues:
                total_issues += 1
                message += f"  • #{issue_num}\n"
            message += "\n"

    message += f"<b>Total:</b> {total_issues} issue(s)\n\n"
    message += "<i>Use /untrack &lt;project&gt; &lt;issue#&gt; to stop tracking</i>"

    await ctx.reply_text(message)


async def tracked_handler(ctx: InteractiveContext, deps: IssueHandlerDeps) -> None:
    """Show globally tracked issues stored in HostStateManager."""
    deps.logger.info(f"Global tracked issues requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    tracked = deps.tracked_issues_ref or {}
    if not tracked:
        await ctx.reply_text("📌 No globally tracked issues.\n\n" "Use /track <issue#> to add one.")
        return

    lines = ["📌 <b>Global Tracked Issues</b>", ""]
    active_total = 0
    for issue_num, issue_data in sorted(
        tracked.items(), key=lambda item: int(item[0]) if str(item[0]).isdigit() else 10**9
    ):
        issue_payload = issue_data if isinstance(issue_data, dict) else {}
        status = str(issue_payload.get("status", "active")).strip().lower() or "active"
        if status in {"done", "closed", "resolved", "completed", "implemented", "rejected"}:
            continue
        project = str(issue_payload.get("project", "global")).strip() or "global"
        active_total += 1
        lines.append(f"• #{issue_num} ({project}) — {status}")

    if active_total == 0:
        await ctx.reply_text(
            "📌 No active globally tracked issues.\n\n" "Use /track <issue#> to add one."
        )
        return

    lines.append("")
    lines.append(f"<b>Active:</b> {active_total}")
    await ctx.reply_text("\n".join(lines))


async def comments_handler(ctx: InteractiveContext, deps: IssueHandlerDeps) -> None:
    deps.logger.info(f"Comments requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "comments")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "comments")
    if not project_key:
        return

    repo = deps.project_repo(project_key)
    issue_url = deps.project_issue_url(project_key, issue_num)

    msg_id = await ctx.reply_text(f"💬 Fetching comments for issue #{issue_num}...")

    try:
        plugin = _resolve_direct_issue_plugin(
            deps,
            repo,
            _resolve_requester_nexus_id(ctx, deps),
        )
        if not plugin:
            await ctx.edit_message_text(
                message_id=msg_id,
                text=f"❌ Failed to fetch comments for issue #{issue_num}",
            )
            return

        data = plugin.get_issue(issue_num, ["comments", "title"])
        if not data:
            await ctx.edit_message_text(
                message_id=msg_id,
                text=f"❌ Failed to fetch comments for issue #{issue_num}",
            )
            return

        title = data.get("title", "Unknown")
        comments = data.get("comments", [])

        if not comments:
            await ctx.edit_message_text(
                message_id=msg_id,
                text=(
                    f"💬 **Issue #{issue_num}: {title}**\n\n"
                    "No comments yet.\n\n"
                    f"🔗 {issue_url}"
                ),
            )
            return

        comments_text = f"💬 **Issue #{issue_num}: {title}**\n\n"
        comments_text += f"Total comments: {len(comments)}\n\n"

        recent_comments = comments[-5:]
        for comment in recent_comments:
            author_data = comment.get("author")
            if isinstance(author_data, dict):
                author = author_data.get("login", "unknown")
            else:
                author = author_data or "unknown"
            created = comment.get("created") or comment.get("createdAt", "")
            body = comment.get("body", "")

            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                time_str = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                time_str = created

            preview = body[:200] + "..." if len(body) > 200 else body
            comments_text += f"**{author}** ({time_str}):\n{preview}\n\n"

        if len(comments) > 5:
            comments_text += f"_...and {len(comments) - 5} more comments_\n\n"

        comments_text += f"🔗 {issue_url}"

        max_len = 3500
        if len(comments_text) <= max_len:
            await ctx.edit_message_text(
                message_id=msg_id,
                text=comments_text,
            )
        else:
            chunks = [comments_text[i : i + max_len] for i in range(0, len(comments_text), max_len)]
            await ctx.edit_message_text(
                message_id=msg_id,
                text=chunks[0],
            )
            for part in chunks[1:]:
                await ctx.reply_text(text=part)

    except Exception as exc:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"❌ Error: {exc}",
        )


async def respond_handler(ctx: InteractiveContext, deps: IssueHandlerDeps) -> None:
    deps.logger.info(f"Respond requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "respond")
        return

    project_key, issue_num, rest = await deps.ensure_project_issue(ctx, "respond")
    if not project_key:
        return
    if not rest:
        await ctx.reply_text("⚠️ Please include a response message.")
        return

    response_text = " ".join(rest)

    msg_id = await ctx.reply_text(f"📝 Posting response to issue #{issue_num}...")

    try:
        details = None
        requester_nexus_id = _resolve_requester_nexus_id(ctx, deps)
        repo = deps.project_repo(project_key)
        task_file = (
            None
            if is_postgres_backend(NEXUS_STORAGE_BACKEND)
            else deps.find_task_file_by_issue(issue_num)
        )
        if not task_file:
            details = _resolve_issue_details(
                deps,
                issue_num,
                repo=repo,
                requester_nexus_id=requester_nexus_id,
            )
            if details and not is_postgres_backend(NEXUS_STORAGE_BACKEND):
                body = details.get("body", "")
                match = re.search(r"Task File:\s*`([^`]+)`", body)
                task_file = match.group(1) if match else None

        if is_postgres_backend(NEXUS_STORAGE_BACKEND):
            project_name = project_key
            project_cfg = (
                deps.project_config.get(project_key)
                if isinstance(deps.project_config, dict)
                else None
            )
            config = project_cfg if isinstance(project_cfg, dict) else None
            if not config or not config.get("agents_dir") or not config.get("workspace"):
                await ctx.reply_text(
                    "⚠️ Posted comment but no agents/workspace config for project."
                )
                return
            repo = deps.resolve_repo(config, deps.default_repo)
        else:
            if not task_file or not os.path.exists(task_file):
                await ctx.reply_text(
                    "⚠️ Posted comment but couldn't find task file to continue agent."
                )
                return

            project_name, config = deps.resolve_project_config_from_task(task_file)
            if not config or not config.get("agents_dir"):
                await ctx.reply_text("⚠️ Posted comment but no agents config for project.")
                return

            repo = deps.resolve_repo(config, deps.default_repo)

        plugin = _resolve_direct_issue_plugin(
            deps,
            repo,
            requester_nexus_id,
        )
        if not plugin or not plugin.add_comment(issue_num, response_text):
            await ctx.edit_message_text(
                message_id=msg_id,
                text=f"❌ Failed to post response to issue #{issue_num}.",
            )
            return

        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"✅ Response posted to issue #{issue_num}.\n\n🤖 Continuing agent...",
        )

        if not details:
            details = _resolve_issue_details(
                deps,
                issue_num,
                repo=repo,
                requester_nexus_id=requester_nexus_id,
            )
            if not details:
                await ctx.reply_text(
                    "⚠️ Posted comment but couldn't fetch issue details to continue agent."
                )
                return

        if is_postgres_backend(NEXUS_STORAGE_BACKEND):
            issue_body = str(details.get("body") or "") if isinstance(details, dict) else ""
            issue_title = str(details.get("title") or "") if isinstance(details, dict) else ""
            content = (
                f"# {issue_title}\n\n{issue_body}"
                if issue_title and issue_body
                else (issue_body or issue_title or f"Issue #{issue_num}")
            )
            task_type = "feature"
            labels = details.get("labels") if isinstance(details, dict) else None
            if isinstance(labels, list):
                for label in labels:
                    name = (
                        str(label.get("name") if isinstance(label, dict) else label or "")
                        .strip()
                        .lower()
                    )
                    if name.startswith("type:"):
                        candidate = name.split(":", 1)[1].strip()
                        if candidate:
                            task_type = candidate
                            break
        else:
            with open(task_file, encoding="utf-8") as handle:
                content = handle.read()

            type_match = re.search(r"\*\*Type:\*\*\s*(.+)", content)
            task_type = type_match.group(1).strip().lower() if type_match else "feature"

        tier_name, _, _ = deps.get_sop_tier(task_type)
        issue_url = deps.build_issue_url(repo, issue_num, config)

        agents_abs = os.path.join(deps.base_dir, config["agents_dir"])
        workspace_abs = os.path.join(deps.base_dir, config["workspace"])

        continuation_prompt = (
            f"@Ghabs has provided input:\n\n{response_text}\n\n"
            "Please proceed with the next step of the workflow."
        )

        log_subdir = project_name
        pid, tool_used = deps.invoke_ai_agent(
            agents_dir=agents_abs,
            workspace_dir=workspace_abs,
            issue_url=issue_url,
            tier_name=tier_name,
            task_content=content,
            continuation=True,
            continuation_prompt=continuation_prompt,
            log_subdir=log_subdir,
            project_name=log_subdir,
            requester_nexus_id=requester_nexus_id,
        )

        if pid:
            await ctx.reply_text(
                f"✅ Agent resumed for issue #{issue_num} (PID: {pid}, Tool: {tool_used})\n\n"
                f"Check /logs {issue_num} to monitor progress.\n\n"
                f"🔗 {issue_url}"
            )
        else:
            await ctx.reply_text(
                f"⚠️ Response posted but failed to continue agent.\n"
                f"Use /continue {issue_num} to resume manually.\n\n"
                f"🔗 {issue_url}"
            )

    except subprocess.TimeoutExpired:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"❌ Timeout posting comment to issue #{issue_num}",
        )
    except subprocess.CalledProcessError as exc:
        error = exc.stderr if exc.stderr else str(exc)
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"❌ Failed to post comment: {error}",
        )
    except Exception as exc:
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"❌ Error: {exc}",
        )
