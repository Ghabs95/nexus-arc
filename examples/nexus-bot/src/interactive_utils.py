"""Utility functions for interactive context."""

from collections.abc import Callable
from typing import Any

from nexus.adapters.notifications.base import Button
from nexus.core.interactive.context import InteractiveContext


async def prompt_project_selection(
    ctx: InteractiveContext,
    command: str,
    get_project_label: Callable[[str], str],
    project_keys: list[str],
) -> None:
    actions: list[list[Button]] = []
    for key in project_keys:
        actions.append([Button(label=get_project_label(key), callback_data=f"pickcmd:{command}:{key}")])
    actions.append([Button(label="❌ Close", callback_data="flow:close")])

    await ctx.reply_text(f"Select a project for /{command}:", buttons=actions)
    ctx.user_state["pending_command"] = command


async def prompt_issue_selection(
    ctx: InteractiveContext,
    command: str,
    project_key: str,
    get_recent_issues: Callable[[str], list[Any]],
) -> None:
    issues = get_recent_issues(project_key)
    actions: list[list[Button]] = []

    if not issues:
        await ctx.reply_text(f"No recent issues found for {project_key}.")
        return

    for issue in issues[:5]:  # show top 5
        actions.append(
            [
                Button(
                    label=f"#{issue['number']} {issue['title'][:20]}...",
                    callback_data=f"pickissue:{command}:{project_key}:{issue['number']}",
                )
            ]
        )
    actions.append([Button(label="❌ Close", callback_data="flow:close")])

    await ctx.reply_text(f"Select an issue for /{command} in {project_key}:", buttons=actions)


def parse_project_issue_args(args: list[str]) -> tuple[str | None, str | None, list[str]]:
    if len(args) < 2:
        return None, None, []
    # simple normalization (assume user typed project_key)
    project_key = args[0]
    issue_num = args[1].lstrip("#")
    rest = args[2:]
    return project_key, issue_num, rest


async def ensure_project_issue(
    ctx: InteractiveContext,
    command: str,
    project_keys: list[str],
    get_project_label: Callable[[str], str],
    normalize_project_key: Callable[[str], str],
    get_recent_issues: Callable[[str], list[Any]],
) -> tuple[str | None, str | None, list[str]]:
    project_key, issue_num, rest = parse_project_issue_args(ctx.args)

    if not project_key or not issue_num:
        if len(ctx.args) == 1:
            await _handle_single_partial_arg(
                ctx=ctx,
                command=command,
                project_keys=project_keys,
                get_project_label=get_project_label,
                normalize_project_key=normalize_project_key,
                get_recent_issues=get_recent_issues,
            )
        else:
            await prompt_project_selection(ctx, command, get_project_label, project_keys)
        return None, None, []

    if project_key not in project_keys:
        await ctx.reply_text(f"❌ Unknown project '{project_key}'.")
        return None, None, []

    if not issue_num.isdigit():
        await ctx.reply_text("❌ Invalid issue number.")
        return None, None, []

    return project_key, issue_num, rest


async def _handle_single_partial_arg(
    *,
    ctx: InteractiveContext,
    command: str,
    project_keys: list[str],
    get_project_label: Callable[[str], str],
    normalize_project_key: Callable[[str], str],
    get_recent_issues: Callable[[str], list[Any]],
) -> None:
    arg = ctx.args[0]
    maybe_issue = arg.lstrip("#")
    if maybe_issue.isdigit():
        # Just an issue number — still need project selection
        ctx.user_state["pending_issue"] = maybe_issue
        await prompt_project_selection(ctx, command, get_project_label, project_keys)
        return

    # Might be a project key — show issue list for that project
    normalized = normalize_project_key(arg)
    if normalized and normalized in project_keys:
        ctx.user_state["pending_command"] = command
        ctx.user_state["pending_project"] = normalized
        await prompt_issue_selection(ctx, command, normalized, get_recent_issues)
        return

    await prompt_project_selection(ctx, command, get_project_label, project_keys)
