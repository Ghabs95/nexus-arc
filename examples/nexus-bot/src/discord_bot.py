import glob
import io
import logging
import os
import shlex
import sys
from datetime import datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from nexus.core.config.bootstrap import initialize_runtime
from nexus.core.utils.logging_filters import install_secret_redaction

initialize_runtime(configure_logging=False)

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

from nexus.core.config import (
    AI_PERSONA,
    BASE_DIR,
    DISCORD_ALLOWED_USER_IDS,
    DISCORD_GUILD_ID,
    DISCORD_TOKEN,
    NEXUS_AUTH_ENABLED,
    NEXUS_GITHUB_CLIENT_ID,
    NEXUS_GITLAB_CLIENT_ID,
    NEXUS_PUBLIC_BASE_URL,
    ORCHESTRATOR_CONFIG,
    PROJECT_CONFIG,
    get_chat_agent_types,
    get_inbox_dir,
    get_repos,
)
from nexus.core.project.key_utils import normalize_project_key_str as _normalize_project_key
from nexus.core.telegram.telegram_issue_selection_service import (
    parse_project_issue_args as _parse_project_issue_args,
)

install_secret_redaction([DISCORD_TOKEN or ""], logging.getLogger())

from nexus.core.handlers.common_routing import (
    extract_json_dict,
    parse_intent_result,
    route_task_with_context,
    run_conversation_turn,
)
from nexus.core.handlers.feature_ideation_handlers import (
    FeatureIdeationHandlerDeps,
    _build_feature_suggestions,
    detect_feature_project,
    is_feature_ideation_request,
)
from nexus.core.handlers.inbox_routing_handler import PROJECTS as ROUTING_PROJECTS
from nexus.core.handlers.inbox_routing_handler import TYPES as ROUTING_TASK_TYPES
from nexus.core.handlers.inbox_routing_handler import process_inbox_task, save_resolved_task
from nexus.core.handlers.monitoring_command_handlers import (
    active_handler as monitoring_active_handler,
)
from nexus.core.handlers.monitoring_command_handlers import (
    fuse_handler as monitoring_fuse_handler,
)
from nexus.core.handlers.monitoring_command_handlers import (
    logs_handler as monitoring_logs_handler,
)
from nexus.core.handlers.monitoring_command_handlers import (
    logsfull_handler as monitoring_logsfull_handler,
)
from nexus.core.handlers.monitoring_command_handlers import (
    tail_handler as monitoring_tail_handler,
)
from nexus.core.handlers.monitoring_command_handlers import (
    tailstop_handler as monitoring_tailstop_handler,
)
from nexus.core.handlers.ops_command_handlers import inboxq_handler as ops_inboxq_handler
from nexus.core.handlers.ops_command_handlers import audit_handler as ops_audit_handler
from nexus.core.handlers.ops_command_handlers import agents_handler as ops_agents_handler
from nexus.core.handlers.ops_command_handlers import direct_handler as ops_direct_handler
from nexus.core.handlers.ops_command_handlers import stats_handler as ops_stats_handler
from nexus.core.handlers.issue_command_handlers import assign_handler as issue_assign_handler
from nexus.core.handlers.issue_command_handlers import comments_handler as issue_comments_handler
from nexus.core.handlers.issue_command_handlers import (
    plan_handler as issue_plan_handler,
)
from nexus.core.handlers.issue_command_handlers import (
    prepare_handler as issue_prepare_handler,
)
from nexus.core.handlers.issue_command_handlers import respond_handler as issue_respond_handler
from nexus.core.handlers.issue_command_handlers import untrack_handler as issue_untrack_handler
from nexus.core.handlers.workflow_command_handlers import continue_handler as workflow_continue_handler
from nexus.core.handlers.workflow_command_handlers import forget_handler as workflow_forget_handler
from nexus.core.handlers.workflow_command_handlers import kill_handler as workflow_kill_handler
from nexus.core.handlers.workflow_command_handlers import pause_handler as workflow_pause_handler
from nexus.core.handlers.workflow_command_handlers import reconcile_handler as workflow_reconcile_handler
from nexus.core.handlers.workflow_command_handlers import reprocess_handler as workflow_reprocess_handler
from nexus.core.handlers.workflow_command_handlers import resume_handler as workflow_resume_handler
from nexus.core.handlers.workflow_command_handlers import stop_handler as workflow_stop_handler
from nexus.core.handlers.workflow_command_handlers import wfstate_handler as workflow_wfstate_handler
from nexus.core.handlers.visualize_command_handlers import visualize_handler as workflow_visualize_handler
from nexus.core.handlers.watch_command_handlers import watch_handler as workflow_watch_handler
from nexus.core.handlers.feature_registry_command_handlers import (
    feature_done_handler as feature_done_command_handler,
)
from nexus.core.handlers.feature_registry_command_handlers import (
    feature_forget_handler as feature_forget_command_handler,
)
from nexus.core.handlers.feature_registry_command_handlers import (
    feature_list_handler as feature_list_command_handler,
)
from nexus.core.orchestration.ai_orchestrator import get_orchestrator
from nexus.core.orchestration.plugin_runtime import get_profiled_plugin
from nexus.core.telegram.telegram_bootstrap_ui_service import build_help_text
from nexus.core.telegram.telegram_issue_selection_service import (
    issue_state_for_command as _svc_issue_state_for_command,
)
from nexus.core.telegram.telegram_issue_selection_service import (
    list_project_issues as _svc_list_project_issues,
)
from nexus.core.callbacks.callback_menu_service import menu_section_text as _shared_menu_section_text
from nexus.core.telegram.telegram_ui_prompts_service import resolve_issue_choices
from nexus.core.command_contract import (
    validate_command_parity,
    validate_required_command_interface,
)
from nexus.core.chat.chat_context_service import (
    CHAT_MODES,
    agent_display_label,
    agent_type_label,
    available_chat_agents,
    chat_context_summary,
)
from nexus.core.git.direct_issue_plugin_service import (
    get_direct_issue_plugin as _svc_get_direct_issue_plugin,
)
from nexus.core.project.catalog import (
    get_project_label as _svc_get_project_label,
)
from nexus.core.project.catalog import (
    get_project_workspace as _svc_get_project_workspace,
)
from nexus.core.project.catalog import (
    get_single_project_key as _svc_get_single_project_key,
)
from nexus.core.project.catalog import (
    iter_project_keys as _svc_iter_project_keys,
)
from nexus.core.telegram.telegram_handler_deps_service import (
    build_feature_ideation_handler_deps as _svc_build_feature_ideation_handler_deps,
)
from nexus.core.discord.discord_bridge_deps_service import (
    feature_registry_bridge_deps as _svc_feature_registry_bridge_deps,
)
from nexus.core.discord.discord_bridge_deps_service import (
    issue_bridge_deps as _svc_issue_bridge_deps,
)
from nexus.core.discord.discord_bridge_deps_service import (
    monitoring_bridge_deps as _svc_monitoring_bridge_deps,
)
from nexus.core.discord.discord_bridge_deps_service import (
    ops_bridge_deps as _svc_ops_bridge_deps,
)
from nexus.core.discord.discord_bridge_deps_service import (
    visualize_bridge_deps as _svc_visualize_bridge_deps,
)
from nexus.core.discord.discord_bridge_deps_service import (
    watch_bridge_deps as _svc_watch_bridge_deps,
)
from nexus.core.discord.discord_bridge_deps_service import (
    workflow_bridge_deps as _svc_workflow_bridge_deps,
)
from nexus.core.auth import (
    check_project_access as _svc_check_project_access,
)
from nexus.core.auth import (
    create_login_session_for_user as _svc_create_login_session_for_user,
)
from nexus.core.auth import (
    get_setup_status as _svc_get_setup_status,
)
from nexus.core.auth import register_onboarding_message as _svc_register_onboarding_message
from nexus.core.auth import (
    has_project_access as _svc_has_project_access,
)
from nexus.core.memory import (
    append_message,
    create_chat,
    delete_chat,
    get_active_chat,
    get_chat,
    get_chat_history,
    list_chats,
    rename_chat,
    set_active_chat,
    update_chat_metadata,
)
from nexus.core.state_manager import HostStateManager
from nexus.core.user_manager import get_user_manager

# --- SETUP BOT ---
intents = discord.Intents.default()
intents.message_content = str(
    os.getenv("DISCORD_ENABLE_MESSAGE_CONTENT_INTENT", "false")
).strip().lower() in {"1", "true", "yes", "on"}
bot = commands.Bot(command_prefix="!", intents=intents)

# Initialize Orchestrator
orchestrator = get_orchestrator(ORCHESTRATOR_CONFIG)
user_manager = get_user_manager()
_pending_project_resolution: dict[int, dict] = {}
_pending_feature_ideation: dict[int, dict] = {}
_pending_new_task_capture: dict[int, dict[str, str]] = {}
_discord_user_state: dict[int, dict[str, Any]] = {}


class DiscordInteractiveCtx:
    _DISCORD_MAX_MESSAGE_LEN = 2000

    def __init__(
        self,
        interaction: discord.Interaction,
        *,
        text: str,
        args: list[str],
    ) -> None:
        self.interaction = interaction
        self.user_id = str(interaction.user.id)
        self.chat_id = int(interaction.channel_id or 0)
        self.text = text
        self.args = list(args)
        self.raw_event = interaction
        self.user_state = _discord_user_state.setdefault(interaction.user.id, {})
        self.client = type("_Client", (), {"name": "discord"})()
        self.query = None

    async def reply_text(
        self,
        text: str,
        buttons=None,
        parse_mode: str | None = "Markdown",
        disable_web_page_preview: bool = True,
    ) -> str:
        content = str(text or "")
        if buttons:
            option_lines: list[str] = []
            for row in buttons:
                labels = [str(getattr(btn, "label", "")).strip() for btn in row]
                labels = [label for label in labels if label]
                if labels:
                    option_lines.append(" • " + " | ".join(labels))
            if option_lines:
                content = f"{content}\n\nOptions:\n" + "\n".join(option_lines)

        chunks = [
            content[i : i + self._DISCORD_MAX_MESSAGE_LEN]
            for i in range(0, len(content), self._DISCORD_MAX_MESSAGE_LEN)
        ] or [""]

        if not self.interaction.response.is_done():
            await self.interaction.response.send_message(chunks[0])
            sent = await self.interaction.original_response()
            for part in chunks[1:]:
                await self.interaction.followup.send(part)
            return str(sent.id)

        sent = await self.interaction.followup.send(chunks[0], wait=True)
        for part in chunks[1:]:
            await self.interaction.followup.send(part)
        return str(sent.id)

    async def edit_message_text(
        self,
        message_id: str,
        text: str,
        buttons=None,
        parse_mode: str | None = "Markdown",
        disable_web_page_preview: bool = True,
    ) -> None:
        content = str(text or "")
        chunks = [
            content[i : i + self._DISCORD_MAX_MESSAGE_LEN]
            for i in range(0, len(content), self._DISCORD_MAX_MESSAGE_LEN)
        ] or [""]
        try:
            if self.interaction.channel and str(message_id).isdigit():
                target = await self.interaction.channel.fetch_message(int(message_id))
                await target.edit(content=chunks[0])
                for part in chunks[1:]:
                    await self.interaction.followup.send(part)
                return
        except Exception:
            pass
        await self.interaction.followup.send(chunks[0])
        for part in chunks[1:]:
            await self.interaction.followup.send(part)

    async def answer_callback_query(self, text: str | None = None) -> None:
        return


async def _ctx_prompt_project_selection_discord(ctx: DiscordInteractiveCtx, command: str) -> None:
    options = ", ".join(_svc_iter_project_keys(project_config=PROJECT_CONFIG)) or "(none)"
    await ctx.reply_text(
        f"Usage: `/{command} <project> <issue#>`\nAvailable projects: {options}",
        parse_mode=None,
    )


