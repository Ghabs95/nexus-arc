from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, cast

from nexus.adapters.notifications.base import Button


async def handle_feature_ideation_callback(
    *,
    ctx: Any,
    deps: Any,
    feature_state_key: str,
    is_project_locked: Callable[[dict[str, Any]], bool],
    feature_project_keyboard: Callable[[Any], list[list[Button]]],
    clamp_feature_count: Callable[[Any], int],
    build_feature_suggestions: Callable[..., list[dict[str, Any]]],
    feature_generation_retry_text: Callable[[str, Any], str],
    feature_list_text: Callable[..., str],
    feature_list_keyboard: Callable[..., list[list[Button]]],
    feature_count_prompt_text: Callable[[str, Any], str],
    feature_count_keyboard: Callable[..., list[list[Button]]],
    feature_to_task_text: Callable[[str, dict[str, Any], Any], str],
    log_unauthorized_callback_access: Callable[[Any, Any], None],
) -> None:
    try:
        await ctx.answer_callback_query()
    except Exception as exc:
        logger = getattr(deps, "logger", None)
        if logger is not None:
            log_warning = getattr(logger, "warning", None)
            if callable(log_warning):
                log_warning("Feature callback answer failed (continuing): %s", exc)

    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_callback_access(getattr(deps, "logger", None), ctx.user_id)
        return

    data = ctx.query.data or ""
    feature_state = ctx.user_state.get(feature_state_key) or {}

    if data == "feat:choose_project":
        if is_project_locked(feature_state):
            await ctx.edit_message_text(
                text=(
                    "🔒 Project is fixed by your active context for this ideation flow.\n"
                    "Start a new request to use a different project context."
                )
            )
            return

        ctx.user_state[feature_state_key] = {
            **feature_state,
            "project": None,
            "items": [],
            "selected_items": [],
        }
        await ctx.edit_message_text(
            text="📁 Select a project to continue feature ideation:",
            buttons=feature_project_keyboard(deps),
        )
        return

    if data.startswith("feat:count:"):
        parts = data.split(":")
        if len(parts) != 3:
            await ctx.edit_message_text("⚠️ Invalid count selection.")
            return

        feature_count = clamp_feature_count(parts[2])
        project_key = feature_state.get("project")
        preferred_agent_type = feature_state.get("agent_type")
        source_text = str(feature_state.get("source_text") or "")
        ctx.user_state[feature_state_key] = {
            **feature_state,
            "feature_count": feature_count,
            "items": [],
            "selected_items": [],
        }

        if not project_key:
            await ctx.edit_message_text(
                text=(
                    "📁 Great — now choose a project to continue.\n\n"
                    f"Selected count: *{feature_count}*"
                ),
                buttons=feature_project_keyboard(deps),
            )
            return

        await ctx.edit_message_text(text="🧠 *Nexus thinking...*")
        features = build_feature_suggestions(
            project_key=project_key,
            text=source_text,
            deps=deps,
            preferred_agent_type=preferred_agent_type,
            feature_count=feature_count,
        )
        ctx.user_state[feature_state_key] = {
            **feature_state,
            "project": project_key,
            "items": features,
            "selected_items": [],
            "agent_type": preferred_agent_type,
            "feature_count": feature_count,
            "source_text": source_text,
        }
        if not features:
            project_locked = is_project_locked(feature_state)
            retry_keyboard_rows = []
            if not project_locked:
                retry_keyboard_rows.append(
                    [Button("📁 Choose project", callback_data="feat:choose_project")]
                )
            retry_keyboard_rows.append([Button("❌ Close", callback_data="flow:close")])
            await ctx.edit_message_text(
                text=feature_generation_retry_text(project_key, deps),
                buttons=retry_keyboard_rows,
            )
            return

        project_locked = is_project_locked(feature_state)
        await ctx.edit_message_text(
            text=feature_list_text(
                project_key,
                features,
                deps,
                preferred_agent_type,
                selected_features=[],
            ),
            buttons=feature_list_keyboard(features, allow_project_change=not project_locked),
        )
        return

    if data.startswith("feat:project:"):
        project_key = data.split(":", 2)[2]
        if project_key not in deps.projects:
            await ctx.edit_message_text("⚠️ Invalid project selection.")
            return

        project_locked = is_project_locked(feature_state)
        current_project = str(feature_state.get("project") or "")
        current_items = (
            feature_state.get("items") if isinstance(feature_state.get("items"), list) else []
        )
        selected_items = (
            feature_state.get("selected_items")
            if isinstance(feature_state.get("selected_items"), list)
            else []
        )
        if project_locked and current_project and project_key != current_project:
            await ctx.edit_message_text(
                text=(
                    "🔒 Project is fixed by your active context for this ideation flow.\n"
                    "Start a new request to use a different project context."
                )
            )
            return

        preferred_agent_type = feature_state.get("agent_type")
        if project_key == current_project and current_items:
            await ctx.edit_message_text(
                text=feature_list_text(
                    project_key,
                    current_items,
                    deps,
                    preferred_agent_type,
                    selected_features=selected_items,
                ),
                buttons=feature_list_keyboard(
                    current_items, allow_project_change=not project_locked
                ),
            )
            return

        source_text = str(feature_state.get("source_text") or "")
        feature_count_raw = feature_state.get("feature_count")
        if feature_count_raw is None:
            ctx.user_state[feature_state_key] = {
                **feature_state,
                "project": project_key,
                "items": [],
                "selected_items": [],
            }
            await ctx.edit_message_text(
                text=feature_count_prompt_text(project_key, deps),
                buttons=feature_count_keyboard(allow_project_change=not project_locked),
            )
            return

        feature_count = clamp_feature_count(feature_count_raw)
        await ctx.edit_message_text(text="🧠 *Nexus thinking...*")
        features = build_feature_suggestions(
            project_key=project_key,
            text=source_text,
            deps=deps,
            preferred_agent_type=preferred_agent_type,
            feature_count=feature_count,
        )
        ctx.user_state[feature_state_key] = {
            **feature_state,
            "project": project_key,
            "items": features,
            "selected_items": [],
            "agent_type": preferred_agent_type,
            "feature_count": feature_count,
            "source_text": source_text,
        }
        if not features:
            retry_keyboard_rows = []
            if not project_locked:
                retry_keyboard_rows.append(
                    [Button("📁 Choose project", callback_data="feat:choose_project")]
                )
            retry_keyboard_rows.append([Button("❌ Close", callback_data="flow:close")])
            await ctx.edit_message_text(
                text=feature_generation_retry_text(project_key, deps),
                buttons=retry_keyboard_rows,
            )
            return

        await ctx.edit_message_text(
            text=feature_list_text(
                project_key, features, deps, preferred_agent_type, selected_features=[]
            ),
            buttons=feature_list_keyboard(features, allow_project_change=not project_locked),
        )
        return

    if data.startswith("feat:pick:"):
        parts = data.split(":")
        if len(parts) != 3:
            await ctx.edit_message_text("⚠️ Invalid selection.")
            return

        project_key = feature_state.get("project")
        features = feature_state.get("items") or []
        if not project_key or not features:
            await ctx.edit_message_text(
                text="📁 Session expired. Select a project to get feature proposals:",
                buttons=feature_project_keyboard(deps),
            )
            return

        try:
            selected_index = int(parts[2])
        except ValueError:
            await ctx.edit_message_text("⚠️ Invalid feature selection.")
            return
        if selected_index < 0 or selected_index >= len(features):
            await ctx.edit_message_text("⚠️ Invalid feature selection.")
            return

        selected = features[selected_index]
        selected_items = (
            feature_state.get("selected_items")
            if isinstance(feature_state.get("selected_items"), list)
            else []
        )
        remaining_features = [item for idx, item in enumerate(features) if idx != selected_index]
        ctx.user_state[feature_state_key] = {
            **feature_state,
            "project": project_key,
            "items": remaining_features,
            "selected_items": [*selected_items, selected],
        }

        create_feature_task = getattr(deps, "create_feature_task", None)
        if callable(create_feature_task):
            await ctx.edit_message_text(text="🧠 *Nexus thinking...*")
            task_text = feature_to_task_text(project_key, selected, deps)
            trigger_message_id = (
                getattr(ctx.raw_event, "message_id", "feature-pick")
                if hasattr(ctx.raw_event, "message_id")
                else "feature-pick"
            )
            if hasattr(ctx.raw_event, "message") and hasattr(ctx.raw_event.message, "message_id"):
                trigger_message_id = str(ctx.raw_event.message.message_id)
            create_feature_task_fn = cast(_CreateFeatureTaskFn, create_feature_task)
            try:
                result = await create_feature_task_fn(
                    task_text,
                    trigger_message_id,
                    str(project_key),
                    str(ctx.user_id),
                )
            except TypeError:
                result = await create_feature_task_fn(task_text, trigger_message_id, str(project_key))
            message = str(result.get("message") or "⚠️ Task processing completed.")
            project_locked = is_project_locked(feature_state)
            keyboard_rows: list[list[Button]] = []
            if remaining_features:
                keyboard_rows.append(
                    [Button("⬅️ Back to feature list", callback_data=f"feat:project:{project_key}")]
                )
            elif message:
                message = (
                    f"{message}\n\n✅ All feature proposals from this list have been selected."
                )
            if remaining_features and not project_locked:
                keyboard_rows.append(
                    [Button("📁 Choose project", callback_data="feat:choose_project")]
                )
            keyboard_rows.append([Button("❌ Close", callback_data="flow:close")])
            await ctx.edit_message_text(text=message, buttons=keyboard_rows)
            return

        detail_lines = [
            f"💡 *{selected['title']}*",
            "",
            selected["summary"],
            "",
            "*Why now*",
            selected["why"],
            "",
            "*Implementation outline*",
        ]
        for idx, step in enumerate(selected.get("steps", []), start=1):
            detail_lines.append(f"{idx}. {step}")

        project_locked = is_project_locked(feature_state)
        keyboard_rows = []
        if remaining_features:
            keyboard_rows.append(
                [Button("⬅️ Back to feature list", callback_data=f"feat:project:{project_key}")]
            )
        else:
            detail_lines.extend(["", "✅ All feature proposals from this list have been selected."])
        if remaining_features and not project_locked:
            keyboard_rows.append([Button("📁 Choose project", callback_data="feat:choose_project")])
        keyboard_rows.append([Button("❌ Close", callback_data="flow:close")])
        await ctx.edit_message_text(text="\n".join(detail_lines), buttons=keyboard_rows)
        return

    await ctx.edit_message_text("⚠️ Unknown feature action.")


class _CreateFeatureTaskFn(Protocol):
    def __call__(
        self,
        text: str,
        message_id: str,
        project_key: str,
        user_id: str | None = None,
    ) -> Awaitable[dict[str, Any]]: ...
