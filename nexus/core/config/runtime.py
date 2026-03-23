import os
from typing import Callable


VALID_RUNTIME_MODES = {"standalone", "openclaw", "advanced"}
VALID_TRANSCRIPT_OWNERS = {"nexus", "openclaw", "split"}
VALID_AUTH_AUTHORITIES = {"nexus", "openclaw"}
VALID_EXECUTION_CREDENTIAL_SOURCES = {"nexus-store", "openclaw-broker"}


def normalize_runtime_mode(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_RUNTIME_MODES:
        return normalized
    return "standalone"


def default_chat_transcript_owner(runtime_mode: str | None = None) -> str:
    normalized_mode = normalize_runtime_mode(runtime_mode)
    if normalized_mode == "openclaw":
        return "openclaw"
    return "nexus"


def normalize_chat_transcript_owner(value: str | None, runtime_mode: str | None = None) -> str:
    normalized_owner = str(value or "").strip().lower()
    if normalized_owner in VALID_TRANSCRIPT_OWNERS:
        return normalized_owner
    return default_chat_transcript_owner(runtime_mode)


def chat_transcript_persistence_enabled(
    transcript_owner: str | None,
    runtime_mode: str | None = None,
) -> bool:
    normalized_owner = normalize_chat_transcript_owner(transcript_owner, runtime_mode)
    return normalized_owner == "nexus"


def chat_metadata_backend(
    transcript_owner: str | None,
    runtime_mode: str | None = None,
) -> str:
    if chat_transcript_persistence_enabled(transcript_owner, runtime_mode):
        return "redis"
    return "filesystem"


def default_auth_authority(runtime_mode: str | None = None) -> str:
    normalized_mode = normalize_runtime_mode(runtime_mode)
    if normalized_mode == "openclaw":
        return "openclaw"
    return "nexus"


def normalize_auth_authority(value: str | None, runtime_mode: str | None = None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_AUTH_AUTHORITIES:
        return normalized
    return default_auth_authority(runtime_mode)


def bridge_requires_authorized_sender(
    auth_authority: str | None,
    runtime_mode: str | None = None,
) -> bool:
    return normalize_auth_authority(auth_authority, runtime_mode) == "openclaw"


def default_execution_credential_source(runtime_mode: str | None = None) -> str:
    normalized_mode = normalize_runtime_mode(runtime_mode)
    if normalized_mode == "openclaw":
        return "openclaw-broker"
    return "nexus-store"


def normalize_execution_credential_source(
    value: str | None,
    runtime_mode: str | None = None,
) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_EXECUTION_CREDENTIAL_SOURCES:
        return normalized
    return default_execution_credential_source(runtime_mode)


def default_rate_limit_backend(
    storage_backend: str | None,
    *,
    runtime_mode: str | None = None,
    transcript_owner: str | None = None,
) -> str:
    normalized_storage = str(storage_backend or "").strip().lower()
    if chat_transcript_persistence_enabled(transcript_owner, runtime_mode):
        return "redis"
    if normalized_storage == "postgres":
        return "database"
    return "filesystem"


def get_nexus_dir_name(get_project_config: Callable[[], dict]) -> str:
    """Return configured nexus directory name (defaults to .nexus)."""
    config = get_project_config()
    return str(config.get("nexus_dir", ".nexus"))


def get_nexus_dir(get_project_config: Callable[[], dict], workspace: str | None = None) -> str:
    """Return Nexus directory path under a workspace."""
    target_workspace = workspace if workspace is not None else os.getcwd()
    return os.path.join(target_workspace, get_nexus_dir_name(get_project_config))


def get_inbox_dir(
    get_project_config: Callable[[], dict],
    workspace: str | None = None,
    project: str | None = None,
) -> str:
    """Return inbox directory path, optionally scoped to a project."""
    inbox_dir = os.path.join(get_nexus_dir(get_project_config, workspace), "inbox")
    if project:
        inbox_dir = os.path.join(inbox_dir, project)
    return inbox_dir


def get_tasks_active_dir(
    get_project_config: Callable[[], dict], workspace: str, project: str
) -> str:
    return os.path.join(get_nexus_dir(get_project_config, workspace), "tasks", project, "active")


def get_tasks_closed_dir(
    get_project_config: Callable[[], dict], workspace: str, project: str
) -> str:
    return os.path.join(get_nexus_dir(get_project_config, workspace), "tasks", project, "closed")


def get_tasks_logs_dir(get_project_config: Callable[[], dict], workspace: str, project: str) -> str:
    return os.path.join(get_nexus_dir(get_project_config, workspace), "tasks", project, "logs")