async def _ctx_ensure_project_discord(ctx: DiscordInteractiveCtx, command: str) -> str | None:
    args = list(ctx.args or [])
    if not args:
        single = _svc_get_single_project_key(project_config=PROJECT_CONFIG)
        if single:
            return single
        await _ctx_prompt_project_selection_discord(ctx, command)
        return None
    candidate = _normalize_project_key(str(args[0]))
    if candidate in _svc_iter_project_keys(project_config=PROJECT_CONFIG):
        if NEXUS_AUTH_ENABLED:
            nexus_id = user_manager.resolve_nexus_id("discord", str(ctx.user_id))
            if not nexus_id:
                await ctx.reply_text("🔐 Run `/login` first.")
                return None
            allowed, error = _svc_check_project_access(str(nexus_id), str(candidate))
            if not allowed:
                await ctx.reply_text(error)
                return None
        return candidate
    await ctx.reply_text(f"❌ Unknown project '{args[0]}'.")
    return None


async def _ctx_ensure_project_issue_discord(
    ctx: DiscordInteractiveCtx, command: str
) -> tuple[str | None, str | None, list[str]]:
    args = list(ctx.args or [])
    project_key, issue_num, rest = _parse_project_issue_args(
        args=args,
        normalize_project_key=_normalize_project_key,
    )
    if not project_key or not issue_num:
        await _ctx_prompt_project_selection_discord(ctx, command)
        return None, None, []
    if project_key not in _svc_iter_project_keys(project_config=PROJECT_CONFIG):
        await ctx.reply_text(f"❌ Unknown project '{project_key}'.")
        return None, None, []
    if NEXUS_AUTH_ENABLED:
        nexus_id = user_manager.resolve_nexus_id("discord", str(ctx.user_id))
        if not nexus_id:
            await ctx.reply_text("🔐 Run `/login` first.")
            return None, None, []
        allowed, error = _svc_check_project_access(str(nexus_id), str(project_key))
        if not allowed:
            await ctx.reply_text(error)
            return None, None, []
    if not str(issue_num).isdigit():
        await ctx.reply_text("❌ Invalid issue number.")
        return None, None, []
    return project_key, issue_num, rest


def _monitoring_bridge_deps():
    return _svc_monitoring_bridge_deps(
        allowed_user_ids=DISCORD_ALLOWED_USER_IDS,
        ensure_project=_ctx_ensure_project_discord,
        ensure_project_issue=_ctx_ensure_project_issue_discord,
    )


def _ops_bridge_deps():
    return _svc_ops_bridge_deps(
        allowed_user_ids=DISCORD_ALLOWED_USER_IDS,
        prompt_project_selection=_ctx_prompt_project_selection_discord,
        ensure_project_issue=_ctx_ensure_project_issue_discord,
    )


def _issue_bridge_deps():
    return _svc_issue_bridge_deps(
        allowed_user_ids=DISCORD_ALLOWED_USER_IDS,
        prompt_project_selection=_ctx_prompt_project_selection_discord,
        ensure_project_issue=_ctx_ensure_project_issue_discord,
    )


def _visualize_bridge_deps():
    return _svc_visualize_bridge_deps(
        allowed_user_ids=DISCORD_ALLOWED_USER_IDS,
        prompt_project_selection=_ctx_prompt_project_selection_discord,
        ensure_project_issue=_ctx_ensure_project_issue_discord,
    )


def _watch_bridge_deps():
    return _svc_watch_bridge_deps(
        allowed_user_ids=DISCORD_ALLOWED_USER_IDS,
        prompt_project_selection=_ctx_prompt_project_selection_discord,
        ensure_project_issue=_ctx_ensure_project_issue_discord,
    )


def _workflow_bridge_deps():
    return _svc_workflow_bridge_deps(
        allowed_user_ids=DISCORD_ALLOWED_USER_IDS,
        prompt_project_selection=_ctx_prompt_project_selection_discord,
        ensure_project_issue=_ctx_ensure_project_issue_discord,
    )


def _feature_registry_bridge_deps():
    return _svc_feature_registry_bridge_deps(allowed_user_ids=DISCORD_ALLOWED_USER_IDS)


async def _run_bridge_handler(
    interaction: discord.Interaction,
    *,
    command_name: str,
    args: str,
    handler,
    deps_factory,
) -> None:
    parsed_args = shlex.split(str(args or "").strip()) if str(args or "").strip() else []
    await _run_bridge_handler_args(
        interaction,
        command_name=command_name,
        parsed_args=parsed_args,
        handler=handler,
        deps_factory=deps_factory,
    )


async def _run_bridge_handler_args(
    interaction: discord.Interaction,
    *,
    command_name: str,
    parsed_args: list[str],
    handler,
    deps_factory,
) -> None:
    if not check_permission_for_action(interaction.user.id, action="execute"):
        await interaction.response.send_message(
            _permission_denied_message(interaction.user.id, action="execute"),
            ephemeral=True,
        )
        return

    if NEXUS_AUTH_ENABLED and parsed_args:
        candidate_project = _normalize_project_key(str(parsed_args[0]))
        if candidate_project in _svc_iter_project_keys(project_config=PROJECT_CONFIG):
            nexus_id = user_manager.resolve_nexus_id("discord", str(interaction.user.id))
            if not nexus_id:
                await interaction.response.send_message("🔐 Run `/login` first.", ephemeral=True)
                return
            allowed, error = _svc_check_project_access(str(nexus_id), str(candidate_project))
            if not allowed:
                await interaction.response.send_message(error, ephemeral=True)
                return

    if not interaction.response.is_done():
        await interaction.response.defer(thinking=True)

    ctx = DiscordInteractiveCtx(
        interaction,
        text=f"/{command_name} {' '.join(parsed_args)}".strip(),
        args=list(parsed_args),
    )
    try:
        await handler(ctx, deps_factory())
    except Exception as exc:
        logging.getLogger(__name__).exception(
            "Discord command /%s failed for user %s", command_name, interaction.user.id
        )
        message = f"⚠️ /{command_name} failed: {exc}"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def _chunk_text(text: str, limit: int = 1800) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return [""]

    lines = normalized.splitlines(keepends=True)
    chunks: list[str] = []
    current = ""
    for line in lines:
        if len(current) + len(line) <= limit:
            current += line
            continue
        if current:
            chunks.append(current.rstrip())
            current = ""
        if len(line) <= limit:
            current = line
            continue

        start = 0
        while start < len(line):
            end = min(start + limit, len(line))
            chunks.append(line[start:end].rstrip())
            start = end

    if current:
        chunks.append(current.rstrip())

    return chunks or [normalized[:limit]]


async def _send_long_interaction_text(
    interaction: discord.Interaction,
    text: str,
    *,
    ephemeral: bool = True,
) -> None:
    chunks = _chunk_text(text)
    if not interaction.response.is_done():
        await interaction.response.send_message(chunks[0], ephemeral=ephemeral)
    else:
        await interaction.followup.send(chunks[0], ephemeral=ephemeral)

    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=ephemeral)


def _menu_category_text(category: str) -> str:
    if category == "help":
        return build_help_text()

    shared_key = category
    shared_text = _shared_menu_section_text(shared_key)
    if shared_text != "Unknown menu option.":
        return shared_text
    return "⚠️ Unknown menu category."


class CategoryMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⬅️ Back", style=discord.ButtonStyle.secondary, row=0)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
            return
        await interaction.response.edit_message(
            content="📍 **Nexus Menu**\nChoose a category:",
            view=RootMenuView(),
        )

    @discord.ui.button(label="❌ Close", style=discord.ButtonStyle.danger, row=0)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
            return
        await interaction.response.edit_message(content="✅ Menu closed.", view=None)


class RootMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _show_category(self, interaction: discord.Interaction, category: str):
        if not check_permission(interaction.user.id):
            await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
            return
        chunks = _chunk_text(_menu_category_text(category), limit=1800)
        await interaction.response.edit_message(
            content=chunks[0],
            view=CategoryMenuView(),
        )
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    @discord.ui.button(label="🗣️ Chat", style=discord.ButtonStyle.primary, row=0)
    async def chat_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_category(interaction, "chat")

    @discord.ui.button(label="✨ Task Creation", style=discord.ButtonStyle.primary, row=0)
    async def tasks_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_category(interaction, "tasks")

    @discord.ui.button(label="📊 Monitoring", style=discord.ButtonStyle.secondary, row=1)
    async def monitor_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_category(interaction, "monitor")

    @discord.ui.button(label="🔁 Workflow", style=discord.ButtonStyle.secondary, row=1)
    async def workflow_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_category(interaction, "workflow")

    @discord.ui.button(label="🤝 Agents", style=discord.ButtonStyle.secondary, row=2)
    async def agents_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_category(interaction, "agents")

    @discord.ui.button(label="🔧 Git Platform", style=discord.ButtonStyle.secondary, row=2)
    async def git_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_category(interaction, "git")

    @discord.ui.button(label="ℹ️ Help", style=discord.ButtonStyle.success, row=3)
    async def help_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_category(interaction, "help")

    @discord.ui.button(label="❌ Close", style=discord.ButtonStyle.danger, row=3)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
            return
        await interaction.response.edit_message(content="✅ Menu closed.", view=None)


