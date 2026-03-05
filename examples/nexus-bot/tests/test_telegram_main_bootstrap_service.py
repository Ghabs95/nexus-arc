from nexus.core.telegram import telegram_main_bootstrap_service as svc


def test_build_command_handler_map_includes_plan():
    async def _dummy(*_args, **_kwargs):
        return None

    handlers = {
        "status_handler": _dummy,
        "active_handler": _dummy,
        "inboxq_handler": _dummy,
        "stats_handler": _dummy,
        "logs_handler": _dummy,
        "logsfull_handler": _dummy,
        "tail_handler": _dummy,
        "fuse_handler": _dummy,
        "audit_handler": _dummy,
        "comments_handler": _dummy,
        "wfstate_handler": _dummy,
        "visualize_handler": _dummy,
        "watch_handler": _dummy,
        "reprocess_handler": _dummy,
        "reconcile_handler": _dummy,
        "continue_handler": _dummy,
        "forget_handler": _dummy,
        "respond_handler": _dummy,
        "kill_handler": _dummy,
        "assign_handler": _dummy,
        "implement_handler": _dummy,
        "prepare_handler": _dummy,
        "plan_handler": _dummy,
        "pause_handler": _dummy,
        "resume_handler": _dummy,
        "stop_handler": _dummy,
        "track_handler": _dummy,
        "tracked_handler": _dummy,
        "untrack_handler": _dummy,
        "agents_handler": _dummy,
        "feature_done_handler": _dummy,
        "feature_list_handler": _dummy,
        "feature_forget_handler": _dummy,
    }
    command_map = svc.build_command_handler_map(**handlers)
    assert command_map["plan"] is _dummy


def test_register_application_handlers_registers_plan_command(monkeypatch):
    async def _dummy(*_args, **_kwargs):
        return None

    class _CaptureCommandHandler:
        def __init__(self, command, callback):
            self.command = command
            self.callback = callback

    class _CaptureCallbackQueryHandler:
        def __init__(self, callback, pattern=None):
            self.callback = callback
            self.pattern = pattern

    class _CaptureMessageHandler:
        def __init__(self, filters, callback):
            self.filters = filters
            self.callback = callback

    class _App:
        def __init__(self):
            self.handlers = []
            self.error_handler = None

        def add_handler(self, handler):
            self.handlers.append(handler)

        def add_error_handler(self, handler):
            self.error_handler = handler

    class _Handlers(dict):
        def __missing__(self, _key):
            return _dummy

    monkeypatch.setattr(svc, "CommandHandler", _CaptureCommandHandler)
    monkeypatch.setattr(svc, "CallbackQueryHandler", _CaptureCallbackQueryHandler)
    monkeypatch.setattr(svc, "MessageHandler", _CaptureMessageHandler)

    app = _App()
    handlers = _Handlers()
    svc.register_application_handlers(
        app=app,
        conv_handler=object(),
        handlers=handlers,
        filters_module=type("F", (), {"TEXT": 1, "VOICE": 2, "COMMAND": 4})(),
    )

    commands = [h.command for h in app.handlers if isinstance(h, _CaptureCommandHandler)]
    assert "plan" in commands
