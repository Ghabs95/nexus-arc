"""Operational command handlers extracted from telegram_bot."""

from __future__ import annotations

import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nexus.core.handlers.agent_definition_utils import extract_agent_identity
from nexus.core.handlers.agent_resolution_handler import resolve_agents_for_project
from nexus.core.ops_direct_service import handle_direct_request as _service_handle_direct_request
from nexus.core.utils.log_utils import log_unauthorized_access

if TYPE_CHECKING:
    from nexus.core.interactive.context import InteractiveContext


@dataclass
class OpsHandlerDeps:
    logger: Any
    allowed_user_ids: list[int]
    base_dir: str
    nexus_dir_name: str
    project_config: dict[str, dict[str, Any]]
    prompt_project_selection: Callable[[InteractiveContext, str], Awaitable[None]]
    ensure_project_issue: Callable[
        [InteractiveContext, str], Awaitable[tuple[str | None, str | None, list[str]]]
    ]
    get_project_label: Callable[[str], str]
    get_stats_report: Callable[[int], str]
    get_inbox_storage_backend: Callable[[], str]
    get_inbox_queue_overview: Callable[[int], dict[str, Any]]
    format_error_for_user: Callable[[Exception, str], str]
    get_audit_history: Callable[[str, int], list[dict[str, Any]]]
    get_repo: Callable[[str], str]
    get_direct_issue_plugin: Callable[[str], Any]
    orchestrator: Any
    ai_persona: str
    get_chat_history: Callable[[int], str]
    append_message: Callable[[int, str, str], None]
    create_chat: Callable[..., str]
    requester_context_builder: Callable[[int], dict[str, Any]] | None = None
    run_doctor: Callable[..., Awaitable[dict[str, Any]]] | None = None


def _normalize_agent_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def _resolve_agent_type(
    agent_name: str,
    source_filename: str,
    agents_dir: str,
    nexus_dir_name: str,
    available_agent_types: list[str] | None = None,
) -> str | None:
    candidate_paths = [
        os.path.join(agents_dir, nexus_dir_name, "agents", source_filename),
        os.path.join(agents_dir, source_filename),
    ]

    for candidate_path in candidate_paths:
        if not os.path.isfile(candidate_path):
            continue

        if candidate_path.endswith((".yaml", ".yml")):
            try:
                _agent_name, agent_type = extract_agent_identity(candidate_path)
                if agent_type:
                    return agent_type
            except Exception:
                continue

    normalized = _normalize_agent_key(agent_name)

    normalized_types: list[str] = []
    if isinstance(available_agent_types, list):
        for item in available_agent_types:
            candidate = str(item or "").strip().lower()
            if candidate and candidate not in normalized_types:
                normalized_types.append(candidate)

    if normalized in normalized_types:
        return normalized

    source_stem = os.path.splitext(os.path.basename(source_filename or ""))[0]
    normalized_stem = _normalize_agent_key(source_stem)
    if normalized_stem in normalized_types:
        return normalized_stem

    return None


def _build_direct_chat_persona(
    base_persona: str, project: str, agent_name: str, agent_type: str
) -> str:
    safe_base = base_persona or "You are a helpful AI assistant."
    context_block = (
        "\n\nDirect Conversation Context:\n"
        f"- Project: {project}\n"
        f"- Requested agent: @{agent_name}\n"
        f"- Routed agent_type: {agent_type}\n"
        "Behavior rules:\n"
        f"- Respond in the voice and decision style of `{agent_type}`.\n"
        "- This is a direct chat reply, not a workflow ticket.\n"
        "- Keep the answer concise, actionable, and business-oriented."
    )
    return f"{safe_base}{context_block}"