class NewTaskTypeView(discord.ui.View):
    def __init__(self, user_id: int, project_key: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.project_key = project_key

        for type_key, type_label in ROUTING_TASK_TYPES.items():
            button = discord.ui.Button(
                label=str(type_label)[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"new:type:{type_key}",
            )
            button.callback = self._make_type_callback(type_key)
            self.add_item(button)

        cancel_btn = discord.ui.Button(
            label="❌ Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id="new:type:cancel",
        )
        cancel_btn.callback = self._cancel_callback
        self.add_item(cancel_btn)

    def _make_type_callback(self, type_key: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id or not check_permission_for_action(
                interaction.user.id, action="execute"
            ):
                await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
                return
            _pending_new_task_capture[interaction.user.id] = {
                "project": self.project_key,
                "type": type_key,
            }
            await interaction.response.edit_message(
                content=(
                    f"📝 Project: **{_svc_get_project_label(self.project_key, ROUTING_PROJECTS)}**\n"
                    f"Type: **{ROUTING_TASK_TYPES.get(type_key, type_key)}**\n\n"
                    "Send the task text in your next message.\n"
                    "Type `cancel` to abort."
                ),
                view=None,
            )

        return callback

    async def _cancel_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
            return
        _pending_new_task_capture.pop(interaction.user.id, None)
        await interaction.response.edit_message(content="❎ Task creation canceled.", view=None)


class NewTaskProjectView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        for project_key, project_label in sorted(ROUTING_PROJECTS.items()):
            button = discord.ui.Button(
                label=str(project_label)[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"new:project:{project_key}",
            )
            button.callback = self._make_project_callback(project_key)
            self.add_item(button)

        cancel_btn = discord.ui.Button(
            label="❌ Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id="new:project:cancel",
        )
        cancel_btn.callback = self._cancel_callback
        self.add_item(cancel_btn)

    def _make_project_callback(self, project_key: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.user_id or not check_permission_for_action(
                interaction.user.id, action="execute"
            ):
                await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
                return
            if NEXUS_AUTH_ENABLED:
                requester = _requester_context_for_discord_user(interaction.user)
                allowed, error = _authorize_project_for_requester(project_key, requester)
                if not allowed:
                    await interaction.response.send_message(error, ephemeral=True)
                    return
            await interaction.response.edit_message(
                content=(
                    f"📁 Selected project: **{_svc_get_project_label(project_key, ROUTING_PROJECTS)}**\n\n"
                    "Choose task type:"
                ),
                view=NewTaskTypeView(self.user_id, project_key),
            )

        return callback

    async def _cancel_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
            return
        _pending_new_task_capture.pop(interaction.user.id, None)
        await interaction.response.edit_message(content="❎ Task creation canceled.", view=None)


def _command_issue_state(command_name: str) -> str:
    return _svc_issue_state_for_command(command_name, allow_any=True)


def _list_issue_options_for_project(
    project_key: str,
    *,
    issue_state: str = "open",
    limit: int = 25,
) -> list[dict[str, str]]:
    return resolve_issue_choices(
        list_project_issues=lambda project_key, state, limit=25: _svc_list_project_issues(
            project_key=project_key,
            project_config=PROJECT_CONFIG,
            get_repos=get_repos,
            get_direct_issue_plugin=lambda repo: _svc_get_direct_issue_plugin(
                repo=repo,
                get_profiled_plugin=get_profiled_plugin,
            ),
            logger=logger,
            state=state,
            limit=limit,
        ),
        project_key=project_key,
        issue_state=issue_state,
        include_fallback=True,
        limit=limit,
    )


class CommandIssueSelect(discord.ui.Select):
    def __init__(
        self,
        project_key: str,
        command_name: str,
        handler,
        deps_factory,
        extra_args: list[str] | None = None,
        text_modal: dict[str, Any] | None = None,
        issue_state: str = "any",
    ):
        self.project_key = project_key
        self.command_name = command_name
        self.handler = handler
        self.deps_factory = deps_factory
        self.extra_args = list(extra_args or [])
        self.text_modal = dict(text_modal or {})
        self.issue_state = issue_state

        rows = _list_issue_options_for_project(project_key, issue_state=issue_state)
        select_options: list[discord.SelectOption] = []
        for row in rows[:25]:
            issue_num = row["number"]
            issue_title = row["title"] or "Issue"
            row_state = str(row.get("state") or issue_state).strip().lower()
            state_prefix = "🟢" if row_state == "open" else "⚫"
            label = f"{state_prefix} #{issue_num}"
            description = f"[{row_state}] {issue_title}"[:95] if issue_title else f"[{row_state}]"
            select_options.append(
                discord.SelectOption(label=label, value=issue_num, description=description)
            )

        if not select_options:
            select_options.append(
                discord.SelectOption(
                    label="No issues found", value="__none__", description="Type issue manually"
                )
            )

        super().__init__(
            placeholder="Select issue…",
            min_values=1,
            max_values=1,
            options=select_options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not check_permission(interaction.user.id):
            await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
            return

        issue_value = str(self.values[0])
        if issue_value == "__none__":
            await interaction.response.send_message(
                f"No issue choices available. Run `/{self.command_name} project:{self.project_key} issue:<number>`.",
                ephemeral=True,
            )
            return

        if self.text_modal:
            await interaction.response.send_modal(
                CommandTextModal(
                    command_name=self.command_name,
                    handler=self.handler,
                    deps_factory=self.deps_factory,
                    prefix_args=[self.project_key, issue_value, *self.extra_args],
                    title=str(self.text_modal.get("title") or f"/{self.command_name}"),
                    field_label=str(self.text_modal.get("label") or "Value"),
                    placeholder=str(self.text_modal.get("placeholder") or ""),
                    multiline=bool(self.text_modal.get("multiline", True)),
                    parse_mode=str(self.text_modal.get("parse_mode") or "append"),
                )
            )
            return

        await _run_bridge_handler_args(
            interaction,
            command_name=self.command_name,
            parsed_args=[self.project_key, issue_value, *self.extra_args],
            handler=self.handler,
            deps_factory=self.deps_factory,
        )


class CommandIssueSelectView(discord.ui.View):
    def __init__(
        self,
        project_key: str,
        command_name: str,
        handler,
        deps_factory,
        extra_args: list[str] | None = None,
        text_modal: dict[str, Any] | None = None,
        issue_state: str = "any",
    ):
        super().__init__(timeout=120)
        self.add_item(
            CommandIssueSelect(
                project_key,
                command_name,
                handler,
                deps_factory,
                extra_args=extra_args,
                text_modal=text_modal,
                issue_state=issue_state,
            )
        )


class CommandProjectSelect(discord.ui.Select):
    def __init__(
        self,
        command_name: str,
        handler,
        deps_factory,
        *,
        require_issue: bool,
        extra_args: list[str] | None = None,
        text_modal: dict[str, Any] | None = None,
        issue_state: str = "any",
    ):
        self.command_name = command_name
        self.handler = handler
        self.deps_factory = deps_factory
        self.require_issue = require_issue
        self.extra_args = list(extra_args or [])
        self.text_modal = dict(text_modal or {})
        self.issue_state = issue_state

        projects = _svc_iter_project_keys(project_config=PROJECT_CONFIG)[:25]
        select_options = [
            discord.SelectOption(
                label=_svc_get_project_label(project_key, ROUTING_PROJECTS)[:100],
                value=project_key,
                description=project_key,
            )
            for project_key in projects
        ]

        super().__init__(
            placeholder="Select project…",
            min_values=1,
            max_values=1,
            options=select_options,
        )

    async def callback(self, interaction: discord.Interaction):
        if not check_permission(interaction.user.id):
            await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
            return

        project_key = str(self.values[0])
        if not self.require_issue:
            if self.text_modal:
                await interaction.response.send_modal(
                    CommandTextModal(
                        command_name=self.command_name,
                        handler=self.handler,
                        deps_factory=self.deps_factory,
                        prefix_args=[project_key, *self.extra_args],
                        title=str(self.text_modal.get("title") or f"/{self.command_name}"),
                        field_label=str(self.text_modal.get("label") or "Value"),
                        placeholder=str(self.text_modal.get("placeholder") or ""),
                        multiline=bool(self.text_modal.get("multiline", True)),
                        parse_mode=str(self.text_modal.get("parse_mode") or "append"),
                    )
                )
                return
            await _run_bridge_handler_args(
                interaction,
                command_name=self.command_name,
                parsed_args=[project_key, *self.extra_args],
                handler=self.handler,
                deps_factory=self.deps_factory,
            )
            return

        await interaction.response.edit_message(
            content=f"📁 Project selected: `{project_key}`. Now choose an issue:",
            view=CommandIssueSelectView(
                project_key,
                self.command_name,
                self.handler,
                self.deps_factory,
                extra_args=self.extra_args,
                text_modal=self.text_modal,
                issue_state=self.issue_state,
            ),
        )


class CommandProjectSelectView(discord.ui.View):
    def __init__(
        self,
        command_name: str,
        handler,
        deps_factory,
        *,
        require_issue: bool,
        extra_args: list[str] | None = None,
        text_modal: dict[str, Any] | None = None,
        issue_state: str = "any",
    ):
        super().__init__(timeout=120)
        self.add_item(
            CommandProjectSelect(
                command_name,
                handler,
                deps_factory,
                require_issue=require_issue,
                extra_args=extra_args,
                text_modal=text_modal,
                issue_state=issue_state,
            )
        )


class CommandTextModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        command_name: str,
        handler,
        deps_factory,
        prefix_args: list[str],
        title: str,
        field_label: str,
        placeholder: str,
        multiline: bool,
        parse_mode: str,
    ):
        super().__init__(title=title[:45] if title else f"/{command_name}")
        self.command_name = command_name
        self.handler = handler
        self.deps_factory = deps_factory
        self.prefix_args = list(prefix_args)
        self.parse_mode = parse_mode

        self.input_value = discord.ui.TextInput(
            label=field_label[:45] if field_label else "Value",
            placeholder=placeholder[:100] if placeholder else None,
            style=discord.TextStyle.paragraph if multiline else discord.TextStyle.short,
            required=True,
            max_length=1500,
        )
        self.add_item(self.input_value)

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.input_value.value or "").strip()
        if not raw:
            await interaction.response.send_message("❌ Input cannot be empty.", ephemeral=True)
            return

        parsed_args = list(self.prefix_args)
        if self.parse_mode == "direct_pair":
            parts = shlex.split(raw)
            if len(parts) < 2:
                await interaction.response.send_message(
                    "❌ Format: `@agent your message`", ephemeral=True
                )
                return
            parsed_args.extend([parts[0], " ".join(parts[1:])])
        else:
            parsed_args.append(raw)

        await _run_bridge_handler_args(
            interaction,
            command_name=self.command_name,
            parsed_args=parsed_args,
            handler=self.handler,
            deps_factory=self.deps_factory,
        )


async def _run_bridge_with_picker(
    interaction: discord.Interaction,
    *,
    command_name: str,
    handler,
    deps_factory,
    project: str | None,
    issue: str | None = None,
    require_issue: bool = True,
    extra_args: list[str] | None = None,
    text_modal: dict[str, Any] | None = None,
    issue_state: str | None = None,
) -> None:
    if not check_permission_for_action(interaction.user.id, action="execute"):
        await interaction.response.send_message(
            _permission_denied_message(interaction.user.id, action="execute"),
            ephemeral=True,
        )
        return

    if not project:
        resolved_issue_state = issue_state or _command_issue_state(command_name)
        await interaction.response.send_message(
            f"📁 Select a project for `/{command_name}`:",
            view=CommandProjectSelectView(
                command_name,
                handler,
                deps_factory,
                require_issue=require_issue,
                extra_args=extra_args,
                text_modal=text_modal,
                issue_state=resolved_issue_state,
            ),
            ephemeral=True,
        )
        return

    if NEXUS_AUTH_ENABLED:
        normalized_project = _normalize_project_key(project)
        nexus_id = user_manager.resolve_nexus_id("discord", str(interaction.user.id))
        if not nexus_id or not _svc_has_project_access(str(nexus_id), str(normalized_project)):
            await interaction.response.send_message(
                f"🔒 You are not authorized for project `{normalized_project}`.",
                ephemeral=True,
            )
            return

    if require_issue and not issue:
        resolved_issue_state = issue_state or _command_issue_state(command_name)
        await interaction.response.send_message(
            f"📋 Select an issue for `{project}`:",
            view=CommandIssueSelectView(
                project,
                command_name,
                handler,
                deps_factory,
                extra_args=extra_args,
                text_modal=text_modal,
                issue_state=resolved_issue_state,
            ),
            ephemeral=True,
        )
        return

    if text_modal:
        await interaction.response.send_modal(
            CommandTextModal(
                command_name=command_name,
                handler=handler,
                deps_factory=deps_factory,
                prefix_args=[project, issue] if require_issue and issue else [project],
                title=str(text_modal.get("title") or f"/{command_name}"),
                field_label=str(text_modal.get("label") or "Value"),
                placeholder=str(text_modal.get("placeholder") or ""),
                multiline=bool(text_modal.get("multiline", True)),
                parse_mode=str(text_modal.get("parse_mode") or "append"),
            )
        )
        return

    parsed_args: list[str] = [project]
    if require_issue and issue:
        parsed_args.append(issue)
    parsed_args.extend(list(extra_args or []))
    await _run_bridge_handler_args(
        interaction,
        command_name=command_name,
        parsed_args=parsed_args,
        handler=handler,
        deps_factory=deps_factory,
    )


def transcribe_audio(audio_file_path: str) -> str | None:
    return orchestrator.transcribe_audio(audio_file_path)


def _discord_feature_ideation_handler_deps() -> FeatureIdeationHandlerDeps:
    return _svc_build_feature_ideation_handler_deps(
        logger=logger,
        allowed_user_ids=DISCORD_ALLOWED_USER_IDS,
        projects=ROUTING_PROJECTS,
        get_project_label=lambda key: _svc_get_project_label(key, ROUTING_PROJECTS),
        orchestrator=orchestrator,
        base_dir=BASE_DIR,
        project_config=PROJECT_CONFIG,
        process_inbox_task=process_inbox_task,
    )


def _clamp_feature_count(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 3
    return max(1, min(5, parsed))


def _build_feature_task_text(project_key: str, selected: dict[str, Any]) -> str:
    lines = [
        f"New feature proposal for {_svc_get_project_label(project_key, ROUTING_PROJECTS)}",
        "",
        f"Title: {selected.get('title', '')}",
        f"Summary: {selected.get('summary', '')}",
        f"Why now: {selected.get('why', '')}",
        "",
        "Implementation outline:",
    ]
    steps = selected.get("steps") if isinstance(selected.get("steps"), list) else []
    if steps:
        for index, step in enumerate(steps, start=1):
            lines.append(f"{index}. {step}")
    else:
        lines.extend(
            [
                "1. Define technical approach",
                "2. Implement core changes",
                "3. Validate and document",
            ]
        )
    return "\n".join(lines).strip()


def _feature_list_text(project_key: str, features: list[dict[str, Any]], feature_count: int) -> str:
    lines = [
        f"💡 **Feature proposals for {_svc_get_project_label(project_key, ROUTING_PROJECTS)}**",
        f"Requested: {feature_count}",
        "",
        "Reply with the feature number to start implementation:",
    ]
    for index, item in enumerate(features, start=1):
        lines.append(f"{index}. **{item['title']}** — {item['summary']}")
    lines.append("")
    lines.append("Type a project key anytime to switch project, or `cancel` to stop.")
    return "\n".join(lines)


def _parse_count_reply(text: str) -> int | None:
    candidate = str(text or "").strip().lower()
    if not candidate:
        return None
    if candidate.isdigit():
        return _clamp_feature_count(candidate)

    words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
    }
    return words.get(candidate)


async def _begin_feature_ideation(message: discord.Message, text: str) -> bool:
    if not is_feature_ideation_request(text):
        return False

    user_id = message.author.id
    active_chat = get_chat(user_id) or {}
    metadata = active_chat.get("metadata") if isinstance(active_chat, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}

    preferred_project_key = metadata.get("project_key")
    project_key = detect_feature_project(text, ROUTING_PROJECTS)
    if not project_key and preferred_project_key in ROUTING_PROJECTS:
        project_key = preferred_project_key
    if NEXUS_AUTH_ENABLED and project_key in ROUTING_PROJECTS:
        requester = _requester_context_for_discord_user(message.author)
        allowed_project, _ = _authorize_project_for_requester(project_key, requester)
        if not allowed_project:
            project_key = None

    _pending_feature_ideation[user_id] = {
        "step": "awaiting_count",
        "source_text": text,
        "project": project_key,
        "feature_count": None,
        "items": [],
    }

    project_label = (
        _svc_get_project_label(project_key, ROUTING_PROJECTS) if project_key else "not selected"
    )
    await message.channel.send(
        "🔢 How many feature proposals do you want? Reply with a number from 1 to 5.\n\n"
        f"Current project: **{project_label}**"
    )
    return True


async def _handle_pending_feature_ideation(message: discord.Message, text: str) -> bool:
    state = _pending_feature_ideation.get(message.author.id)
    if not isinstance(state, dict):
        return False

    candidate_text = str(text or "").strip()
    candidate_lower = candidate_text.lower()
    if candidate_lower in {"cancel", "/cancel"}:
        _pending_feature_ideation.pop(message.author.id, None)
        await message.channel.send("❎ Feature ideation canceled.")
        return True

    if state.get("step") == "awaiting_count":
        feature_count = _parse_count_reply(candidate_text)
        if feature_count is None:
            await message.channel.send("⚠️ Please reply with a number between 1 and 5.")
            return True

        state["feature_count"] = _clamp_feature_count(feature_count)
        project_key = state.get("project")
        if project_key not in ROUTING_PROJECTS:
            state["step"] = "awaiting_project"
            options = ", ".join(sorted(ROUTING_PROJECTS.keys()))
            await message.channel.send(
                "📁 Great — now choose a project key to continue:\n" f"{options}"
            )
            return True

        deps = _discord_feature_ideation_handler_deps()
        features = _build_feature_suggestions(
            project_key=project_key,
            text=str(state.get("source_text") or ""),
            deps=deps,
            preferred_agent_type=None,
            feature_count=state["feature_count"],
        )
        state["items"] = features
        state["step"] = "awaiting_pick"

        if not features:
            _pending_feature_ideation.pop(message.author.id, None)
            await message.channel.send(
                "⚠️ I couldn't generate feature proposals right now. Please try again."
            )
            return True

        await message.channel.send(
            _feature_list_text(project_key, features, state["feature_count"])
        )
        return True

    if state.get("step") == "awaiting_project":
        project_key = _normalize_project_key(candidate_text)
        if project_key not in ROUTING_PROJECTS:
            options = ", ".join(sorted(ROUTING_PROJECTS.keys()))
            await message.channel.send(f"⚠️ Invalid project key. Choose one of: {options}")
            return True
        if NEXUS_AUTH_ENABLED:
            requester = _requester_context_for_discord_user(message.author)
            allowed_project, error_project = _authorize_project_for_requester(
                project_key,
                requester,
            )
            if not allowed_project:
                await message.channel.send(error_project)
                return True

        state["project"] = project_key
        deps = _discord_feature_ideation_handler_deps()
        features = _build_feature_suggestions(
            project_key=project_key,
            text=str(state.get("source_text") or ""),
            deps=deps,
            preferred_agent_type=None,
            feature_count=_clamp_feature_count(state.get("feature_count")),
        )
        state["items"] = features
        state["step"] = "awaiting_pick"

        if not features:
            _pending_feature_ideation.pop(message.author.id, None)
            await message.channel.send(
                "⚠️ I couldn't generate feature proposals right now. Please try again."
            )
            return True

        await message.channel.send(
            _feature_list_text(
                project_key, features, _clamp_feature_count(state.get("feature_count"))
            )
        )
        return True

    if state.get("step") == "awaiting_pick":
        project_key = state.get("project")
        items = state.get("items") if isinstance(state.get("items"), list) else []
        if project_key not in ROUTING_PROJECTS or not items:
            _pending_feature_ideation.pop(message.author.id, None)
            await message.channel.send("⚠️ Feature session expired. Start a new request.")
            return True

        project_candidate = _normalize_project_key(candidate_text)
        if project_candidate in ROUTING_PROJECTS:
            if NEXUS_AUTH_ENABLED:
                requester = _requester_context_for_discord_user(message.author)
                allowed_project, error_project = _authorize_project_for_requester(
                    project_candidate,
                    requester,
                )
                if not allowed_project:
                    await message.channel.send(error_project)
                    return True
            state["project"] = project_candidate
            deps = _discord_feature_ideation_handler_deps()
            features = _build_feature_suggestions(
                project_key=project_candidate,
                text=str(state.get("source_text") or ""),
                deps=deps,
                preferred_agent_type=None,
                feature_count=_clamp_feature_count(state.get("feature_count")),
            )
            state["items"] = features
            if not features:
                _pending_feature_ideation.pop(message.author.id, None)
                await message.channel.send(
                    "⚠️ I couldn't generate feature proposals right now. Please try again."
                )
                return True
            await message.channel.send(
                _feature_list_text(
                    project_candidate, features, _clamp_feature_count(state.get("feature_count"))
                )
            )
            return True

        if not candidate_text.isdigit():
            await message.channel.send("⚠️ Reply with a feature number to start implementation.")
            return True

        selected_index = int(candidate_text) - 1
        if selected_index < 0 or selected_index >= len(items):
            await message.channel.send(
                "⚠️ Invalid feature selection. Reply with one of the listed numbers."
            )
            return True

        selected = items[selected_index]
        task_text = _build_feature_task_text(project_key, selected)
        result = await process_inbox_task(
            task_text,
            orchestrator,
            str(message.id),
            project_hint=project_key,
            requester_context=_requester_context_for_discord_user(message.author),
            authorize_project=_authorize_project_for_requester,
        )
        _pending_feature_ideation.pop(message.author.id, None)
        await message.channel.send(str(result.get("message") or "⚠️ Task processing completed."))
        return True

    _pending_feature_ideation.pop(message.author.id, None)
    return False


def check_permission(user_id: int) -> bool:
    """Check if the user is allowed to interact with the bot."""
    if not DISCORD_ALLOWED_USER_IDS:
        base_allowed = True
    else:
        base_allowed = user_id in DISCORD_ALLOWED_USER_IDS
    if not base_allowed:
        return False
    if not NEXUS_AUTH_ENABLED:
        return True
    nexus_id = user_manager.resolve_nexus_id("discord", str(user_id))
    if not nexus_id:
        return False
    try:
        setup = _svc_get_setup_status(str(nexus_id))
    except Exception:
        return False
    return bool(setup.get("ready"))


def check_permission_for_action(user_id: int, *, action: str = "execute") -> bool:
    """Check user permission with explicit action scope."""
    action_value = str(action or "execute").strip().lower()
    if not DISCORD_ALLOWED_USER_IDS:
        base_allowed = True
    else:
        base_allowed = user_id in DISCORD_ALLOWED_USER_IDS
    if not base_allowed:
        return False
    if not NEXUS_AUTH_ENABLED:
        return True
    if action_value in {"readonly", "onboarding", "help"}:
        return True
    return check_permission(user_id)


def _permission_denied_message(user_id: int, *, action: str = "execute") -> str:
    if DISCORD_ALLOWED_USER_IDS and user_id not in DISCORD_ALLOWED_USER_IDS:
        return "🔒 Unauthorized."
    if not NEXUS_AUTH_ENABLED:
        return "🔒 Unauthorized."
    action_value = str(action or "execute").strip().lower()
    if action_value in {"readonly", "onboarding", "help"}:
        return "🔒 Unauthorized."
    nexus_id = user_manager.resolve_nexus_id("discord", str(user_id))
    if not nexus_id:
        return "🔐 Complete setup with `/login` before using task/workflow commands."
    try:
        setup = _svc_get_setup_status(str(nexus_id))
    except Exception:
        return "🔐 Auth storage is unavailable. Ask an admin to check auth configuration."
    missing: list[str] = []
    if not setup.get("git_provider_linked"):
        missing.append("Git provider login (GitHub or GitLab)")
    if not setup.get("ai_provider_ready"):
        missing.append(
            "AI provider credentials (Codex/OpenAI, Gemini, Claude, or GitHub for Copilot)"
        )
    if not setup.get("org_verified"):
        missing.append("allowed org/group membership")
    if int(setup.get("project_access_count") or 0) <= 0:
        missing.append("project team/group access")
    if missing:
        return (
            "🔐 Setup incomplete: " + ", ".join(missing) + ". "
            "Run `/login` then `/setup-status`."
        )
    return "🔒 Unauthorized."


def _get_or_create_discord_user(discord_user: Any):
    return user_manager.get_or_create_user_by_identity(
        platform="discord",
        platform_user_id=str(getattr(discord_user, "id", "")),
        username=getattr(discord_user, "name", None),
        first_name=getattr(discord_user, "display_name", None),
    )


def _requester_context_for_discord_user(discord_user: Any) -> dict[str, str]:
    user = _get_or_create_discord_user(discord_user)
    return {
        "nexus_id": str(user.nexus_id),
        "platform": "discord",
        "platform_user_id": str(getattr(discord_user, "id", "")),
    }


def _authorize_project_for_requester(
    project_key: str,
    requester_context: dict[str, Any] | None,
) -> tuple[bool, str]:
    if not NEXUS_AUTH_ENABLED:
        return True, ""
    context = requester_context if isinstance(requester_context, dict) else {}
    nexus_id = str(context.get("nexus_id") or "").strip()
    if not nexus_id:
        return False, "🔐 Missing requester identity. Run `/login` and retry."
    return _svc_check_project_access(nexus_id, project_key)


def _autoselect_chat_project_from_auth(user_id: int, chat_id: str) -> tuple[bool, str | None]:
    """Auto-select chat project from auth setup when context project is missing."""
    if not NEXUS_AUTH_ENABLED or not chat_id:
        return False, None
    try:
        chat_data = get_chat(user_id, chat_id)
        metadata = (chat_data or {}).get("metadata") or {}
        existing_project = str(metadata.get("project_key") or "").strip().lower()
        if existing_project:
            return False, existing_project

        nexus_id = user_manager.resolve_nexus_id("discord", str(user_id))
        if not nexus_id:
            return False, None
        setup = _svc_get_setup_status(str(nexus_id))
        raw_projects = setup.get("projects") or []
        projects = [
            str(item).strip().lower()
            for item in raw_projects
            if str(item).strip().lower() in ROUTING_PROJECTS
        ]
        if not projects:
            return False, None

        selected_project: str | None = None
        if len(projects) == 1:
            selected_project = projects[0]
        elif "nexus" in projects:
            selected_project = "nexus"
        if not selected_project:
            return False, None

        updated = update_chat_metadata(user_id, chat_id, {"project_key": selected_project})
        return bool(updated), selected_project
    except Exception as exc:
        logger.debug("Chat project auto-select skipped for user_id=%s: %s", user_id, exc)
        return False, None


def _active_status(value: str) -> bool:
    status = str(value or "").strip().lower()
    if not status:
        status = "active"
    return status not in {"done", "closed", "resolved", "completed", "implemented", "rejected"}


# --- DISCORD UI VIEWS (Similar to Telegram Inline Keyboards) ---


class ChatRenameModal(discord.ui.Modal, title="Rename Active Chat"):
    name = discord.ui.TextInput(
        label="New Chat Name",
        placeholder="Enter the new active chat name...",
        min_length=1,
        max_length=100,
    )

    def __init__(self, user_id: int, chat_id: str):
        super().__init__()
        self.user_id = user_id
        self.chat_id = chat_id

    async def on_submit(self, interaction: discord.Interaction):
        new_name = str(self.name.value or "").strip()
        rename_chat(self.user_id, self.chat_id, new_name)
        await send_chat_menu(
            interaction,
            self.user_id,
            notice=f"✅ Active chat renamed to: **{new_name}**",
        )


class ChatRenamePromptView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(
        label="✏️ Open Rename", style=discord.ButtonStyle.primary, custom_id="chat:rename:open"
    )
    async def open_rename_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return

        active_chat_id = get_active_chat(interaction.user.id)
        if active_chat_id:
            await interaction.response.send_modal(
                ChatRenameModal(interaction.user.id, active_chat_id)
            )
        else:
            await interaction.response.send_message("No active chat to rename.", ephemeral=True)

    @discord.ui.button(
        label="❌ Cancel", style=discord.ButtonStyle.secondary, custom_id="chat:rename:cancel"
    )
    async def cancel_rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return
        await send_chat_menu(interaction, interaction.user.id, notice="❎ Rename canceled.")


class ChatContextView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(
        label="📁 Set Project", style=discord.ButtonStyle.primary, custom_id="chat:context:project"
    )
    async def set_project(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return
        await interaction.response.edit_message(
            content="📁 **Select project for active chat:**",
            view=ChatProjectPickerView(interaction.user.id),
        )

    @discord.ui.button(
        label="🧭 Set Mode", style=discord.ButtonStyle.secondary, custom_id="chat:context:mode"
    )
    async def set_mode(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return
        await interaction.response.edit_message(
            content="🧭 **Select mode for active chat:**",
            view=ChatModePickerView(interaction.user.id),
        )

    @discord.ui.button(
        label="🤖 Set Primary Agent", style=discord.ButtonStyle.secondary, custom_id="chat:context:agent"
    )
    async def set_primary_agent(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return
        await interaction.response.edit_message(
            content="🤖 **Select primary agent for active chat:**",
            view=ChatAgentPickerView(interaction.user.id),
        )

    @discord.ui.button(
        label="🔙 Back to Menu", style=discord.ButtonStyle.secondary, custom_id="chat:context:back"
    )
    async def back_to_menu(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return
        await send_chat_menu(interaction, interaction.user.id)


class ChatProjectPickerView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

        for project_key, project_label in sorted(ROUTING_PROJECTS.items()):
            button = discord.ui.Button(
                label=project_label[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"chat:project:{project_key}",
            )
            button.callback = self.create_project_callback(project_key)
            self.add_item(button)

        back_btn = discord.ui.Button(
            label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="chat:project:back"
        )
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    def create_project_callback(self, project_key: str):
        async def callback(interaction: discord.Interaction):
            active_chat_id = get_active_chat(interaction.user.id)
            if project_key not in ROUTING_PROJECTS:
                await send_chat_context(interaction, interaction.user.id, notice="⚠️ Invalid project.")
                return
            update_chat_metadata(interaction.user.id, active_chat_id, {"project_key": project_key})
            await send_chat_context(
                interaction,
                interaction.user.id,
                notice=f"✅ Project set to **{_svc_get_project_label(project_key, ROUTING_PROJECTS)}**.",
            )

        return callback

    async def back_callback(self, interaction: discord.Interaction):
        await send_chat_context(interaction, interaction.user.id)


class ChatModePickerView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

        for mode_key, mode_label in CHAT_MODES.items():
            button = discord.ui.Button(
                label=mode_label[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"chat:mode:{mode_key}",
            )
            button.callback = self.create_mode_callback(mode_key)
            self.add_item(button)

        back_btn = discord.ui.Button(
            label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="chat:mode:back"
        )
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    def create_mode_callback(self, mode_key: str):
        async def callback(interaction: discord.Interaction):
            if mode_key not in CHAT_MODES:
                await send_chat_context(interaction, interaction.user.id, notice="⚠️ Invalid mode.")
                return
            active_chat_id = get_active_chat(interaction.user.id)
            update_chat_metadata(interaction.user.id, active_chat_id, {"chat_mode": mode_key})
            await send_chat_context(
                interaction,
                interaction.user.id,
                notice=f"✅ Mode set to **{CHAT_MODES[mode_key]}**.",
            )

        return callback

    async def back_callback(self, interaction: discord.Interaction):
        await send_chat_context(interaction, interaction.user.id)


class ChatAgentPickerView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

        active_chat_id = get_active_chat(user_id)
        active_chat = get_chat(user_id, active_chat_id)
        for agent in available_chat_agents(active_chat):
            agent_type = str(agent.get("agent_type") or "").strip().lower()
            if not agent_type:
                continue
            button = discord.ui.Button(
                label=agent_display_label(agent)[:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"chat:agent:{agent_type}",
            )
            button.callback = self.create_agent_callback(agent_type)
            self.add_item(button)

        back_btn = discord.ui.Button(
            label="🔙 Back", style=discord.ButtonStyle.secondary, custom_id="chat:agent:back"
        )
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    def create_agent_callback(self, agent_type: str):
        async def callback(interaction: discord.Interaction):
            active_chat_id = get_active_chat(interaction.user.id)
            active_chat = get_chat(interaction.user.id, active_chat_id)
            available_types = [
                str(item.get("agent_type") or "").strip().lower()
                for item in available_chat_agents(active_chat)
                if str(item.get("agent_type") or "").strip()
            ]
            if agent_type not in available_types:
                await send_chat_context(
                    interaction,
                    interaction.user.id,
                    notice="⚠️ Invalid primary agent.",
                )
                return
            update_chat_metadata(interaction.user.id, active_chat_id, {"primary_agent_type": agent_type})
            await send_chat_context(
                interaction,
                interaction.user.id,
                notice=f"✅ Primary agent set to **{agent_type_label(agent_type)}** (`{agent_type}`).",
            )

        return callback

    async def back_callback(self, interaction: discord.Interaction):
        await send_chat_context(interaction, interaction.user.id)


class ChatMenuView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="📝 New Chat", style=discord.ButtonStyle.primary, custom_id="chat:new")
    async def new_chat(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return

        create_chat(interaction.user.id)
        # Re-render the menu
        await send_chat_menu(interaction, interaction.user.id)

    @discord.ui.button(
        label="📋 Switch Chat", style=discord.ButtonStyle.secondary, custom_id="chat:list"
    )
    async def switch_chat_list(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return

        view = ChatListView(interaction.user.id)
        await interaction.response.edit_message(content="**Select a chat:**", view=view)

    @discord.ui.button(
        label="⚙️ Context", style=discord.ButtonStyle.secondary, custom_id="chat:context"
    )
    async def context(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return

        await send_chat_context(interaction, interaction.user.id)

    @discord.ui.button(
        label="✏️ Rename", style=discord.ButtonStyle.secondary, custom_id="chat:rename"
    )
    async def rename_current_chat(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not check_permission(interaction.user.id):
            return

        await interaction.response.edit_message(
            content=(
                "✏️ **Rename Active Chat**\n\n"
                "Open rename to enter a new name, or cancel to go back."
            ),
            view=ChatRenamePromptView(interaction.user.id),
        )

    @discord.ui.button(
        label="🗑️ Delete Current", style=discord.ButtonStyle.danger, custom_id="chat:delete"
    )
    async def delete_active_chat(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not check_permission(interaction.user.id):
            return

        active_chat_id = get_active_chat(interaction.user.id)
        if active_chat_id:
            delete_chat(interaction.user.id, active_chat_id)

        # After deleting, send the main menu which will pick the next active chat or create a default
        await send_chat_menu(interaction, interaction.user.id)


class ChatListView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

        chats = list_chats(user_id)
        active_chat_id = get_active_chat(user_id)

        for c in chats:
            chat_id = c.get("id")
            title = c.get("title", "Chat")

            # Truncate title for button label limits (Discord limit is 80)
            if len(title) > 70:
                title = title[:67] + "..."

            label = f"✅ {title}" if chat_id == active_chat_id else title
            style = (
                discord.ButtonStyle.success
                if chat_id == active_chat_id
                else discord.ButtonStyle.secondary
            )

            # Using dynamic callback creation
            button = discord.ui.Button(label=label, style=style, custom_id=f"switch_chat:{chat_id}")
            button.callback = self.create_switch_callback(chat_id)
            self.add_item(button)

        # Back button
        back_btn = discord.ui.Button(
            label="⬅️ Back", style=discord.ButtonStyle.danger, custom_id="chat:back"
        )
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    def create_switch_callback(self, chat_id: str):
        async def callback(interaction: discord.Interaction):
            set_active_chat(interaction.user.id, chat_id)
            await send_chat_menu(interaction, interaction.user.id)

        return callback

    async def back_callback(self, interaction: discord.Interaction):
        await send_chat_menu(interaction, interaction.user.id)


async def send_chat_menu(interaction: discord.Interaction, user_id: int, notice: str = ""):
    """Helper to send or edit the current message with the main chat menu."""
    active_chat_id = get_active_chat(user_id)
    auto_selected, selected_project = _autoselect_chat_project_from_auth(user_id, active_chat_id)
    chats = list_chats(user_id)

    active_chat_title = "Unknown"
    for c in chats:
        if c.get("id") == active_chat_id:
            active_chat_title = c.get("title")
            break

    active_chat = get_chat(user_id, active_chat_id)

    text = "🗣️ **Nexus Chat Menu**\n\n"
    if notice:
        text += f"{notice}\n"
    if auto_selected and selected_project:
        text += (
            "ℹ️ Auto-selected project from your auth access: "
            f"**{_svc_get_project_label(selected_project, ROUTING_PROJECTS)}**.\n"
        )
    text += f"**Active Chat:** {active_chat_title}\n"
    text += f"{chat_context_summary(active_chat, ROUTING_PROJECTS, markdown_style='discord')}\n"
    text += "_(All conversational history is saved under this thread)_"

    view = ChatMenuView(user_id)

    # If this is responding to a button click, edit the message
    if interaction.response.is_done():
        await interaction.message.edit(content=text, view=view)
    else:
        await interaction.response.edit_message(content=text, view=view)


async def send_chat_context(interaction: discord.Interaction, user_id: int, notice: str = ""):
    active_chat_id = get_active_chat(user_id)
    _autoselect_chat_project_from_auth(user_id, active_chat_id)
    active_chat = get_chat(user_id, active_chat_id)
    text = "⚙️ **Chat Context**\n\n"
    if notice:
        text += f"{notice}\n"
    text += chat_context_summary(active_chat, ROUTING_PROJECTS, markdown_style="discord")
    view = ChatContextView(user_id)
    if interaction.response.is_done():
        await interaction.message.edit(content=text, view=view)
    else:
        await interaction.response.edit_message(content=text, view=view)


# --- SLASH COMMANDS ---


@bot.tree.command(name="help", description="Show available Discord commands")
async def help_command(interaction: discord.Interaction):
    if not check_permission_for_action(interaction.user.id, action="help"):
        await interaction.response.send_message(
            _permission_denied_message(interaction.user.id, action="help"),
            ephemeral=True,
        )
        return

    help_text = build_help_text()
    await _send_long_interaction_text(interaction, help_text, ephemeral=True)


@bot.tree.command(name="login", description="Link GitHub/GitLab and configure AI provider keys")
@app_commands.describe(provider="OAuth provider (github or gitlab)")
@app_commands.choices(
    provider=[
        app_commands.Choice(name="GitLab", value="gitlab"),
        app_commands.Choice(name="GitHub", value="github"),
    ]
)
async def login_command(
    interaction: discord.Interaction,
    provider: app_commands.Choice[str] | None = None,
):
    if not check_permission_for_action(interaction.user.id, action="onboarding"):
        await interaction.response.send_message(
            _permission_denied_message(interaction.user.id, action="onboarding"),
            ephemeral=True,
        )
        return
    if not NEXUS_AUTH_ENABLED:
        await interaction.response.send_message(
            "ℹ️ Auth onboarding is disabled in this environment.",
            ephemeral=True,
        )
        return
    if not NEXUS_PUBLIC_BASE_URL:
        await interaction.response.send_message(
            "⚠️ NEXUS_PUBLIC_BASE_URL is not configured. Ask an admin to configure auth.",
            ephemeral=True,
        )
        return

    selected_provider = str(provider.value if provider else "").strip().lower()
    available_providers: list[str] = []
    if NEXUS_GITHUB_CLIENT_ID:
        available_providers.append("github")
    if NEXUS_GITLAB_CLIENT_ID:
        available_providers.append("gitlab")
    if not available_providers:
        await interaction.response.send_message(
            "⚠️ No OAuth providers are configured. Ask an admin to configure GitHub/GitLab OAuth.",
            ephemeral=True,
        )
        return

    user = _get_or_create_discord_user(interaction.user)
    session_id = _svc_create_login_session_for_user(
        nexus_id=str(user.nexus_id),
        discord_user_id=str(interaction.user.id),
        discord_username=getattr(interaction.user, "name", None),
    )

    if not selected_provider:
        view = discord.ui.View()
        for auth_provider in available_providers:
            login_url = (
                f"{NEXUS_PUBLIC_BASE_URL}/auth/start?session={session_id}&provider={auth_provider}"
            )
            view.add_item(
                discord.ui.Button(
                    label=f"Continue with {auth_provider.title()}",
                    style=discord.ButtonStyle.link,
                    url=login_url,
                )
            )
        try:
            dm_channel = await interaction.user.create_dm()
            sent = await dm_channel.send(
                "🔐 Setup required before task execution.\n\n"
                "Choose your Git provider to continue OAuth onboarding.",
                view=view,
            )
            await interaction.response.send_message(
                "📩 I sent you a DM with onboarding links.",
                ephemeral=True,
            )
        except Exception as exc:
            logger.warning("Failed to send Discord onboarding DM for session %s: %s", session_id, exc)
            await interaction.response.send_message(
                "⚠️ I could not DM you. Enable DMs and run `/login` again.",
                ephemeral=True,
            )
            return
        try:
            _svc_register_onboarding_message(
                session_id=session_id,
                chat_platform="discord",
                chat_id=str(getattr(dm_channel, "id", "") or ""),
                message_id=str(getattr(sent, "id", "") or ""),
            )
        except Exception as exc:
            logger.warning("Failed to register Discord onboarding message for session %s: %s", session_id, exc)
        return

    if selected_provider not in {"github", "gitlab"}:
        await interaction.response.send_message(
            "⚠️ Invalid provider. Use `/login provider:github`, `/login provider:gitlab`, or run `/login`.",
            ephemeral=True,
        )
        return
    if selected_provider not in available_providers:
        await interaction.response.send_message(
            f"⚠️ {selected_provider.title()} OAuth is not configured in this environment.",
            ephemeral=True,
        )
        return

    login_url = (
        f"{NEXUS_PUBLIC_BASE_URL}/auth/start?session={session_id}&provider={selected_provider}"
    )
    try:
        dm_channel = await interaction.user.create_dm()
        sent = await dm_channel.send(
            "🔐 Setup required before task execution.\n\n"
            f"1. Open: <{login_url}>\n"
            f"2. Sign in with {selected_provider.title()}\n"
            "3. Add Codex/OpenAI, Gemini, and/or Claude key, or use Copilot with linked GitHub OAuth\n"
            "4. Use `/menu` to continue."
        )
        await interaction.response.send_message(
            "📩 I sent you a DM with onboarding instructions.",
            ephemeral=True,
        )
    except Exception as exc:
        logger.warning("Failed to send Discord onboarding DM for session %s: %s", session_id, exc)
        await interaction.response.send_message(
            "⚠️ I could not DM you. Enable DMs and run `/login` again.",
            ephemeral=True,
        )
        return
    try:
        _svc_register_onboarding_message(
            session_id=session_id,
            chat_platform="discord",
            chat_id=str(getattr(dm_channel, "id", "") or ""),
            message_id=str(getattr(sent, "id", "") or ""),
        )
    except Exception as exc:
        logger.warning("Failed to register Discord onboarding message for session %s: %s", session_id, exc)


@bot.tree.command(name="setup-status", description="Show your onboarding and project access status")
async def setup_status_command(interaction: discord.Interaction):
    if not check_permission_for_action(interaction.user.id, action="readonly"):
        await interaction.response.send_message(
            _permission_denied_message(interaction.user.id, action="readonly"),
            ephemeral=True,
        )
        return
    user = _get_or_create_discord_user(interaction.user)
    status = _svc_get_setup_status(str(user.nexus_id))
    if not status.get("auth_enabled"):
        await interaction.response.send_message(
            "ℹ️ Auth onboarding is disabled in this environment.",
            ephemeral=True,
        )
        return

    projects = status.get("projects") or []
    projects_line = ", ".join(projects) if projects else "(none)"
    lines = [
        "🧾 **Setup Status**",
        f"- Nexus ID: `{user.nexus_id}`",
        f"- GitHub linked: {'✅' if status.get('github_linked') else '❌'}",
        f"- GitLab linked: {'✅' if status.get('gitlab_linked') else '❌'}",
        f"- GitHub login: `{status.get('github_login') or 'n/a'}`",
        f"- GitLab username: `{status.get('gitlab_username') or 'n/a'}`",
        f"- Codex key set: {'✅' if status.get('codex_key_set') else '❌'}",
        f"- Gemini key set: {'✅' if status.get('gemini_key_set') else '❌'}",
        f"- Claude key set: {'✅' if status.get('claude_key_set') else '❌'}",
        f"- Copilot ready (GitHub OAuth or Copilot Token): {'✅' if status.get('copilot_ready') else '❌'}",
        f"- Org verified: {'✅' if status.get('org_verified') else '❌'}",
        f"- Project access: `{int(status.get('project_access_count') or 0)}`",
        f"- Projects: {projects_line}",
        f"- Ready: {'✅' if status.get('ready') else '❌'}",
    ]
    if not status.get("ready"):
        lines.append("")
        lines.append("Run `/login` to complete any missing steps.")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="whoami", description="Show your Discord/Nexus identity mapping")
async def whoami_command(interaction: discord.Interaction):
    if not check_permission_for_action(interaction.user.id, action="readonly"):
        await interaction.response.send_message(
            _permission_denied_message(interaction.user.id, action="readonly"),
            ephemeral=True,
        )
        return
    user = _get_or_create_discord_user(interaction.user)
    setup = _svc_get_setup_status(str(user.nexus_id)) if NEXUS_AUTH_ENABLED else {}
    identities = ", ".join(
        f"{platform}:{value}" for platform, value in sorted((user.identities or {}).items())
    )
    lines = [
        "👤 **Identity**",
        f"- Nexus ID: `{user.nexus_id}`",
        f"- Discord ID: `{interaction.user.id}`",
        f"- Username: `{interaction.user.name}`",
        f"- Linked identities: {identities or '(none)'}",
    ]
    if NEXUS_AUTH_ENABLED:
        lines.append(f"- GitHub login: `{setup.get('github_login') or 'n/a'}`")
        lines.append(f"- GitLab username: `{setup.get('gitlab_username') or 'n/a'}`")
        lines.append(f"- Ready: {'✅' if setup.get('ready') else '❌'}")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="menu", description="Open the categorized Nexus command menu")
async def menu_command(interaction: discord.Interaction):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return

    await interaction.response.send_message(
        content="📍 **Nexus Menu**\nChoose a category:",
        view=RootMenuView(),
        ephemeral=True,
    )


@bot.tree.command(name="active", description="Show active tasks")
@app_commands.describe(args="Optional: <project|all> [cleanup]")
async def active_command(interaction: discord.Interaction, args: str = ""):
    await _run_bridge_handler(
        interaction,
        command_name="active",
        args=args,
        handler=monitoring_active_handler,
        deps_factory=_monitoring_bridge_deps,
    )


@bot.tree.command(name="inboxq", description="Inspect inbox queue (postgres mode)")
@app_commands.describe(args="Optional: [limit]")
async def inboxq_command(interaction: discord.Interaction, args: str = ""):
    await _run_bridge_handler(
        interaction,
        command_name="inboxq",
        args=args,
        handler=ops_inboxq_handler,
        deps_factory=_ops_bridge_deps,
    )


@bot.tree.command(name="stats", description="Show analytics report")
@app_commands.describe(args="Optional: [days]")
async def stats_command(interaction: discord.Interaction, args: str = ""):
    await _run_bridge_handler(
        interaction,
        command_name="stats",
        args=args,
        handler=ops_stats_handler,
        deps_factory=_ops_bridge_deps,
    )


@bot.tree.command(name="logs", description="Show issue logs")
@app_commands.describe(project="Optional project key", issue="Optional issue number")
async def logs_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="logs",
        handler=monitoring_logs_handler,
        deps_factory=_monitoring_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="logsfull", description="Show full issue logs")
@app_commands.describe(project="Optional project key", issue="Optional issue number")
async def logsfull_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="logsfull",
        handler=monitoring_logsfull_handler,
        deps_factory=_monitoring_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="tail", description="Follow issue logs live")
@app_commands.describe(
    project="Project key",
    issue="Issue number",
    lines="Optional line count",
    seconds="Optional follow duration",
)
async def tail_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
    lines: int | None = None,
    seconds: int | None = None,
):
    parsed_args: list[str] = []
    if lines is not None:
        parsed_args.append(str(lines))
    if seconds is not None:
        parsed_args.append(str(seconds))
    await _run_bridge_with_picker(
        interaction,
        command_name="tail",
        handler=monitoring_tail_handler,
        deps_factory=_monitoring_bridge_deps,
        project=project,
        issue=issue,
        extra_args=parsed_args,
    )


@bot.tree.command(name="tailstop", description="Stop live log tail")
async def tailstop_command(interaction: discord.Interaction):
    await _run_bridge_handler(
        interaction,
        command_name="tailstop",
        args="",
        handler=monitoring_tailstop_handler,
        deps_factory=_monitoring_bridge_deps,
    )


@bot.tree.command(name="fuse", description="Show retry fuse status")
@app_commands.describe(project="Optional project key", issue="Optional issue number")
async def fuse_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="fuse",
        handler=monitoring_fuse_handler,
        deps_factory=_monitoring_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="audit", description="Show workflow audit trail")
@app_commands.describe(project="Optional project key", issue="Optional issue number")
async def audit_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="audit",
        handler=ops_audit_handler,
        deps_factory=_monitoring_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="comments", description="Show issue comments")
@app_commands.describe(project="Optional project key", issue="Optional issue number")
async def comments_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="comments",
        handler=issue_comments_handler,
        deps_factory=_issue_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="visualize", description="Render Mermaid workflow diagram")
@app_commands.describe(project="Optional project key", issue="Optional issue number")
async def visualize_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="visualize",
        handler=workflow_visualize_handler,
        deps_factory=_visualize_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="watch", description="Watch workflow updates")
@app_commands.describe(
    mode="start | status | stop | mermaid",
    project="Project key (required when mode=start)",
    issue="Issue number (required when mode=start)",
    mermaid="on|off (required when mode=mermaid)",
)
@app_commands.choices(
    mode=[
        app_commands.Choice(name="start", value="start"),
        app_commands.Choice(name="status", value="status"),
        app_commands.Choice(name="stop", value="stop"),
        app_commands.Choice(name="mermaid", value="mermaid"),
    ],
    mermaid=[
        app_commands.Choice(name="on", value="on"),
        app_commands.Choice(name="off", value="off"),
    ],
)
async def watch_command(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str] | None = None,
    project: str | None = None,
    issue: str | None = None,
    mermaid: app_commands.Choice[str] | None = None,
):
    selected_mode = (mode.value if mode else "start").strip().lower()
    parsed_args: list[str]
    if selected_mode == "status":
        parsed_args = ["status"]
    elif selected_mode == "stop":
        parsed_args = ["stop"]
    elif selected_mode == "mermaid":
        if not mermaid:
            await interaction.response.send_message(
                "❌ `mermaid` mode requires `mermaid=on|off`.", ephemeral=True
            )
            return
        parsed_args = ["mermaid", mermaid.value]
    else:
        await _run_bridge_with_picker(
            interaction,
            command_name="watch",
            handler=workflow_watch_handler,
            deps_factory=_watch_bridge_deps,
            project=project,
            issue=issue,
        )
        return

    await _run_bridge_handler_args(
        interaction,
        command_name="watch",
        parsed_args=parsed_args,
        handler=workflow_watch_handler,
        deps_factory=_watch_bridge_deps,
    )


@bot.tree.command(name="wfstate", description="Show workflow state snapshot")
@app_commands.describe(project="Optional project key", issue="Optional issue number")
async def wfstate_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="wfstate",
        handler=workflow_wfstate_handler,
        deps_factory=_workflow_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="reprocess", description="Re-run issue workflow")
@app_commands.describe(project="Project key", issue="Issue number")
async def reprocess_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="reprocess",
        handler=workflow_reprocess_handler,
        deps_factory=_workflow_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="reconcile", description="Reconcile workflow signals")
@app_commands.describe(project="Project key", issue="Issue number")
async def reconcile_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="reconcile",
        handler=workflow_reconcile_handler,
        deps_factory=_workflow_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="continue", description="Continue a stuck workflow")
@app_commands.describe(project="Project key", issue="Issue number")
async def continue_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="continue",
        handler=workflow_continue_handler,
        deps_factory=_workflow_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="forget", description="Forget local state for issue")
@app_commands.describe(project="Optional project key", issue="Optional issue number")
async def forget_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="forget",
        handler=workflow_forget_handler,
        deps_factory=_workflow_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="kill", description="Kill running agent for issue")
