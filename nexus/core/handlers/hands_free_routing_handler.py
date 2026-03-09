"""Hands-free message intent routing extracted from telegram_bot."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nexus.core.interactive.context import InteractiveContext

from nexus.adapters.notifications.base import Button
from nexus.core.chat_agents_schema import (
    get_default_project_chat_agent_type,
    get_project_chat_agent_config,
)

from nexus.core.handlers.agent_context_utils import (
    load_agent_prompt_from_definition,
    load_role_context,
    resolve_project_root,
)
from nexus.core.handlers.common_routing import (
    parse_intent_result,
    route_task_with_context,
    run_conversation_turn,
)
from nexus.core.handlers.feature_ideation_handlers import (
    FEATURE_STATE_KEY,
    detect_feature_ideation_intent,
    handle_feature_ideation_request,
)


@dataclass
class HandsFreeRoutingDeps:
    logger: logging.Logger
    orchestrator: Any
    ai_persona: str
    projects: dict[str, str]
    extract_json_dict: Callable[[str], dict[str, Any] | None]
    get_chat_history: Callable[[int], str]
    append_message: Callable[[int, str, str], None]
    get_chat: Callable[[int], dict[str, Any]]
    process_inbox_task: Callable[..., Awaitable[dict[str, Any]]]
    normalize_project_key: Callable[[str], str | None]
    save_resolved_task: Callable[..., Awaitable[dict[str, Any]]]
    task_confirmation_mode: str
    requester_context_builder: Callable[[int], dict[str, Any]] | None = None
    authorize_project: Callable[[str, dict[str, Any] | None], tuple[bool, str]] | None = None
    feature_ideation_deps: Any = None
    base_dir: str = ""
    project_config: dict[str, Any] | None = None


def _configured_primary_agent_type(project_key: str) -> str:
    normalized_project_key = str(project_key or "").strip().lower()
    if not normalized_project_key:
        return ""

    try:
        from nexus.core.config import _get_project_config

        full_config = _get_project_config()
    except Exception:
        return ""

    if not isinstance(full_config, dict):
        return ""

    project_cfg = full_config.get(normalized_project_key)
    if not isinstance(project_cfg, dict):
        return ""

    return get_default_project_chat_agent_type(project_cfg)


def _resolve_primary_agent_type(metadata: dict[str, Any]) -> str:
    primary = str(metadata.get("primary_agent_type") or "").strip().lower()
    if primary:
        return primary

    allowed = _normalize_allowed_agent_types(metadata)
    if allowed:
        return allowed[0]

    project_key = str(metadata.get("project_key") or "").strip().lower()
    return _configured_primary_agent_type(project_key)


def _normalize_allowed_agent_types(metadata: dict[str, Any]) -> list[str]:
    allowed = metadata.get("allowed_agent_types")
    if not isinstance(allowed, list):
        return []
    cleaned: list[str] = []
    for item in allowed:
        if isinstance(item, str) and item.strip():
            cleaned.append(item.strip())
    return cleaned


def _detect_conversation_intent(text: str) -> str:
    candidate = (text or "").lower()
    if not candidate:
        return "general"

    gtm_terms = [
        "go to market",
        "gtm",
        "positioning",
        "campaign",
        "acquisition",
        "funnel",
        "brand",
        "launch plan",
        "channel strategy",
    ]
    business_terms = [
        "revenue",
        "pricing",
        "profit",
        "margin",
        "monetization",
        "kpi",
        "retention",
        "business model",
        "roi",
        "unit economics",
    ]
    strategy_terms = [
        "vision",
        "roadmap",
        "priorit",
        "north star",
        "objective",
        "strategy",
        "direction",
        "focus",
        "decision",
        "tradeoff",
    ]

    if any(term in candidate for term in gtm_terms):
        return "gtm"
    if any(term in candidate for term in business_terms):
        return "business"
    if any(term in candidate for term in strategy_terms):
        return "strategy"
    return "general"


def _select_conversation_agent_type(metadata: dict[str, Any], text: str) -> tuple[str, str, str]:
    allowed_agent_types = _normalize_allowed_agent_types(metadata)
    primary_agent_type = _resolve_primary_agent_type(metadata)
    chat_mode = str(metadata.get("chat_mode", "strategy")).lower().strip() or "strategy"
    intent = _detect_conversation_intent(text)
    business_role = "business"
    if allowed_agent_types and business_role not in allowed_agent_types:
        business_role = primary_agent_type

    preferred_by_intent = {
        "gtm": "marketing",
        "business": business_role,
        "strategy": primary_agent_type,
        "general": primary_agent_type,
    }
    execution_overrides = {
        "gtm": "marketing",
        "business": business_role,
        "strategy": business_role,
        "general": primary_agent_type,
    }

    preferred = preferred_by_intent.get(intent, primary_agent_type)
    if chat_mode == "execution":
        preferred = execution_overrides.get(intent, preferred)

    if allowed_agent_types:
        if preferred in allowed_agent_types:
            return preferred, intent, f"intent={intent}, mode={chat_mode}, allowed_match"
        if primary_agent_type in allowed_agent_types:
            return (
                primary_agent_type,
                intent,
                f"intent={intent}, mode={chat_mode}, primary_fallback",
            )
        return (
            allowed_agent_types[0],
            intent,
            f"intent={intent}, mode={chat_mode}, first_allowed_fallback",
        )

    if preferred:
        return preferred, intent, f"intent={intent}, mode={chat_mode}, unrestricted"

    return "triage", intent, f"intent={intent}, mode={chat_mode}, global_fallback"


def _build_chat_persona(
    deps: HandsFreeRoutingDeps,
    user_id: int,
    routed_agent_type: str,
    detected_intent: str,
    routing_reason: str,
    user_text: str,
) -> str:
    chat_data = deps.get_chat(user_id) or {}
    metadata = chat_data.get("metadata") or {}

    project_key = metadata.get("project_key")
    project_label = deps.projects.get(project_key, project_key or "Not set")
    chat_mode = str(metadata.get("chat_mode", "strategy"))
    primary_agent_type = _resolve_primary_agent_type(metadata) or "unknown"
    project_cfg = {}
    if isinstance(getattr(deps, "project_config", None), dict) and project_key:
        candidate = deps.project_config.get(project_key)
        if isinstance(candidate, dict):
            project_cfg = candidate

    project_root = ""
    agent_prompt = ""
    role_context = ""
    if project_key and project_cfg:
        try:
            project_root = resolve_project_root(
                str(getattr(deps, "base_dir", "") or ""), project_key, project_cfg
            )
            agent_prompt = load_agent_prompt_from_definition(
                base_dir=str(getattr(deps, "base_dir", "") or ""),
                project_root=project_root,
                project_cfg=project_cfg,
                routed_agent_type=routed_agent_type,
            )
            agent_cfg = get_project_chat_agent_config(project_cfg, routed_agent_type)
            role_context = load_role_context(project_root=project_root, agent_cfg=agent_cfg)
        except Exception as exc:
            deps.logger.warning(
                "Could not load chat role context for project=%s agent_type=%s: %s",
                project_key,
                routed_agent_type,
                exc,
            )

    context_block = (
        "Active Chat Context:\n"
        f"- Project: {project_label} ({project_key or 'none'})\n"
        f"- Chat mode: {chat_mode}\n"
        f"- Primary agent_type: {primary_agent_type}\n"
        f"- Routed agent_type: {routed_agent_type}\n"
        f"- Detected intent: {detected_intent}\n"
        f"- Routing reason: {routing_reason}\n"
        "Behavior rules:\n"
        f"- Respond in the voice and decision style of `{routed_agent_type}`.\n"
        "- Keep recommendations scoped to the active project context.\n"
        "- If context is missing, ask a short clarification before making assumptions."
    )
    execution_requested = _looks_like_explicit_task_request(user_text)
    if chat_mode != "execution" and not execution_requested:
        context_block += (
            "\n- Strategy mode: do not ask for issue numbers, PR links, branch names, or commit artifacts "
            "unless the user explicitly asks to implement/execute."
            "\n- For feature ideation, provide a complete proposal first (solution, acceptance criteria, risks/tradeoffs)."
        )
    else:
        context_block += "\n- Execution details (issue/PR/branch/commit) are allowed only when implementation is explicitly requested."
    sections: list[str] = []
    if agent_prompt:
        sections.append(
            "Use this dedicated agent definition as your operating role and voice "
            f"for `{routed_agent_type}`:\n{agent_prompt}"
        )
    sections.append(context_block)
    if role_context:
        sections.append(role_context.strip())
    return "\n\n".join(part for part in sections if part)


def _has_active_feature_ideation(ctx: InteractiveContext) -> bool:
    feature_state = ctx.user_state.get(FEATURE_STATE_KEY)
    if not isinstance(feature_state, dict):
        return False
    items = feature_state.get("items")
    return isinstance(items, list) and len(items) > 0


def _has_active_chat_session(ctx: InteractiveContext) -> bool:
    return bool(ctx.user_state.get("chat_session_active"))


def _looks_like_explicit_task_request(text: str) -> bool:
    candidate = str(text or "").strip().lower()
    if not candidate:
        return False

    explicit_phrases = (
        "create task",
        "create a task",
        "make this a task",
        "route this",
        "execute this",
        "execute it",
        "open issue",
        "file issue",
        "implement this",
        "implement it",
        "build this",
        "ship this",
        "go with option",
        "use option",
    )
    return any(phrase in candidate for phrase in explicit_phrases)


async def resolve_pending_project_selection(
    ctx: InteractiveContext,
    deps: HandsFreeRoutingDeps,
) -> bool:
    pending_project = ctx.user_state.get("pending_task_project_resolution")
    if not pending_project:
        return False

    if len(deps.projects) == 1:
        selected = next(iter(deps.projects.keys()))
    else:
        selected = deps.normalize_project_key((ctx.text or "").strip())
    if not selected or selected not in deps.projects:
        options = ", ".join(sorted(deps.projects.keys()))
        await ctx.reply_text(f"Please reply with a valid project key: {options}")
        return True

    ctx.user_state.pop("pending_task_project_resolution", None)

    # We don't have update.message.message_id easily without raw_event casting, but we can pass an empty string or dummy if necessary
    trigger_message_id = (
        getattr(ctx.raw_event, "message_id", "") if hasattr(ctx.raw_event, "message_id") else ""
    )
    if hasattr(ctx.raw_event, "message") and hasattr(ctx.raw_event.message, "message_id"):
        trigger_message_id = str(ctx.raw_event.message.message_id)

    requester_context = (
        deps.requester_context_builder(int(ctx.user_id))
        if callable(deps.requester_context_builder)
        else None
    )
    result = await deps.save_resolved_task(
        pending_project,
        selected,
        str(trigger_message_id),
        requester_context=requester_context,
        authorize_project=deps.authorize_project,
    )
    await ctx.reply_text(result["message"])
    return True


async def route_hands_free_text(ctx: InteractiveContext, deps: HandsFreeRoutingDeps) -> None:
    text = ctx.text
    requester_context = (
        deps.requester_context_builder(int(ctx.user_id))
        if callable(deps.requester_context_builder) and str(ctx.user_id).isdigit()
        else None
    )
    user_numeric_id = int(ctx.user_id) if str(ctx.user_id).isdigit() else 0
    chat_data = deps.get_chat(user_numeric_id) or {}
    metadata = chat_data.get("metadata") if isinstance(chat_data, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}

    # First check if we're resolving a project selection
    if await resolve_pending_project_selection(ctx, deps):
        return

    if _has_active_chat_session(ctx) and _looks_like_explicit_task_request(text):
        await ctx.reply_text(
            "🗣️ Chat mode is active.\n\n"
            "Exit chat mode before using hands-free task creation, or use /new for guided task creation."
        )
        return

    active_chat_session = _has_active_chat_session(ctx)
    force_conversation = _has_active_feature_ideation(
        ctx
    ) and not _looks_like_explicit_task_request(text)
    if force_conversation:
        deps.logger.info(
            "Active feature ideation detected; routing follow-up as conversation: %s",
            text[:50],
        )
        intent_result = {
            "intent": "conversation",
            "feature_ideation": False,
            "confidence": 0.0,
            "reason": (
                "chat_session_active"
                if _has_active_chat_session(ctx)
                else "active_feature_ideation_followup"
            ),
        }
    else:
        deps.logger.info("Detecting intent for: %s...", text[:50])
        intent_result = parse_intent_result(
            deps.orchestrator,
            text,
            deps.extract_json_dict,
            requester_context=requester_context,
        )

    intent = intent_result.get("intent", "task")
    raw_feature_ideation = intent_result.get("feature_ideation")
    feature_ideation = (
        raw_feature_ideation
        if isinstance(raw_feature_ideation, bool)
        else (str(raw_feature_ideation).strip().lower() in {"1", "true", "yes"})
    )
    try:
        fi_confidence = float(intent_result.get("feature_ideation_confidence", 0.0))
    except (TypeError, ValueError):
        fi_confidence = 0.0
    fi_reason = str(intent_result.get("feature_ideation_reason", "")).strip() or "not_provided"
    feature_detector = getattr(deps.orchestrator, "run_text_to_speech_analysis", None)
    if (
        not feature_ideation
        and not force_conversation
        and callable(feature_detector)
    ):
        feature_ideation, fi_confidence, fi_reason = detect_feature_ideation_intent(
            text,
            run_analysis=feature_detector,
            logger=deps.logger,
        )
    deps.logger.info(
        "Feature ideation detection: matched=%s confidence=%.2f reason=%s",
        feature_ideation,
        fi_confidence,
        fi_reason,
    )
    if active_chat_session and not feature_ideation:
        intent = "conversation"
    if feature_ideation:
        deps.logger.info("Feature ideation request detected in hands-free text: %s", text[:50])
        status_msg = await ctx.reply_text("🧠 *Thinking about features...*")
        await handle_feature_ideation_request(
            ctx=ctx,
            status_msg_id=status_msg,
            text=text,
            deps=deps.feature_ideation_deps,
            preferred_project_key=str(metadata.get("project_key") or "").strip() or None,
            preferred_agent_type=str(metadata.get("primary_agent_type") or "").strip() or None,
            detected_feature_ideation=True,
            detection_confidence=fi_confidence,
            detection_reason=fi_reason,
        )
        return

    is_voice = (
        str(getattr(ctx.raw_event, "voice", False)) == "True"
        or getattr(getattr(ctx.raw_event, "message", None), "voice", None) is not None
    )

    status_msg_id = await ctx.reply_text("🤖 *Nexus:* Thinking...")

    if intent == "conversation":
        routed_agent_type, detected_intent, routing_reason = _select_conversation_agent_type(
            metadata, text
        )

        deps.logger.info(
            "Conversation routing selected agent_type=%s (%s)",
            routed_agent_type,
            routing_reason,
        )
        persona = _build_chat_persona(
            deps,
            user_numeric_id,
            routed_agent_type,
            detected_intent,
            routing_reason,
            text,
        )

        reply_text = run_conversation_turn(
            user_id=user_numeric_id,
            text=text,
            orchestrator=deps.orchestrator,
            get_chat_history=deps.get_chat_history,
            append_message=deps.append_message,
            persona=persona,
            project_name=metadata.get("project_key"),
            requester_context=requester_context,
        )

        await ctx.edit_message_text(
            message_id=status_msg_id,
            text=f"🤖 *Nexus ({routed_agent_type})*: \n\n{reply_text}",
        )
        return

    confirmation_mode = str(deps.task_confirmation_mode or "smart").strip().lower()
    if confirmation_mode not in {"off", "always", "smart"}:
        confirmation_mode = "smart"

    confidence = None
    if isinstance(intent_result, dict):
        confidence = intent_result.get("intent_confidence", intent_result.get("confidence"))
    try:
        confidence_value = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence_value = None

    has_project_context = bool(metadata.get("project_key"))

    should_confirm = False
    if confirmation_mode == "always":
        should_confirm = True
    elif confirmation_mode == "smart":
        should_confirm = bool(
            is_voice
            or not has_project_context
            or (confidence_value is not None and confidence_value < 0.8)
        )

    trigger_message_id = (
        getattr(ctx.raw_event, "message_id", "") if hasattr(ctx.raw_event, "message_id") else ""
    )
    if hasattr(ctx.raw_event, "message") and hasattr(ctx.raw_event.message, "message_id"):
        trigger_message_id = str(ctx.raw_event.message.message_id)

    if should_confirm:
        ctx.user_state["pending_task_confirmation"] = {
            "text": text,
            "message_id": str(trigger_message_id),
            "attachments": ctx.attachments or [],
        }
        reason = "voice input" if is_voice else "auto-routing safety check"
        if not has_project_context:
            reason = "missing project context"
        elif confidence_value is not None and confidence_value < 0.8:
            reason = f"low intent confidence ({confidence_value:.2f})"

        preview = text if len(text) <= 300 else f"{text[:300]}..."
        buttons = [
            [Button("✅ Confirm", callback_data="taskconfirm:confirm")],
            [Button("✏️ Edit", callback_data="taskconfirm:edit")],
            [Button("❌ Cancel", callback_data="taskconfirm:cancel")],
        ]
        await ctx.edit_message_text(
            message_id=status_msg_id,
            text=(
                "🛡️ *Confirm task creation*\n\n"
                f"Reason: {reason}\n"
                "I’m about to create a task from this request:\n\n"
                f"_{preview}_"
            ),
            buttons=buttons,
        )
        return

    result = await route_task_with_context(
        user_id=user_numeric_id,
        text=text,
        orchestrator=deps.orchestrator,
        message_id=str(trigger_message_id),
        get_chat=deps.get_chat,
        process_inbox_task=deps.process_inbox_task,
        requester_context=requester_context,
        authorize_project=deps.authorize_project,
        attachments=ctx.attachments or None,
    )

    if not result["success"] and "pending_resolution" in result:
        if len(deps.projects) == 1:
            selected_project = next(iter(deps.projects.keys()))
            resolved = await deps.save_resolved_task(
                result["pending_resolution"],
                selected_project,
                str(trigger_message_id),
                requester_context=requester_context,
                authorize_project=deps.authorize_project,
            )
            result = resolved
        else:
            ctx.user_state["pending_task_project_resolution"] = result["pending_resolution"]

    await ctx.edit_message_text(
        message_id=status_msg_id,
        text=result["message"],
    )
