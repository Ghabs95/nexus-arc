"""Centralized configuration for Nexus bot and processor."""

import logging
import os
import sys
from dataclasses import dataclass
from typing import Any

from nexus.core.config.backend_enums import (
    RATE_LIMIT_BACKEND_ALIASES as _RATE_LIMIT_BACKEND_ALIASES,
    STORAGE_BACKEND_ALIASES as _STORAGE_BACKEND_ALIASES,
    VALID_RATE_LIMIT_BACKENDS as _VALID_RATE_LIMIT_BACKENDS,
    VALID_STORAGE_BACKENDS as _VALID_STORAGE_BACKENDS,
    normalize_backend_enum as _normalize_backend_enum,
)
from nexus.core.config.chat import (
    get_chat_agent_types as _svc_get_chat_agent_types,
    get_chat_agents as _svc_get_chat_agents,
    get_system_operations as _svc_get_system_operations,
)
from nexus.core.config.env import (
    env_bool as _env_bool,
    env_float as _env_float,
    get_int_env as _get_int_env,
    parse_csv_list as _parse_csv_list,
    parse_int_list as _parse_int_list,
)
from nexus.core.config.loaders import (
    load_and_validate_project_config as _svc_load_and_validate_project_config,
)
from nexus.core.config.paths import load_path_config_from_env
from nexus.core.config.projects import (
    get_default_project as _svc_get_default_project,
    get_track_short_projects as _svc_get_track_short_projects,
    get_workflow_profile as _svc_get_workflow_profile,
)
from nexus.core.config.repos import (
    get_default_repo as _svc_get_default_repo,
    get_gitlab_base_url as _svc_get_gitlab_base_url,
    get_project_platform as _svc_get_project_platform,
    get_repo as _svc_get_repo,
    get_repos as _svc_get_repos,
)
from nexus.core.config.runtime import (
    get_inbox_dir as _svc_get_inbox_dir,
    get_nexus_dir as _svc_get_nexus_dir,
    get_nexus_dir_name as _svc_get_nexus_dir_name,
    get_tasks_active_dir as _svc_get_tasks_active_dir,
    get_tasks_closed_dir as _svc_get_tasks_closed_dir,
    get_tasks_logs_dir as _svc_get_tasks_logs_dir,
)
from nexus.core.config.validators import validate_project_config as _svc_validate_project_config
from nexus.core.project.registry import (
    get_project_aliases as _svc_get_project_aliases,
    get_project_registry as _svc_get_project_registry,
    normalize_project_key as _svc_normalize_project_key,
)

# --- TELEGRAM CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

TELEGRAM_ALLOWED_USER_IDS = _parse_int_list("TELEGRAM_ALLOWED_USER_IDS")
TELEGRAM_CHAT_ID = TELEGRAM_ALLOWED_USER_IDS[0] if TELEGRAM_ALLOWED_USER_IDS else None

# --- DISCORD CONFIGURATION ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_ALLOWED_USER_IDS = _parse_int_list("DISCORD_ALLOWED_USER_IDS")
DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID")) if os.getenv("DISCORD_GUILD_ID") else None

# --- PATHS & DIRECTORIES ---
_PATH_CONFIG = load_path_config_from_env()
BASE_DIR = _PATH_CONFIG["BASE_DIR"]
NEXUS_RUNTIME_DIR = _PATH_CONFIG["NEXUS_RUNTIME_DIR"]
NEXUS_STATE_DIR = _PATH_CONFIG["NEXUS_STATE_DIR"]
LOGS_DIR = _PATH_CONFIG["LOGS_DIR"]
# Compatibility alias used by older call sites/tests.
DATA_DIR = NEXUS_STATE_DIR
TRACKED_ISSUES_FILE = _PATH_CONFIG["TRACKED_ISSUES_FILE"]
LAUNCHED_AGENTS_FILE = _PATH_CONFIG["LAUNCHED_AGENTS_FILE"]
WORKFLOW_STATE_FILE = _PATH_CONFIG["WORKFLOW_STATE_FILE"]
AUDIT_LOG_FILE = _PATH_CONFIG["AUDIT_LOG_FILE"]
INBOX_PROCESSOR_LOG_FILE = _PATH_CONFIG["INBOX_PROCESSOR_LOG_FILE"]
TELEGRAM_BOT_LOG_FILE = _PATH_CONFIG["TELEGRAM_BOT_LOG_FILE"]