async def audit_handler(ctx: InteractiveContext, deps: OpsHandlerDeps) -> None:
    deps.logger.info(f"Audit trail requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "audit")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "audit")
    if not project_key:
        return

    msg_id = await ctx.reply_text(f"📊 Fetching audit trail for issue #{issue_num}...")

    try:
        audit_history = deps.get_audit_history(issue_num, 100)

        if not audit_history:
            await ctx.edit_message_text(
                message_id=msg_id,
                text=f"📊 **Audit Trail for Issue #{issue_num}**\n\nNo audit events recorded yet.",
            )
            return

        timeline = f"📊 **Audit Trail for Issue #{issue_num}**\n"
        timeline += "=" * 40 + "\n\n"

        event_emoji = {
            "AGENT_LAUNCHED": "🚀",
            "AGENT_TIMEOUT_KILL": "⏱️",
            "AGENT_RETRY": "🔄",
            "AGENT_FAILED": "❌",
            "WORKFLOW_PAUSED": "⏸️",
            "WORKFLOW_RESUMED": "▶️",
            "WORKFLOW_STOPPED": "🛑",
            "AGENT_COMPLETION": "✅",
            "WORKFLOW_STARTED": "🎬",
            "WORKFLOW_CREATED": "📋",
            "STEP_STARTED": "▶️",
            "STEP_COMPLETED": "✅",
        }

        for evt in audit_history:
            try:
                event_type = evt.get("event_type", "?")
                timestamp = evt.get("timestamp", "?")
                data = evt.get("data", {})
                details = data.get("details", "") if isinstance(data, dict) else ""
                emoji = event_emoji.get(event_type, "•")

                timeline += f"{emoji} **{event_type}** ({timestamp})\n"
                if details:
                    timeline += f"   {details}\n"
                timeline += "\n"
            except Exception as exc:
                deps.logger.warning(f"Error formatting audit event: {exc}")
                timeline += f"• {evt}\n\n"

        max_len = 3500
        if len(timeline) <= max_len:
            await ctx.edit_message_text(
                message_id=msg_id,
                text=timeline,
            )
        else:
            chunks = [timeline[i : i + max_len] for i in range(0, len(timeline), max_len)]
            await ctx.edit_message_text(
                message_id=msg_id,
                text=chunks[0],
            )
            for chunk in chunks[1:]:
                await ctx.reply_text(text=chunk)
    except Exception as exc:
        deps.logger.error(f"Error in audit_handler: {exc}", exc_info=True)
        error_msg = deps.format_error_for_user(exc, "while fetching audit trail")
        await ctx.reply_text(error_msg)


async def stats_handler(ctx: InteractiveContext, deps: OpsHandlerDeps) -> None:
    deps.logger.info(f"Stats requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    msg_id = await ctx.reply_text("📊 Generating analytics report...")

    try:
        lookback_days = 30
        if ctx.args and len(ctx.args) > 0:
            try:
                lookback_days = int(ctx.args[0])
                if lookback_days < 1 or lookback_days > 365:
                    await ctx.reply_text(
                        "⚠️ Lookback days must be between 1 and 365. Using default 30 days."
                    )
                    lookback_days = 30
            except ValueError:
                await ctx.reply_text("⚠️ Invalid lookback days. Using default 30 days.")
                lookback_days = 30

        report = deps.get_stats_report(lookback_days)

        max_len = 3500
        if len(report) <= max_len:
            await ctx.edit_message_text(
                message_id=msg_id,
                text=report,
            )
        else:
            chunks = [report[i : i + max_len] for i in range(0, len(report), max_len)]
            await ctx.edit_message_text(
                message_id=msg_id,
                text=chunks[0],
            )
            for chunk in chunks[1:]:
                await ctx.reply_text(text=chunk)

    except FileNotFoundError:
        await ctx.edit_message_text(
            message_id=msg_id,
            text="📊 No audit log found. System has not logged any workflow events yet.",
        )
    except Exception as exc:
        deps.logger.error(f"Error in stats_handler: {exc}", exc_info=True)
        error_msg = deps.format_error_for_user(exc, "while generating analytics report")
        await ctx.edit_message_text(
            message_id=msg_id,
            text=error_msg,
        )


def _doctor_fix_requested(args: list[str]) -> bool:
    return any(str(item or "").strip().lower() in {"fix", "--fix"} for item in args)


def _doctor_issue_args(args: list[str]) -> list[str]:
    return [item for item in args if str(item or "").strip().lower() not in {"fix", "--fix"}]


def _doctor_runtime_target(args: list[str]) -> str | None:
    aliases = {
        "oc": "openclaw",
        "openclaw": "openclaw",
        "runtime": "openclaw",
        "instance": "openclaw",
        "gateway": "gateway",
        "chat": "chat",
        "session": "session",
    }
    for item in args:
        token = str(item or "").strip().lower()
        if token in aliases:
            return aliases[token]
    return None


def _format_doctor_payload(payload: dict[str, Any], *, fix_requested: bool) -> str:
    if not payload.get("ok"):
        return f"❌ Doctor failed: {payload.get('error') or 'unknown error'}"

    lines: list[str] = ["🩺 **Doctor**"]
    scope = str(payload.get("scope") or "runtime").strip().lower()
    if scope == "workflow":
        workflow = payload.get("workflow") if isinstance(payload.get("workflow"), dict) else {}
        diagnosis = payload.get("diagnosis") if isinstance(payload.get("diagnosis"), dict) else {}
        fix = payload.get("fix") if isinstance(payload.get("fix"), dict) else {}
        workflow_id = str(workflow.get("workflow_id") or payload.get("workflow_id") or "").strip()
        issue_number = str(workflow.get("issue_number") or payload.get("issue_number") or "").strip()
        state = str(workflow.get("state") or "unknown").strip()
        current_step = workflow.get("current_step") if isinstance(workflow.get("current_step"), dict) else {}
        current_agent = str(current_step.get("agent") or "").strip() or "unknown"
        summary = str(diagnosis.get("summary") or "").strip()
        cause = str(diagnosis.get("likely_cause") or "").strip()
        lines.append(f"Workflow: `{workflow_id or 'unknown'}`")
        if issue_number:
            lines.append(f"Issue: `#{issue_number}`")
        lines.append(f"State: `{state}`")
        lines.append(f"Current agent: `{current_agent}`")
        if summary:
            lines.append(f"Diagnosis: {summary}")
        if cause:
            lines.append(f"Likely cause: {cause}")
        if fix_requested:
            action = str(fix.get("action") or "").strip() or "none"
            if fix.get("applied"):
                target = str(fix.get("target_agent") or "").strip()
                lines.append(f"Fix: applied `{action}`{f' via `{target}`' if target else ''}")
            else:
                reason = str(fix.get("reason") or "").strip() or "No safe automated fix available."
                lines.append(f"Fix: not applied. {reason}")
    else:
        runtime = payload.get("runtime_health") if isinstance(payload.get("runtime_health"), dict) else {}
        incidents = payload.get("recent_incidents") if isinstance(payload.get("recent_incidents"), dict) else {}
        openclaw = runtime.get("openclaw") if isinstance(runtime.get("openclaw"), dict) else {}
        oc_checks = openclaw.get("checks") if isinstance(openclaw.get("checks"), list) else []
        runtime_warnings = runtime.get("warnings") if isinstance(runtime.get("warnings"), list) else []
        warning_count = len(runtime_warnings) + sum(
            1 for item in oc_checks if isinstance(item, dict) and str(item.get("status")) == "warning"
        )
        error_count = sum(1 for item in oc_checks if isinstance(item, dict) and str(item.get("status")) == "error")
        count = int(incidents.get("count") or 0)
        summary = str(incidents.get("summary") or "").strip()
        target = str(payload.get("target") or "openclaw").strip()
        lines.append(f"Target: `{target}`")
        lines.append(f"OpenClaw installed: `{'yes' if openclaw.get('installed') else 'no'}`")
        lines.append(f"OpenClaw healthy: `{'yes' if openclaw.get('healthy') else 'no'}`")
        lines.append(f"Runtime warnings: `{warning_count}`")
        lines.append(f"Runtime errors: `{error_count}`")
        lines.append(f"Recent incidents: `{count}`")
        if summary:
            lines.append(f"Summary: {summary}")
        if fix_requested:
            fix = payload.get("fix") if isinstance(payload.get("fix"), dict) else {}
            action = str(fix.get("action") or "").strip()
            if fix.get("applied"):
                lines.append(f"Fix: applied `{action or 'openclaw_recovery'}`")
            else:
                reason = str(fix.get("reason") or "").strip() or "No automated recovery was applied."
                lines.append(f"Fix: not applied. {reason}")
    return "\n".join(lines)


async def doctor_handler(ctx: InteractiveContext, deps: OpsHandlerDeps) -> None:
    deps.logger.info(f"Doctor requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return
    if not callable(deps.run_doctor):
        await ctx.reply_text("❌ Doctor service is not configured for this runtime.")
        return

    fix_requested = _doctor_fix_requested(ctx.args)
    target = _doctor_runtime_target(ctx.args)
    original_args = list(ctx.args)
    try:
        msg_id = await ctx.reply_text("🩺 Running OpenClaw doctor checks...")
        payload = await deps.run_doctor(
            project_key=None,
            issue_number=None,
            target=target,
            apply_fix=fix_requested,
        )
        await ctx.edit_message_text(
            message_id=msg_id,
            text=_format_doctor_payload(payload, fix_requested=fix_requested),
        )
    except Exception as exc:
        deps.logger.error(f"Error in doctor_handler: {exc}", exc_info=True)
        error_msg = deps.format_error_for_user(exc, "while running doctor")
        await ctx.reply_text(error_msg)
    finally:
        ctx.args = original_args


async def inboxq_handler(ctx: InteractiveContext, deps: OpsHandlerDeps) -> None:
    deps.logger.info(f"Inbox queue overview requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    backend = deps.get_inbox_storage_backend()
    if backend == "filesystem":
        await ctx.reply_text(
            "📥 Inbox backend is `filesystem` (queue inspection is available for postgres mode)."
        )
        return

    limit = 10
    if ctx.args and len(ctx.args) > 0:
        try:
            limit = max(1, min(int(ctx.args[0]), 50))
        except ValueError:
            await ctx.reply_text("⚠️ Invalid limit. Using default 10.")
            limit = 10

    msg_id = await ctx.reply_text("📥 Reading inbox queue status...")

    try:
        overview = deps.get_inbox_queue_overview(limit)
        counts = overview.get("counts", {}) if isinstance(overview, dict) else {}
        recent = overview.get("recent", []) if isinstance(overview, dict) else []
        effective_limit = int(overview.get("limit", limit)) if isinstance(overview, dict) else limit

        lines = [
            "📥 **Inbox Queue Overview**",
            f"Backend: `{backend}`",
            (
                "Counts: "
                f"pending={int(counts.get('pending', 0))}, "
                f"processing={int(counts.get('processing', 0))}, "
                f"done={int(counts.get('done', 0))}, "
                f"failed={int(counts.get('failed', 0))}, "
                f"total={int(counts.get('total', 0))}"
            ),
            "",
            f"Latest {effective_limit} tasks:",
        ]

        if not recent:
            lines.append("- (no tasks)")
        else:
            for item in recent:
                task_id = int(item.get("id", 0))
                project_key = str(item.get("project_key", "?"))
                status = str(item.get("status", "?"))
                filename = str(item.get("filename", ""))
                if len(filename) > 40:
                    filename = f"…{filename[-40:]}"
                lines.append(f"- #{task_id} [{status}] {project_key} · {filename}")

        await ctx.edit_message_text(
            message_id=msg_id,
            text="\n".join(lines),
        )
    except Exception as exc:
        deps.logger.error(f"Error in inboxq_handler: {exc}", exc_info=True)
        error_msg = deps.format_error_for_user(exc, "while reading inbox queue")
        await ctx.edit_message_text(
            message_id=msg_id,
            text=error_msg,
        )


async def agents_handler(ctx: InteractiveContext, deps: OpsHandlerDeps) -> None:
    deps.logger.info(f"Agents requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "agents")
        return

    project = ctx.args[0].lower()
    if project not in deps.project_config:
        await ctx.reply_text(
            f"❌ Unknown project '{project}'\n\n"
            f"Available: " + ", ".join(deps.project_config.keys())
        )
        return

    agents_dir = os.path.join(deps.base_dir, deps.project_config[project]["agents_dir"])
    if not os.path.exists(agents_dir):
        await ctx.reply_text(f"⚠️ Agents directory not found for '{project}'")
        return

    try:
        agents_map = resolve_agents_for_project(agents_dir, deps.nexus_dir_name)

        if not agents_map:
            await ctx.reply_text(f"No agents configured for '{project}'")
            return

        agents_list = "\n".join([f"• @{agent}" for agent in sorted(agents_map.keys())])
        await ctx.reply_text(
            f"🤖 **Agents for {project}:**\n\n{agents_list}\n\n"
            "Use /direct <project> <@agent> <message> to send a direct request.\n"
            "Use /chat for project-scoped conversations and strategy threads."
        )
    except Exception as exc:
        deps.logger.error(f"Error listing agents: {exc}")
        await ctx.reply_text(f"❌ Error: {exc}")


async def direct_handler(ctx: InteractiveContext, deps: OpsHandlerDeps) -> None:
    await _service_handle_direct_request(
        ctx,
        deps,
        resolve_agent_type=_resolve_agent_type,
        build_direct_chat_persona=_build_direct_chat_persona,
    )
    return
