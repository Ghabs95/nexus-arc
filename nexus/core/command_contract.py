"""Shared command contract and parity checks across bot frontends."""

from __future__ import annotations

import os

TELEGRAM_COMMANDS: set[str] = {
    "start",
    "help",
    "menu",
    "new",
    "cancel",
    "chat",
    "chatagents",
    "status",
    "inboxq",
    "active",
    "progress",
    "track",
    "tracked",
    "untrack",
    "myissues",
    "logs",
    "logsfull",
    "tail",
    "tailstop",
    "fuse",
    "audit",
    "wfstate",
    "visualize",
    "watch",
    "stats",
    "comments",
    "reprocess",
    "reconcile",
    "continue",
    "forget",
    "kill",
    "pause",
    "resume",
    "stop",
    "agents",
    "direct",
    "respond",
    "assign",
    "implement",
    "prepare",
    "plan",
    "feature_done",
    "feature_list",
    "feature_forget",
}


DISCORD_COMMANDS: set[str] = {
    "start",
    "help",
    "menu",
    "new",
    "cancel",
    "chat",
    "chatagents",
    "active",
    "progress",
    "inboxq",
    "stats",
    "logs",
    "logsfull",
    "tail",
    "tailstop",
    "fuse",
    "audit",
    "comments",
    "visualize",
    "watch",
    "wfstate",
    "reprocess",
    "reconcile",
    "continue",
    "forget",
    "kill",
    "pause",
    "resume",
    "stop",
    "agents",
    "direct",
    "assign",
    "implement",
    "prepare",
    "plan",
    "respond",
    "untrack",
    "feature_done",
    "feature_list",
    "feature_forget",
    "track",
    "tracked",
    "myissues",
    "status",
}


REQUIRED_PARITY_COMMANDS: set[str] = {
    "chat",
    "track",
    "tracked",
    "myissues",
    "status",
}


BRIDGE_RENDER_MODES: set[str] = {
    "text",
    "rich",
}


BRIDGE_SESSION_CONTEXT_FIELDS: set[str] = {
    "current_project",
    "current_workflow_id",
    "current_issue_ref",
}


OPENCLAW_BRIDGE_COMMANDS: set[str] = {
    "active",
    "agents",
    "audit",
    "continue",
    "implement",
    "logs",
    "myissues",
    "new",
    "pause",
    "plan",
    "prepare",
    "respond",
    "resume",
    "stats",
    "status",
    "stop",
    "track",
    "tracked",
    "untrack",
    "usage",
    "wfstate",
}


PLATFORM_COMMANDS: dict[str, set[str]] = {
    "telegram": TELEGRAM_COMMANDS,
    "discord": DISCORD_COMMANDS,
}


def is_parity_strict_enabled() -> bool:
    """Return whether strict command parity enforcement is enabled."""
    return os.getenv("COMMAND_PARITY_STRICT", "false").strip().lower() == "true"


def get_command_parity_report() -> dict[str, set[str]]:
    """Build a parity report between Telegram and Discord command sets."""
    telegram = set(PLATFORM_COMMANDS.get("telegram", set()))
    discord = set(PLATFORM_COMMANDS.get("discord", set()))
    return {
        "telegram_only": telegram - discord,
        "discord_only": discord - telegram,
        "shared": telegram & discord,
    }


def validate_command_parity(strict: bool | None = None) -> dict[str, set[str]]:
    """Validate command parity and optionally raise when strict mode is enabled."""
    report = get_command_parity_report()
    strict_mode = is_parity_strict_enabled() if strict is None else strict

    if strict_mode and (report["telegram_only"] or report["discord_only"]):
        raise ValueError(
            "Command parity mismatch detected: "
            f"telegram_only={sorted(report['telegram_only'])}, "
            f"discord_only={sorted(report['discord_only'])}"
        )

    return report


def validate_required_command_interface() -> None:
    """Ensure both platforms implement required parity commands."""
    missing: dict[str, set[str]] = {}
    for platform, commands in PLATFORM_COMMANDS.items():
        gap = REQUIRED_PARITY_COMMANDS - set(commands)
        if gap:
            missing[platform] = gap

    if missing:
        raise ValueError(
            "Required command interface mismatch: "
            + ", ".join(
                f"{platform} missing {sorted(commands)}"
                for platform, commands in sorted(missing.items())
            )
        )