# --- AI CONFIGURATION ---
AI_PERSONA = os.getenv(
    "AI_PERSONA",
    "You are Nexus, a brilliant business advisor and technical architect (like Jarvis from Iron Man).\n\nAnswer the following question or brainstorm ideas directly and concisely. Keep your tone professional, highly capable, and slightly witty but always helpful.",
)

# --- REDIS CONFIGURATION ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# --- GIT PLATFORM CONFIGURATION ---
# Note: PROJECT_CONFIG_PATH is read from environment each time it's needed (for testing with monkeypatch)

# Lazy-load PROJECT_CONFIG to support testing with monkeypatch
_project_config_cache = None
_cached_config_path = None  # Track which path was cached


def _load_project_config(path: str) -> dict:
    from nexus.core.config.loaders import load_project_config_yaml

    return load_project_config_yaml(path)


def _validate_config_with_project_config(config: dict) -> None:
    _svc_validate_project_config(config)


def _load_and_validate_project_config() -> dict:
    """Load and validate PROJECT_CONFIG from file.

    Raises:
        ValueError: If PROJECT_CONFIG_PATH is not set
        FileNotFoundError: If config file not found
        ValueError: If config is invalid
    """
    global _project_config_cache, _cached_config_path
    cache = {"value": _project_config_cache, "path": _cached_config_path}
    loaded = _svc_load_and_validate_project_config(
        base_dir=BASE_DIR,
        cache=cache,
        validator=_validate_config_with_project_config,
    )
    _project_config_cache = cache.get("value")
    _cached_config_path = cache.get("path")
    return loaded


# Create a property-like accessor for PROJECT_CONFIG
def _get_project_config() -> dict:
    """Get PROJECT_CONFIG, loading it lazily on first access."""
    return _load_and_validate_project_config()


# Initialize PROJECT_CONFIG on module load
# Note: This loads the config immediately when the module is imported
# If you need truly lazy loading, wrap in a property descriptor instead
PROJECT_CONFIG = _get_project_config()


# --- WEBHOOK CONFIGURATION ---
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8081"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")  # Git webhook secret for signature verification