@app_commands.describe(project="Optional project key", issue="Optional issue number")
async def kill_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="kill",
        handler=workflow_kill_handler,
        deps_factory=_workflow_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="pause", description="Pause workflow auto-chaining")
@app_commands.describe(project="Project key", issue="Issue number")
async def pause_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="pause",
        handler=workflow_pause_handler,
        deps_factory=_workflow_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="resume", description="Resume workflow auto-chaining")
@app_commands.describe(project="Project key", issue="Issue number")
async def resume_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="resume",
        handler=workflow_resume_handler,
        deps_factory=_workflow_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="stop", description="Stop workflow for issue")
@app_commands.describe(project="Project key", issue="Issue number")
async def stop_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="stop",
        handler=workflow_stop_handler,
        deps_factory=_workflow_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="agents", description="List project agents")
@app_commands.describe(project="Optional project key")
async def agents_command(interaction: discord.Interaction, project: str | None = None):
    await _run_bridge_with_picker(
        interaction,
        command_name="agents",
        handler=ops_agents_handler,
        deps_factory=_ops_bridge_deps,
        project=project,
        require_issue=False,
    )


@bot.tree.command(name="direct", description="Send direct agent request")
@app_commands.describe(project="Project key", agent="Agent handle (e.g. @triage)", message="Message")
async def direct_command(
    interaction: discord.Interaction,
    project: str | None = None,
    agent: str | None = None,
    message: str | None = None,
):
    if agent and message and project:
        await _run_bridge_handler_args(
            interaction,
            command_name="direct",
            parsed_args=[project, agent, message],
            handler=ops_direct_handler,
            deps_factory=_ops_bridge_deps,
        )
        return

    await _run_bridge_with_picker(
        interaction,
        command_name="direct",
        handler=ops_direct_handler,
        deps_factory=_ops_bridge_deps,
        project=project,
        require_issue=False,
        text_modal={
            "title": "Direct Agent Request",
            "label": "Agent and Message",
            "placeholder": "@triage investigate issue behavior",
            "multiline": True,
            "parse_mode": "direct_pair",
        },
    )


