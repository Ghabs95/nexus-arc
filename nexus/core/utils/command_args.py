"""Common platform-agnostic routing logic for Nexus Bot."""

from collections.abc import Sequence


# Commands that only require a project key, not a specific issue number.
PROJECT_ONLY_COMMANDS = {
    "agents",
    "feature_done",
    "feature_list",
    "feature_forget",
    "active",
    "stats",
    "inboxq",
}


def normalize_command_args(
    command: str,
    project_key: str,
    issue_num: str | None = None,
    rest: Sequence[str] | None = None,
) -> list[str]:
    """
    Normalizes project/issue arguments into a consistent list.
    
    This ensures that handlers receive the arguments they expect in a predictable 
    order, regardless of which chat platform triggered the command.
    """
    if command in PROJECT_ONLY_COMMANDS:
        return [project_key] + list(rest or [])
    
    # For issue-specific commands, we expect [project, issue, ...rest]
    return [project_key, str(issue_num or "")] + list(rest or [])
