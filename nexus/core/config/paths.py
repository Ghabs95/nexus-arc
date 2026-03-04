"""Path and runtime directory configuration helpers for Telegram bot modules."""

from __future__ import annotations

import os


def load_path_config_from_env() -> dict[str, str]:
    """Return path-related config values from environment with defaults."""
    base_dir = os.getenv("BASE_DIR", "/home/ubuntu/git")
    nexus_runtime_dir = os.getenv("NEXUS_RUNTIME_DIR", "/var/lib/nexus")
    nexus_state_dir = os.getenv("DATA_DIR")
    if not nexus_state_dir:
        nexus_state_dir = os.path.join(nexus_runtime_dir, "state")
    logs_dir = os.getenv("LOGS_DIR", "/var/log/nexus")
    return {
        "BASE_DIR": base_dir,
        "NEXUS_RUNTIME_DIR": nexus_runtime_dir,
        "NEXUS_STATE_DIR": nexus_state_dir,
        "LOGS_DIR": logs_dir,
        "TRACKED_ISSUES_FILE": os.path.join(nexus_state_dir, "tracked_issues.json"),
        "LAUNCHED_AGENTS_FILE": os.path.join(nexus_state_dir, "launched_agents.json"),
        "WORKFLOW_STATE_FILE": os.path.join(nexus_state_dir, "workflow_state.json"),
        "AUDIT_LOG_FILE": os.path.join(logs_dir, "audit.log"),
        "INBOX_PROCESSOR_LOG_FILE": os.path.join(logs_dir, "inbox_processor.log"),
        "TELEGRAM_BOT_LOG_FILE": os.path.join(logs_dir, "telegram_bot.log"),
    }