@bot.tree.command(name="assign", description="Assign issue")
@app_commands.describe(project="Project key", issue="Issue number", assignee="Optional assignee")
async def assign_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
    assignee: str | None = None,
):
    if project and issue:
        parsed_args = [project, issue]
        if assignee:
            parsed_args.append(assignee)
        await _run_bridge_handler_args(
            interaction,
            command_name="assign",
            parsed_args=parsed_args,
            handler=issue_assign_handler,
            deps_factory=_issue_bridge_deps,
        )
        return

    await _run_bridge_with_picker(
        interaction,
        command_name="assign",
        handler=issue_assign_handler,
        deps_factory=_issue_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="implement", description="Request implementation")
@app_commands.describe(project="Project key", issue="Issue number")
async def implement_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="implement",
        handler=issue_implement_handler,
        deps_factory=_issue_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="prepare", description="Prepare issue instructions")
@app_commands.describe(project="Project key", issue="Issue number")
async def prepare_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="prepare",
        handler=issue_prepare_handler,
        deps_factory=_issue_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="plan", description="Request an implementation plan")
@app_commands.describe(project="Project key", issue="Issue number")
async def plan_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="plan",
        handler=issue_plan_handler,
        deps_factory=_issue_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="respond", description="Respond to issue/agent")
@app_commands.describe(project="Project key", issue="Issue number", message="Response text")
async def respond_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
    message: str | None = None,
):
    if project and issue and message:
        await _run_bridge_handler_args(
            interaction,
            command_name="respond",
            parsed_args=[project, issue, message],
            handler=issue_respond_handler,
            deps_factory=_issue_bridge_deps,
        )
        return

    await _run_bridge_with_picker(
        interaction,
        command_name="respond",
        handler=issue_respond_handler,
        deps_factory=_issue_bridge_deps,
        project=project,
        issue=issue,
        text_modal={
            "title": "Respond to Issue",
            "label": "Response Message",
            "placeholder": "Type your response to the issue or agent",
            "multiline": True,
            "parse_mode": "append",
        },
    )
    await _run_bridge_handler_args(
        interaction,
        command_name="direct",
        parsed_args=[project, agent, message],
        handler=ops_direct_handler,
        deps_factory=_ops_bridge_deps,
    )


