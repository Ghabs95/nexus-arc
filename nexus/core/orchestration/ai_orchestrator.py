"""Compatibility wrapper for AI orchestrator backed by nexus-arc plugin."""

from collections.abc import Mapping
from typing import Any

from nexus.plugins.builtin.ai_runtime_plugin import (
    AIOrchestrator,
)
from .plugin_runtime import clear_cached_plugin, get_profiled_plugin

_orchestrator: AIOrchestrator | None = None


def _resolve_tasks_logs_dir(workspace: str, project: str | None = None) -> str:
    """Resolve tasks logs directory via config."""
    from ..config import get_tasks_logs_dir

    return get_tasks_logs_dir(workspace, project)


def get_orchestrator(config: Any | None = None) -> AIOrchestrator:
    """Get or create global orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        overrides: dict[str, Any] = {}
        if config is None:
            pass
        elif isinstance(config, dict):
            overrides.update(config)
        elif isinstance(config, Mapping) or hasattr(config, "items"):
            overrides.update(config.items())  # type: ignore
        elif hasattr(config, "get"):
            keys = (
                "copilot_cli_path",
                "gemini_cli_path",
                "gemini_model",
                "codex_cli_path",
                "codex_model",
                "claude_cli_path",
                "claude_model",
                "tool_preferences",
                "tool_preferences_resolver",
                "model_profiles",
                "model_profiles_resolver",
                "profile_provider_priority",
                "profile_provider_priority_resolver",
                "system_operations",
                "system_operations_resolver",
                "copilot_permissions",
                "copilot_permissions_resolver",
                "execution_mode_cli_config",
                "execution_mode_cli_config_resolver",
                "chat_agent_types_resolver",
                "fallback_enabled",
                "rate_limit_ttl",
                "max_retries",
                "analysis_timeout",
                "refine_description_timeout",
                "transcription_timeout",
                "whisper_model",
                "whisper_language",
                "whisper_languages",
            )
            for key in keys:
                val = config.get(key)
                if val is not None:
                    overrides[key] = val
        else:
            try:
                overrides.update(dict(config))  # type: ignore
            except Exception:
                pass
        overrides["tasks_logs_dir_resolver"] = _resolve_tasks_logs_dir
        plugin = get_profiled_plugin(
            "ai_runtime_default",
            overrides=overrides,
            cache_key="ai:orchestrator",
        )
        _orchestrator = plugin or AIOrchestrator(overrides)
    return _orchestrator


def reset_orchestrator() -> None:
    """Reset global orchestrator (for testing)."""
    global _orchestrator
    _orchestrator = None
    clear_cached_plugin("ai:orchestrator")
