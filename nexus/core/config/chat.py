from typing import Any, Callable

from nexus.core.chat_agents_schema import get_project_chat_agents


def get_system_operations(
    get_project_config: Callable[[], dict],
    project: str = "nexus",
) -> dict:
    """Return operation-task -> agent-type mapping for a project."""
    config = get_project_config()

    if project in config:
        proj_config = config[project]
        if isinstance(proj_config, dict) and "system_operations" in proj_config:
            value = proj_config["system_operations"]
            if isinstance(value, dict):
                return value

    if "system_operations" in config:
        value = config["system_operations"]
        if isinstance(value, dict):
            return value

    return {"default": "triage"}


def get_chat_agents(
    get_project_config: Callable[[], dict],
    get_ai_tool_preferences: Callable[[str], dict],
    project: str = "nexus",
) -> list[dict[str, Any]]:
    """Return ordered chat agent metadata for a project."""
    config = get_project_config()
    entries: list[dict[str, Any]] = []

    project_cfg = config.get(project)
    if isinstance(project_cfg, dict):
        entries = get_project_chat_agents(project_cfg)
        if entries:
            return entries

    global_system_operations = config.get("system_operations")
    if isinstance(global_system_operations, dict):
        raw_chat = global_system_operations.get("chat")
        if isinstance(raw_chat, (dict, list)):
            entries = get_project_chat_agents({"system_operations": {"chat": raw_chat}})
            if entries:
                return entries

    preferences = get_ai_tool_preferences(project)
    if isinstance(preferences, dict) and preferences:
        for agent_type in preferences:
            normalized = str(agent_type).strip().lower()
            if normalized:
                entries.append({"agent_type": normalized})
        if entries:
            return entries

    return [{"agent_type": "triage"}]


def get_chat_agent_types(
    get_chat_agents_fn: Callable[[str], list[dict[str, Any]]],
    project: str = "nexus",
) -> list[str]:
    """Return ordered chat agent type names for a project."""
    entries = get_chat_agents_fn(project)
    if entries:
        return [entry["agent_type"] for entry in entries]
    return ["triage"]