@bot.tree.command(name="untrack", description="Stop tracking issue")
@app_commands.describe(project="Optional project key", issue="Optional issue number")
async def untrack_command(
    interaction: discord.Interaction,
    project: str | None = None,
    issue: str | None = None,
):
    await _run_bridge_with_picker(
        interaction,
        command_name="untrack",
        handler=issue_untrack_handler,
        deps_factory=_issue_bridge_deps,
        project=project,
        issue=issue,
    )


@bot.tree.command(name="feature_done", description="Mark feature implemented")
@app_commands.describe(project="Optional project key", title="Feature title")
async def feature_done_command(
    interaction: discord.Interaction,
    project: str | None = None,
    title: str | None = None,
):
    if project and title:
        await _run_bridge_handler_args(
            interaction,
            command_name="feature_done",
            parsed_args=[project, title],
            handler=feature_done_command_handler,
            deps_factory=_feature_registry_bridge_deps,
        )
        return

    await _run_bridge_with_picker(
        interaction,
        command_name="feature_done",
        handler=feature_done_command_handler,
        deps_factory=_feature_registry_bridge_deps,
        project=project,
        require_issue=False,
        text_modal={
            "title": "Feature Done",
            "label": "Feature Title",
            "placeholder": "Describe implemented feature title",
            "multiline": False,
            "parse_mode": "append",
        },
    )


