from __future__ import annotations

from collections.abc import Iterable

from nexus.core.storage.capabilities import get_storage_capabilities

FILESYSTEM_ONLY_COMMANDS = frozenset(
    {
        "active",
        "logs",
        "logsfull",
        "tail",
        "tailstop",
    }
)


def is_command_visible(command: str, *, local_task_files: bool | None = None) -> bool:
    """Return whether a command should be exposed for the active storage mode."""
    if local_task_files is None:
        local_task_files = get_storage_capabilities().local_task_files
    return bool(local_task_files) or command not in FILESYSTEM_ONLY_COMMANDS


def filter_visible_commands(
    commands: Iterable[str], *, local_task_files: bool | None = None
) -> list[str]:
    """Preserve order while filtering commands hidden for the active storage mode."""
    return [
        command
        for command in commands
        if is_command_visible(command, local_task_files=local_task_files)
    ]
