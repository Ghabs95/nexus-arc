"""Workflow control commands: pause, resume, stop, continue, new."""

import asyncio
import logging

from nexus.core.audit_store import AuditStore
from nexus.core.config import NEXUS_CORE_STORAGE_DIR, PROJECT_CONFIG, TELEGRAM_ALLOWED_USER_IDS
from nexus.core.integrations.workflow_state_factory import get_workflow_state
from nexus.core.interactive.context import InteractiveContext
from nexus.core.orchestration.nexus_core_helpers import get_git_platform
from nexus.core.orchestration.plugin_runtime import (
    get_profiled_plugin,
    get_runtime_ops_plugin,
    get_workflow_state_plugin,
)
from nexus.core.project.key_utils import normalize_project_key_str as _normalize_project_key
from nexus.core.state_manager import HostStateManager

logger = logging.getLogger(__name__)

_issue_plugin_cache = {}
_WORKFLOW_STATE_PLUGIN_KWARGS = {
    "storage_dir": NEXUS_CORE_STORAGE_DIR,
    "issue_to_workflow_id": lambda n: get_workflow_state().get_workflow_id(n),
}


def _get_project_repo(project_key: str) -> str:
    cfg = PROJECT_CONFIG.get(project_key, {})
    if isinstance(cfg, dict) and cfg.get("repo_key"):
        return cfg["repo_key"]
    raise ValueError(f"Unknown project '{project_key}'")


def _get_issue_plugin(repo: str):
    """Return a configured Git issue plugin for the repo."""
    if repo in _issue_plugin_cache:
        return _issue_plugin_cache[repo]

    plugin = get_profiled_plugin(
        "git_workflow",
        overrides={
            "repo": repo,
        },
        cache_key=f"github:workflow:{repo}",
    )
    if plugin:
        _issue_plugin_cache[repo] = plugin
    return plugin


async def pause_handler(ctx: InteractiveContext):
    """Pause auto-chaining for a workflow."""
    if TELEGRAM_ALLOWED_USER_IDS and int(ctx.user_id) not in TELEGRAM_ALLOWED_USER_IDS:
        return

    if not ctx.args or len(ctx.args) < 2:
        await ctx.reply_text("⚠️ Usage: /pause <project> <issue#>")
        return

    project_key = _normalize_project_key(ctx.args[0])
    if project_key not in PROJECT_CONFIG:
        await ctx.reply_text("❌ Invalid project.")
        return

    issue_num = ctx.args[1].lstrip("#")
    if not issue_num.isdigit():
        await ctx.reply_text("❌ Invalid issue number.")
        return

    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_KWARGS,
        cache_key="workflow:state-engine",
    )

    # Idempotent pause: report success when workflow is already paused.
    current = await workflow_plugin.get_workflow_status(issue_num)
    if current and str(current.get("state", "")).strip().lower() == "paused":
        await ctx.reply_text(
            f"⏸️ Workflow for issue #{issue_num} is already paused.\n\n"
            f"Use /resume {project_key} {issue_num} to re-enable auto-chaining."
        )
        return

    success = await workflow_plugin.pause_workflow(
        issue_num,
        reason="User requested via Telegram",
    )
    if not success:
        await ctx.reply_text(f"⚠️ Unable to pause workflow for issue #{issue_num}.")
        return

    AuditStore.audit_log(int(issue_num), "WORKFLOW_PAUSED", "via nexus-arc")

    status = await workflow_plugin.get_workflow_status(issue_num)
    status_text = ""
    if status:
        status_text = (
            f"\n\n**Workflow:** {status['name']}\n"
            f"**Step:** {status['current_step']}/{status['total_steps']} - {status['current_step_name']}"
        )

    await ctx.reply_text(
        f"⏸️ **Workflow paused for issue #{issue_num}**{status_text}\n\n"
        f"Auto-chaining is disabled. Agents can still complete work, but the next agent won't be launched automatically.\n\n"
        f"Use /resume {project_key} {issue_num} to re-enable auto-chaining."
    )


async def resume_handler(ctx: InteractiveContext):
    """Resume auto-chaining for a paused workflow."""
    if TELEGRAM_ALLOWED_USER_IDS and int(ctx.user_id) not in TELEGRAM_ALLOWED_USER_IDS:
        return

    if not ctx.args or len(ctx.args) < 2:
        await ctx.reply_text("⚠️ Usage: /resume <project> <issue#>")
        return

    project_key = _normalize_project_key(ctx.args[0])
    if project_key not in PROJECT_CONFIG:
        await ctx.reply_text("❌ Invalid project.")
        return

    issue_num = ctx.args[1].lstrip("#")
    if not issue_num.isdigit():
        await ctx.reply_text("❌ Invalid issue number.")
        return

    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_KWARGS,
        cache_key="workflow:state-engine",
    )
    status_before = await workflow_plugin.get_workflow_status(issue_num)
    state_before = str((status_before or {}).get("state", "")).strip().lower()

    if state_before and state_before != "paused":
        if state_before == "running":
            await ctx.reply_text(
                f"ℹ️ Workflow for issue #{issue_num} is already running.\n"
                f"Use /continue {project_key} {issue_num} to launch/relaunch the expected agent."
            )
            return
        if state_before == "failed":
            await ctx.reply_text(
                f"⚠️ Workflow for issue #{issue_num} is in `failed` state.\n"
                f"`/resume` only applies to paused workflows.\n\n"
                f"Use /wfstate {project_key} {issue_num} to inspect drift, then /continue {project_key} {issue_num} or /reprocess {project_key} {issue_num}."
            )
            return
        if state_before in {"completed", "cancelled"}:
            await ctx.reply_text(
                f"ℹ️ Workflow for issue #{issue_num} is `{state_before}`.\n"
                f"`/resume` is not applicable."
            )
            return

    success = await workflow_plugin.resume_workflow(issue_num)
    if not success:
        details = await workflow_plugin.get_workflow_status(issue_num)
        state_after = str((details or {}).get("state", "unknown")).strip().lower() or "unknown"
        await ctx.reply_text(
            f"⚠️ Unable to resume workflow for issue #{issue_num}.\n"
            f"Current state: `{state_after}`."
        )
        return

    AuditStore.audit_log(int(issue_num), "WORKFLOW_RESUMED", "via nexus-arc")

    status = await workflow_plugin.get_workflow_status(issue_num)
    status_text = ""
    if status:
        status_text = (
            f"\n\n**Workflow:** {status['name']}\n"
            f"**Step:** {status['current_step']}/{status['total_steps']} - {status['current_step_name']}"
        )

    await ctx.reply_text(
        f"▶️ **Workflow resumed for issue #{issue_num}**{status_text}\n\n"
        f"Auto-chaining is re-enabled. This command does not launch an agent immediately.\n"
        f"Use /continue {project_key} {issue_num} to launch the next agent now, or wait for the current step to complete.\n"
        f"Check /active to see current progress."
    )