@bot.tree.command(name="feature_list", description="List implemented features")
@app_commands.describe(project="Optional project key")
async def feature_list_command(interaction: discord.Interaction, project: str | None = None):
    await _run_bridge_with_picker(
        interaction,
        command_name="feature_list",
        handler=feature_list_command_handler,
        deps_factory=_feature_registry_bridge_deps,
        project=project,
        require_issue=False,
    )


@bot.tree.command(name="feature_forget", description="Forget feature from registry")
@app_commands.describe(project="Optional project key", feature="Feature id or title")
async def feature_forget_command(
    interaction: discord.Interaction,
    project: str | None = None,
    feature: str | None = None,
):
    if project and feature:
        await _run_bridge_handler_args(
            interaction,
            command_name="feature_forget",
            parsed_args=[project, feature],
            handler=feature_forget_command_handler,
            deps_factory=_feature_registry_bridge_deps,
        )
        return

    await _run_bridge_with_picker(
        interaction,
        command_name="feature_forget",
        handler=feature_forget_command_handler,
        deps_factory=_feature_registry_bridge_deps,
        project=project,
        require_issue=False,
        text_modal={
            "title": "Forget Feature",
            "label": "Feature ID or Title",
            "placeholder": "feature_id or exact title",
            "multiline": False,
            "parse_mode": "append",
        },
    )


@bot.tree.command(name="start", description="Show welcome and quick actions")
async def start_command(interaction: discord.Interaction):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return
    await interaction.response.send_message(
        "👋 Welcome to Nexus Discord.\nUse `/menu` for categorized actions, `/chat` for threads, and `/help` for full command docs.",
        ephemeral=True,
    )


@bot.tree.command(name="new", description="Start task creation guidance")
async def new_command(interaction: discord.Interaction):
    if not check_permission_for_action(interaction.user.id, action="execute"):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return
    _pending_new_task_capture.pop(interaction.user.id, None)
    await interaction.response.send_message(
        "✨ Create a new task.\n\nSelect a project:",
        view=NewTaskProjectView(interaction.user.id),
    )


@bot.tree.command(name="cancel", description="Cancel pending interactive flows")
async def cancel_command(interaction: discord.Interaction):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return
    _pending_project_resolution.pop(interaction.user.id, None)
    _pending_feature_ideation.pop(interaction.user.id, None)
    _pending_new_task_capture.pop(interaction.user.id, None)
    await interaction.response.send_message("❎ Pending Discord flow canceled.", ephemeral=True)


@bot.tree.command(name="chatagents", description="Show effective chat agent types")
@app_commands.describe(project="Optional project key")
async def chatagents_command(interaction: discord.Interaction, project: str | None = None):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return

    project_key = _normalize_project_key(project) if project else "nexus"
    if project and project_key not in _svc_iter_project_keys(project_config=PROJECT_CONFIG):
        options = ", ".join(_svc_iter_project_keys(project_config=PROJECT_CONFIG))
        await interaction.response.send_message(
            f"❌ Invalid project '{project}'. Valid: {options}", ephemeral=True
        )
        return

    agents = get_chat_agent_types(project_key or "nexus") or []
    if not agents:
        await interaction.response.send_message("ℹ️ No chat agents configured.", ephemeral=True)
        return

    lines = [
        f"🤖 Effective chat agent types for `{project_key}`:",
        *[f"- `{agent}`" for agent in agents],
        "",
        f"Primary: `{agents[0]}`",
    ]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="progress", description="Show running agent progress")
async def progress_command(interaction: discord.Interaction):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return

    launched = HostStateManager.load_launched_agents(recent_only=False) or {}
    if not isinstance(launched, dict) or not launched:
        await interaction.response.send_message("📊 No running agent progress found.", ephemeral=True)
        return

    lines = ["📊 **Running Agent Progress**", ""]
    shown = 0
    for issue_num, payload in sorted(launched.items(), key=lambda item: str(item[0])):
        if not isinstance(payload, dict):
            continue
        pid = payload.get("pid")
        agent = payload.get("agent_type") or payload.get("agent") or "unknown"
        project = payload.get("project") or "unknown"
        state = payload.get("status") or "running"
        lines.append(f"- #{issue_num} · {project} · {agent} · {state} · pid={pid or 'n/a'}")
        shown += 1
        if shown >= 25:
            break

    if shown == 0:
        await interaction.response.send_message("📊 No running agent progress found.", ephemeral=True)
        return

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="chat", description="Manage conversational chat threads")
async def chat_command(interaction: discord.Interaction):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return

    user_id = interaction.user.id
    active_chat_id = get_active_chat(user_id)
    _autoselect_chat_project_from_auth(user_id, active_chat_id)
    chats = list_chats(user_id)

    active_chat_title = "Unknown"
    for c in chats:
        if c.get("id") == active_chat_id:
            active_chat_title = c.get("title")
            break

    active_chat = get_chat(user_id, active_chat_id)

    text = "🗣️ **Nexus Chat Menu**\n\n"
    text += f"**Active Chat:** {active_chat_title}\n"
    text += f"{chat_context_summary(active_chat, ROUTING_PROJECTS, markdown_style='discord')}\n"
    text += "_(All conversational history is saved under this thread)_"

    view = ChatMenuView(user_id)
    await interaction.response.send_message(content=text, view=view)


