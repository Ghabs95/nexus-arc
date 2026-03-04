"""Polling loop orchestration extracted from inbox_processor."""

import time
from collections.abc import Callable
from typing import Any


def run_processor_loop(
    *,
    logger,
    base_dir: str,
    sleep_interval: int | float,
    check_interval: int | float,
    get_inbox_storage_backend: Callable[[], str],
    drain_postgres_inbox_queue: Callable[[], None],
    process_filesystem_inbox_once: Callable[[str], None],
    check_stuck_agents: Callable[[], None],
    check_agent_comments: Callable[[], None],
    check_completed_agents: Callable[[], None],
    merge_queue_auto_merge_once: Callable[[], None],
    cleanup_stale_worktrees_once: Callable[[], None] | None = None,
    runtime_state=None,
    time_module: Any = time,
) -> None:
    """Run the main processor polling loop forever."""
    last_check = time_module.time()

    while True:
        inbox_backend = get_inbox_storage_backend()

        if inbox_backend == "postgres":
            drain_postgres_inbox_queue()
        else:
            process_filesystem_inbox_once(base_dir)

        current_time = time_module.time()
        if current_time - last_check >= check_interval:
            check_stuck_agents()
            check_agent_comments()
            check_completed_agents()
            merge_queue_auto_merge_once()
            if callable(cleanup_stale_worktrees_once):
                cleanup_stale_worktrees_once()
            last_check = current_time

        time_module.sleep(sleep_interval)
