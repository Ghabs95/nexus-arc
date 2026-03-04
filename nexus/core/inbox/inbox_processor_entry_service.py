import contextlib
import os
import uuid


def run_inbox_processor_main(
    *,
    logger,
    base_dir: str,
    sleep_interval: int | float,
    get_inbox_storage_backend,
    reconcile_completion_signals_on_startup,
    check_stuck_agents,
    check_agent_comments,
    check_completed_agents,
    merge_queue_auto_merge_once,
    cleanup_stale_worktrees_once=None,
    drain_postgres_inbox_queue,
    process_filesystem_inbox_once,
    run_processor_loop,
    runtime_state,
    time_module,
    setup_event_handlers,
) -> None:
    logger.info(f"Inbox Processor started on {base_dir}")
    setup_event_handlers()
    logger.info("Inbox storage backend (effective): %s", get_inbox_storage_backend())
    logger.info("Stuck agent monitoring enabled (using workflow agent timeout)")
    logger.info("Agent comment monitoring enabled")
    try:
        reconcile_completion_signals_on_startup()
    except Exception as e:
        logger.error(f"Startup completion-signal drift check failed: {e}")
    check_stuck_agents()
    run_processor_loop(
        logger=logger,
        base_dir=base_dir,
        sleep_interval=sleep_interval,
        check_interval=60,
        get_inbox_storage_backend=get_inbox_storage_backend,
        drain_postgres_inbox_queue=drain_postgres_inbox_queue,
        process_filesystem_inbox_once=process_filesystem_inbox_once,
        check_stuck_agents=check_stuck_agents,
        check_agent_comments=check_agent_comments,
        check_completed_agents=check_completed_agents,
        merge_queue_auto_merge_once=merge_queue_auto_merge_once,
        cleanup_stale_worktrees_once=cleanup_stale_worktrees_once,
        runtime_state=runtime_state,
        time_module=time_module,
    )


def drain_postgres_inbox_queue_once(
    *,
    batch_size: int,
    logger,
    claim_pending_tasks,
    process_task_payload,
    mark_task_done,
    mark_task_failed,
) -> None:
    worker_id = f"{os.uname().nodename}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    try:
        tasks = claim_pending_tasks(limit=batch_size, worker_id=worker_id)
    except Exception as exc:
        logger.error("Failed to claim Postgres inbox tasks: %s", exc)
        return

    if not tasks:
        return

    for task in tasks:
        try:
            processed_ok = process_task_payload(
                project_key=str(task.project_key),
                workspace=str(task.workspace),
                filename=str(task.filename),
                content=str(task.markdown_content),
            )
            if not processed_ok:
                mark_task_failed(task.id, "Task processing failed (see processor logs)")
                continue
            mark_task_done(task.id)
        except Exception as exc:
            logger.error(
                "Failed processing Postgres inbox task id=%s: %s", task.id, exc, exc_info=True
            )
            with contextlib.suppress(Exception):
                mark_task_failed(task.id, str(exc))