@bot.tree.command(name="track", description="Track an issue globally or for a specific project")
@discord.app_commands.describe(issue="Issue number (e.g., 123)", project="Optional project key")
async def track_command(interaction: discord.Interaction, issue: str, project: str | None = None):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return

    issue_num = str(issue).lstrip("#").strip()
    if not issue_num.isdigit():
        await interaction.response.send_message("❌ Invalid issue number.", ephemeral=True)
        return

    if project:
        normalized_project = _normalize_project_key(project)
        if normalized_project not in ROUTING_PROJECTS:
            options = ", ".join(sorted(ROUTING_PROJECTS.keys()))
            await interaction.response.send_message(
                f"❌ Invalid project '{project}'. Valid: {options}",
                ephemeral=True,
            )
            return
        if NEXUS_AUTH_ENABLED:
            user = _get_or_create_discord_user(interaction.user)
            allowed_project, error_project = _svc_check_project_access(
                str(user.nexus_id),
                str(normalized_project),
            )
            if not allowed_project:
                await interaction.response.send_message(
                    error_project,
                    ephemeral=True,
                )
                return

        user = _get_or_create_discord_user(interaction.user)
        user_manager.track_issue_by_nexus_id(
            nexus_id=user.nexus_id,
            project=normalized_project,
            issue_number=issue_num,
        )
        await interaction.response.send_message(
            f"👁️ Now tracking {normalized_project} issue #{issue_num} for you."
        )
        return

    tracked = HostStateManager.load_tracked_issues() or {}
    tracked[str(issue_num)] = {
        "project": "global",
        "status": "active",
        "description": f"Issue #{issue_num}",
        "added_at": datetime.now().isoformat(),
        "last_seen_state": None,
        "last_seen_labels": [],
    }
    HostStateManager.save_tracked_issues(tracked)
    await interaction.response.send_message(f"👁️ Now globally tracking issue #{issue_num}.")


@bot.tree.command(name="tracked", description="Show active globally tracked issues")
async def tracked_command(interaction: discord.Interaction):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return

    tracked = HostStateManager.load_tracked_issues() or {}
    lines = ["📌 **Global Tracked Issues**", ""]
    active_count = 0
    for issue_num, payload in sorted(
        tracked.items(),
        key=lambda item: int(item[0]) if str(item[0]).isdigit() else 10**9,
    ):
        entry = payload if isinstance(payload, dict) else {}
        status = str(entry.get("status", "active")).strip().lower() or "active"
        if not _active_status(status):
            continue
        project = str(entry.get("project", "global")).strip() or "global"
        lines.append(f"• #{issue_num} ({project}) — {status}")
        active_count += 1

    if active_count == 0:
        await interaction.response.send_message("📌 No active globally tracked issues.")
        return

    lines.append("")
    lines.append(f"**Active:** {active_count}")
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="myissues", description="Show your tracked issues")
async def myissues_command(interaction: discord.Interaction):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return

    nexus_id = user_manager.resolve_nexus_id("discord", str(interaction.user.id))
    tracked = user_manager.get_user_tracked_issues_by_nexus_id(nexus_id) if nexus_id else {}
    if not tracked:
        await interaction.response.send_message("📋 You're not tracking any project issues yet.")
        return

    lines = ["📋 **Your Tracked Issues**", ""]
    total = 0
    for project, issues in sorted(tracked.items()):
        if not issues:
            continue
        lines.append(f"**{project}**")
        for issue_num in issues:
            lines.append(f"• #{issue_num}")
            total += 1
        lines.append("")
    lines.append(f"**Total:** {total}")
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="status", description="Show pending inbox tasks")
@discord.app_commands.describe(project="Optional project key")
async def status_command(interaction: discord.Interaction, project: str | None = None):
    if not check_permission(interaction.user.id):
        await interaction.response.send_message("🔒 Unauthorized.", ephemeral=True)
        return

    projects = _svc_iter_project_keys(project_config=PROJECT_CONFIG)
    if NEXUS_AUTH_ENABLED:
        user = _get_or_create_discord_user(interaction.user)
        status = _svc_get_setup_status(str(user.nexus_id))
        allowed_projects = {
            str(item).strip().lower()
            for item in (status.get("projects") or [])
            if str(item).strip()
        }
        projects = [p for p in projects if str(p).strip().lower() in allowed_projects]

    if project:
        requested = _normalize_project_key(project)
        if requested not in projects:
            options = ", ".join(sorted(projects))
            await interaction.response.send_message(
                f"❌ Invalid project '{project}'. Valid: {options}",
                ephemeral=True,
            )
            return
        projects = [requested]

    lines = ["📊 **Pending Inbox Tasks**", ""]
    total = 0
    for project_key in sorted(projects):
        workspace = _svc_get_project_workspace(
            project_key=project_key,
            project_config=PROJECT_CONFIG,
        )
        inbox_dir = get_inbox_dir(os.path.join(BASE_DIR, workspace), project_key)
        count = len(glob.glob(os.path.join(inbox_dir, "*.md"))) if os.path.isdir(inbox_dir) else 0
        total += count
        lines.append(f"• {project_key}: {count}")

    lines.append("")
    lines.append(f"**Total Pending:** {total}")
    await interaction.response.send_message("\n".join(lines))


# --- MESSAGE HANDLING ---


@bot.event
async def on_message(message: discord.Message):
    # Ignore bot's own messages
    if message.author == bot.user:
        return

    # Ignore messages not from allowed user
    if not check_permission_for_action(message.author.id, action="execute"):
        if (
            NEXUS_AUTH_ENABLED
            and (not DISCORD_ALLOWED_USER_IDS or message.author.id in DISCORD_ALLOWED_USER_IDS)
        ):
            try:
                await message.reply(_permission_denied_message(message.author.id, action="execute"))
            except Exception:
                pass
        return

    requester_context = _requester_context_for_discord_user(message.author)
    raw_content = (message.content or "").strip()

    # Ignore slash commands or other prefix commands
    if raw_content.startswith("!") or raw_content.startswith("/"):
        return

    text = ""
    status_msg = await message.reply("⚡ Processing...")

    # Check for voice attachments (Discord native voice messages are just .ogg attachments)
    if message.attachments:
        attachment = message.attachments[0]
        if attachment.content_type and "audio/ogg" in attachment.content_type:
            logger.info("Processing voice message...")

            # Download audio to a BytesIO object
            audio_data = io.BytesIO()
            await attachment.save(audio_data)
            audio_data.seek(0)

            # Since our transcribe_audio expects a path, write to a temp file
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as tmp:
                tmp.write(audio_data.read())
                tmp_path = tmp.name

            try:
                # Transcribe
                text = transcribe_audio(tmp_path)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

            if not text:
                logger.warning("Voice transcription returned empty text")
                await status_msg.edit(content="⚠️ Transcription failed")
                return

    # If no voice, or in addition to voice, use message text
    if not text:
        text = message.content

    text = (text or "").strip()
    if not text:
        await status_msg.edit(
            content=(
                "⚠️ I can't read plain message text right now. "
                "Use `/help` and slash commands, or enable Message Content Intent "
                "in Discord Developer Portal and set "
                "`DISCORD_ENABLE_MESSAGE_CONTENT_INTENT=true`."
            )
        )
        return

    if await _handle_pending_feature_ideation(message, text):
        await status_msg.delete()
        return

    if await _begin_feature_ideation(message, text):
        await status_msg.delete()
        return

    pending_new = _pending_new_task_capture.get(message.author.id)
    if isinstance(pending_new, dict):
        candidate = str(text or "").strip().lower()
        if candidate in {"cancel", "/cancel"}:
            _pending_new_task_capture.pop(message.author.id, None)
            await status_msg.edit(content="❎ Task creation canceled.")
            return

        project_key = str(pending_new.get("project") or "").strip().lower()
        task_type = str(pending_new.get("type") or "").strip().lower()
        if project_key not in ROUTING_PROJECTS:
            _pending_new_task_capture.pop(message.author.id, None)
            await status_msg.edit(content="⚠️ Task creation session expired. Run `/new` again.")
            return
        if NEXUS_AUTH_ENABLED:
            allowed_project, error_project = _authorize_project_for_requester(
                project_key,
                requester_context,
            )
            if not allowed_project:
                _pending_new_task_capture.pop(message.author.id, None)
                await status_msg.edit(content=error_project)
                return

        task_prefix = ROUTING_TASK_TYPES.get(task_type, task_type)
        routed_text = f"{task_prefix}: {text}" if task_prefix else text
        result = await process_inbox_task(
            routed_text,
            orchestrator,
            str(message.id),
            project_hint=project_key,
            requester_context=requester_context,
            authorize_project=_authorize_project_for_requester,
        )
        if not result.get("success") and "pending_resolution" in result:
            _pending_project_resolution[message.author.id] = result["pending_resolution"]
        _pending_new_task_capture.pop(message.author.id, None)
        await status_msg.edit(content=result.get("message", "⚠️ Task processing completed."))
        return

    pending_resolution = _pending_project_resolution.get(message.author.id)
    if isinstance(pending_resolution, dict):
        candidate = _normalize_project_key(text)

        if candidate in {"cancel", "/cancel"}:
            _pending_project_resolution.pop(message.author.id, None)
            await status_msg.edit(content="❎ Pending project resolution canceled.")
            return

        if candidate in ROUTING_PROJECTS:
            result = await save_resolved_task(
                pending_resolution,
                candidate,
                str(message.id),
                requester_context=requester_context,
                authorize_project=_authorize_project_for_requester,
            )
            _pending_project_resolution.pop(message.author.id, None)
            await status_msg.edit(content=result.get("message", "✅ Task routed."))
            return

        options = ", ".join(sorted(ROUTING_PROJECTS.keys()))
        await status_msg.edit(
            content=(
                "⚠️ Pending task needs a project key. "
                f"Reply with one of: {options} (or type `cancel`)."
            )
        )
        return

    logger.info(f"Detecting intent for: {text[:50]}...")
    intent_result = parse_intent_result(orchestrator, text, extract_json_dict)
    intent = intent_result.get("intent", "task")

    if intent == "conversation":
        user_id = message.author.id
        await status_msg.edit(content="🤖 **Nexus:** Thinking...")

        reply_text = run_conversation_turn(
            user_id=user_id,
            text=text,
            orchestrator=orchestrator,
            get_chat_history=get_chat_history,
            append_message=append_message,
            persona=AI_PERSONA,
            project_name=((get_chat(user_id) or {}).get("metadata", {}) or {}).get("project_key"),
        )

        await status_msg.edit(content=f"🤖 **Nexus**: \n\n{reply_text}")
        return

    # If it's a task, route through the shared inbox_routing_handler
    result = await route_task_with_context(
        user_id=message.author.id,
        text=text,
        orchestrator=orchestrator,
        message_id=str(message.id),
        get_chat=get_chat,
        process_inbox_task=process_inbox_task,
        requester_context=requester_context,
        authorize_project=_authorize_project_for_requester,
    )

    # Store pending_resolution state if manual project selection is needed
    if not result["success"] and "pending_resolution" in result:
        _pending_project_resolution[message.author.id] = result["pending_resolution"]
        logger.warning(f"Task needs manual project resolution: {result['pending_resolution']}")

    await status_msg.edit(content=result["message"])


@bot.event
async def on_ready():
    logger.info(f"Discord bot connected as {bot.user}")

    try:
        validate_required_command_interface()
        parity = validate_command_parity()
        telegram_only = sorted(parity.get("telegram_only", set()))
        discord_only = sorted(parity.get("discord_only", set()))
        if telegram_only or discord_only:
            logger.warning(
                "Command parity drift detected: telegram_only=%s discord_only=%s",
                telegram_only,
                discord_only,
            )
    except Exception:
        logger.exception("Command parity strict check failed")
        raise

@bot.event
async def setup_hook():
    # Sync slash commands during setup.
    # If a guild is configured, keep commands guild-scoped to avoid duplicate
    # command listings from having both global + guild registrations.
    if DISCORD_GUILD_ID:
        guild = discord.Object(id=DISCORD_GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        guild_synced = await bot.tree.sync(guild=guild)
        logger.info(
            "Synced %s slash commands to guild %s",
            len(guild_synced),
            DISCORD_GUILD_ID,
        )
        bot.tree.clear_commands(guild=None)
        cleared_global = await bot.tree.sync()
        logger.info("Cleared global slash commands (remaining count=%s)", len(cleared_global))
        return

    global_synced = await bot.tree.sync()
    logger.info("Synced %s slash commands globally", len(global_synced))


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN environment variable not set.")
        sys.exit(1)

    bot.run(DISCORD_TOKEN)