# --- AUTH / CREDENTIALS CONFIGURATION ---
NEXUS_AUTH_ENABLED = str(os.getenv("NEXUS_AUTH_ENABLED", "false")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
NEXUS_PUBLIC_BASE_URL = str(os.getenv("NEXUS_PUBLIC_BASE_URL", "")).strip().rstrip("/")
NEXUS_GITLAB_BASE_URL = (
    str(os.getenv("NEXUS_GITLAB_BASE_URL", os.getenv("GITLAB_BASE_URL", "https://gitlab.com")))
    .strip()
    .rstrip("/")
)
NEXUS_GITHUB_CLIENT_ID = str(os.getenv("NEXUS_GITHUB_CLIENT_ID", "")).strip()
NEXUS_GITHUB_CLIENT_SECRET = str(os.getenv("NEXUS_GITHUB_CLIENT_SECRET", "")).strip()
NEXUS_GITLAB_CLIENT_ID = str(os.getenv("NEXUS_GITLAB_CLIENT_ID", "")).strip()
NEXUS_GITLAB_CLIENT_SECRET = str(os.getenv("NEXUS_GITLAB_CLIENT_SECRET", "")).strip()
NEXUS_AUTH_ALLOWED_GITHUB_ORGS = [
    str(value).strip().lower()
    for value in _parse_csv_list("NEXUS_AUTH_ALLOWED_GITHUB_ORGS")
    if str(value).strip()
]
NEXUS_AUTH_ALLOWED_GITLAB_GROUPS = [
    str(value).strip().lower()
    for value in _parse_csv_list("NEXUS_AUTH_ALLOWED_GITLAB_GROUPS")
    if str(value).strip()
]
NEXUS_CREDENTIALS_MASTER_KEY = str(os.getenv("NEXUS_CREDENTIALS_MASTER_KEY", "")).strip()
NEXUS_CREDENTIALS_KEY_VERSION = _get_int_env("NEXUS_CREDENTIALS_KEY_VERSION", 1)
NEXUS_AUTH_SESSION_TTL_SECONDS = _get_int_env("NEXUS_AUTH_SESSION_TTL_SECONDS", 900)
NEXUS_ACCESS_SYNC_INTERVAL_MINUTES = _get_int_env("NEXUS_ACCESS_SYNC_INTERVAL_MINUTES", 30)


# --- AI ORCHESTRATOR CONFIGURATION ---
# These are now loaded from project_config.yaml
# Get defaults from config, with per-project overrides supported
def get_ai_tool_preferences(project: str = "nexus") -> dict:
    """Get AI tool preferences for a project.

    Priority:
    1. Project-specific ai_tool_preferences in PROJECT_CONFIG
    2. Global ai_tool_preferences in PROJECT_CONFIG
    3. Empty dict (no preferences defined)

    Args:
        project: Project name (default: "nexus")

    Returns:
        Dictionary mapping agent names to tool preferences
        (provider + profile).
    """
    config = _get_project_config()

    # Check project-specific override
    if project in config:
        proj_config = config[project]
        if isinstance(proj_config, dict) and "ai_tool_preferences" in proj_config:
            return proj_config["ai_tool_preferences"]

    # Fall back to global
    if "ai_tool_preferences" in config:
        return config["ai_tool_preferences"]

    return {}


def get_model_profiles(project: str = "nexus") -> dict:
    """Get model profiles for a project.

    Priority:
    1. Project-specific model_profiles in PROJECT_CONFIG
    2. Global model_profiles in PROJECT_CONFIG
    3. Empty dict (no profiles defined)
    """
    config = _get_project_config()

    if project in config:
        proj_config = config[project]
        if isinstance(proj_config, dict) and "model_profiles" in proj_config:
            return proj_config["model_profiles"]

    if "model_profiles" in config:
        return config["model_profiles"]

    return {}


def get_profile_provider_priority(project: str = "nexus") -> dict:
    """Get profile -> provider-order mapping for auto provider selection.

    Priority:
    1. Project-specific profile_provider_priority in PROJECT_CONFIG
    2. Global profile_provider_priority in PROJECT_CONFIG
    3. Empty dict (built-in defaults)
    """
    config = _get_project_config()

    if project in config:
        proj_config = config[project]
        if isinstance(proj_config, dict) and "profile_provider_priority" in proj_config:
            return proj_config["profile_provider_priority"]

    if "profile_provider_priority" in config:
        return config["profile_provider_priority"]

    return {}


def get_system_operations(project: str = "nexus") -> dict:
    """Get operation-task -> agent-type mapping for a project.

    Priority:
    1. Project-specific ``system_operations`` in PROJECT_CONFIG
    2. Global ``system_operations`` in PROJECT_CONFIG
    3. ``{"default": "triage"}``

    The orchestrator then resolves provider preference for the selected
    agent type via ``ai_tool_preferences``.
    """
    return _svc_get_system_operations(_get_project_config, project)


def get_chat_agent_types(project: str = "nexus") -> list[str]:
    """Get ordered chat agent types for a project.

    Priority:
    1. Project-specific ``system_operations.chat`` in PROJECT_CONFIG (ordered)
    2. Global ``system_operations.chat`` in PROJECT_CONFIG (ordered)
    3. Keys of ``get_ai_tool_preferences(project)`` (ordered)
    4. ["triage"] fallback

    The first item is treated as the default primary chat agent.
    """
    return _svc_get_chat_agent_types(get_chat_agents, project)


def get_chat_agents(project: str = "nexus") -> list[dict[str, Any]]:
    """Return ordered chat agent metadata for a project.

    Supported config shapes for ``system_operations.chat``:
    - Mapping form:
        ``system_operations: {chat: {business: {label: "Business"}, marketing: {...}}}``
    - List form:
        ``system_operations: {chat: [{business: {..}}, {agent_type: "marketing", ...}]}``

    Fallbacks:
    1. keys of ``ai_tool_preferences``
    """
    return _svc_get_chat_agents(_get_project_config, get_ai_tool_preferences, project)


def get_project_registry() -> dict[str, dict[str, object]]:
    """Return normalized short-key project registry from PROJECT_CONFIG['projects']."""
    return _svc_get_project_registry(_get_project_config)


def get_project_display_names() -> dict[str, str]:
    """Return configured canonical projects mapped to human-friendly display labels."""
    config = _get_project_config()
    labels: dict[str, str] = {}

    for project_key, project_cfg in config.items():
        if not isinstance(project_cfg, dict) or "workspace" not in project_cfg:
            continue

        canonical = str(project_key).strip().lower()
        if not canonical:
            continue

        display_name = str(project_cfg.get("display_name", "")).strip()
        if not display_name:
            display_name = canonical.replace("_", " ").replace("-", " ").title()

        labels[canonical] = display_name

    return labels


_DEFAULT_TASK_TYPES: dict[str, str] = {
    "feature": "Feature",
    "feature-simple": "Feature (Simple)",
    "bug": "Bug",
    "hotfix": "Hotfix",
    "release": "Release",
    "chore": "Chore",
    "improvement": "Improvement",
    "improvement-simple": "Improvement (Simple)",
}


def get_task_types() -> dict[str, str]:
    """Return normalized task-type labels from config (falls back to defaults)."""
    config = _get_project_config()
    raw_task_types = config.get("task_types")
    if not isinstance(raw_task_types, dict):
        return dict(_DEFAULT_TASK_TYPES)

    normalized: dict[str, str] = {}
    for task_key, task_label in raw_task_types.items():
        normalized_key = str(task_key).strip().lower()
        normalized_label = str(task_label).strip()
        if normalized_key and normalized_label:
            normalized[normalized_key] = normalized_label

    if normalized:
        return normalized
    return dict(_DEFAULT_TASK_TYPES)


def get_project_aliases() -> dict[str, str]:
    """Return normalized aliases resolved from PROJECT_CONFIG['projects']."""
    return _svc_get_project_aliases(_get_project_config, get_project_registry)


def normalize_project_key(project: str) -> str | None:
    """Normalize a project key using configured aliases."""
    return _svc_normalize_project_key(get_project_aliases, project)


def get_track_short_projects() -> list[str]:
    """Return short project keys for /track commands from projects registry."""
    return _svc_get_track_short_projects(get_project_registry)


def get_workflow_profile(project: str = "nexus") -> str:
    """Resolve workflow profile/path for a project from PROJECT_CONFIG.

    Priority:
    1. Project-specific ``workflow_definition_path``
    2. Global ``workflow_definition_path``
    3. ``ghabs_org_workflow`` fallback
    """
    return _svc_get_workflow_profile(_get_project_config, project)


# Caching wrappers for lazy-loading on first access (support monkeypatch in tests)
_ai_tool_preferences_cache = {}
_model_profiles_cache = {}
_profile_provider_priority_cache = {}
_system_operations_cache = {}


class _LazyConfigWrapper:
    """Wrapper that lazily loads config values to support monkeypatch."""

    def __init__(self, get_func, cache_dict, project="nexus"):
        self.get_func = get_func
        self.cache_dict = cache_dict
        self.project = project

    def _ensure_loaded(self):
        """Load value from config if not cached."""
        if "value" not in self.cache_dict:
            self.cache_dict["value"] = self.get_func(self.project)
        return self.cache_dict["value"]

    def keys(self):
        return self._ensure_loaded().keys()

    def items(self):
        return self._ensure_loaded().items()

    def values(self):
        return self._ensure_loaded().values()

    def get(self, *args):
        return self._ensure_loaded().get(*args)

    def __getitem__(self, key):
        return self._ensure_loaded()[key]

    def __contains__(self, key):
        return key in self._ensure_loaded()

    def __iter__(self):
        return iter(self._ensure_loaded())

    def __len__(self):
        return len(self._ensure_loaded())

    def __repr__(self):
        return repr(self._ensure_loaded())


# Create lazy-loading wrappers (for backward compatibility with code that accesses these directly)
# Note: These will get the global defaults from project_config.yaml when first accessed
AI_TOOL_PREFERENCES = _LazyConfigWrapper(
    get_ai_tool_preferences, _ai_tool_preferences_cache, "nexus"
)
MODEL_PROFILES = _LazyConfigWrapper(get_model_profiles, _model_profiles_cache, "nexus")
PROFILE_PROVIDER_PRIORITY = _LazyConfigWrapper(
    get_profile_provider_priority,
    _profile_provider_priority_cache,
    "nexus",
)
SYSTEM_OPERATIONS = _LazyConfigWrapper(get_system_operations, _system_operations_cache, "nexus")

# Orchestrator configuration (lazy-loaded)
_orchestrator_config_cache = {}


def _get_orchestrator_config():
    """Get orchestrator config, loading AI_TOOL_PREFERENCES lazily."""
    if "value" not in _orchestrator_config_cache:
        _orchestrator_config_cache["value"] = {
            "copilot_cli_path": os.getenv(
                "COPILOT_PROVIDER", os.getenv("COPILOT_CLI_PATH", "copilot")
            ),
            "copilot_model": os.getenv("COPILOT_MODEL", "").strip(),
            "copilot_supports_model": os.getenv("COPILOT_SUPPORTS_MODEL", "false").lower()
            == "true",
            "gemini_cli_path": os.getenv("GEMINI_CLI_PATH", "gemini"),
            "gemini_model": os.getenv("GEMINI_MODEL", "").strip(),
            "codex_cli_path": os.getenv("CODEX_CLI_PATH", "codex"),
            "codex_model": os.getenv("CODEX_MODEL", "").strip(),
            "claude_cli_path": os.getenv("CLAUDE_CLI_PATH", "claude"),
            "claude_model": os.getenv("CLAUDE_MODEL", "").strip(),
            "ai_tool_preferences_strict": os.getenv("AI_TOOL_PREFERENCES_STRICT", "false").lower()
            == "true",
            "tool_preferences": AI_TOOL_PREFERENCES._ensure_loaded(),
            "tool_preferences_resolver": get_ai_tool_preferences,
            "model_profiles": MODEL_PROFILES._ensure_loaded(),
            "model_profiles_resolver": get_model_profiles,
            "profile_provider_priority": PROFILE_PROVIDER_PRIORITY._ensure_loaded(),
            "profile_provider_priority_resolver": get_profile_provider_priority,
            "system_operations": SYSTEM_OPERATIONS._ensure_loaded(),
            "system_operations_resolver": get_system_operations,
            "chat_agent_types_resolver": get_chat_agent_types,
            "fallback_enabled": os.getenv("AI_FALLBACK_ENABLED", "true").lower() == "true",
            "rate_limit_ttl": int(os.getenv("AI_RATE_LIMIT_TTL", "3600")),
            "max_retries": int(os.getenv("AI_MAX_RETRIES", "3")),
            "analysis_timeout": _get_int_env("AI_ANALYSIS_TIMEOUT", 120),
            "ai_prompt_max_chars": _get_int_env("AI_PROMPT_MAX_CHARS", 16000),
            "ai_context_summary_max_chars": _get_int_env("AI_CONTEXT_SUMMARY_MAX_CHARS", 1200),
            "transcription_timeout": _get_int_env("TRANSCRIPTION_TIMEOUT", 120),
            "whisper_model": os.getenv("WHISPER_MODEL", "whisper-1").strip(),
            "whisper_language": os.getenv("WHISPER_LANGUAGE", "").strip().lower(),
            "whisper_languages": os.getenv("WHISPER_LANGUAGES", "").strip().lower(),
        }
    return _orchestrator_config_cache["value"]


class _LazyOrchestrator:
    """Lazy-loading wrapper for ORCHESTRATOR_CONFIG."""

    def __getitem__(self, key):
        return _get_orchestrator_config()[key]

    def __contains__(self, key):
        return key in _get_orchestrator_config()

    def __repr__(self):
        return repr(_get_orchestrator_config())

    def get(self, *args):
        return _get_orchestrator_config().get(*args)


ORCHESTRATOR_CONFIG = _LazyOrchestrator()

# --- NEXUS-ARC FRAMEWORK CONFIGURATION ---
# nexus-arc workflow engine is mandatory
NEXUS_CORE_STORAGE_DIR = os.getenv(
    "NEXUS_CORE_STORAGE_DIR",
    os.path.join(NEXUS_RUNTIME_DIR, "nexus-arc"),
)
WORKFLOW_ID_MAPPING_FILE = os.path.join(NEXUS_STATE_DIR, "workflow_id_mapping.json")
APPROVAL_STATE_FILE = os.path.join(NEXUS_STATE_DIR, "approval_state.json")

# Backend enum configuration


NEXUS_STORAGE_BACKEND = _normalize_backend_enum(
    os.getenv("NEXUS_STORAGE_BACKEND"),
    env_name="NEXUS_STORAGE_BACKEND",
    default="filesystem",
    allowed=_VALID_STORAGE_BACKENDS,
    aliases=_STORAGE_BACKEND_ALIASES,
)
NEXUS_WORKFLOW_BACKEND = _normalize_backend_enum(
    os.getenv("NEXUS_WORKFLOW_BACKEND"),
    env_name="NEXUS_WORKFLOW_BACKEND",
    default="postgres" if NEXUS_STORAGE_BACKEND == "postgres" else "filesystem",
    allowed=_VALID_STORAGE_BACKENDS,
    aliases=_STORAGE_BACKEND_ALIASES,
)
NEXUS_INBOX_BACKEND = _normalize_backend_enum(
    os.getenv("NEXUS_INBOX_BACKEND"),
    env_name="NEXUS_INBOX_BACKEND",
    default=NEXUS_STORAGE_BACKEND,
    allowed=_VALID_STORAGE_BACKENDS,
    aliases=_STORAGE_BACKEND_ALIASES,
)
NEXUS_RATE_LIMIT_BACKEND = _normalize_backend_enum(
    os.getenv("NEXUS_RATE_LIMIT_BACKEND"),
    env_name="NEXUS_RATE_LIMIT_BACKEND",
    default="redis",
    allowed=_VALID_RATE_LIMIT_BACKENDS,
    aliases=_RATE_LIMIT_BACKEND_ALIASES,
)
NEXUS_STORAGE_DSN = os.getenv("NEXUS_STORAGE_DSN", "").strip()


NEXUS_FEATURE_REGISTRY_ENABLED = _env_bool("NEXUS_FEATURE_REGISTRY_ENABLED", True)
NEXUS_FEATURE_REGISTRY_MAX_ITEMS_PER_PROJECT = max(
    10, _get_int_env("NEXUS_FEATURE_REGISTRY_MAX_ITEMS_PER_PROJECT", 500)
)
NEXUS_FEATURE_REGISTRY_DEDUP_SIMILARITY = min(
    1.0,
    max(0.0, _env_float("NEXUS_FEATURE_REGISTRY_DEDUP_SIMILARITY", 0.86)),
)

# Compatibility alias retained for older code paths that still reference this constant.
NEXUS_CORE_STORAGE_BACKEND = NEXUS_WORKFLOW_BACKEND


@dataclass(frozen=True)
class RuntimeSettings:
    """Typed runtime settings for injectable configuration."""

    nexus_state_dir: str
    nexus_storage_backend: str
    nexus_rate_limit_backend: str
    redis_url: str
    nexus_core_storage_dir: str


def get_runtime_settings() -> RuntimeSettings:
    """Return current runtime settings snapshot."""
    return RuntimeSettings(
        nexus_state_dir=NEXUS_STATE_DIR,
        nexus_storage_backend=NEXUS_STORAGE_BACKEND,
        nexus_rate_limit_backend=NEXUS_RATE_LIMIT_BACKEND,
        redis_url=REDIS_URL,
        nexus_core_storage_dir=NEXUS_CORE_STORAGE_DIR,
    )


def get_inbox_storage_backend() -> str:
    """Return effective inbox storage backend.

    Values:
    - ``filesystem``: markdown files under ``.nexus/inbox``
    - ``postgres``: queue table in PostgreSQL
    """
    return NEXUS_INBOX_BACKEND


# --- PROJECT CONFIGURATION ---
def get_default_project() -> str:
    """Return default project key for legacy call sites.

    Preference order:
    1. explicit "nexus" project when present
    2. first configured project dict containing workspace + repo metadata
    """
    return _svc_get_default_project(_get_project_config)


def get_repos(project: str) -> list[str]:
    """Get all git repositories configured for a project.

    Uses provider-neutral ``git_repo`` / ``git_repos``.
    """
    return _svc_get_repos(_get_project_config, BASE_DIR, project)


def _discover_workspace_repos(project_cfg: dict) -> list[str]:
    """Discover repository slugs from local git remotes in workspace.

    Scans workspace root and first-level subdirectories that are git repos.
    """
    from nexus.core.config.repos import discover_workspace_repos

    return discover_workspace_repos(project_cfg, BASE_DIR)


def _repo_slug_from_remote_url(remote_url: str) -> str:
    """Normalize git remote URL into ``namespace/repo`` slug."""
    from nexus.core.config.repos import repo_slug_from_remote_url

    return repo_slug_from_remote_url(remote_url)


def get_default_repo() -> str:
    """Return default git repo for legacy single-repo call sites."""
    return _svc_get_default_repo(get_repo, get_default_project)


def get_project_platform(project: str) -> str:
    """Return VCS platform type for a project (``github`` or ``gitlab``)."""
    return _svc_get_project_platform(_get_project_config, project)


def get_gitlab_base_url(project: str) -> str:
    """Return GitLab base URL for a project.

    Priority:
    1. project-level ``gitlab_base_url``
    2. env var ``GITLAB_BASE_URL``
    3. default ``https://gitlab.com``
    """
    return _svc_get_gitlab_base_url(_get_project_config, project)


def get_repo(project: str) -> str:
    """Get git repo for a project from PROJECT_CONFIG.

    Args:
        project: Project name (e.g., "nexus")

    Returns:
        Repo string (e.g., "namespace/repository")

    Raises:
        KeyError: If project not found in PROJECT_CONFIG
    """
    return _svc_get_repo(get_repos, project)


def get_nexus_dir_name() -> str:
    """Get the nexus directory name for globbing patterns.

    Returns:
        Directory name (e.g., ".nexus") from config
    """
    return _svc_get_nexus_dir_name(_get_project_config)


def get_nexus_dir(workspace: str = None) -> str:
    """Get Nexus directory path (VCS-agnostic inbox/tasks storage).

    Default: workspace_root/.nexus (can be configured via config)

    Args:
        workspace: Workspace directory (uses current if not specified)

    Returns:
        Path to nexus directory (e.g., /path/to/workspace/.nexus)
    """
    return _svc_get_nexus_dir(_get_project_config, workspace)


def get_inbox_dir(workspace: str = None, project: str = None) -> str:
    """Get inbox directory path for workflow tasks.

    Args:
        workspace: Workspace directory
        project: Optional project key subdirectory under inbox

    Returns:
        Path to {nexus_dir}/inbox or {nexus_dir}/inbox/{project}
    """
    return _svc_get_inbox_dir(_get_project_config, workspace, project)


def get_tasks_active_dir(workspace: str, project: str) -> str:
    """Get active tasks directory path for in-progress work.

    Args:
        workspace: Workspace directory
        project: Project key subdirectory under tasks (required)

    Returns:
        Path to {nexus_dir}/tasks/{project}/active
    """
    return _svc_get_tasks_active_dir(_get_project_config, workspace, project)


def get_tasks_closed_dir(workspace: str, project: str) -> str:
    """Get closed tasks directory path for archived work.

    Args:
        workspace: Workspace directory
        project: Project key subdirectory under tasks (required)

    Returns:
        Path to {nexus_dir}/tasks/{project}/closed
    """
    return _svc_get_tasks_closed_dir(_get_project_config, workspace, project)


def get_tasks_logs_dir(workspace: str, project: str) -> str:
    """Get task logs directory path for agent execution logs.

    Args:
        workspace: Workspace directory
        project: Project key subdirectory under tasks (required)

    Returns:
        Path to {nexus_dir}/tasks/{project}/logs
    """
    return _svc_get_tasks_logs_dir(_get_project_config, workspace, project)


# --- TIMING CONFIGURATION ---
INBOX_CHECK_INTERVAL = _get_int_env(
    "INBOX_CHECK_INTERVAL", 10
)  # seconds - how often to check for new completions
SLEEP_INTERVAL = INBOX_CHECK_INTERVAL  # Alias for backward compatibility
AGENT_RECENT_WINDOW = _get_int_env("AGENT_RECENT_WINDOW", 120)  # seconds
AGENT_TIMEOUT = _get_int_env("AGENT_TIMEOUT", 3600)  # seconds
COPILOT_PROVIDER = os.getenv("COPILOT_PROVIDER", os.getenv("COPILOT_CLI_PATH", "copilot"))
AUTO_CHAIN_CYCLE = _get_int_env("AUTO_CHAIN_CYCLE", 60)  # seconds - frequency of auto-chain polling

# --- LOGGING CONFIGURATION ---
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_LEVEL = logging.INFO

# --- VALIDATION ---
logger = logging.getLogger(__name__)


def configure_runtime_logging(*, force: bool = False) -> None:
    """Configure runtime logging explicitly during app startup."""
    logging.basicConfig(format=LOG_FORMAT, level=LOG_LEVEL, force=force)
    logger.info(f"Using BASE_DIR: {BASE_DIR}")


def validate_configuration():
    """Validate all configuration on startup with detailed error messages.

    Note: This must be called AFTER PROJECT_CONFIG is loaded (via _get_project_config()).
    """
    errors = []
    warnings = []

    # Check required environment variables
    if not TELEGRAM_TOKEN:
        errors.append("TELEGRAM_TOKEN is missing! Set it in .env or environment.")

    if not TELEGRAM_ALLOWED_USER_IDS:
        warnings.append("TELEGRAM_ALLOWED_USER_IDS are missing! Bot will not respond to anyone.")

    if NEXUS_AUTH_ENABLED:
        if NEXUS_STORAGE_BACKEND != "postgres":
            errors.append(
                "NEXUS_AUTH_ENABLED=true requires NEXUS_STORAGE_BACKEND=postgres "
                "(alias: database)."
            )
        if not NEXUS_STORAGE_DSN:
            errors.append("NEXUS_AUTH_ENABLED=true requires NEXUS_STORAGE_DSN.")
        if not NEXUS_PUBLIC_BASE_URL:
            errors.append("NEXUS_AUTH_ENABLED=true requires NEXUS_PUBLIC_BASE_URL.")
        github_enabled = bool(NEXUS_GITHUB_CLIENT_ID and NEXUS_GITHUB_CLIENT_SECRET)
        gitlab_enabled = bool(NEXUS_GITLAB_CLIENT_ID and NEXUS_GITLAB_CLIENT_SECRET)
        if not github_enabled and not gitlab_enabled:
            errors.append(
                "NEXUS_AUTH_ENABLED=true requires GitHub or GitLab OAuth credentials "
                "(NEXUS_GITHUB_CLIENT_ID/SECRET or NEXUS_GITLAB_CLIENT_ID/SECRET)."
            )
        if github_enabled and not NEXUS_AUTH_ALLOWED_GITHUB_ORGS:
            errors.append(
                "NEXUS_AUTH_ENABLED=true with GitHub OAuth requires NEXUS_AUTH_ALLOWED_GITHUB_ORGS."
            )
        if gitlab_enabled and not NEXUS_AUTH_ALLOWED_GITLAB_GROUPS:
            errors.append(
                "NEXUS_AUTH_ENABLED=true with GitLab OAuth requires NEXUS_AUTH_ALLOWED_GITLAB_GROUPS."
            )
        if not NEXUS_CREDENTIALS_MASTER_KEY:
            errors.append("NEXUS_AUTH_ENABLED=true requires NEXUS_CREDENTIALS_MASTER_KEY.")

    # Validate PROJECT_CONFIG (when loaded)
    try:
        config = _get_project_config()
        if config:
            global_keys = {
                "nexus_dir",
                "workflow_definition_path",
                "projects",
                "task_types",
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
                # Skip non-dict values (e.g., global settings like workflow_definition_path)
                if not isinstance(proj_config, dict):
                    errors.append(f"PROJECT_CONFIG['{project}'] must be a dict")
                else:
                    if "workspace" not in proj_config:
                        errors.append(f"PROJECT_CONFIG['{project}'] missing 'workspace' key")
                    # git_repo/git_repos are optional when workspace auto-discovery is used.
                    repo_list = proj_config.get("git_repos")
                    if repo_list is not None and not isinstance(repo_list, list):
                        errors.append(f"PROJECT_CONFIG['{project}']['git_repos'] must be a list")
    except Exception:
        # If PROJECT_CONFIG can't be loaded, that's okay during import (tests handle this)
        pass

    # Check if BASE_DIR is writable
    try:
        test_file = os.path.join(BASE_DIR, ".config_test")
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
    except Exception as e:
        errors.append(f"BASE_DIR ({BASE_DIR}) is not writable: {e}")

    # Log results
    if errors:
        logger.error("❌ CONFIGURATION VALIDATION FAILED:")
        for error in errors:
            logger.error(f"  - {error}")
        logger.error("Please fix configuration errors before running.")
        sys.exit(1)

    if warnings:
        logger.warning("⚠️  Configuration warnings:")
        for warning in warnings:
            logger.warning(f"  - {warning}")

    logger.info("✅ Configuration validation passed")


def ensure_state_dir():
    """Ensure runtime state directory exists."""
    os.makedirs(NEXUS_STATE_DIR, exist_ok=True)
    logger.debug(f"✅ State directory ready: {NEXUS_STATE_DIR}")


def ensure_nexus_storage_dir():
    """Ensure nexus-arc file storage directory exists."""
    os.makedirs(NEXUS_CORE_STORAGE_DIR, exist_ok=True)
    logger.debug(f"✅ Nexus storage directory ready: {NEXUS_CORE_STORAGE_DIR}")


def ensure_logs_dir():
    """Ensure logs directory exists."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    logger.debug(f"✅ Logs directory ready: {LOGS_DIR}")


def initialize_runtime_directories() -> None:
    """Initialize required runtime directories (non-blocking)."""
    try:
        ensure_state_dir()
        ensure_nexus_storage_dir()
        ensure_logs_dir()
    except Exception as e:
        logger.warning(f"Could not initialize directories: {e}")
