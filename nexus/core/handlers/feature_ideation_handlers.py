"""Feature ideation chat/callback handlers extracted from telegram_bot."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nexus.core.interactive.context import InteractiveContext
from nexus.adapters.notifications.base import Button
from nexus.core.feature_ideation_callback_service import (
    handle_feature_ideation_callback as _service_handle_feature_ideation_callback,
)
from nexus.core.feature_ideation_generation_service import (
    build_feature_suggestions as _service_build_feature_suggestions,
)
from nexus.core.chat_agents_schema import (
    get_default_project_chat_agent_type,
    get_project_chat_agent_config,
)

from nexus.core.handlers.agent_context_utils import (
    collect_context_candidate_files,
    extract_agent_prompt_metadata_from_yaml,
    extract_referenced_paths_from_markdown,
    load_agent_prompt_from_definition,
    load_role_context,
    normalize_paths,
    resolve_path,
    resolve_project_root,
)
from nexus.core.handlers.common_routing import extract_json_dict
from nexus.core.utils.log_utils import log_unauthorized_callback_access

FEATURE_STATE_KEY = "feature_suggestions"
FEATURE_MIN_COUNT = 1
FEATURE_MAX_COUNT = 5
FEATURE_DEFAULT_COUNT = 3
FEATURE_IDEATION_CONTEXT_MODE_DEFAULT = os.getenv("FEATURE_IDEATION_CONTEXT_MODE", "index")
FEATURE_IDEATION_CONTEXT_MAX_CHARS_DEFAULT = int(
    os.getenv("FEATURE_IDEATION_CONTEXT_MAX_CHARS", "6000")
)
FEATURE_IDEATION_CONTEXT_SUMMARY_MAX_CHARS = int(os.getenv("AI_CONTEXT_SUMMARY_MAX_CHARS", "1200"))


@dataclass
class FeatureIdeationHandlerDeps:
    logger: Any
    allowed_user_ids: list[int]
    projects: dict[str, str]
    get_project_label: Callable[[str], str]
    orchestrator: Any
    base_dir: str = ""
    project_config: dict[str, Any] | None = None
    create_feature_task: Callable[[str, str, str], Awaitable[dict[str, Any]]] | None = None
    feature_registry_service: Any | None = None
    dedup_similarity: float = 0.86


def _legacy_reply_markup(buttons: list[list[Button]] | None) -> Any | None:
    if not buttons:
        return None
    inline_keyboard: list[list[Any]] = []
    for row in buttons:
        if not isinstance(row, list):
            continue
        inline_row: list[Any] = []
        for button in row:
            if not isinstance(button, Button):
                continue
            payload = {
                "text": button.label,
                "callback_data": button.callback_data,
            }
            if button.url:
                payload = {
                    "text": button.label,
                    "url": button.url,
                }
            inline_row.append(SimpleNamespace(**payload))
        if inline_row:
            inline_keyboard.append(inline_row)
    return SimpleNamespace(inline_keyboard=inline_keyboard)


def _coerce_legacy_ctx(update: Any, context: Any, *, source_text: str | None = None):
    user_id = str(getattr(getattr(update, "effective_user", None), "id", ""))
    query_obj = getattr(update, "callback_query", None)

    class _LegacyCtx:
        def __init__(self):
            self.user_id = user_id
            self.text = source_text if source_text is not None else ""
            self.args = []
            self.raw_event = update
            self.user_state = getattr(context, "user_data", {})
            self.query = SimpleNamespace(data=getattr(query_obj, "data", "")) if query_obj else None

        async def edit_message_text(
            self,
            text: str,
            message_id: str | None = None,
            buttons: list[list[Button]] | None = None,
        ) -> None:
            target_message_id = message_id
            if (
                target_message_id is None
                and query_obj is not None
                and getattr(query_obj, "message", None)
            ):
                target_message_id = str(getattr(query_obj.message, "message_id", ""))

            reply_markup = _legacy_reply_markup(buttons)
            if query_obj is not None and hasattr(query_obj, "edit_message_text"):
                await query_obj.edit_message_text(
                    text=text, reply_markup=reply_markup, parse_mode="Markdown"
                )
                return

            await getattr(context, "bot").edit_message_text(
                chat_id=getattr(getattr(update, "effective_chat", None), "id", None),
                message_id=target_message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="Markdown",
            )

        async def answer_callback_query(self) -> None:
            if query_obj is not None and hasattr(query_obj, "answer"):
                await query_obj.answer()

    return _LegacyCtx()


def is_feature_ideation_request(text: str) -> bool:
    candidate = (text or "").strip().lower()
    if not candidate:
        return False

    feature_terms = ["feature", "features", "implement", "add", "new"]
    if not any(term in candidate for term in feature_terms):
        return False

    triggers = [
        "new feature",
        "new features",
        "which feature",
        "which new",
        "what feature",
        "what new feature",
        "what can we add",
        "what should we add",
        "features can we add",
        "features should we add",
        "propose",
        "ideas",
        "roadmap",
    ]
    return any(trigger in candidate for trigger in triggers)


def detect_feature_ideation_intent(
    text: str,
    *,
    run_analysis: Callable[..., dict[str, Any]] | None = None,
    logger: Any | None = None,
    confidence_threshold: float = 0.65,
) -> tuple[bool, float, str]:
    """Detect feature-ideation intent with model-first classification."""
    phrase_match = is_feature_ideation_request(text)
    if not callable(run_analysis):
        return (
            (True, 0.55, "phrase_fallback_no_model")
            if phrase_match
            else (
                False,
                0.0,
                "phrase_miss_no_model",
            )
        )

    try:
        result = run_analysis(text=text, task="detect_feature_ideation")
    except Exception as exc:
        if logger:
            logger.warning("Feature ideation detector model fallback failed: %s", exc)
        return (
            (True, 0.55, "phrase_fallback_model_error")
            if phrase_match
            else (
                False,
                0.0,
                "model_error",
            )
        )

    if not isinstance(result, dict):
        return False, 0.0, "model_non_dict"

    parsed = dict(result)
    if "feature_ideation" not in parsed:
        reparsed = extract_json_dict(str(parsed.get("text", "")))
        if reparsed:
            parsed.update(reparsed)

    raw_flag = parsed.get("feature_ideation")
    if isinstance(raw_flag, bool):
        is_ideation = raw_flag
    else:
        is_ideation = str(raw_flag).strip().lower() in {"1", "true", "yes"}

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    reason = str(parsed.get("reason", "")).strip() or "model_fallback"
    return bool(is_ideation and confidence >= float(confidence_threshold)), confidence, reason


def detect_feature_project(text: str, projects: dict[str, str] | None = None) -> str | None:
    candidate = (text or "").strip().lower()
    if not candidate:
        return None

    aliases: dict[str, str] = {}
    try:
        from nexus.core.config import get_project_aliases

        aliases.update(get_project_aliases())
    except Exception:
        pass

    if isinstance(projects, dict):
        for key in projects:
            normalized = str(key).strip().lower()
            if normalized:
                aliases.setdefault(normalized, normalized)

    for alias, project_key in aliases.items():
        if alias in candidate:
            return project_key
    return None


def _requested_feature_count(text: str, default_count: int = 3, max_count: int = 5) -> int:
    candidate = (text or "").lower()
    if not candidate:
        return default_count

    if "top 5" in candidate or "max 5" in candidate or "five" in candidate:
        return max_count
    if "top 4" in candidate or "four" in candidate:
        return 4
    if "top 3" in candidate or "three" in candidate:
        return 3

    number_match = re.search(r"\b([1-9])\b", candidate)
    if number_match:
        requested = int(number_match.group(1))
        return max(1, min(max_count, requested))

    return default_count


def _clamp_feature_count(value: Any, default_count: int = FEATURE_DEFAULT_COUNT) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default_count)
    return max(FEATURE_MIN_COUNT, min(FEATURE_MAX_COUNT, parsed))


def _is_project_locked(feature_state: dict[str, Any]) -> bool:
    if not isinstance(feature_state, dict):
        return False
    if "project_locked" in feature_state:
        return bool(feature_state.get("project_locked"))
    return bool(feature_state.get("project"))


def _extract_json_dict(raw_text: str) -> dict[str, Any] | None:
    if not raw_text:
        return None
    parsed = extract_json_dict(raw_text)
    return parsed if parsed else None


def _extract_json_payload(raw_text: str) -> Any:
    if not raw_text:
        return None

    cleaned = str(raw_text).replace("```json", "").replace("```", "").strip()

    for candidate in (str(raw_text).strip(), cleaned):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, (dict, list)):
                return parsed
        except Exception:
            pass

    text = str(raw_text)
    if "[" in text and "]" in text:
        start = text.find("[")
        end = text.rfind("]") + 1
        try:
            parsed = json.loads(text[start:end])
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass

    parsed_dict = _extract_json_dict(raw_text)
    if parsed_dict is not None:
        return parsed_dict

    return None


def _normalize_generated_features(items: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        why = str(item.get("why") or "").strip()
        steps = item.get("steps")
        if not title or not summary:
            continue
        if not isinstance(steps, list):
            steps = []
        cleaned_steps = [
            str(step).strip() for step in steps if isinstance(step, str) and step.strip()
        ]
        normalized.append(
            {
                "title": title,
                "summary": summary,
                "why": why or "Aligns with roadmap, speed, and measurable impact.",
                "steps": cleaned_steps[:3],
            }
        )
        if len(normalized) >= limit:
            break
    return normalized


def _project_config_for_key(project_key: str, deps: FeatureIdeationHandlerDeps) -> dict[str, Any]:
    config = deps.project_config if isinstance(deps.project_config, dict) else {}
    project_cfg = config.get(project_key)
    if not isinstance(project_cfg, dict):
        return {}
    return project_cfg


def _default_chat_agent_type_for_project(project_key: str, deps: FeatureIdeationHandlerDeps) -> str:
    project_cfg = _project_config_for_key(project_key, deps)
    return get_default_project_chat_agent_type(project_cfg)


def _chat_agent_config_for_project(
    project_key: str,
    routed_agent_type: str,
    deps: FeatureIdeationHandlerDeps,
) -> dict[str, Any]:
    project_cfg = _project_config_for_key(project_key, deps)
    return get_project_chat_agent_config(project_cfg, routed_agent_type)


def _resolve_project_root(project_key: str, deps: FeatureIdeationHandlerDeps) -> str:
    project_cfg = _project_config_for_key(project_key, deps)
    return resolve_project_root(str(getattr(deps, "base_dir", "") or ""), project_key, project_cfg)


def _resolve_path(project_root: str, raw_path: str) -> str:
    return resolve_path(project_root, raw_path)


def _normalize_paths(value: Any) -> list[str]:
    return normalize_paths(value)


def _extract_referenced_paths_from_agents(agents_text: str) -> list[str]:
    return extract_referenced_paths_from_markdown(agents_text)


def _collect_context_candidate_files(
    context_root: str, seed_files: list[str] | None = None
) -> list[str]:
    return collect_context_candidate_files(context_root, seed_files=seed_files)


def _extract_agent_prompt_metadata_from_yaml(path: str, max_chars: int = 3000) -> tuple[str, str]:
    return extract_agent_prompt_metadata_from_yaml(path, max_chars=max_chars)


def _load_agent_prompt_from_definition(
    project_key: str,
    routed_agent_type: str,
    deps: FeatureIdeationHandlerDeps,
) -> str:
    project_root = _resolve_project_root(project_key, deps)
    project_cfg = _project_config_for_key(project_key, deps)
    return load_agent_prompt_from_definition(
        base_dir=str(deps.base_dir or ""),
        project_root=project_root,
        project_cfg=project_cfg,
        routed_agent_type=routed_agent_type,
    )


def _load_role_context(
    project_key: str,
    routed_agent_type: str,
    deps: FeatureIdeationHandlerDeps,
    max_chars: int = FEATURE_IDEATION_CONTEXT_MAX_CHARS_DEFAULT,
    query: str = "",
) -> str:
    """Load prompt context based on system_operations.chat and AGENTS protocol."""
    agent_cfg = _chat_agent_config_for_project(project_key, routed_agent_type, deps)
    configured_mode = (
        str(
            agent_cfg.get("feature_ideation_context_mode")
            or agent_cfg.get("context_mode")
            or FEATURE_IDEATION_CONTEXT_MODE_DEFAULT
        )
        .strip()
        .lower()
    )
    configured_max = agent_cfg.get("feature_ideation_context_max_chars")
    if not isinstance(configured_max, int) or configured_max <= 0:
        configured_max = (
            agent_cfg.get("context_max_chars")
            if isinstance(agent_cfg.get("context_max_chars"), int)
            else FEATURE_IDEATION_CONTEXT_MAX_CHARS_DEFAULT
        )
    project_root = _resolve_project_root(project_key, deps)
    return load_role_context(
        project_root=project_root,
        agent_cfg=agent_cfg,
        max_chars=min(max_chars, int(configured_max)),
        mode=configured_mode,
        query=query,
        summary_max_chars=FEATURE_IDEATION_CONTEXT_SUMMARY_MAX_CHARS,
    )


def _build_feature_persona(
    project_label: str,
    routed_agent_type: str,
    feature_count: int,
    context_block: str,
    agent_prompt: str,
    excluded_titles: list[str] | None = None,
) -> str:
    role = str(routed_agent_type or "").strip().lower()
    role_prompt = (
        f"Use this dedicated agent definition as your operating role and voice for `{role}`:\n"
        f"{agent_prompt}"
    )

    exclude_block = ""
    excluded = [str(item).strip() for item in (excluded_titles or []) if str(item).strip()]
    if excluded:
        rendered = "\n".join(f"- {title}" for title in excluded[:50])
        exclude_block = (
            "\n\nAlready implemented features for this project (exclude these from suggestions):\n"
            f"{rendered}"
        )

    return (
        f"{role_prompt}\n"
        f"Project: {project_label}\n"
        "Return ONLY JSON with this schema:\n"
        '{"items":[{"title":"...","summary":"...","why":"...","steps":["...","...","..."]}]}\n'
        f"Generate exactly {feature_count} items. Keep titles concise and action-oriented."
        f"{context_block}"
        f"{exclude_block}"
    )


def _build_feature_suggestions(
    project_key: str,
    text: str,
    deps: FeatureIdeationHandlerDeps,
    preferred_agent_type: str | None,
    feature_count: int,
) -> list[dict[str, Any]]:
    project_label = deps.get_project_label(project_key)
    routed_agent_type = str(preferred_agent_type or "").strip().lower()
    if not routed_agent_type:
        routed_agent_type = _default_chat_agent_type_for_project(project_key, deps)
    if not routed_agent_type:
        if getattr(deps, "logger", None):
            deps.logger.warning(
                "Feature ideation requires configured system_operations.chat for project '%s'",
                project_key,
            )
        return []

    excluded_titles: list[str] = []
    feature_registry = getattr(deps, "feature_registry_service", None)
    if feature_registry is not None and getattr(feature_registry, "is_enabled", None):
        try:
            if feature_registry.is_enabled():
                excluded_titles = feature_registry.list_excluded_titles(project_key)
        except Exception as exc:
            if getattr(deps, "logger", None):
                deps.logger.warning("Feature registry exclude-list load failed: %s", exc)

    agent_prompt = _load_agent_prompt_from_definition(project_key, routed_agent_type, deps)
    if not agent_prompt:
        if getattr(deps, "logger", None):
            deps.logger.warning(
                "Feature ideation requires agent prompt definition for agent_type '%s' in project '%s'",
                routed_agent_type,
                project_key,
            )
        return []

    context_block = _load_role_context(project_key, routed_agent_type, deps, query=text)
    persona = _build_feature_persona(
        project_label,
        routed_agent_type,
        feature_count,
        context_block,
        agent_prompt,
        excluded_titles,
    )

    generated = _service_build_feature_suggestions(
        project_key=project_key,
        feature_count=feature_count,
        text=text,
        routed_agent_type=routed_agent_type,
        persona=persona,
        orchestrator=deps.orchestrator,
        logger=getattr(deps, "logger", None),
        normalize_generated_features=_normalize_generated_features,
        extract_json_payload=_extract_json_payload,
    )

    if not generated or feature_registry is None:
        return generated

    try:
        filtered, removed = feature_registry.filter_ideation_items(
            project_key=project_key,
            items=generated,
            similarity_threshold=float(getattr(deps, "dedup_similarity", 0.86)),
        )
        if removed and getattr(deps, "logger", None):
            deps.logger.info(
                "Feature registry filtered %d implemented suggestion(s) for project=%s",
                len(removed),
                project_key,
            )
        return filtered
    except Exception as exc:
        if getattr(deps, "logger", None):
            deps.logger.warning("Feature registry post-filter failed: %s", exc)
        return generated


def _feature_generation_retry_text(project_key: str, deps: FeatureIdeationHandlerDeps) -> str:
    return (
        f"⚠️ I couldn't generate feature proposals for *{deps.get_project_label(project_key)}* right now.\n\n"
        "Please try again."
    )


def _feature_list_text(
    project_key: str,
    features: list[dict[str, Any]],
    deps: FeatureIdeationHandlerDeps,
    preferred_agent_type: str | None,
    selected_features: list[dict[str, Any]] | None = None,
) -> str:
    routed_agent_type = str(preferred_agent_type or "").strip().lower()
    if not routed_agent_type:
        routed_agent_type = _default_chat_agent_type_for_project(project_key, deps)
    agent_label = routed_agent_type or "unknown"
    lines = [
        f"💡 *Feature proposals for {deps.get_project_label(project_key)}*",
        f"Perspective: `{agent_label}`",
        "",
        "Tap one option:",
    ]
    for index, item in enumerate(features, start=1):
        lines.append(f"{index}. *{item['title']}* — {item['summary']}")
    done_items = selected_features if isinstance(selected_features, list) else []
    if done_items:
        lines.append("")
        lines.append("Already selected:")
        for item in done_items:
            title = str(item.get("title") or "").strip()
            if title:
                lines.append(f"✅ {title}")
    return "\n".join(lines)


def _feature_list_keyboard(
    features: list[dict[str, Any]],
    allow_project_change: bool,
) -> list[list[Button]]:
    keyboard = [
        [Button(item["title"], callback_data=f"feat:pick:{idx}")]
        for idx, item in enumerate(features)
    ]
    if allow_project_change:
        keyboard.append([Button("📁 Choose project", callback_data="feat:choose_project")])
    keyboard.append([Button("❌ Close", callback_data="flow:close")])
    return keyboard


def _feature_count_keyboard(allow_project_change: bool) -> list[list[Button]]:
    keyboard: list[list[Button]] = [
        [Button(str(value), callback_data=f"feat:count:{value}") for value in range(1, 6)]
    ]
    if allow_project_change:
        keyboard.append([Button("📁 Choose project", callback_data="feat:choose_project")])
    keyboard.append([Button("❌ Close", callback_data="flow:close")])
    return keyboard


def _feature_count_prompt_text(project_key: str | None, deps: FeatureIdeationHandlerDeps) -> str:
    project_label = deps.get_project_label(project_key) if project_key else "not selected"
    return (
        "🔢 How many feature proposals do you want?\n"
        "Choose between 1 and 5.\n\n"
        f"Current project: *{project_label}*"
    )


def _feature_to_task_text(
    project_key: str, selected: dict[str, Any], deps: FeatureIdeationHandlerDeps
) -> str:
    lines = [
        f"New feature proposal for {deps.get_project_label(project_key)}",
        "",
        f"Title: {selected.get('title', '')}",
        f"Summary: {selected.get('summary', '')}",
        f"Why now: {selected.get('why', '')}",
        "",
        "Implementation outline:",
    ]
    steps = selected.get("steps") if isinstance(selected.get("steps"), list) else []
    if not steps:
        lines.append("1. Define technical approach")
        lines.append("2. Implement core changes")
        lines.append("3. Validate and document")
    else:
        for index, step in enumerate(steps, start=1):
            lines.append(f"{index}. {step}")
    return "\n".join(lines).strip()


async def _prompt_feature_count(
    ctx: InteractiveContext,
    status_msg_id: str,
    deps: FeatureIdeationHandlerDeps,
) -> None:
    feature_state = ctx.user_state.get(FEATURE_STATE_KEY) or {}
    project_key = feature_state.get("project")
    project_locked = _is_project_locked(feature_state)
    await ctx.edit_message_text(
        message_id=status_msg_id,
        text=_feature_count_prompt_text(project_key, deps),
        buttons=_feature_count_keyboard(allow_project_change=not project_locked),
    )


def _feature_project_keyboard(deps: FeatureIdeationHandlerDeps) -> list[list[Button]]:
    keyboard = [
        [Button(deps.get_project_label(key), callback_data=f"feat:project:{key}")]
        for key in sorted(deps.projects.keys())
    ]
    keyboard.append([Button("❌ Close", callback_data="flow:close")])
    return keyboard


async def show_feature_project_picker(
    ctx: InteractiveContext,
    status_msg_id: str,
    deps: FeatureIdeationHandlerDeps,
) -> None:
    feature_state = ctx.user_state.get(FEATURE_STATE_KEY) or {}
    selected_count = feature_state.get("feature_count")
    count_suffix = ""
    if selected_count is not None:
        count_suffix = f"\n\nSelected count: *{_clamp_feature_count(selected_count)}*"

    ctx.user_state[FEATURE_STATE_KEY] = {
        **feature_state,
        "project": None,
        "items": [],
    }
    await ctx.edit_message_text(
        message_id=status_msg_id,
        text=(
            "📁 I couldn't detect the project. Select one to continue feature ideation:"
            f"{count_suffix}"
        ),
        buttons=_feature_project_keyboard(deps),
    )


async def show_feature_suggestions(
    ctx: InteractiveContext,
    status_msg_id: str,
    project_key: str,
    text: str,
    preferred_agent_type: str | None,
    feature_count: int,
    deps: FeatureIdeationHandlerDeps,
) -> None:
    feature_state = ctx.user_state.get(FEATURE_STATE_KEY) or {}
    features = _build_feature_suggestions(
        project_key=project_key,
        text=text,
        deps=deps,
        preferred_agent_type=preferred_agent_type,
        feature_count=feature_count,
    )
    ctx.user_state[FEATURE_STATE_KEY] = {
        "project": project_key,
        "items": features,
        "selected_items": [],
        "agent_type": preferred_agent_type,
        "feature_count": feature_count,
        "source_text": text,
    }

    if not features:
        project_locked = _is_project_locked(feature_state)
        retry_keyboard_rows = []
        if not project_locked:
            retry_keyboard_rows.append(
                [Button("📁 Choose project", callback_data="feat:choose_project")]
            )
        retry_keyboard_rows.append([Button("❌ Close", callback_data="flow:close")])
        await ctx.edit_message_text(
            message_id=status_msg_id,
            text=_feature_generation_retry_text(project_key, deps),
            buttons=retry_keyboard_rows,
        )
        return

    project_locked = _is_project_locked(feature_state)

    await ctx.edit_message_text(
        message_id=status_msg_id,
        text=_feature_list_text(
            project_key,
            features,
            deps,
            preferred_agent_type,
            selected_features=[],
        ),
        buttons=_feature_list_keyboard(features, allow_project_change=not project_locked),
    )


async def handle_feature_ideation_request(
    ctx: Any = None,
    status_msg_id: str | None = None,
    text: str | None = None,
    deps: Any = None,
    preferred_project_key: str | None = None,
    preferred_agent_type: str | None = None,
    detected_feature_ideation: bool | None = None,
    detection_confidence: float | None = None,
    detection_reason: str | None = None,
    *,
    update: Any | None = None,
    context: Any | None = None,
    status_msg: Any | None = None,
) -> bool:
    if ctx is None and update is not None and context is not None:
        ctx = _coerce_legacy_ctx(update, context, source_text=text)
        status_msg_id = str(getattr(status_msg, "message_id", ""))

    if ctx is None or status_msg_id is None or text is None or deps is None:
        return False

    if detected_feature_ideation is None:
        feature_ideation, fi_confidence, fi_reason = detect_feature_ideation_intent(
            text,
            run_analysis=deps.orchestrator.run_text_to_speech_analysis,
            logger=deps.logger,
        )
    else:
        feature_ideation = bool(detected_feature_ideation)
        try:
            fi_confidence = float(detection_confidence if detection_confidence is not None else 0.0)
        except (TypeError, ValueError):
            fi_confidence = 0.0
        fi_reason = str(detection_reason or "preclassified").strip() or "preclassified"

    deps.logger.info(
        "Feature ideation detection (entrypoint): matched=%s confidence=%.2f reason=%s",
        feature_ideation,
        fi_confidence,
        fi_reason,
    )
    if not feature_ideation:
        return False

    project_key = detect_feature_project(text, deps.projects)
    if not project_key and preferred_project_key in deps.projects:
        project_key = preferred_project_key
    project_locked = bool(project_key)
    if not project_key:
        project_key = None

    ctx.user_state[FEATURE_STATE_KEY] = {
        "project": project_key,
        "project_locked": project_locked,
        "items": [],
        "selected_items": [],
        "agent_type": preferred_agent_type,
        "feature_count": None,
        "source_text": text,
        "requested_feature_count": _requested_feature_count(text),
    }
    await _prompt_feature_count(ctx, status_msg_id, deps)
    return True


async def feature_callback_handler(
    ctx: Any = None,
    deps: Any = None,
    *,
    update: Any | None = None,
    context: Any | None = None,
) -> None:
    if ctx is None and update is not None and context is not None:
        ctx = _coerce_legacy_ctx(update, context)

    if ctx is None or deps is None:
        return

    await _service_handle_feature_ideation_callback(
        ctx=ctx,
        deps=deps,
        feature_state_key=FEATURE_STATE_KEY,
        is_project_locked=_is_project_locked,
        feature_project_keyboard=_feature_project_keyboard,
        clamp_feature_count=_clamp_feature_count,
        build_feature_suggestions=_build_feature_suggestions,
        feature_generation_retry_text=_feature_generation_retry_text,
        feature_list_text=_feature_list_text,
        feature_list_keyboard=_feature_list_keyboard,
        feature_count_prompt_text=_feature_count_prompt_text,
        feature_count_keyboard=_feature_count_keyboard,
        feature_to_task_text=_feature_to_task_text,
        log_unauthorized_callback_access=log_unauthorized_callback_access,
    )
