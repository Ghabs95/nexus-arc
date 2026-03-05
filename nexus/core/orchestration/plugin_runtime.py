"""Shared plugin runtime utilities for Nexus app modules."""

import importlib
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

try:
    from nexus.plugins import PluginKind, PluginRegistry
except Exception:
    PluginKind = None
    PluginRegistry = None

_registry = None
_registry_initialized = False
_plugin_cache: dict[str, Any] = {}

_PLUGIN_PROFILES: dict[str, dict[str, Any]] = {
    "state_store_default": {
        "kind": "STORAGE_BACKEND",
        "name": "json-state-store",
        "config": {},
    },
    "notification_telegram": {
        "kind": "NOTIFICATION_CHANNEL",
        "name": "telegram-notification-http",
        "config": {
            "parse_mode": "Markdown",
            "timeout": 10,
        },
    },
    "ai_runtime_default": {
        "kind": "AI_PROVIDER",
        "name": "ai-runtime-orchestrator",
        "config": {
            "fallback_enabled": True,
            "rate_limit_ttl": 3600,
            "max_retries": 3,
            "analysis_timeout": 30,
            "refine_description_timeout": 90,
        },
    },
    "agent_launch_policy": {
        "kind": "INPUT_ADAPTER",
        "name": "agent-launch-policy",
        "config": {},
    },
    "git_agent_launcher": {
        "kind": "GIT_PLATFORM",
        "name": "github-issue-cli",
        "config": {},
    },
    "gitlab_agent_launcher": {
        "kind": "GIT_PLATFORM",
        "name": "gitlab-issue-cli",
        "config": {},
    },
    "workflow_state_engine": {
        "kind": "INPUT_ADAPTER",
        "name": "workflow-state-engine",
        "config": {},
    },
    "runtime_ops_default": {
        "kind": "INPUT_ADAPTER",
        "name": "runtime-ops-process-guard",
        "config": {
            "process_name": "copilot",
            "pgrep_timeout": 5,
            "kill_timeout": 5,
        },
    },
    "workflow_monitor_policy": {
        "kind": "INPUT_ADAPTER",
        "name": "workflow-monitor-policy",
        "config": {},
    },
    "webhook_policy": {
        "kind": "INPUT_ADAPTER",
        "name": "git-webhook-policy",
        "config": {},
    },
    "workflow_policy": {
        "kind": "INPUT_ADAPTER",
        "name": "workflow-policy",
        "config": {},
    },
}

_BUILTIN_REGISTER_MODULES = (
    "nexus.plugins.builtin.ai_runtime_plugin",
    "nexus.plugins.builtin.agent_launch_policy_plugin",
    "nexus.plugins.builtin.github_issue_plugin",
    "nexus.plugins.builtin.gitlab_issue_plugin",
    "nexus.plugins.builtin.telegram_notification_plugin",
    "nexus.plugins.builtin.telegram_interactive_plugin",
    "nexus.plugins.builtin.telegram_event_handler_plugin",
    "nexus.plugins.builtin.discord_interactive_plugin",
    "nexus.plugins.builtin.discord_event_handler_plugin",
    "nexus.plugins.builtin.json_state_plugin",
    "nexus.plugins.builtin.runtime_ops_plugin",
    "nexus.plugins.builtin.workflow_policy_plugin",
    "nexus.plugins.builtin.workflow_monitor_policy_plugin",
    "nexus.plugins.builtin.git_webhook_policy_plugin",
    "nexus.plugins.builtin.workflow_state_engine_plugin",
)


def _get_registry():
    """Initialize plugin registry once and register built-in plugins."""
    global _registry, _registry_initialized

    if _registry_initialized:
        if _registry is None:
            raise RuntimeError("Plugin registry initialization previously failed")
        return _registry

    _registry_initialized = True
    if not PluginRegistry or not PluginKind:
        message = "Plugin runtime unavailable (nexus.plugins missing)"
        logger.error(message)
        raise RuntimeError(message)

    try:
        registry = PluginRegistry()
        for module_name in _BUILTIN_REGISTER_MODULES:
            module = importlib.import_module(module_name)
            register = getattr(module, "register_plugins", None)
            if callable(register):
                register(registry)
        _registry = registry
    except Exception as exc:
        logger.error("Plugin registry initialization failed: %s", exc)
        _registry = None
        raise RuntimeError("Plugin registry initialization failed") from exc

    return registry


def get_builtin_plugin(
    *,
    kind: str,
    name: str,
    config: dict[str, Any] | None = None,
    cache_key: str | None = None,
):
    """Create or fetch a cached built-in plugin instance.

    Args:
        kind: PluginKind member name (for example, "GIT_PLATFORM")
        name: Registered plugin name.
        config: Plugin configuration dictionary.
        cache_key: Optional cache key for reusable plugin instances.
    """
    if cache_key and cache_key in _plugin_cache:
        return _plugin_cache[cache_key]

    registry = _get_registry()

    try:
        kind_enum = getattr(PluginKind, kind)
    except AttributeError as exc:
        raise RuntimeError(f"Unknown plugin kind: {kind}") from exc

    try:
        plugin = registry.create(kind_enum, name, config or {})
    except Exception as exc:
        logger.warning("Failed to create plugin kind=%s name=%s: %s", kind, name, exc)
        raise RuntimeError(f"Failed to create plugin kind={kind} name={name}") from exc

    if plugin is None:
        raise RuntimeError(f"Plugin creation returned None kind={kind} name={name}")

    if cache_key:
        _plugin_cache[cache_key] = plugin
    return plugin


