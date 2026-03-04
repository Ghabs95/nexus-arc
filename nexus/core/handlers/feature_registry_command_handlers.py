"""Feature registry operator command handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from nexus.core.utils.log_utils import log_unauthorized_access

if TYPE_CHECKING:
    from nexus.core.interactive.context import InteractiveContext


@dataclass
class FeatureRegistryCommandDeps:
    logger: Any
    allowed_user_ids: list[int]
    iter_project_keys: Callable[[], list[str]]
    normalize_project_key: Callable[[str | None], str | None]
    get_project_label: Callable[[str], str]
    feature_registry: Any


def _resolve_project_and_rest(
    args: list[str],
    *,
    iter_project_keys: Callable[[], list[str]],
    normalize_project_key: Callable[[str | None], str | None],
) -> tuple[str | None, list[str]]:
    if not args:
        return None, []

    raw_project = str(args[0] or "").strip()
    normalized = normalize_project_key(raw_project)
    if normalized and normalized in set(iter_project_keys()):
        return normalized, list(args[1:])

    return None, []


async def feature_done_handler(ctx: InteractiveContext, deps: FeatureRegistryCommandDeps) -> None:
    deps.logger.info("Feature done requested by user: %s", ctx.user_id)
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not deps.feature_registry.is_enabled():
        await ctx.reply_text(
            "⚠️ Feature registry is disabled (`NEXUS_FEATURE_REGISTRY_ENABLED=false`)."
        )
        return

    project_key, rest = _resolve_project_and_rest(
        ctx.args,
        iter_project_keys=deps.iter_project_keys,
        normalize_project_key=deps.normalize_project_key,
    )
    if not project_key or not rest:
        await ctx.reply_text("Usage: `/feature_done <project> <title>`")
        return

    title = " ".join(str(part) for part in rest).strip()
    if not title:
        await ctx.reply_text("Usage: `/feature_done <project> <title>`")
        return

    saved = deps.feature_registry.upsert_feature(
        project_key=project_key,
        canonical_title=title,
        aliases=[],
        source_issue="manual",
        source_pr="manual",
        manual_override=True,
    )
    if not saved:
        await ctx.reply_text("⚠️ Could not add feature to registry.")
        return

    await ctx.reply_text(
        f"✅ Added implemented feature for *{deps.get_project_label(project_key)}*: "
        f"`{saved.get('canonical_title')}` (`{saved.get('feature_id')}`)"
    )


async def feature_list_handler(ctx: InteractiveContext, deps: FeatureRegistryCommandDeps) -> None:
    deps.logger.info("Feature list requested by user: %s", ctx.user_id)
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not deps.feature_registry.is_enabled():
        await ctx.reply_text(
            "⚠️ Feature registry is disabled (`NEXUS_FEATURE_REGISTRY_ENABLED=false`)."
        )
        return

    project_key, rest = _resolve_project_and_rest(
        ctx.args,
        iter_project_keys=deps.iter_project_keys,
        normalize_project_key=deps.normalize_project_key,
    )
    if not project_key or rest:
        await ctx.reply_text("Usage: `/feature_list <project>`")
        return

    features = deps.feature_registry.list_features(project_key)
    if not features:
        await ctx.reply_text(
            f"No implemented features registered for *{deps.get_project_label(project_key)}*."
        )
        return

    lines = [f"📚 Implemented features for *{deps.get_project_label(project_key)}*:"]
    for item in features[:50]:
        title = str(item.get("canonical_title") or "").strip()
        feature_id = str(item.get("feature_id") or "").strip()
        source_issue = str(item.get("source_issue") or "").strip()
        suffix = f" (`{feature_id}`)"
        if source_issue:
            suffix += f" • issue `{source_issue}`"
        lines.append(f"- {title}{suffix}")

    await ctx.reply_text("\n".join(lines))


async def feature_forget_handler(ctx: InteractiveContext, deps: FeatureRegistryCommandDeps) -> None:
    deps.logger.info("Feature forget requested by user: %s", ctx.user_id)
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not deps.feature_registry.is_enabled():
        await ctx.reply_text(
            "⚠️ Feature registry is disabled (`NEXUS_FEATURE_REGISTRY_ENABLED=false`)."
        )
        return

    project_key, rest = _resolve_project_and_rest(
        ctx.args,
        iter_project_keys=deps.iter_project_keys,
        normalize_project_key=deps.normalize_project_key,
    )
    if not project_key or not rest:
        await ctx.reply_text("Usage: `/feature_forget <project> <feature_id|title>`")
        return

    feature_ref = " ".join(str(part) for part in rest).strip()
    removed = deps.feature_registry.forget_feature(project_key=project_key, feature_ref=feature_ref)
    if not removed:
        await ctx.reply_text(
            f"⚠️ Feature not found in *{deps.get_project_label(project_key)}*: `{feature_ref}`"
        )
        return

    await ctx.reply_text(
        f"🗑️ Removed implemented feature from *{deps.get_project_label(project_key)}*: "
        f"`{removed.get('canonical_title')}`"
    )
