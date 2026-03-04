"""Validation helpers for Telegram bot project configuration."""

from __future__ import annotations

from enum import Enum
from typing import Any

from nexus.plugins.builtin.ai_runtime.provider_registry import parse_tool_preference


class _AIProviderEnum(Enum):
    COPILOT = "copilot"
    GEMINI = "gemini"
    CODEX = "codex"
    CLAUDE = "claude"


def _known_provider_names() -> set[str]:
    """Return normalized provider identifiers accepted in config."""
    return {provider.value for provider in _AIProviderEnum}


def _validate_tool_preferences_block(
    payload: Any,
    *,
    label: str,
    known_profiles: set[str] | None = None,
) -> None:
    if payload is None:
        return
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a mapping")

    for agent_name, value in payload.items():
        spec = parse_tool_preference(value, _AIProviderEnum)
        if not getattr(spec, "valid", False):
            raise ValueError(
                f"{label}.{agent_name} is invalid: {getattr(spec, 'reason', 'parse error')} "
                f"(value={value!r})"
            )

        profile_name = str(getattr(spec, "profile", "") or "").strip()
        if known_profiles is not None and profile_name not in known_profiles:
            raise ValueError(f"{label}.{agent_name} references unknown profile '{profile_name}'")


def _validate_model_profiles_block(payload: Any, *, label: str) -> set[str]:
    if payload is None:
        return set()
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a mapping")

    known_profiles: set[str] = set()
    known_provider_names = _known_provider_names()

    for profile_name, provider_map in payload.items():
        normalized_profile = str(profile_name or "").strip()
        if not normalized_profile:
            raise ValueError(f"{label} contains an empty profile name")
        if not isinstance(provider_map, dict):
            raise ValueError(f"{label}.{normalized_profile} must be a mapping")

        known_profiles.add(normalized_profile)
        for provider_name, model_name in provider_map.items():
            normalized_provider = str(provider_name or "").strip().lower()
            if normalized_provider not in known_provider_names:
                raise ValueError(
                    f"{label}.{normalized_profile} has unsupported provider " f"'{provider_name}'"
                )
            if not str(model_name or "").strip():
                raise ValueError(
                    f"{label}.{normalized_profile}.{normalized_provider} must be a non-empty model"
                )

    return known_profiles


def _validate_profile_provider_priority_block(
    payload: Any,
    *,
    label: str,
    known_profiles: set[str],
) -> None:
    if payload is None:
        return
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a mapping")

    known_provider_names = _known_provider_names()
    for profile_name, providers in payload.items():
        normalized_profile = str(profile_name or "").strip()
        if not normalized_profile:
            raise ValueError(f"{label} contains an empty profile name")
        if normalized_profile not in known_profiles:
            raise ValueError(f"{label}.{normalized_profile} references unknown profile")
        if not isinstance(providers, list) or not providers:
            raise ValueError(f"{label}.{normalized_profile} must be a non-empty list")

        seen: set[str] = set()
        for provider_name in providers:
            normalized_provider = str(provider_name or "").strip().lower()
            if normalized_provider not in known_provider_names:
                raise ValueError(
                    f"{label}.{normalized_profile} has unsupported provider '{provider_name}'"
                )
            if normalized_provider in seen:
                raise ValueError(
                    f"{label}.{normalized_profile} contains duplicate provider "
                    f"'{normalized_provider}'"
                )
            seen.add(normalized_provider)


