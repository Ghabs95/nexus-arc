import os
from collections.abc import Mapping
from typing import Any

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler


def build_post_init_with_scheduler(
    *,
    original_post_init,
    report_scheduler,
    alerting_system,
    logger,
):
    async def post_init_with_scheduler(application):
        await original_post_init(application)
        if report_scheduler:
            report_scheduler.start()
            logger.info("📊 Scheduled reports started")
        if alerting_system:
            alerting_system.start()
            logger.info("🚨 Alerting system started")
        try:
            from orchestration.nexus_core_helpers import setup_event_handlers

            setup_event_handlers()
            logger.info("🔔 EventBus event handlers initialized")
        except Exception as exc:
            logger.warning("EventBus event handler setup failed: %s", exc)

    return post_init_with_scheduler


def register_application_handlers(
    *,
    app,
    conv_handler,
    handlers: Mapping[str, Any],
    filters_module,
) -> None:
    app.add_handler(conv_handler)
    for cmd, handler_name in (
        ("start", "start_handler"),
        ("help", "help_handler"),
        ("menu", "menu_handler"),
        ("rename", "rename_handler"),
        ("cancel", "cancel"),
        ("status", "status_handler"),
        ("inboxq", "inboxq_handler"),
        ("active", "active_handler"),
        ("progress", "progress_handler"),
        ("track", "track_handler"),
        ("tracked", "tracked_handler"),
        ("untrack", "untrack_handler"),
        ("myissues", "myissues_handler"),
        ("logs", "logs_handler"),
        ("logsfull", "logsfull_handler"),
        ("tail", "tail_handler"),
        ("tailstop", "tailstop_handler"),
        ("fuse", "fuse_handler"),
        ("audit", "audit_handler"),
        ("wfstate", "wfstate_handler"),
        ("visualize", "visualize_handler"),
        ("watch", "watch_handler"),
        ("stats", "stats_handler"),
        ("comments", "comments_handler"),
        ("reprocess", "reprocess_handler"),
        ("reconcile", "reconcile_handler"),
        ("continue", "continue_handler"),
        ("forget", "forget_handler"),
        ("kill", "kill_handler"),
        ("pause", "pause_handler"),
        ("resume", "resume_handler"),
        ("stop", "stop_handler"),
        ("agents", "agents_handler"),
        ("direct", "direct_handler"),
        ("respond", "respond_handler"),
        ("assign", "assign_handler"),
        ("implement", "implement_handler"),
        ("prepare", "prepare_handler"),
        ("plan", "plan_handler"),
        ("feature_done", "feature_done_handler"),
        ("feature_list", "feature_list_handler"),
        ("feature_forget", "feature_forget_handler"),
        ("chat", "chat_menu_handler"),
        ("chatagents", "chat_agents_handler"),
    ):
        app.add_handler(CommandHandler(cmd, handlers[handler_name]))

    app.add_handler(CallbackQueryHandler(handlers["chat_callback_handler"], pattern=r"^chat:"))
    app.add_handler(CallbackQueryHandler(handlers["menu_callback_handler"], pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(handlers["project_picker_handler"], pattern=r"^pickcmd:"))
    app.add_handler(CallbackQueryHandler(handlers["issue_picker_handler"], pattern=r"^pickissue"))
    app.add_handler(
        CallbackQueryHandler(handlers["monitor_project_picker_handler"], pattern=r"^pickmonitor:")
    )
    app.add_handler(CallbackQueryHandler(handlers["close_flow_handler"], pattern=r"^flow:close$"))
    app.add_handler(CallbackQueryHandler(handlers["feature_callback_handler"], pattern=r"^feat:"))
    app.add_handler(
        CallbackQueryHandler(
            handlers["task_confirmation_callback_handler"],
            pattern=r"^taskconfirm:",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            handlers["inline_keyboard_handler"],
            pattern=r"^(logs|logsfull|status|pause|resume|stop|audit|reprocess|respond|approve|reject|wfapprove|wfdeny|report_bug)_",
        )
    )
    app.add_handler(
        MessageHandler(
            (filters_module.TEXT | filters_module.VOICE) & (~filters_module.COMMAND),
            handlers["hands_free_handler"],
        )
    )
    app.add_error_handler(handlers["telegram_error_handler"])


def reports_enabled(getenv=os.getenv) -> bool:
    return getenv("ENABLE_SCHEDULED_REPORTS", "true").lower() == "true"


def alerting_enabled(getenv=os.getenv) -> bool:
    return getenv("ENABLE_ALERTING", "true").lower() == "true"


def allowed_updates_all_types() -> list[str]:
    return Update.ALL_TYPES


def build_command_handler_map(**handlers):
    return {
        "status": handlers["status_handler"],
        "active": handlers["active_handler"],
        "inboxq": handlers["inboxq_handler"],
        "stats": handlers["stats_handler"],
        "logs": handlers["logs_handler"],
        "logsfull": handlers["logsfull_handler"],
        "tail": handlers["tail_handler"],
        "fuse": handlers["fuse_handler"],
        "audit": handlers["audit_handler"],
        "comments": handlers["comments_handler"],
        "wfstate": handlers["wfstate_handler"],
        "visualize": handlers["visualize_handler"],
        "watch": handlers["watch_handler"],
        "reprocess": handlers["reprocess_handler"],
        "reconcile": handlers["reconcile_handler"],
        "continue": handlers["continue_handler"],
        "forget": handlers["forget_handler"],
        "respond": handlers["respond_handler"],
        "kill": handlers["kill_handler"],
        "assign": handlers["assign_handler"],
        "implement": handlers["implement_handler"],
        "prepare": handlers["prepare_handler"],
        "plan": handlers["plan_handler"],
        "pause": handlers["pause_handler"],
        "resume": handlers["resume_handler"],
        "stop": handlers["stop_handler"],
        "track": handlers["track_handler"],
        "tracked": handlers["tracked_handler"],
        "untrack": handlers["untrack_handler"],
        "agents": handlers["agents_handler"],
        "feature_done": handlers["feature_done_handler"],
        "feature_list": handlers["feature_list_handler"],
        "feature_forget": handlers["feature_forget_handler"],
    }
