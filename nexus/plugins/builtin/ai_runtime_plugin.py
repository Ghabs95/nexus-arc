"""Built-in plugin: AI runtime orchestration for Copilot/Gemini/Codex/Claude CLIs."""

import logging
import os
import subprocess
import time
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any

from nexus.core.execution_mode import normalize_execution_mode
from nexus.core.prompt_budget import prompt_prefix_fingerprint
from nexus.plugins.builtin.ai_runtime.agent_invoke_service import (
    invoke_agent_with_fallback as invoke_agent_with_fallback_impl,
)
from nexus.plugins.builtin.ai_runtime.analysis_service import (
    build_analysis_prompt as build_analysis_prompt_impl,
    parse_analysis_result as parse_analysis_result_impl,
    run_analysis_attempts as run_analysis_attempts_impl,
    run_analysis_with_provider as run_analysis_with_provider_impl,
    strip_cli_tool_output as strip_cli_tool_output_impl,
)
from nexus.plugins.builtin.ai_runtime.fallback_policy import (
    fallback_order_from_preferences as fallback_order_from_preferences_impl,
    resolve_analysis_tool_order as resolve_analysis_tool_order_impl,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.agent_invokers import (
    invoke_copilot_agent_cli as invoke_copilot_agent_cli_impl,
    invoke_gemini_agent_cli as invoke_gemini_agent_cli_impl,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.analysis_invokers import (
    run_copilot_analysis_cli as run_copilot_analysis_cli_impl,
    run_codex_analysis_cli as run_codex_analysis_cli_impl,
    run_gemini_analysis_cli as run_gemini_analysis_cli_impl,
    run_claude_analysis_cli as run_claude_analysis_cli_impl,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.claude_invoker import (
    invoke_claude_cli as invoke_claude_cli_impl,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.codex_invoker import (
    invoke_codex_cli as invoke_codex_cli_impl,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.ollama_invoker import (
    invoke_ollama_agent_cli as invoke_ollama_agent_cli_impl,
    run_ollama_analysis_cli as run_ollama_analysis_cli_impl,
    run_ollama_transcription_cli as run_ollama_transcription_cli_impl,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.transcription_invokers import (
    transcribe_with_copilot_cli as transcribe_with_copilot_cli_impl,
    transcribe_with_gemini_cli as transcribe_with_gemini_cli_impl,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.whisper_invoker import (
    transcribe_with_local_whisper as transcribe_with_local_whisper_impl,
)
from nexus.plugins.builtin.ai_runtime.provider_registry import (
    parse_tool_preference as parse_tool_preference_impl,
    parse_provider as parse_provider_impl,
    supports_analysis as supports_analysis_impl,
    unique_tools as unique_tools_impl,
)
from nexus.plugins.builtin.ai_runtime.system_operation_policy import (
    resolve_issue_override_agent as resolve_issue_override_agent_impl,
)
from nexus.plugins.builtin.ai_runtime.transcription_service import (
    is_non_transcription_artifact as is_non_transcription_artifact_impl,
    is_transcription_refusal as is_transcription_refusal_impl,
    normalize_local_whisper_model_name as normalize_local_whisper_model_name_impl,
    run_transcription_attempts as run_transcription_attempts_impl,
    resolve_transcription_attempts as resolve_transcription_attempts_impl,
)

logger = logging.getLogger(__name__)


class AIProvider(Enum):
    """AI provider enumeration."""

    COPILOT = "copilot"
    GEMINI = "gemini"
    CODEX = "codex"
    CLAUDE = "claude"
    OLLAMA = "ollama"


class ToolUnavailableError(Exception):
    """Raised when a tool is unavailable or fails."""


class RateLimitedError(Exception):
    """Raised when a tool hits rate limits."""


def _default_tasks_logs_dir(workspace: str, project: str | None = None) -> str:
    """Resolve default logs dir when host app does not provide a resolver."""
    logs_dir = os.path.join(workspace, ".nexus", "tasks", "logs")
    if project:
        logs_dir = os.path.join(logs_dir, project)
    return logs_dir


class AIOrchestrator:
    """Manages AI tool orchestration with fallback support."""

    _rate_limits: dict[str, dict[str, Any]] = {}
    _tool_available: dict[str, bool | float] = {}

    def __init__(self, config: dict[str, Any] | None = None):
        self._tool_available: dict[str, bool | float] = {}
        self._rate_limits: dict[str, dict[str, Any]] = {}
        self.config = config or {}
        self.copilot_cli_path = self.config.get("copilot_cli_path", "copilot")
        self.gemini_cli_path = self.config.get("gemini_cli_path", "gemini")
        self.gemini_model = str(self.config.get("gemini_model", "")).strip()
        self.codex_cli_path = self.config.get("codex_cli_path", "codex")
        self.codex_model = str(self.config.get("codex_model", "")).strip()
        self.claude_cli_path = self.config.get("claude_cli_path", "claude")
        self.claude_model = str(self.config.get("claude_model", "")).strip()
        self.ollama_cli_path = self.config.get("ollama_cli_path", "ollama")
        self.ollama_model = str(self.config.get("ollama_model", "")).strip()
        self.copilot_model = str(self.config.get("copilot_model", "")).strip()
        self.copilot_supports_model = bool(self.config.get("copilot_supports_model", False))
        self.ai_tool_preferences_strict = bool(self.config.get("ai_tool_preferences_strict", False))
        self.tool_preferences = self.config.get("tool_preferences", {})
        self.tool_preferences_resolver: Callable[[str], dict[str, Any] | None] | None = (
            self.config.get(
                "tool_preferences_resolver",
            )
        )
        self.model_profiles = self.config.get("model_profiles", {})
        self.model_profiles_resolver: Callable[[str], dict[str, Any] | None] | None = (
            self.config.get("model_profiles_resolver")
        )
        self.profile_provider_priority = self.config.get("profile_provider_priority", {})
        self.profile_provider_priority_resolver: Callable[[str], dict[str, Any] | None] | None = (
            self.config.get("profile_provider_priority_resolver")
        )
        self.system_operations = self.config.get("system_operations", {})
        self.system_operations_resolver: Callable[[str], dict[str, Any] | None] | None = (
            self.config.get(
                "system_operations_resolver",
            )
        )
        self.copilot_permissions = self.config.get("copilot_permissions", {})
        self.copilot_permissions_resolver: Callable[[str], dict[str, Any] | None] | None = (
            self.config.get("copilot_permissions_resolver")
        )
        self.execution_mode_cli_config = self.config.get("execution_mode_cli_config", {})
        self.execution_mode_cli_config_resolver: Callable[
            [str], dict[str, Any] | None
        ] | None = self.config.get("execution_mode_cli_config_resolver")
        self.chat_agent_types_resolver: Callable[[str], list[str] | None] | None = self.config.get(
            "chat_agent_types_resolver",
        )
        self.fallback_enabled = self.config.get("fallback_enabled", True)
        self.rate_limit_ttl = self.config.get("rate_limit_ttl", 3600)
        self.max_retries = self.config.get("max_retries", 3)
        self.analysis_timeout = self.config.get("analysis_timeout", 120)
        self.refine_description_timeout = self.config.get("refine_description_timeout", 90)
        self.ai_prompt_max_chars = int(self.config.get("ai_prompt_max_chars", 16000) or 16000)
        self.ai_context_summary_max_chars = int(
            self.config.get("ai_context_summary_max_chars", 1200) or 1200
        )
        self.transcription_timeout = self.config.get("transcription_timeout", 120)
        self.whisper_model = (
            str(self.config.get("whisper_model", "whisper-1")).strip() or "whisper-1"
        )
        self.whisper_language = str(self.config.get("whisper_language", "")).strip().lower() or None
        raw_whisper_languages = str(self.config.get("whisper_languages", "")).strip().lower()
        self.whisper_languages = [
            value.strip() for value in raw_whisper_languages.split(",") if value.strip()
        ]
        self._whisper_model_instance = None
        self._whisper_model_name = ""
        self._warned_tool_preferences: set[str] = set()
        self.get_tasks_logs_dir: Callable[[str, str | None], str] = self.config.get(
            "tasks_logs_dir_resolver",
            _default_tasks_logs_dir,
        )

    @staticmethod
    def _parse_provider(candidate: Any) -> AIProvider | None:
        parsed = parse_provider_impl(candidate, AIProvider)
        return parsed if isinstance(parsed, AIProvider) else None

    @staticmethod
    def _parse_tool_preference(candidate: Any) -> Any:
        return parse_tool_preference_impl(candidate, AIProvider)

    def _parse_provider_with_policy(
        self,
        candidate: Any,
        *,
        project_name: str | None = None,
        agent_name: str | None = None,
    ) -> AIProvider | None:
        spec = self._parse_tool_preference(candidate)
        if getattr(spec, "valid", False):
            provider = getattr(spec, "provider", None)
            return provider if isinstance(provider, AIProvider) else None

        key = f"{project_name or 'default'}:{agent_name or '*'}:{candidate!r}"
        if self.ai_tool_preferences_strict:
            raise ValueError(
                f"Invalid ai_tool_preferences entry for {agent_name or 'unknown'}"
                f" in project {project_name or 'default'}: {getattr(spec, 'reason', 'parse error')}"
            )
        if key not in self._warned_tool_preferences:
            self._warned_tool_preferences.add(key)
            logger.warning(
                "Ignoring invalid ai_tool_preferences entry for agent=%s project=%s: %r (%s)",
                agent_name or "unknown",
                project_name or "default",
                candidate,
                getattr(spec, "reason", "parse error"),
            )
        return None

    @staticmethod
    def _supports_analysis(tool: AIProvider) -> bool:
        return supports_analysis_impl(
            tool,
            supported_tools=[
                AIProvider.GEMINI,
                AIProvider.COPILOT,
                AIProvider.CODEX,
                AIProvider.CLAUDE,
                AIProvider.OLLAMA,
            ],
        )

    @staticmethod
    def _looks_like_bug_issue(text: str) -> bool:
        candidate = str(text or "").strip().lower()
        if not candidate:
            return False
        bug_markers = (
            "bug",
            "issue",
            "error",
            "exception",
            "traceback",
            "stack trace",
            "fails",
            "failing",
            "failed",
            "crash",
            "broken",
            "regression",
            "hotfix",
            "not working",
            "doesn't work",
            "doesnt work",
        )
        return any(marker in candidate for marker in bug_markers)

    @staticmethod
    def _unique_tools(order: list[AIProvider]) -> list[AIProvider]:
        return unique_tools_impl(order)

    def _resolved_tool_preferences(self, project_name: str | None = None) -> dict[str, Any]:
        if project_name and callable(self.tool_preferences_resolver):
            try:
                resolved = self.tool_preferences_resolver(str(project_name))
                if isinstance(resolved, dict):
                    return resolved
            except Exception as exc:
                logger.warning(
                    "Could not resolve tool preferences for project %s: %s",
                    project_name,
                    exc,
                )
        return self.tool_preferences if isinstance(self.tool_preferences, dict) else {}

    def _resolved_system_operations(self, project_name: str | None = None) -> dict[str, Any]:
        if project_name and callable(self.system_operations_resolver):
            try:
                resolved = self.system_operations_resolver(str(project_name))
                if isinstance(resolved, dict):
                    return resolved
            except Exception as exc:
                logger.warning(
                    "Could not resolve operation agents for project %s: %s",
                    project_name,
                    exc,
                )
        return self.system_operations if isinstance(self.system_operations, dict) else {}

    def _resolved_copilot_permissions(self, project_name: str | None = None) -> dict[str, Any]:
        if project_name and callable(self.copilot_permissions_resolver):
            try:
                resolved = self.copilot_permissions_resolver(str(project_name))
                if isinstance(resolved, dict):
                    return resolved
            except Exception as exc:
                logger.warning(
                    "Could not resolve Copilot permissions for project %s: %s",
                    project_name,
                    exc,
                )
        return self.copilot_permissions if isinstance(self.copilot_permissions, dict) else {}

    def _resolved_execution_mode_cli_config(self, project_name: str | None = None) -> dict[str, Any]:
        if project_name and callable(self.execution_mode_cli_config_resolver):
            try:
                resolved = self.execution_mode_cli_config_resolver(str(project_name))
                if isinstance(resolved, dict):
                    return resolved
            except Exception as exc:
                logger.warning(
                    "Could not resolve execution-mode CLI config for project %s: %s",
                    project_name,
                    exc,
                )
        return (
            self.execution_mode_cli_config
            if isinstance(self.execution_mode_cli_config, dict)
            else {}
        )

    def _resolve_execution_mode_provider_config(
        self,
        tool: AIProvider,
        execution_mode: str | None,
        *,
        project_name: str | None = None,
    ) -> dict[str, Any]:
        normalized_mode = normalize_execution_mode(execution_mode)
        if not normalized_mode:
            return {}

        provider_config = self._resolved_execution_mode_cli_config(project_name).get(tool.value)
        if not isinstance(provider_config, Mapping):
            provider_config = {}
        mode_config = provider_config.get(normalized_mode)
        if not isinstance(mode_config, Mapping):
            mode_config = {}

        resolved: dict[str, Any] = {}
        profile_name = str(mode_config.get("profile") or "").strip()
        if profile_name:
            resolved["profile"] = profile_name

        raw_args = mode_config.get("args")
        if isinstance(raw_args, list):
            resolved["args"] = [str(value).strip() for value in raw_args if str(value).strip()]

        raw_env = mode_config.get("env")
        if isinstance(raw_env, Mapping):
            resolved["env"] = {
                str(key).strip(): str(value or "")
                for key, value in raw_env.items()
                if str(key).strip()
            }
        return resolved

    def _resolve_copilot_permissions_for_agent(
        self,
        *,
        agent_name: str | None,
        project_name: str | None = None,
    ) -> dict[str, Any]:
        resolved = self._resolved_copilot_permissions(project_name)
        merged = dict(resolved) if isinstance(resolved, Mapping) else {}
        if not agent_name:
            return merged
        agent_spec = self._resolved_tool_preferences(project_name).get(agent_name)
        if isinstance(agent_spec, Mapping):
            agent_override = agent_spec.get("copilot_permissions")
            if isinstance(agent_override, Mapping):
                merged.update(agent_override)
        return merged

    def _resolved_model_profiles(self, project_name: str | None = None) -> dict[str, Any]:
        if project_name and callable(self.model_profiles_resolver):
            try:
                resolved = self.model_profiles_resolver(str(project_name))
                if isinstance(resolved, dict):
                    return resolved
            except Exception as exc:
                logger.warning(
                    "Could not resolve model profiles for project %s: %s",
                    project_name,
                    exc,
                )
        return self.model_profiles if isinstance(self.model_profiles, dict) else {}

    def _auto_primary_tool_for_profile(
        self,
        profile_name: str,
        project_name: str | None = None,
    ) -> AIProvider:
        providers_for_profile = self._providers_for_profile(
            profile_name,
            project_name=project_name,
        )
        if not providers_for_profile:
            return AIProvider.COPILOT

        for tool in providers_for_profile:
            if self.check_tool_available(tool):
                return tool
        return providers_for_profile[0]

    def _resolved_profile_provider_priority(
        self, project_name: str | None = None
    ) -> dict[str, Any]:
        if project_name and callable(self.profile_provider_priority_resolver):
            try:
                resolved = self.profile_provider_priority_resolver(str(project_name))
                if isinstance(resolved, dict):
                    return resolved
            except Exception as exc:
                logger.warning(
                    "Could not resolve profile provider priority for project %s: %s",
                    project_name,
                    exc,
                )
        return (
            self.profile_provider_priority
            if isinstance(self.profile_provider_priority, dict)
            else {}
        )

    def _profile_priority_order(
        self,
        profile_name: str,
        project_name: str | None = None,
    ) -> list[AIProvider]:
        normalized = str(profile_name or "").strip().lower()

        configured = self._resolved_profile_provider_priority(project_name).get(normalized)
        if isinstance(configured, list):
            parsed: list[AIProvider] = []
            seen: set[AIProvider] = set()
            for item in configured:
                parsed_provider = self._parse_provider({"provider": item, "profile": normalized})
                if isinstance(parsed_provider, AIProvider) and parsed_provider not in seen:
                    parsed.append(parsed_provider)
                    seen.add(parsed_provider)
            if parsed:
                return parsed

        if normalized in {"fast", "flash", "small", "low"}:
            return [
                AIProvider.GEMINI,
                AIProvider.COPILOT,
                AIProvider.CLAUDE,
                AIProvider.CODEX,
                AIProvider.OLLAMA,
            ]
        if normalized in {"reasoning", "pro", "large", "high"}:
            return [
                AIProvider.CLAUDE,
                AIProvider.CODEX,
                AIProvider.COPILOT,
                AIProvider.GEMINI,
                AIProvider.OLLAMA,
            ]
        return [
            AIProvider.COPILOT,
            AIProvider.GEMINI,
            AIProvider.CLAUDE,
            AIProvider.CODEX,
            AIProvider.OLLAMA,
        ]

    def _providers_for_profile(
        self,
        profile_name: str,
        *,
        project_name: str | None = None,
    ) -> list[AIProvider]:
        profiles = self._resolved_model_profiles(project_name)
        profile_cfg = profiles.get(str(profile_name or "").strip())
        if not isinstance(profile_cfg, Mapping):
            return []

        ordered: list[AIProvider] = []
        for tool in self._profile_priority_order(profile_name, project_name=project_name):
            model_name = str(profile_cfg.get(tool.value) or "").strip()
            if model_name:
                ordered.append(tool)

        # Keep backward-compatible behavior for custom profile names
        # by appending any configured provider not already in the profile order.
        for tool in AIProvider:
            if tool in ordered:
                continue
            model_name = str(profile_cfg.get(tool.value) or "").strip()
            if model_name:
                ordered.append(tool)
        return ordered

    def _fallback_order_from_preferences(self, project_name: str | None = None) -> list[AIProvider]:
        tool_preferences = self._resolved_tool_preferences(project_name)
        return fallback_order_from_preferences_impl(
            resolved_tool_preferences=tool_preferences,
            parse_provider=lambda candidate: self._parse_provider_with_policy(
                candidate,
                project_name=project_name,
            ),
        )

    def _resolved_tool_spec(
        self,
        agent_name: str | None,
        project_name: str | None = None,
    ) -> Any:
        if not agent_name:
            return self._parse_tool_preference("")
        preferences = self._resolved_tool_preferences(project_name)
        raw_value = preferences.get(agent_name)
        return self._parse_tool_preference(raw_value)

    def _resolve_model_for_tool(
        self,
        *,
        tool: AIProvider,
        agent_name: str | None,
        project_name: str | None = None,
    ) -> str:
        if not agent_name:
            return ""

        spec = self._resolved_tool_spec(agent_name, project_name=project_name)
        spec_profile = str(getattr(spec, "profile", "") or "").strip()
        spec_valid = bool(getattr(spec, "valid", False))

        if not spec_valid:
            self._parse_provider_with_policy(
                self._resolved_tool_preferences(project_name).get(agent_name),
                project_name=project_name,
                agent_name=agent_name,
            )
            return ""
        if not spec_profile:
            return ""

        profiles = self._resolved_model_profiles(project_name)
        profile_cfg = profiles.get(spec_profile)
        if not isinstance(profile_cfg, Mapping):
            if self.ai_tool_preferences_strict:
                raise ValueError(
                    f"Unknown model profile '{spec_profile}' for agent '{agent_name}' "
                    f"in project '{project_name or 'default'}'"
                )
            warn_key = f"missing_profile:{project_name or 'default'}:{agent_name}:{spec_profile}"
            if warn_key not in self._warned_tool_preferences:
                self._warned_tool_preferences.add(warn_key)
                logger.warning(
                    "Ignoring unknown model profile for agent=%s project=%s: %s",
                    agent_name,
                    project_name or "default",
                    spec_profile,
                )
            return ""

        spec_model = str(profile_cfg.get(tool.value) or "").strip()
        if not spec_model:
            return ""
        if tool == AIProvider.COPILOT and not self.copilot_supports_model:
            warn_key = (
                f"copilot_model_disabled:{project_name or 'default'}:{agent_name}:{spec_model}"
            )
            if warn_key not in self._warned_tool_preferences:
                self._warned_tool_preferences.add(warn_key)
                logger.warning(
                    "Copilot model override ignored for agent=%s project=%s because "
                    "copilot_supports_model is disabled",
                    agent_name,
                    project_name or "default",
                )
            return ""
        return spec_model

    def _default_chat_agent_type(self, project_name: str | None = None) -> str:
        if not project_name or not callable(self.chat_agent_types_resolver):
            return ""
        try:
            values = self.chat_agent_types_resolver(str(project_name))
        except Exception as exc:
            logger.warning(
                "Could not resolve chat agent types for project %s: %s", project_name, exc
            )
            return ""
        if not isinstance(values, list):
            return ""
        for item in values:
            normalized = str(item or "").strip().lower()
            if normalized:
                return normalized
        return ""

    def _resolve_issue_override_agent(
        self,
        *,
        task_key: str,
        mapped_agent: str,
        text: str,
        system_operations: Mapping[str, Any] | None,
    ) -> str:
        return resolve_issue_override_agent_impl(
            task_key=task_key,
            mapped_agent=mapped_agent,
            text=text,
            system_operations=system_operations,
            looks_like_bug_issue=self._looks_like_bug_issue,
        )

    def check_tool_available(self, tool: AIProvider) -> bool:
        if tool.value in self._tool_available:
            cached_at = float(self._tool_available.get(f"{tool.value}_cached_at", 0.0))
            if time.time() - cached_at < 300:
                return bool(self._tool_available[tool.value])

        if tool.value in self._rate_limits:
            rate_info = self._rate_limits[tool.value]
            if time.time() < rate_info["until"]:
                logger.warning(
                    "⏸️  %s is rate-limited until %s (retries: %s/%s)",
                    tool.value.upper(),
                    rate_info["until"],
                    rate_info["retries"],
                    self.max_retries,
                )
                return False
            del self._rate_limits[tool.value]

        try:
            if tool == AIProvider.GEMINI:
                path = self.gemini_cli_path
            elif tool == AIProvider.CODEX:
                path = self.codex_cli_path
            elif tool == AIProvider.CLAUDE:
                path = self.claude_cli_path
            elif tool == AIProvider.OLLAMA:
                path = self.ollama_cli_path
            else:
                path = self.copilot_cli_path
            result = subprocess.run(
                [path, "--version"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            available = result.returncode == 0
            if available:
                logger.info("✅ %s available", tool.value.upper())
            else:
                logger.warning(
                    "⚠️  %s unavailable: version check failed (exit=%s)",
                    tool.value.upper(),
                    result.returncode,
                )
        except Exception as exc:
            available = False
            logger.warning("⚠️  %s unavailable: %s", tool.value.upper(), exc)

        tool_key = str(tool.value)
        self._tool_available[tool_key] = available
        self._tool_available[f"{tool_key}_cached_at"] = time.time()
        return available

    def get_primary_tool(
        self, agent_name: str | None = None, project_name: str | None = None
    ) -> AIProvider:
        tool_preferences = self._resolved_tool_preferences(project_name)
        if agent_name and agent_name in tool_preferences:
            spec = self._parse_tool_preference(tool_preferences.get(agent_name))
            if bool(getattr(spec, "valid", False)):
                pref = getattr(spec, "provider", None)
                if isinstance(pref, AIProvider):
                    return pref
                profile_name = str(getattr(spec, "profile", "") or "").strip()
                if profile_name:
                    return self._auto_primary_tool_for_profile(profile_name, project_name)
            else:
                self._parse_provider_with_policy(
                    tool_preferences.get(agent_name),
                    project_name=project_name,
                    agent_name=agent_name,
                )

        return AIProvider.COPILOT

    def get_fallback_tool(self, primary: AIProvider) -> AIProvider | None:
        if not self.fallback_enabled:
            return None

        fallbacks = [tool for tool in AIProvider if tool != primary]
        for fallback in fallbacks:
            if self.check_tool_available(fallback):
                logger.info(
                    "🔄 Fallback ready from %s → %s (will use only if primary fails)",
                    primary.value,
                    fallback.value,
                )
                return fallback

        logger.error("❌ No fallback providers available for %s", primary.value)
        return None

    def _resolve_analysis_tool_order(
        self,
        task: str,
        project_name: str | None = None,
        text: str = "",
    ) -> list[AIProvider]:
        system_operations = self._resolved_system_operations(project_name)
        if not isinstance(system_operations, Mapping):
            system_operations = {}
        ordered = resolve_analysis_tool_order_impl(
            task=task,
            text=text,
            project_name=project_name,
            fallback_enabled=bool(self.fallback_enabled),
            system_operations=system_operations,
            default_chat_agent_type=self._default_chat_agent_type(project_name),
            resolve_issue_override_agent=self._resolve_issue_override_agent,
            get_primary_tool=lambda agent, project: self.get_primary_tool(
                agent,
                project_name=project,
            ),
            fallback_order_from_preferences_fn=self._fallback_order_from_preferences,
            unique_tools=self._unique_tools,
            supports_analysis=self._supports_analysis,
            default_tools=[
                AIProvider.GEMINI,
                AIProvider.COPILOT,
                AIProvider.CLAUDE,
                AIProvider.CODEX,
                AIProvider.OLLAMA,
            ],
        )
        # Keep refine-description fallback deterministic across environments:
        # use primary + one explicit fallback rather than walking all providers.
        if str(task or "").strip().lower() == "refine_description" and ordered:
            primary = ordered[0]
            if not self.fallback_enabled:
                return [primary]
            fallback = self.get_fallback_tool(primary)
            if fallback and fallback != primary:
                return [primary, fallback]
            return [primary]
        return ordered

    def _resolve_transcription_attempts(self, project_name: str | None = None) -> list[str]:
        system_operations = self._resolved_system_operations(project_name)
        if not isinstance(system_operations, Mapping):
            system_operations = {}
        return resolve_transcription_attempts_impl(
            project_name=project_name,
            system_operations=system_operations,
            fallback_enabled=bool(self.fallback_enabled),
            fallback_provider=os.getenv("TRANSCRIPT_PROVIDER", "").strip().lower(),
            get_primary_tool=lambda agent, project: self.get_primary_tool(
                agent, project_name=project
            ),
            fallback_order_from_preferences_fn=self._fallback_order_from_preferences,
            unique_tools=self._unique_tools,
            supported_providers=[AIProvider.GEMINI, AIProvider.COPILOT, AIProvider.OLLAMA],
            warn_unsupported_mapped_provider=lambda provider, agent: logger.warning(
                "Ignoring unsupported transcription provider '%s' for mapped agent '%s'; "
                "falling back to TRANSCRIPT_PROVIDER",
                provider,
                agent,
            ),
        )

    def _run_analysis_with_provider(
        self, tool: AIProvider, text: str, task: str, **kwargs
    ) -> dict[str, Any]:
        return run_analysis_with_provider_impl(
            tool=tool,
            providers_map={
                AIProvider.GEMINI: self._run_gemini_cli_analysis,
                AIProvider.COPILOT: self._run_copilot_analysis,
                AIProvider.CODEX: self._run_codex_analysis,
                AIProvider.CLAUDE: self._run_claude_analysis,
                AIProvider.OLLAMA: self._run_ollama_analysis,
            },
            text=text,
            task=task,
            kwargs=kwargs,
            tool_unavailable_error=ToolUnavailableError,
        )

    def _get_tool_order(
        self,
        agent_name: str | None = None,
        preferred_tool: str | AIProvider | None = None,
        project_name: str | None = None,
    ) -> list:
        """Return all known tools in priority order for this agent.

        The preferred tool goes first; the rest follow in enum declaration order.
        Adding a new provider (Claude, Codex, …) only requires extending AIProvider
        and implementing _invoke_tool dispatch — no other changes needed.
        """
        if preferred_tool:
            if isinstance(preferred_tool, str):
                try:
                    preferred = AIProvider(preferred_tool.lower().strip())
                except ValueError:
                    logger.warning("Invalid preferred tool: %s", preferred_tool)
                    preferred = None
            else:
                preferred = preferred_tool

            if preferred:
                all_tools = list(AIProvider)
                return [preferred] + [t for t in all_tools if t != preferred]

        spec = self._resolved_tool_spec(agent_name, project_name=project_name)
        spec_valid = bool(getattr(spec, "valid", False))
        spec_profile = str(getattr(spec, "profile", "") or "").strip()
        providers_for_profile = (
            self._providers_for_profile(
                spec_profile,
                project_name=project_name,
            )
            if spec_valid and spec_profile
            else []
        )

        preferred = self.get_primary_tool(agent_name, project_name=project_name)
        if providers_for_profile:
            return [preferred] + [t for t in providers_for_profile if t != preferred]

        all_tools = list(AIProvider)
        return [preferred] + [t for t in all_tools if t != preferred]

    def _invoke_tool(
        self,
        tool: AIProvider,
        agent_prompt: str,
        workspace_dir: str,
        agents_dir: str,
        base_dir: str,
        agent_name: str | None = None,
        project_name: str | None = None,
        issue_num: str | None = None,
        log_subdir: str | None = None,
        env: dict[str, str] | None = None,
        execution_mode: str | None = None,
    ) -> int | None:
        """Dispatch to the correct CLI for *tool*. Extend here when adding new providers."""
        model_override = self._resolve_model_for_tool(
            tool=tool,
            agent_name=agent_name,
            project_name=project_name,
        )
        execution_mode_config = self._resolve_execution_mode_provider_config(
            tool,
            execution_mode,
            project_name=project_name,
        )
        if tool == AIProvider.COPILOT:
            return self._invoke_copilot(
                agent_prompt,
                workspace_dir,
                agents_dir,
                base_dir,
                agent_name=agent_name,
                project_name=project_name,
                model_override=model_override,
                issue_num=issue_num,
                log_subdir=log_subdir,
                env=env,
                execution_mode=execution_mode,
                execution_mode_config=execution_mode_config,
            )
        if tool == AIProvider.GEMINI:
            return self._invoke_gemini_agent(
                agent_prompt,
                workspace_dir,
                agents_dir,
                base_dir,
                model_override=model_override,
                issue_num=issue_num,
                log_subdir=log_subdir,
                env=env,
                execution_mode=execution_mode,
                execution_mode_config=execution_mode_config,
            )
        if tool == AIProvider.CODEX:
            return self._invoke_codex(
                agent_prompt,
                workspace_dir,
                agents_dir,
                base_dir,
                model_override=model_override,
                issue_num=issue_num,
                log_subdir=log_subdir,
                env=env,
                execution_mode=execution_mode,
                execution_mode_config=execution_mode_config,
            )
        if tool == AIProvider.CLAUDE:
            return self._invoke_claude(
                agent_prompt,
                workspace_dir,
                agents_dir,
                base_dir,
                model_override=model_override,
                issue_num=issue_num,
                log_subdir=log_subdir,
                env=env,
                execution_mode=execution_mode,
                execution_mode_config=execution_mode_config,
            )
        if tool == AIProvider.OLLAMA:
            return self._invoke_ollama(
                agent_prompt,
                workspace_dir,
                agents_dir,
                base_dir,
                model_override=model_override,
                issue_num=issue_num,
                log_subdir=log_subdir,
                env=env,
                execution_mode=execution_mode,
            )
        raise ToolUnavailableError(f"No invoker implemented for tool: {tool.value}")

    def record_rate_limit(self, tool: AIProvider, retry_count: int = 1):
        self._rate_limits[tool.value] = {
            "until": time.time() + self.rate_limit_ttl,
            "retries": retry_count,
        }
        logger.warning("⏸️  %s rate-limited for %ss", tool.value.upper(), self.rate_limit_ttl)

    def _record_rate_limit_with_context(
        self,
        tool: AIProvider,
        error: Exception,
        retry_count: int = 1,
        context: str = "",
    ) -> None:
        """Record rate-limit cooldown and include root-cause context in logs."""
        self.record_rate_limit(tool, retry_count=retry_count)
        message = str(error).strip() or repr(error)
        if len(message) > 600:
            message = f"{message[:600]}..."
        context_prefix = f" ({context})" if context else ""
        logger.warning(
            "⚠️  %s rate-limit detail%s: %s",
            tool.value.upper(),
            context_prefix,
            message,
        )

    def record_failure(self, tool: AIProvider):
        if tool.value not in self._rate_limits:
            self._tool_available[tool.value] = False
            logger.error("❌ %s marked unavailable", tool.value.upper())
            return

        current = self._rate_limits[tool.value]
        current["retries"] += 1
        if current["retries"] >= self.max_retries:
            logger.error("❌ %s exceeded max retries, marking unavailable", tool.value.upper())
            self._tool_available[tool.value] = False

    def invoke_agent(
        self,
        agent_prompt: str,
        workspace_dir: str,
        agents_dir: str,
        base_dir: str,
        issue_url: str | None = None,
        agent_name: str | None = None,
        project_name: str | None = None,
        preferred_tool: str | AIProvider | None = None,
        exclude_tools: list | None = None,
        log_subdir: str | None = None,
        env: dict[str, str] | None = None,
        execution_mode: str | None = None,
    ) -> tuple[int | None, AIProvider]:
        """Try each available tool in priority order, skipping any in *exclude_tools*.

        *exclude_tools* is a list of tool value strings (e.g. ["gemini"]) that should
        be skipped entirely — used by the retry path so a crashed tool is not retried.
        Adding a new provider only requires extending AIProvider + _invoke_tool.
        """
        fingerprint = prompt_prefix_fingerprint(agent_prompt)
        logger.info(
            "🧾 Agent prompt metrics: chars=%s prefix_fp=%s agent=%s",
            len(agent_prompt),
            fingerprint,
            agent_name or "unknown",
        )
        pid, selected_tool = invoke_agent_with_fallback_impl(
            issue_url=issue_url,
            exclude_tools=exclude_tools,
            get_tool_order=lambda: self._get_tool_order(
                agent_name,
                preferred_tool,
                project_name,
            ),
            check_tool_available=self.check_tool_available,
            invoke_tool=lambda tool, issue_num: self._invoke_tool(
                tool,
                agent_prompt,
                workspace_dir,
                agents_dir,
                base_dir,
                agent_name=agent_name,
                project_name=project_name,
                issue_num=issue_num,
                log_subdir=log_subdir,
                env=env,
                execution_mode=execution_mode,
            ),
            record_rate_limit_with_context=lambda tool, exc, context: self._record_rate_limit_with_context(
                tool,
                exc,
                context=context,
            ),
            record_failure=self.record_failure,
            rate_limited_error_type=RateLimitedError,
            tool_unavailable_error_type=ToolUnavailableError,
            logger=logger,
        )
        selected_model = self._resolve_model_for_tool(
            tool=selected_tool,
            agent_name=agent_name,
            project_name=project_name,
        )
        logger.info(
            "✅ Agent provider selected: agent=%s provider=%s model=%s pid=%s",
            agent_name or "unknown",
            selected_tool.value,
            selected_model or "default",
            pid,
        )
        return pid, selected_tool

    def _invoke_copilot(
        self,
        agent_prompt: str,
        workspace_dir: str,
        agents_dir: str,
        base_dir: str,
        agent_name: str | None = None,
        project_name: str | None = None,
        model_override: str = "",
        issue_num: str | None = None,
        log_subdir: str | None = None,
        env: dict[str, str] | None = None,
        execution_mode: str | None = None,
        execution_mode_config: Mapping[str, Any] | None = None,
    ) -> int | None:
        return invoke_copilot_agent_cli_impl(
            check_tool_available=self.check_tool_available,
            copilot_provider=AIProvider.COPILOT,
            copilot_cli_path=self.copilot_cli_path,
            copilot_model=model_override or self.copilot_model,
            copilot_supports_model=self.copilot_supports_model,
            copilot_permissions=self._resolve_copilot_permissions_for_agent(
                agent_name=agent_name,
                project_name=project_name,
            ),
            get_tasks_logs_dir=self.get_tasks_logs_dir,
            tool_unavailable_error=ToolUnavailableError,
            rate_limited_error=RateLimitedError,
            logger=logger,
            agent_prompt=agent_prompt,
            workspace_dir=workspace_dir,
            agents_dir=agents_dir,
            base_dir=base_dir,
            issue_num=issue_num,
            log_subdir=log_subdir,
            env=env,
            execution_mode=execution_mode,
            execution_mode_config=execution_mode_config,
        )

    def _invoke_gemini_agent(
        self,
        agent_prompt: str,
        workspace_dir: str,
        agents_dir: str,
        base_dir: str,
        model_override: str = "",
        issue_num: str | None = None,
        log_subdir: str | None = None,
        env: dict[str, str] | None = None,
        execution_mode: str | None = None,
        execution_mode_config: Mapping[str, Any] | None = None,
    ) -> int | None:
        return invoke_gemini_agent_cli_impl(
            check_tool_available=self.check_tool_available,
            gemini_provider=AIProvider.GEMINI,
            gemini_cli_path=self.gemini_cli_path,
            gemini_model=model_override or self.gemini_model,
            get_tasks_logs_dir=self.get_tasks_logs_dir,
            tool_unavailable_error=ToolUnavailableError,
            rate_limited_error=RateLimitedError,
            logger=logger,
            agent_prompt=agent_prompt,
            workspace_dir=workspace_dir,
            agents_dir=agents_dir,
            issue_num=issue_num,
            log_subdir=log_subdir,
            env=env,
            execution_mode=execution_mode,
            execution_mode_config=execution_mode_config,
        )

    def _invoke_codex(
        self,
        agent_prompt: str,
        workspace_dir: str,
        agents_dir: str,
        base_dir: str,
        model_override: str = "",
        issue_num: str | None = None,
        log_subdir: str | None = None,
        env: dict[str, str] | None = None,
        execution_mode: str | None = None,
        execution_mode_config: Mapping[str, Any] | None = None,
    ) -> int | None:
        return invoke_codex_cli_impl(
            check_tool_available=self.check_tool_available,
            codex_provider=AIProvider.CODEX,
            codex_cli_path=self.codex_cli_path,
            codex_model=model_override or self.codex_model,
            get_tasks_logs_dir=self.get_tasks_logs_dir,
            tool_unavailable_error=ToolUnavailableError,
            logger=logger,
            agent_prompt=agent_prompt,
            workspace_dir=workspace_dir,
            issue_num=issue_num,
            log_subdir=log_subdir,
            env=env,
            execution_mode=execution_mode,
            execution_mode_config=execution_mode_config,
        )

    def _invoke_claude(
        self,
        agent_prompt: str,
        workspace_dir: str,
        agents_dir: str,
        base_dir: str,
        model_override: str = "",
        issue_num: str | None = None,
        log_subdir: str | None = None,
        env: dict[str, str] | None = None,
        execution_mode: str | None = None,
        execution_mode_config: Mapping[str, Any] | None = None,
    ) -> int | None:
        return invoke_claude_cli_impl(
            check_tool_available=self.check_tool_available,
            claude_provider=AIProvider.CLAUDE,
            claude_cli_path=self.claude_cli_path,
            claude_model=model_override or self.claude_model,
            get_tasks_logs_dir=self.get_tasks_logs_dir,
            tool_unavailable_error=ToolUnavailableError,
            rate_limited_error=RateLimitedError,
            logger=logger,
            agent_prompt=agent_prompt,
            workspace_dir=workspace_dir,
            agents_dir=agents_dir,
            issue_num=issue_num,
            log_subdir=log_subdir,
            env=env,
            execution_mode=execution_mode,
            execution_mode_config=execution_mode_config,
        )

    def _invoke_ollama(
        self,
        agent_prompt: str,
        workspace_dir: str,
        agents_dir: str,
        base_dir: str,
        model_override: str = "",
        issue_num: str | None = None,
        log_subdir: str | None = None,
        env: dict[str, str] | None = None,
        execution_mode: str | None = None,
    ) -> int | None:
        return invoke_ollama_agent_cli_impl(
            check_tool_available=self.check_tool_available,
            ollama_provider=AIProvider.OLLAMA,
            ollama_cli_path=self.ollama_cli_path,
            ollama_model=model_override or str(self.config.get("ollama_model", "")).strip(),
            get_tasks_logs_dir=self.get_tasks_logs_dir,
            tool_unavailable_error=ToolUnavailableError,
            rate_limited_error=RateLimitedError,
            logger=logger,
            agent_prompt=agent_prompt,
            workspace_dir=workspace_dir,
            agents_dir=agents_dir,
            issue_num=issue_num,
            log_subdir=log_subdir,
            env=env,
        )

    def transcribe_audio(self, audio_file_path: str, project_name: str | None = None) -> str | None:
        attempts = self._resolve_transcription_attempts(project_name=project_name)
        return run_transcription_attempts_impl(
            attempts=attempts,
            audio_file_path=audio_file_path,
            transcribers_map={
                "whisper": self._transcribe_with_whisper_api,
                "gemini": self._transcribe_with_gemini_cli,
                "copilot": self._transcribe_with_copilot_cli,
                "ollama": self._transcribe_with_ollama_cli,
            },
            rate_limited_error_type=RateLimitedError,
            record_rate_limit_with_context=lambda tool, exc, context: self._record_rate_limit_with_context(
                tool,
                exc,
                context=context,
            ),
            provider_to_tool={
                "gemini": AIProvider.GEMINI,
                "copilot": AIProvider.COPILOT,
                "ollama": AIProvider.OLLAMA,
            },
            logger=logger,
        )

    def _transcribe_with_whisper_api(self, audio_file_path: str) -> str | None:
        result = transcribe_with_local_whisper_impl(
            audio_file_path=audio_file_path,
            current_model_instance=self._whisper_model_instance,
            current_model_name=self._whisper_model_name,
            configured_model=self.whisper_model,
            whisper_language=self.whisper_language,
            whisper_languages=self.whisper_languages,
            normalize_local_whisper_model_name=self._normalize_local_whisper_model_name,
            tool_unavailable_error=ToolUnavailableError,
            logger=logger,
        )
        self._whisper_model_instance = result["model_instance"]
        self._whisper_model_name = str(result["model_name"])
        return str(result["text"])

    @staticmethod
    def _normalize_local_whisper_model_name(configured_model: str) -> str:
        return normalize_local_whisper_model_name_impl(configured_model)

    @staticmethod
    def _is_transcription_refusal(text: str) -> bool:
        return is_transcription_refusal_impl(text)

    @staticmethod
    def _is_non_transcription_artifact(text: str, audio_file_path: str) -> bool:
        return is_non_transcription_artifact_impl(text, audio_file_path)

    def _transcribe_with_gemini_cli(self, audio_file_path: str) -> str | None:
        return transcribe_with_gemini_cli_impl(
            check_tool_available=self.check_tool_available,
            gemini_provider=AIProvider.GEMINI,
            gemini_cli_path=self.gemini_cli_path,
            strip_cli_tool_output=self._strip_cli_tool_output,
            is_non_transcription_artifact=self._is_non_transcription_artifact,
            tool_unavailable_error=ToolUnavailableError,
            rate_limited_error=RateLimitedError,
            logger=logger,
            audio_file_path=audio_file_path,
            timeout=self.transcription_timeout,
        )

    def _transcribe_with_copilot_cli(self, audio_file_path: str) -> str | None:
        return transcribe_with_copilot_cli_impl(
            check_tool_available=self.check_tool_available,
            copilot_provider=AIProvider.COPILOT,
            copilot_cli_path=self.copilot_cli_path,
            strip_cli_tool_output=self._strip_cli_tool_output,
            is_non_transcription_artifact=self._is_non_transcription_artifact,
            tool_unavailable_error=ToolUnavailableError,
            logger=logger,
            audio_file_path=audio_file_path,
            timeout=self.transcription_timeout,
        )

    def _transcribe_with_ollama_cli(self, audio_file_path: str) -> str | None:
        return run_ollama_transcription_cli_impl(
            check_tool_available=self.check_tool_available,
            ollama_provider=AIProvider.OLLAMA,
            ollama_cli_path=self.ollama_cli_path,
            ollama_model=self.ollama_model,
            strip_cli_tool_output=self._strip_cli_tool_output,
            is_non_transcription_artifact=self._is_non_transcription_artifact,
            tool_unavailable_error=ToolUnavailableError,
            logger=logger,
            audio_file_path=audio_file_path,
            timeout=self.transcription_timeout,
        )

    def run_text_to_speech_analysis(
        self, text: str, task: str = "classify", **kwargs
    ) -> dict[str, Any]:
        project_name = kwargs.get("project_name")
        tool_order = self._resolve_analysis_tool_order(task, project_name=project_name, text=text)
        mapped_agent = self._resolve_analysis_mapped_agent(
            task=task,
            text=text,
            project_name=project_name,
        )
        model_overrides = {
            "gemini": self._resolve_model_for_tool(
                tool=AIProvider.GEMINI,
                agent_name=mapped_agent or None,
                project_name=project_name,
            ),
            "copilot": self._resolve_model_for_tool(
                tool=AIProvider.COPILOT,
                agent_name=mapped_agent or None,
                project_name=project_name,
            ),
            "codex": self._resolve_model_for_tool(
                tool=AIProvider.CODEX,
                agent_name=mapped_agent or None,
                project_name=project_name,
            ),
            "claude": self._resolve_model_for_tool(
                tool=AIProvider.CLAUDE,
                agent_name=mapped_agent or None,
                project_name=project_name,
            ),
            "ollama": self._resolve_model_for_tool(
                tool=AIProvider.OLLAMA,
                agent_name=mapped_agent or None,
                project_name=project_name,
            ),
        }
        if tool_order:
            preferred = tool_order[0]
            preferred_name = str(getattr(preferred, "value", preferred))
            fallback_names = [str(getattr(tool, "value", tool)) for tool in tool_order[1:]]
            logger.info(
                "🧭 Analysis provider order: task=%s project=%s mapped_agent=%s model=%s order=%s",
                task,
                project_name or "default",
                mapped_agent or "unknown",
                model_overrides.get(preferred_name, "") or "default",
                " -> ".join([preferred_name] + fallback_names),
            )
            if not self.check_tool_available(preferred):
                fallback_display = ", ".join(fallback_names) if fallback_names else "none"
                logger.warning(
                    "⚠️ Preferred provider '%s' unavailable for task '%s' (project=%s); "
                    "mapped_agent=%s; falling back to: %s",
                    preferred_name,
                    task,
                    project_name or "default",
                    mapped_agent or "unknown",
                    fallback_display,
                )
        analysis_kwargs = dict(kwargs)
        analysis_kwargs.setdefault("prompt_max_chars", self.ai_prompt_max_chars)
        analysis_kwargs.setdefault("summary_max_chars", self.ai_context_summary_max_chars)
        analysis_kwargs["_gemini_model_override"] = model_overrides["gemini"]
        analysis_kwargs["_copilot_model_override"] = model_overrides["copilot"]
        analysis_kwargs["_codex_model_override"] = model_overrides["codex"]
        analysis_kwargs["_claude_model_override"] = model_overrides["claude"]
        analysis_kwargs["_ollama_model_override"] = model_overrides["ollama"]
        provider_env, env_error = self._resolve_analysis_provider_env(kwargs.get("requester_context"))
        if provider_env:
            analysis_kwargs["_provider_env"] = provider_env
        elif env_error and task == "chat":
            logger.info("Requester-scoped analysis auth unavailable for task=%s: %s", task, env_error)

        analysis_cwd = self._resolve_analysis_cwd(project_name)
        if analysis_cwd:
            analysis_kwargs["_analysis_cwd"] = analysis_cwd
        logger.info(
            "🧾 Analysis prompt metrics: input_chars=%s history_chars=%s prefix_fp=%s task=%s",
            len(text),
            len(str(kwargs.get("history", "") or "")),
            prompt_prefix_fingerprint(text),
            task,
        )
        return run_analysis_attempts_impl(
            tool_order=tool_order,
            text=text,
            task=task,
            kwargs=analysis_kwargs,
            invoke_provider=lambda tool, text_arg, task_arg, kw: self._run_analysis_with_provider(
                tool, text_arg, task_arg, **kw
            ),
            rate_limited_error_type=RateLimitedError,
            record_rate_limit_with_context=lambda tool, exc, retry_count, context: self._record_rate_limit_with_context(
                tool,
                exc,
                retry_count=retry_count,
                context=context,
            ),
            get_default_analysis_result=self._get_default_analysis_result,
            logger=logger,
        )

    def _resolve_analysis_provider_env(
        self, requester_context: Mapping[str, Any] | None
    ) -> tuple[dict[str, str] | None, str | None]:
        if not isinstance(requester_context, Mapping):
            return None, None

        nexus_id = str(requester_context.get("nexus_id") or "").strip()
        if not nexus_id:
            return None, None

        try:
            from nexus.core.auth.access_domain import build_execution_env

            env, err = build_execution_env(nexus_id)
        except Exception as exc:
            logger.warning(
                "Could not resolve requester-scoped analysis auth for nexus_id=%s: %s",
                nexus_id,
                exc,
            )
            return None, str(exc)

        if err:
            return None, err
        return env or None, None

    def _resolve_analysis_cwd(self, project_name: str | None) -> str | None:
        project_key = str(project_name or "").strip()
        if not project_key:
            return None

        try:
            from nexus.core.config import BASE_DIR, PROJECT_CONFIG
        except Exception as exc:
            logger.warning("Could not resolve analysis cwd for project=%s: %s", project_key, exc)
            return None

        project_cfg = PROJECT_CONFIG.get(project_key)
        if not isinstance(project_cfg, Mapping):
            return None

        workspace = str(project_cfg.get("workspace") or "").strip()
        if not workspace:
            return None

        candidate = workspace if os.path.isabs(workspace) else os.path.join(BASE_DIR, workspace)
        candidate = os.path.abspath(candidate)
        return candidate if os.path.isdir(candidate) else None

    def _resolve_analysis_mapped_agent(
        self,
        *,
        task: str,
        text: str,
        project_name: str | None,
    ) -> str:
        def _coerce_chat_agent_type(chat_config: Any) -> str:
            if isinstance(chat_config, str):
                return str(chat_config).strip()
            if isinstance(chat_config, list):
                for item in chat_config:
                    if isinstance(item, str) and item.strip():
                        return item.strip()
                    if isinstance(item, Mapping):
                        explicit = str(item.get("agent_type") or "").strip()
                        if explicit:
                            return explicit
                        for key in item:
                            normalized = str(key).strip()
                            if normalized:
                                return normalized
            if isinstance(chat_config, Mapping):
                explicit = str(chat_config.get("agent_type") or "").strip()
                if explicit:
                    return explicit
                default_item = chat_config.get("default")
                if isinstance(default_item, str) and default_item.strip():
                    return default_item.strip()
                if isinstance(default_item, Mapping):
                    nested = str(default_item.get("agent_type") or "").strip()
                    if nested:
                        return nested
                for key in chat_config:
                    normalized = str(key).strip()
                    if normalized and normalized not in {"default", "agent_type"}:
                        return normalized
            return ""

        system_operations = self._resolved_system_operations(project_name)
        if not isinstance(system_operations, Mapping):
            system_operations = {}

        task_key = str(task or "").strip().lower()
        if task_key == "chat":
            mapped_agent = _coerce_chat_agent_type(system_operations.get(task_key))
            if not mapped_agent:
                mapped_agent = self._default_chat_agent_type(project_name)
            if not mapped_agent:
                mapped_agent = str(system_operations.get("default") or "").strip()
        else:
            mapped_agent = str(
                system_operations.get(task_key) or system_operations.get("default") or ""
            ).strip()

        mapped_agent = self._resolve_issue_override_agent(
            task_key=task_key,
            mapped_agent=mapped_agent,
            text=text,
            system_operations=system_operations,
        )
        return mapped_agent

    def _run_gemini_cli_analysis(self, text: str, task: str, **kwargs) -> dict[str, Any]:
        gemini_model = str(kwargs.get("_gemini_model_override") or "").strip() or self.gemini_model
        provider_env = kwargs.get("_provider_env")
        analysis_cwd = kwargs.get("_analysis_cwd")
        prompt_kwargs = {k: v for k, v in kwargs.items() if not str(k).startswith("_")}
        return run_gemini_analysis_cli_impl(
            check_tool_available=self.check_tool_available,
            gemini_provider=AIProvider.GEMINI,
            gemini_cli_path=self.gemini_cli_path,
            gemini_model=gemini_model,
            build_analysis_prompt=self._build_analysis_prompt,
            parse_analysis_result=self._parse_analysis_result,
            tool_unavailable_error=ToolUnavailableError,
            rate_limited_error=RateLimitedError,
            text=text,
            task=task,
            timeout=self.analysis_timeout,
            kwargs=prompt_kwargs,
            env=provider_env if isinstance(provider_env, dict) else None,
            cwd=analysis_cwd if isinstance(analysis_cwd, str) and analysis_cwd.strip() else None,
        )

    def _run_copilot_analysis(self, text: str, task: str, **kwargs) -> dict[str, Any]:
        timeout = (
            self.refine_description_timeout
            if task == "refine_description"
            else self.analysis_timeout
        )
        copilot_model = (
            str(kwargs.get("_copilot_model_override") or "").strip() or self.copilot_model
        )
        provider_env = kwargs.get("_provider_env")
        analysis_cwd = kwargs.get("_analysis_cwd")
        prompt_kwargs = {k: v for k, v in kwargs.items() if not str(k).startswith("_")}
        return run_copilot_analysis_cli_impl(
            check_tool_available=self.check_tool_available,
            copilot_provider=AIProvider.COPILOT,
            copilot_cli_path=self.copilot_cli_path,
            copilot_model=copilot_model,
            copilot_supports_model=self.copilot_supports_model,
            build_analysis_prompt=self._build_analysis_prompt,
            parse_analysis_result=self._parse_analysis_result,
            tool_unavailable_error=ToolUnavailableError,
            text=text,
            task=task,
            timeout=timeout,
            kwargs=prompt_kwargs,
            env=provider_env if isinstance(provider_env, dict) else None,
            cwd=analysis_cwd if isinstance(analysis_cwd, str) and analysis_cwd.strip() else None,
        )

    def _run_codex_analysis(self, text: str, task: str, **kwargs) -> dict[str, Any]:
        timeout = (
            self.refine_description_timeout
            if task == "refine_description"
            else self.analysis_timeout
        )
        codex_model = str(kwargs.get("_codex_model_override") or "").strip() or self.codex_model
        provider_env = kwargs.get("_provider_env")
        analysis_cwd = kwargs.get("_analysis_cwd")
        prompt_kwargs = {k: v for k, v in kwargs.items() if not str(k).startswith("_")}
        return run_codex_analysis_cli_impl(
            check_tool_available=self.check_tool_available,
            codex_provider=AIProvider.CODEX,
            codex_cli_path=self.codex_cli_path,
            codex_model=codex_model,
            build_analysis_prompt=self._build_analysis_prompt,
            parse_analysis_result=self._parse_analysis_result,
            tool_unavailable_error=ToolUnavailableError,
            rate_limited_error=RateLimitedError,
            text=text,
            task=task,
            timeout=timeout,
            kwargs=prompt_kwargs,
            env=provider_env if isinstance(provider_env, dict) else None,
            cwd=analysis_cwd if isinstance(analysis_cwd, str) and analysis_cwd.strip() else None,
        )

    def _run_claude_analysis(self, text: str, task: str, **kwargs) -> dict[str, Any]:
        timeout = (
            self.refine_description_timeout
            if task == "refine_description"
            else self.analysis_timeout
        )
        claude_model = str(kwargs.get("_claude_model_override") or "").strip() or self.claude_model
        provider_env = kwargs.get("_provider_env")
        analysis_cwd = kwargs.get("_analysis_cwd")
        prompt_kwargs = {k: v for k, v in kwargs.items() if not str(k).startswith("_")}
        return run_claude_analysis_cli_impl(
            check_tool_available=self.check_tool_available,
            claude_provider=AIProvider.CLAUDE,
            claude_cli_path=self.claude_cli_path,
            claude_model=claude_model,
            build_analysis_prompt=self._build_analysis_prompt,
            parse_analysis_result=self._parse_analysis_result,
            tool_unavailable_error=ToolUnavailableError,
            rate_limited_error=RateLimitedError,
            text=text,
            task=task,
            timeout=timeout,
            kwargs=prompt_kwargs,
            env=provider_env if isinstance(provider_env, dict) else None,
            cwd=analysis_cwd if isinstance(analysis_cwd, str) and analysis_cwd.strip() else None,
        )

    def _run_ollama_analysis(self, text: str, task: str, **kwargs) -> dict[str, Any]:
        timeout = (
            self.refine_description_timeout
            if task == "refine_description"
            else self.analysis_timeout
        )
        ollama_model = (
            str(kwargs.get("_ollama_model_override") or "").strip()
            or str(self.config.get("ollama_model", "")).strip()
        )
        prompt_kwargs = {k: v for k, v in kwargs.items() if not str(k).startswith("_")}
        return run_ollama_analysis_cli_impl(
            check_tool_available=self.check_tool_available,
            ollama_provider=AIProvider.OLLAMA,
            ollama_cli_path=self.ollama_cli_path,
            ollama_model=ollama_model,
            build_analysis_prompt=self._build_analysis_prompt,
            parse_analysis_result=self._parse_analysis_result,
            tool_unavailable_error=ToolUnavailableError,
            rate_limited_error=RateLimitedError,
            text=text,
            task=task,
            timeout=timeout,
            kwargs=prompt_kwargs,
        )

    def _build_analysis_prompt(self, text: str, task: str, **kwargs) -> str:
        return build_analysis_prompt_impl(text, task, **kwargs)

    @staticmethod
    def _strip_cli_tool_output(text: str) -> str:
        return strip_cli_tool_output_impl(text)

    def _parse_analysis_result(self, output: str, task: str) -> dict[str, Any]:
        return parse_analysis_result_impl(output, task, logger=logger)

    def _get_default_analysis_result(self, task: str, **kwargs) -> dict[str, Any]:
        if task == "classify":
            return {
                "project": kwargs.get("projects", ["nexus"])[0],
                "type": kwargs.get("types", ["feature"])[0],
                "task_name": "generic-task",
            }
        if task == "route":
            return {"agent": "ProjectLead", "type": "routing", "confidence": 0}
        if task == "generate_name":
            text = kwargs.get("text", "")
            words = text.split()[:3]
            if not words:
                return {"text": "generic-task"}
            return {"text": "-".join(words).lower()}
        if task == "refine_description":
            original_text = kwargs.get("original_text")
            if isinstance(original_text, str):
                return {"text": original_text}
            return {"text": kwargs.get("text", "")}
        if task == "detect_intent":
            return {"intent": "task"}
        if task == "detect_feature_ideation":
            return {
                "feature_ideation": False,
                "confidence": 0.0,
                "reason": "default-no-match",
            }
        if task == "chat":
            attempted = kwargs.get("attempted_providers") or []
            attempted_names = [
                str(item).strip() for item in attempted if isinstance(item, str) and str(item).strip()
            ]
            attempted_label = ", ".join(attempted_names) if attempted_names else "configured providers"
            last_error = str(kwargs.get("last_error") or "").strip().splitlines()[0].strip()
            text = (
                "I couldn't generate a reply right now because these chat providers failed: "
                f"{attempted_label}. Check provider authentication and try again."
            )
            if last_error:
                text += f"\n\nLast error: {last_error}"
            return {"text": text}
        return {}


def register_plugins(registry) -> None:
    """Register built-in AI runtime orchestrator plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.AI_PROVIDER,
        name="ai-runtime-orchestrator",
        version="0.1.0",
        factory=lambda config: AIOrchestrator(config),
        description="Copilot/Gemini/Codex/Ollama orchestration with fallback and cooldown handling",
    )