def get_profiled_plugin(
    profile: str,
    *,
    overrides: dict[str, Any] | None = None,
    cache_key: str | None = None,
):
    """Create plugin from a named profile with optional config overrides."""
    spec = _PLUGIN_PROFILES.get(profile)
    if not spec:
        raise ValueError(f"Unknown plugin profile: {profile}")

    config = dict(spec.get("config", {}))
    if overrides:
        config.update(overrides)

    return get_builtin_plugin(
        kind=spec["kind"],
        name=spec["name"],
        config=config,
        cache_key=cache_key,
    )


def clear_cached_plugin(cache_key: str) -> None:
    """Remove one cached plugin instance by key."""
    _plugin_cache.pop(cache_key, None)


def get_workflow_state_plugin(
    *,
    storage_dir: str,
    storage_type: str | None = None,
    storage_config: dict[str, Any] | None = None,
    issue_to_workflow_id: Callable[[str], str | None] | None = None,
    issue_to_workflow_map_setter: Callable[[str, str], None] | None = None,
    workflow_definition_path_resolver: Callable[[str], str | None] | None = None,
    repo_key: str | None = None,
    set_pending_approval: Callable[..., None] | None = None,
    clear_pending_approval: Callable[[str], None] | None = None,
    audit_log: Callable[..., None] | None = None,
    notify_approval_required: Callable[..., None] | None = None,
    cache_key: str = "workflow:state-engine",
):
    """Create a configured workflow-state adapter plugin instance."""
    overrides: dict[str, Any] = {
        "storage_dir": storage_dir,
    }

    optional_overrides = {
        "storage_type": storage_type,
        "storage_config": storage_config,
        "issue_to_workflow_id": issue_to_workflow_id,
        "issue_to_workflow_map_setter": issue_to_workflow_map_setter,
        "workflow_definition_path_resolver": workflow_definition_path_resolver,
        "repo_key": repo_key,
        "set_pending_approval": set_pending_approval,
        "clear_pending_approval": clear_pending_approval,
        "audit_log": audit_log,
        "notify_approval_required": notify_approval_required,
    }
    for key, value in optional_overrides.items():
        if value is not None:
            overrides[key] = value

    return get_profiled_plugin(
        "workflow_state_engine",
        overrides=overrides,
        cache_key=cache_key,
    )


def get_runtime_ops_plugin(
    *,
    process_name: str = "copilot|codex|gemini|claude",
    pgrep_timeout: int = 5,
    kill_timeout: int = 5,
    cache_key: str = "runtime-ops:process-guard",
):
    """Create a configured runtime process-ops plugin instance."""
    return get_profiled_plugin(
        "runtime_ops_default",
        overrides={
            "process_name": process_name,
            "pgrep_timeout": pgrep_timeout,
            "kill_timeout": kill_timeout,
        },
        cache_key=cache_key,
    )


def get_workflow_monitor_policy_plugin(
    *,
    list_open_issues: Callable[..., list[Any]] | None = None,
    list_issues: Callable[..., list[dict[str, Any]]] | None = None,
    get_comments: Callable[..., list[Any]] | None = None,
    search_linked_prs: Callable[..., list[Any]] | None = None,
    get_issue: Callable[..., Any] | None = None,
    cache_key: str | None = None,
):
    """Create a configured platform-neutral workflow monitor policy plugin instance."""
    overrides: dict[str, Any] = {}
    if list_open_issues is not None:
        overrides["list_open_issues"] = list_open_issues
    if list_issues is not None:
        overrides["list_issues"] = list_issues
    if get_comments is not None:
        overrides["get_comments"] = get_comments
    if search_linked_prs is not None:
        overrides["search_linked_prs"] = search_linked_prs
    if get_issue is not None:
        overrides["get_issue"] = get_issue

    return get_profiled_plugin(
        "workflow_monitor_policy",
        overrides=overrides,
        cache_key=cache_key,
    )


def get_workflow_policy_plugin(
    *,
    resolve_git_dir: Callable[[str], str | None] | None = None,
    resolve_git_dirs: Callable[[str], dict[str, str]] | None = None,
    create_pr_from_changes: Callable[..., str | None] | None = None,
    find_existing_pr: Callable[..., str | None] | None = None,
    cleanup_worktree: Callable[..., bool] | None = None,
    close_issue: Callable[..., bool] | None = None,
    send_notification: Callable[[str], None] | None = None,
    build_workflow_complete_message: Callable[..., str] | None = None,
    cache_key: str | None = "workflow:policy",
):
    """Create a configured workflow policy plugin instance."""
    overrides: dict[str, Any] = {}
    if resolve_git_dir is not None:
        overrides["resolve_git_dir"] = resolve_git_dir
    if resolve_git_dirs is not None:
        overrides["resolve_git_dirs"] = resolve_git_dirs
    if create_pr_from_changes is not None:
        overrides["create_pr_from_changes"] = create_pr_from_changes
    if find_existing_pr is not None:
        overrides["find_existing_pr"] = find_existing_pr
    if cleanup_worktree is not None:
        overrides["cleanup_worktree"] = cleanup_worktree
    if close_issue is not None:
        overrides["close_issue"] = close_issue
    if send_notification is not None:
        overrides["send_notification"] = send_notification
    if build_workflow_complete_message is not None:
        overrides["build_workflow_complete_message"] = build_workflow_complete_message

    return get_profiled_plugin(
        "workflow_policy",
        overrides=overrides,
        cache_key=cache_key,
    )


def get_webhook_policy_plugin(
    *,
    cache_key: str | None = "git-webhook-policy:default",
):
    """Create a configured Git webhook policy plugin instance."""
    return get_profiled_plugin(
        "webhook_policy",
        cache_key=cache_key,
    )
