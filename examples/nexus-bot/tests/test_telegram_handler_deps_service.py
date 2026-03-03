from services.telegram.telegram_handler_deps_service import build_callback_action_handlers


def test_build_callback_action_handlers_includes_plan():
    async def _dummy(*_args, **_kwargs):
        return None

    handlers = build_callback_action_handlers(
        ctx_call_telegram_handler=_dummy,
        logs_handler=_dummy,
        logsfull_handler=_dummy,
        status_handler=_dummy,
        pause_handler=_dummy,
        resume_handler=_dummy,
        stop_handler=_dummy,
        audit_handler=_dummy,
        active_handler=_dummy,
        reprocess_handler=_dummy,
        plan_handler=_dummy,
    )

    assert "plan" in handlers