def validate_project_config(config: dict[str, Any]) -> None:
    """Validate project configuration dict."""
    if not config:
        return

    global_profiles = _validate_model_profiles_block(
        config.get("model_profiles"),
        label="PROJECT_CONFIG['model_profiles']",
    )
    _validate_profile_provider_priority_block(
        config.get("profile_provider_priority"),
        label="PROJECT_CONFIG['profile_provider_priority']",
        known_profiles=global_profiles,
    )

    global_keys = {
        "nexus_dir",
        "workflow_definition_path",
        "projects",
        "task_types",
        "model_profiles",
        "profile_provider_priority",
        "ai_tool_preferences",
        "system_operations",
        "merge_queue",
        "workflow_chains",
        "final_agents",
        "shared_agents_dir",
    }

    for project, proj_config in config.items():
        if project in global_keys:
            continue
        if not isinstance(proj_config, dict):
            raise ValueError(f"PROJECT_CONFIG['{project}'] must be a dict")
        if "workspace" not in proj_config:
            raise ValueError(f"PROJECT_CONFIG['{project}'] missing 'workspace' key")

        repos_list = proj_config.get("git_repos")
        if repos_list is not None:
            if not isinstance(repos_list, list):
                raise ValueError(f"PROJECT_CONFIG['{project}']['git_repos'] must be a list")
            for repo_name in repos_list:
                if not isinstance(repo_name, str) or not repo_name.strip():
                    raise ValueError(
                        f"PROJECT_CONFIG['{project}']['git_repos'] contains invalid repo entry"
                    )

        git_platform = str(proj_config.get("git_platform", "github")).lower().strip()
        if git_platform not in {"github", "gitlab"}:
            raise ValueError(
                f"PROJECT_CONFIG['{project}']['git_platform'] must be 'github' or 'gitlab'"
            )

        access_control = proj_config.get("access_control")
        if access_control is not None:
            if not isinstance(access_control, dict):
                raise ValueError(f"PROJECT_CONFIG['{project}']['access_control'] must be a mapping")
            github_teams = access_control.get("github_teams")
            if github_teams is not None:
                if not isinstance(github_teams, list):
                    raise ValueError(
                        f"PROJECT_CONFIG['{project}']['access_control']['github_teams'] must be a list"
                    )
                for team_slug in github_teams:
                    candidate = str(team_slug or "").strip()
                    if not candidate or "/" not in candidate:
                        raise ValueError(
                            f"PROJECT_CONFIG['{project}']['access_control']['github_teams'] contains invalid team '{team_slug}' (expected org/team-slug)"
                        )
            gitlab_groups = access_control.get("gitlab_groups")
            if gitlab_groups is not None:
                if not isinstance(gitlab_groups, list):
                    raise ValueError(
                        f"PROJECT_CONFIG['{project}']['access_control']['gitlab_groups'] must be a list"
                    )
                for group_path in gitlab_groups:
                    candidate = str(group_path or "").strip().strip("/")
                    if not candidate:
                        raise ValueError(
                            f"PROJECT_CONFIG['{project}']['access_control']['gitlab_groups'] contains invalid group '{group_path}'"
                        )
                    parts = [part for part in candidate.split("/") if str(part).strip()]
                    if not parts:
                        raise ValueError(
                            f"PROJECT_CONFIG['{project}']['access_control']['gitlab_groups'] contains invalid group '{group_path}'"
                        )
            github_users = access_control.get("github_users")
            if github_users is not None:
                if not isinstance(github_users, list):
                    raise ValueError(
                        f"PROJECT_CONFIG['{project}']['access_control']['github_users'] must be a list"
                    )
                for username in github_users:
                    candidate = str(username or "").strip().lstrip("@")
                    if not candidate or "/" in candidate:
                        raise ValueError(
                            f"PROJECT_CONFIG['{project}']['access_control']['github_users'] contains invalid username '{username}'"
                        )
            gitlab_users = access_control.get("gitlab_users")
            if gitlab_users is not None:
                if not isinstance(gitlab_users, list):
                    raise ValueError(
                        f"PROJECT_CONFIG['{project}']['access_control']['gitlab_users'] must be a list"
                    )
                for username in gitlab_users:
                    candidate = str(username or "").strip().lstrip("@")
                    if not candidate or "/" in candidate:
                        raise ValueError(
                            f"PROJECT_CONFIG['{project}']['access_control']['gitlab_users'] contains invalid username '{username}'"
                        )

        project_profiles = set(global_profiles)
        project_profiles.update(
            _validate_model_profiles_block(
                proj_config.get("model_profiles"),
                label=f"PROJECT_CONFIG['{project}']['model_profiles']",
            )
        )
        _validate_profile_provider_priority_block(
            proj_config.get("profile_provider_priority"),
            label=f"PROJECT_CONFIG['{project}']['profile_provider_priority']",
            known_profiles=project_profiles,
        )
        _validate_tool_preferences_block(
            proj_config.get("ai_tool_preferences"),
            label=f"PROJECT_CONFIG['{project}']['ai_tool_preferences']",
            known_profiles=project_profiles,
        )

    _validate_tool_preferences_block(
        config.get("ai_tool_preferences"),
        label="PROJECT_CONFIG['ai_tool_preferences']",
        known_profiles=global_profiles,
    )

    registry = config.get("projects")
    if registry is None:
        return
    if not isinstance(registry, dict) or not registry:
        raise ValueError("PROJECT_CONFIG['projects'] must be a non-empty mapping")

    for short_key, payload in registry.items():
        normalized_short = str(short_key).strip().lower()
        if not normalized_short:
            raise ValueError("PROJECT_CONFIG['projects'] contains empty short key")
        if not isinstance(payload, dict):
            raise ValueError(f"PROJECT_CONFIG['projects']['{normalized_short}'] must be a mapping")

        code = str(payload.get("code", "")).strip().lower()
        if not code:
            raise ValueError(
                f"PROJECT_CONFIG['projects']['{normalized_short}'] missing non-empty 'code'"
            )
        if code not in config or not isinstance(config.get(code), dict):
            raise ValueError(
                f"PROJECT_CONFIG['projects']['{normalized_short}']['code'] references unknown project '{code}'"
            )

        aliases = payload.get("aliases", [])
        if aliases is not None and not isinstance(aliases, list):
            raise ValueError(
                f"PROJECT_CONFIG['projects']['{normalized_short}']['aliases'] must be a list"
            )
        if isinstance(aliases, list):
            for alias in aliases:
                if not isinstance(alias, str) or not alias.strip():
                    raise ValueError(
                        f"PROJECT_CONFIG['projects']['{normalized_short}']['aliases'] contains invalid alias"
                    )
