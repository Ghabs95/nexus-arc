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
    OLLAMA = "ollama"


def _known_provider_names() -> set[str]:
    """Return normalized provider identifiers accepted in config."""
    return {provider.value for provider in _AIProviderEnum}


def _validate_copilot_permissions_block(payload: Any, *, label: str) -> None:
    if payload is None:
        return
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a mapping")

    supported_keys = {"allow_urls", "allow_all_urls", "allow_all_paths"}
    unknown_keys = [key for key in payload if key not in supported_keys]
    if unknown_keys:
        raise ValueError(f"{label} contains unsupported keys: {', '.join(sorted(unknown_keys))}")

    allow_urls = payload.get("allow_urls")
    if allow_urls is not None:
        if not isinstance(allow_urls, list):
            raise ValueError(f"{label}.allow_urls must be a list")
        for idx, value in enumerate(allow_urls):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label}.allow_urls[{idx}] must be a non-empty string")

    for bool_key in ("allow_all_urls", "allow_all_paths"):
        raw = payload.get(bool_key)
        if raw is None:
            continue
        if not isinstance(raw, bool):
            raise ValueError(f"{label}.{bool_key} must be a boolean")


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

        if isinstance(value, dict) and "copilot_permissions" in value:
            _validate_copilot_permissions_block(
                value.get("copilot_permissions"),
                label=f"{label}.{agent_name}.copilot_permissions",
            )


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


def _configured_project_repos(proj_config: dict[str, Any]) -> set[str]:
    repos: set[str] = set()
    primary = proj_config.get("git_repo")
    if isinstance(primary, str) and primary.strip():
        repos.add(primary.strip())
    repo_list = proj_config.get("git_repos")
    if isinstance(repo_list, list):
        for repo_name in repo_list:
            if isinstance(repo_name, str) and repo_name.strip():
                repos.add(repo_name.strip())
    return repos


