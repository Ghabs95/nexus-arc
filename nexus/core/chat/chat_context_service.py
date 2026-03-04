from typing import Any, Mapping

from nexus.core.config import get_chat_agent_types, get_chat_agents

CHAT_MODES = {
    "strategy": "Strategy",
    "execution": "Execution",
}

PRIMARY_AGENT_TYPES = {
    "ceo": "CEO",
    "business": "Business Advisor",
    "marketing": "Marketing Advisor",
    "cto": "CTO",
    "architect": "Architect",
    "triage": "Triage",
    "developer": "Developer",
    "reviewer": "Reviewer",
    "compliance": "Compliance",
    "deployer": "Deployer",
    "debug": "Debug",
    "designer": "Designer",
    "docs": "Docs",
    "writer": "Writer",
    "finalizer": "Finalizer",
}


def agent_type_label(agent_type: str) -> str:
    value = str(agent_type or "").strip().lower()
    if not value:
        return "Unknown"
    return PRIMARY_AGENT_TYPES.get(value, value.replace("_", " ").title())


def agent_display_label(agent: dict[str, Any]) -> str:
    label = str(agent.get("label") or agent.get("display_name") or "").strip()
    if label:
        return label
    return agent_type_label(str(agent.get("agent_type") or ""))


def available_chat_agents(chat_data: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = (chat_data or {}).get("metadata") or {}
    project_key = metadata.get("project_key")

    configured_agents = get_chat_agents(project_key or "nexus") or []
    normalized_agents: list[dict[str, Any]] = []
    for item in configured_agents:
        if not isinstance(item, dict):
            continue
        agent_type = str(item.get("agent_type") or "").strip().lower()
        if not agent_type:
            continue
        payload = dict(item)
        payload["agent_type"] = agent_type
        normalized_agents.append(payload)
    if normalized_agents:
        return normalized_agents

    configured_types = get_chat_agent_types(project_key or "nexus") or []
    cleaned_configured = [
        str(agent_type).strip().lower()
        for agent_type in configured_types
        if str(agent_type).strip()
    ]
    if cleaned_configured:
        return [{"agent_type": value} for value in cleaned_configured]

    allowed = metadata.get("allowed_agent_types")
    if isinstance(allowed, list):
        cleaned = [
            str(item).strip().lower()
            for item in allowed
            if isinstance(item, str) and str(item).strip()
        ]
        if cleaned:
            return [{"agent_type": value} for value in cleaned]

    return [{"agent_type": "triage"}]


def available_primary_agent_types(chat_data: dict[str, Any]) -> list[str]:
    return [item["agent_type"] for item in available_chat_agents(chat_data)]


def chat_context_summary(
    chat_data: dict[str, Any],
    projects_map: Mapping[str, str],
    *,
    markdown_style: str = "telegram",
) -> str:
    metadata = (chat_data or {}).get("metadata") or {}
    project_key = str(metadata.get("project_key") or "").strip().lower()
    project_label = projects_map.get(project_key, "Not set") if project_key else "Not set"
    chat_mode = CHAT_MODES.get(str(metadata.get("chat_mode", "strategy")), "Strategy")
    available_agents = available_chat_agents(chat_data)
    available_agent_types = [item["agent_type"] for item in available_agents]
    primary_agent_type = str(metadata.get("primary_agent_type") or "").strip().lower()
    if not primary_agent_type or primary_agent_type not in available_agent_types:
        primary_agent_type = available_agent_types[0]
    agent_by_type = {item["agent_type"]: item for item in available_agents}
    primary_agent_label = agent_display_label(
        agent_by_type.get(primary_agent_type, {"agent_type": primary_agent_type})
    )

    if str(markdown_style).strip().lower() == "discord":
        return (
            f"**Project:** {project_label}\n"
            f"**Mode:** {chat_mode}\n"
            f"**Primary Agent:** {primary_agent_label} (`{primary_agent_type}`)"
        )

    return (
        f"*Project:* {project_label}\n"
        f"*Mode:* {chat_mode}\n"
        f"*Primary Agent:* {primary_agent_label} (`{primary_agent_type}`)"
    )
