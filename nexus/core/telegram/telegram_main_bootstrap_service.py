import os
from collections.abc import Mapping
from inspect import isawaitable
from typing import Any

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from nexus.core.command_visibility import filter_visible_commands
from nexus.core.storage.capabilities import get_storage_capabilities


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
            from nexus.core.orchestration.nexus_core_helpers import setup_event_handlers

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
    authorize_update=None,
) -> None:
    async def _is_allowed(update, *, command: str | None = None, action: str = "execute") -> bool:
        if not callable(authorize_update):
            return True
        decision = authorize_update(update=update, command=command, action=action)
        if isawaitable(decision):
            decision = await decision
        allowed = False
        message = ""
        if isinstance(decision, tuple):
            allowed = bool(decision[0]) if len(decision) >= 1 else False
            message = str(decision[1] or "") if len(decision) >= 2 else ""
        else:
            allowed = bool(decision)
        if allowed:
            return True
        effective_message = getattr(update, "effective_message", None)
        if message and effective_message is not None:
            try:
                await effective_message.reply_text(message)
            except Exception:
                pass
        return False

    def _wrap(handler, *, command: str | None = None, action: str = "execute"):
        async def wrapped(update, context):
            if not await _is_allowed(update, command=command, action=action):
                return
            return await handler(update, context)

        return wrapped

    local_task_files = get_storage_capabilities().local_task_files
    app.add_handler(conv_handler)
    command_specs = [
        ("start", "start_handler", "onboarding"),
        ("help", "help_handler", "help"),
        ("menu", "menu_handler", "onboarding"),
        ("login", "login_handler", "onboarding"),
        ("setup_status", "setup_status_handler", "readonly"),
        ("whoami", "whoami_handler", "readonly"),
        ("rename", "rename_handler", "execute"),
        ("cancel", "cancel", "execute"),
        ("status", "status_handler", "execute"),
        ("inboxq", "inboxq_handler", "execute"),
        ("active", "active_handler", "execute"),
        ("progress", "progress_handler", "execute"),
        ("track", "track_handler", "execute"),
        ("tracked", "tracked_handler", "execute"),
        ("untrack", "untrack_handler", "execute"),
        ("myissues", "myissues_handler", "execute"),
        ("logs", "logs_handler", "execute"),
        ("logsfull", "logsfull_handler", "execute"),
        ("tail", "tail_handler", "execute"),
        ("tailstop", "tailstop_handler", "execute"),
        ("fuse", "fuse_handler", "execute"),
        ("audit", "audit_handler", "execute"),
        ("wfstate", "wfstate_handler", "execute"),
        ("visualize", "visualize_handler", "execute"),
        ("watch", "watch_handler", "execute"),
        ("stats", "stats_handler", "execute"),
        ("comments", "comments_handler", "execute"),
        ("reprocess", "reprocess_handler", "execute"),
        ("reconcile", "reconcile_handler", "execute"),
        ("continue", "continue_handler", "execute"),
        ("forget", "forget_handler", "execute"),
        ("kill", "kill_handler", "execute"),
        ("pause", "pause_handler", "execute"),
        ("resume", "resume_handler", "execute"),
        ("stop", "stop_handler", "execute"),
        ("agents", "agents_handler", "execute"),
        ("direct", "direct_handler", "execute"),
        ("respond", "respond_handler", "execute"),
        ("assign", "assign_handler", "execute"),
        ("implement", "implement_handler", "execute"),
        ("prepare", "prepare_handler", "execute"),
        ("plan", "plan_handler", "execute"),
        ("feature_done", "feature_done_handler", "execute"),
        ("feature_list", "feature_list_handler", "execute"),
        ("feature_forget", "feature_forget_handler", "execute"),
        ("chat", "chat_menu_handler", "execute"),
        ("chatagents", "chat_agents_handler", "execute"),
    ]
    visible_commands = set(
        filter_visible_commands((cmd for cmd, _handler_name, _action in command_specs), local_task_files=local_task_files)
    )
    for cmd, handler_name, action in command_specs:
        if cmd not in visible_commands:
            continue
        app.add_handler(CommandHandler(cmd, _wrap(handlers[handler_name], command=cmd, action=action)))

    app.add_handler(CallbackQueryHandler(_wrap(handlers["chat_callback_handler"]), pattern=r"^chat:"))
    app.add_handler(CallbackQueryHandler(_wrap(handlers["menu_callback_handler"]), pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(_wrap(handlers["project_picker_handler"]), pattern=r"^pickcmd:"))
    app.add_handler(CallbackQueryHandler(_wrap(handlers["issue_picker_handler"]), pattern=r"^pickissue"))
    app.add_handler(
        CallbackQueryHandler(_wrap(handlers["monitor_project_picker_handler"]), pattern=r"^pickmonitor:")
    )
    app.add_handler(CallbackQueryHandler(_wrap(handlers["close_flow_handler"]), pattern=r"^flow:close$"))
    app.add_handler(CallbackQueryHandler(_wrap(handlers["feature_callback_handler"]), pattern=r"^feat:"))
    app.add_handler(
        CallbackQueryHandler(
            _wrap(handlers["task_confirmation_callback_handler"]),
            pattern=r"^taskconfirm:",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            _wrap(handlers["inline_keyboard_handler"]),
            pattern=r"^(logs|logsfull|status|pause|resume|stop|audit|reprocess|respond|approve|reject|wfapprove|wfdeny|report_bug)_",
        )
    )
    app.add_handler(
        MessageHandler(
            (filters_module.TEXT | filters_module.VOICE) & (~filters_module.COMMAND),
            _wrap(handlers["hands_free_handler"]),
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