async def stop_handler(ctx: InteractiveContext):
    """Stop a workflow and close the issue."""
    if TELEGRAM_ALLOWED_USER_IDS and int(ctx.user_id) not in TELEGRAM_ALLOWED_USER_IDS:
        return

    if not ctx.args or len(ctx.args) < 2:
        await ctx.reply_text("⚠️ Usage: /stop <project> <issue#>")
        return

    project_key = _normalize_project_key(ctx.args[0])
    if project_key not in PROJECT_CONFIG:
        await ctx.reply_text("❌ Invalid project.")
        return

    issue_num = ctx.args[1].lstrip("#")
    if not issue_num.isdigit():
        await ctx.reply_text("❌ Invalid issue number.")
        return

    # Kill any running agent first
    runtime_ops = get_runtime_ops_plugin(cache_key="runtime-ops:workflow")
    pid = runtime_ops.find_agent_pid_for_issue(issue_num) if runtime_ops else None
    if pid and runtime_ops:
        if runtime_ops.kill_process(pid, force=True):
            logger.info(f"Killed agent PID {pid} for issue #{issue_num}")
        else:
            logger.error(f"Failed to kill agent PID {pid} for issue #{issue_num}")

    # Prevent further auto-chaining by pausing workflow in nexus-arc
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_KWARGS,
        cache_key="workflow:state-engine",
    )
    paused_for_stop = await workflow_plugin.pause_workflow(
        issue_num,
        reason="Workflow stopped by user",
    )
    if not paused_for_stop:
        logger.warning(f"Could not pause workflow for issue #{issue_num} before closing")
    AuditStore.audit_log(int(issue_num), "WORKFLOW_STOPPED")

    # Remove from launched_agents tracker to prevent false dead-agent alerts
    launched = HostStateManager.load_launched_agents(recent_only=False)
    issue_key = str(issue_num)
    keys_to_remove = [
        key
        for key, value in launched.items()
        if key == issue_key
        or key.startswith(f"{issue_key}_")
        or (isinstance(value, dict) and str(value.get("issue", "")) == issue_key)
    ]
    for key in keys_to_remove:
        launched.pop(key, None)
    if keys_to_remove:
        HostStateManager.save_launched_agents(launched)
        logger.info(
            "Removed issue #%s from launched_agents tracker (keys=%s)",
            issue_num,
            ",".join(keys_to_remove),
        )

    # Close the Git issue
    issue_close_requested = False
    issue_close_success = False
    issue_state_verified: str | None = None
    try:
        repo = _get_project_repo(project_key)
        platform = get_git_platform(repo, project_name=project_key)
        issue_close_requested = True
        if not platform:
            raise RuntimeError("git platform unavailable")
        close_result = asyncio.run(
            platform.close_issue(
                issue_num,
                comment="Workflow stopped by user via /stop.",
            )
        )
        issue_close_success = bool(close_result)
        if issue_close_success:
            logger.info("Closed issue #%s", issue_num)
        else:
            logger.warning("close_issue returned falsy for issue #%s", issue_num)

        # Best-effort post-close verification for both GitHub/GitLab style plugins.
        issue_data = None
        try:
            issue_data = asyncio.run(platform.get_issue(issue_num))
        except Exception:
            issue_data = None

        if isinstance(issue_data, dict):
            state_raw = str(issue_data.get("state", "")).strip().lower()
        else:
            state_raw = str(getattr(issue_data, "state", "") or "").strip().lower()
        if state_raw:
            issue_state_verified = state_raw
            if state_raw != "closed":
                issue_close_success = False
    except Exception as e:
        logger.error("Failed to close issue #%s: %s", issue_num, e)

    if issue_close_success:
        close_text = "Issue closed"
    elif issue_close_requested and issue_state_verified:
        close_text = f"Issue close failed (current state: {issue_state_verified})"
    elif issue_close_requested:
        close_text = "Issue close failed (remote state unverified)"
    else:
        close_text = "Issue close skipped"

    await ctx.reply_text(
        f"🛑 **Workflow stopped for issue #{issue_num}**\n\n"
        f"Auto-chaining disabled.\n\n"
        f"Status: {pid and '✅ Agent killed' or '✅ No running agent'} | {close_text}"
    )