def _validate_execution_mode_cli_config_block(payload: Any, *, label: str) -> None:
    if payload is None:
        return
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a mapping")

    supported_provider_names = _known_provider_names()
    supported_mode_keys = {"planning"}
    supported_setting_keys = {"profile", "args", "env"}

    for provider_name, provider_modes in payload.items():
        normalized_provider = str(provider_name or "").strip().lower()
        if normalized_provider not in supported_provider_names:
            raise ValueError(f"{label}.{provider_name} has unsupported provider")
        if not isinstance(provider_modes, dict):
            raise ValueError(f"{label}.{normalized_provider} must be a mapping")

        for mode_name, settings in provider_modes.items():
            normalized_mode = str(mode_name or "").strip().lower()
            if normalized_mode not in supported_mode_keys:
                raise ValueError(
                    f"{label}.{normalized_provider}.{mode_name} has unsupported execution mode"
                )
            if not isinstance(settings, dict):
                raise ValueError(f"{label}.{normalized_provider}.{normalized_mode} must be a mapping")

            unknown_keys = [key for key in settings if key not in supported_setting_keys]
            if unknown_keys:
                raise ValueError(
                    f"{label}.{normalized_provider}.{normalized_mode} contains unsupported keys: "
                    + ", ".join(sorted(str(key) for key in unknown_keys))
                )

            profile_name = settings.get("profile")
            if profile_name is not None and (
                not isinstance(profile_name, str) or not profile_name.strip()
            ):
                raise ValueError(
                    f"{label}.{normalized_provider}.{normalized_mode}.profile must be a non-empty string"
                )

            args = settings.get("args")
            if args is not None:
                if not isinstance(args, list):
                    raise ValueError(
                        f"{label}.{normalized_provider}.{normalized_mode}.args must be a list"
                    )
                for idx, value in enumerate(args):
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError(
                            f"{label}.{normalized_provider}.{normalized_mode}.args[{idx}] must be a non-empty string"
                        )

            env = settings.get("env")
            if env is not None:
                if not isinstance(env, dict):
                    raise ValueError(
                        f"{label}.{normalized_provider}.{normalized_mode}.env must be a mapping"
                    )
                for key, value in env.items():
                    if not isinstance(key, str) or not key.strip():
                        raise ValueError(
                            f"{label}.{normalized_provider}.{normalized_mode}.env contains an empty key"
                        )
                    if not isinstance(value, str):
                        raise ValueError(
                            f"{label}.{normalized_provider}.{normalized_mode}.env['{key}'] must be a string"
                        )


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
    _validate_copilot_permissions_block(
        config.get("copilot_permissions"),
        label="PROJECT_CONFIG['copilot_permissions']",
    )
    _validate_execution_mode_cli_config_block(
        config.get("execution_mode_cli_config"),
        label="PROJECT_CONFIG['execution_mode_cli_config']",
    )

    global_keys = {
        "nexus_dir",
        "workflow_definition_path",
        "projects",
        "task_types",
        "model_profiles",
        "profile_provider_priority",
        "ai_tool_preferences",
        "copilot_permissions",
        "execution_mode_cli_config",
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

        git_branches = proj_config.get("git_branches")
        if git_branches is not None:
            if not isinstance(git_branches, dict):
                raise ValueError(f"PROJECT_CONFIG['{project}']['git_branches'] must be a mapping")

            default_branch = git_branches.get("default")
            if default_branch is not None and (
                not isinstance(default_branch, str) or not default_branch.strip()
            ):
                raise ValueError(
                    f"PROJECT_CONFIG['{project}']['git_branches']['default'] must be a non-empty string"
                )

            per_repo = git_branches.get("repos")
            if per_repo is not None:
                if not isinstance(per_repo, dict):
                    raise ValueError(
                        f"PROJECT_CONFIG['{project}']['git_branches']['repos'] must be a mapping"
                    )
                configured_repos = _configured_project_repos(proj_config)
                for repo_key, branch_name in per_repo.items():
                    normalized_repo = str(repo_key or "").strip()
                    if not normalized_repo:
                        raise ValueError(
                            f"PROJECT_CONFIG['{project}']['git_branches']['repos'] contains empty repo key"
                        )
                    if normalized_repo not in configured_repos:
                        raise ValueError(
                            f"PROJECT_CONFIG['{project}']['git_branches']['repos']['{normalized_repo}'] references unknown configured repo"
                        )
                    if not isinstance(branch_name, str) or not branch_name.strip():
                        raise ValueError(
                            f"PROJECT_CONFIG['{project}']['git_branches']['repos']['{normalized_repo}'] must be a non-empty string"
                        )

        git_sync = proj_config.get("git_sync")
        if git_sync is not None:
            if not isinstance(git_sync, dict):
                raise ValueError(f"PROJECT_CONFIG['{project}']['git_sync'] must be a mapping")

            on_workflow_start = git_sync.get("on_workflow_start")
            if on_workflow_start is not None and not isinstance(on_workflow_start, bool):
                raise ValueError(
                    f"PROJECT_CONFIG['{project}']['git_sync']['on_workflow_start'] must be a boolean"
                )
            bootstrap_missing_workspace = git_sync.get("bootstrap_missing_workspace")
            if bootstrap_missing_workspace is not None and not isinstance(
                bootstrap_missing_workspace, bool
            ):
                raise ValueError(
                    f"PROJECT_CONFIG['{project}']['git_sync']['bootstrap_missing_workspace'] must be a boolean"
                )
            bootstrap_missing_repos = git_sync.get("bootstrap_missing_repos")
            if bootstrap_missing_repos is not None and not isinstance(
                bootstrap_missing_repos, bool
            ):
                raise ValueError(
                    f"PROJECT_CONFIG['{project}']['git_sync']['bootstrap_missing_repos'] must be a boolean"
                )

            for key in (
                "network_auth_retries",
                "retry_backoff_seconds",
                "decision_timeout_seconds",
            ):
                value = git_sync.get(key)
                if value is None:
                    continue
                if not isinstance(value, int) or value <= 0:
                    raise ValueError(
                        f"PROJECT_CONFIG['{project}']['git_sync']['{key}'] must be a positive integer"
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
        _validate_copilot_permissions_block(
            proj_config.get("copilot_permissions"),
            label=f"PROJECT_CONFIG['{project}']['copilot_permissions']",
        )
        _validate_execution_mode_cli_config_block(
            proj_config.get("execution_mode_cli_config"),
            label=f"PROJECT_CONFIG['{project}']['execution_mode_cli_config']",
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
