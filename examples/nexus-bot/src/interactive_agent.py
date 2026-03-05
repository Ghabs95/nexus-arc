import asyncio
import logging
import signal
from typing import Any, Awaitable, Callable

from nexus.adapters.notifications.interactive import InteractiveClientPlugin
from nexus.adapters.registry import AdapterRegistry
from nexus.core.config.bootstrap import initialize_runtime
from nexus.core.interactive.context import InteractiveContext

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO, force=True
)
logger = logging.getLogger(__name__)

initialize_runtime(configure_logging=False)

from nexus.core.config import ORCHESTRATOR_CONFIG, PROJECT_CONFIG

# Centralized dependency injection factories
from dependencies import (
    get_hands_free_routing_handler_deps,
    get_issue_handler_deps,
    get_monitoring_handler_deps,
    get_ops_handler_deps,
    get_workflow_handler_deps,
)
from nexus.core.handlers.chat_command_handlers import (
    chat_agents_handler,
    chat_menu_handler,
)
from nexus.core.handlers.issue_command_handlers import (
    assign_handler,
    comments_handler,
    implement_handler,
    myissues_handler,
    plan_handler,
    prepare_handler,
    respond_handler,
    track_handler,
    tracked_handler,
    untrack_handler,
)
from nexus.core.handlers.monitoring_command_handlers import (
    active_handler,
    fuse_handler,
    logs_handler,
    logsfull_handler,
    status_handler,
    tail_handler,
    tailstop_handler,
)
from nexus.core.handlers.ops_command_handlers import (
    agents_handler,
    audit_handler,
    direct_handler,
    stats_handler,
)
from nexus.core.handlers.workflow_command_handlers import (
    continue_handler,
    forget_handler,
    kill_handler,
    pause_handler,
    reconcile_handler,
    reprocess_handler,
    resume_handler,
    stop_handler,
    wfstate_handler,
)
from nexus.core.orchestration.ai_orchestrator import get_orchestrator


async def main() -> None:
    """Initialize and run all configured interactive client plugins."""
    # 1. Initialize core services
    get_orchestrator(ORCHESTRATOR_CONFIG)
    registry = AdapterRegistry()

    # 2. Extract client configs from PROJECT_CONFIG
    client_configs: dict[str, Any] = PROJECT_CONFIG.get("interactive_clients", {})
    if not client_configs:
        logger.error("No interactive_clients configured in project_config.yaml")
        return

    # 3. Load plugins via registry
    plugins: dict[str, InteractiveClientPlugin] = {}
    for client_id, config in client_configs.items():
        if not config.get("enabled", True):
            continue

        plugin_type = config.get("plugin")
        if not plugin_type:
            logger.warning(f"Client {client_id} missing 'plugin' type.")
            continue

        try:
            logger.info(f"Loading interactive plugin: {plugin_type} for {client_id}")
            # The registry builds and configures the plugin
            plugin = registry.create_interactive(plugin_type, config=config)
            if plugin:
                plugins[client_id] = plugin
            else:
                logger.error(f"Failed to load plugin {plugin_type} for {client_id}")
        except Exception as e:
            logger.exception(f"Error loading client {client_id}: {e}")

    if not plugins:
        logger.error("No interactive plugins successfully loaded. Exiting.")
        return

    # 4. Bind unified handlers to all loaded plugins
    for client_id, plugin in plugins.items():
        logger.info(f"Binding handlers for client: {client_id}")
        _bind_handlers(plugin)

    # 5. Handle shutdown elegantly
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler(_signum: int, _frame: Any) -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _signal_handler)

    # 6. Start all plugins concurrently
    logger.info(f"Starting {len(plugins)} interactive plugins...")

    start_tasks = []
    for client_id, plugin in plugins.items():
        # Depending on the plugin, this might block (like telegram long-polling)
        # or it might just setup background tasks.
        start_tasks.append(asyncio.create_task(plugin.start()))
    stop_task = asyncio.create_task(stop_event.wait())

    try:
        # Wait until we get a stop signal or one of the bots hard-crashes
        done, pending = await asyncio.wait(
            {stop_task, *start_tasks}, return_when=asyncio.FIRST_COMPLETED
        )
        logger.info("Main loop terminated. Stopping plugins...")
        for task in pending:
            task.cancel()
        for task in done:
            if task is stop_task:
                continue
            if task.cancelled():
                continue
            if exc := task.exception():
                logger.error("Interactive plugin task exited with error: %s", exc)
    finally:
        # 7. Teardown
        for client_id, plugin in plugins.items():
            try:
                await plugin.stop()
            except Exception as e:
                logger.error(f"Error stopping plugin {client_id}: {e}")


def _bind_handlers(plugin: InteractiveClientPlugin) -> None:
    """Bind all platform-agnostic handlers to a specific plugin instance."""

    # Resolve dependencies once per plugin
    workflow_deps = get_workflow_handler_deps()
    monitoring_deps = get_monitoring_handler_deps()
    issue_deps = get_issue_handler_deps()
    ops_deps = get_ops_handler_deps()
    routing_deps = get_hands_free_routing_handler_deps()

    def _build_ctx(
        *,
        user_id: str,
        text: str,
        args: list[str] | None,
        raw_event: Any,
    ) -> InteractiveContext:
        return InteractiveContext(
            client=plugin,
            user_id=str(user_id),
            text=str(text or ""),
            args=list(args or []),
            raw_event=raw_event,
            user_state={},
        )

    def _wrap_command_handler(
        handler: Callable[..., Awaitable[None]],
        deps: Any | None = None,
    ) -> Callable[..., Awaitable[None]]:
        async def _callback(
            *,
            user_id: str,
            text: str,
            context: list[str] | None = None,
            raw_event: Any = None,
            **_kwargs: Any,
        ) -> None:
            ctx = _build_ctx(user_id=user_id, text=text, args=context, raw_event=raw_event)
            if deps is None:
                await handler(ctx)
                return
            await handler(ctx, deps)

        return _callback

    # Chat Commands
    plugin.register_command("chat", _wrap_command_handler(chat_menu_handler))
    plugin.register_command("chatagents", _wrap_command_handler(chat_agents_handler))

    # Issue Commands
    plugin.register_command("assign", _wrap_command_handler(assign_handler, issue_deps))
    plugin.register_command("comments", _wrap_command_handler(comments_handler, issue_deps))
    plugin.register_command("implement", _wrap_command_handler(implement_handler, issue_deps))
    plugin.register_command("myissues", _wrap_command_handler(myissues_handler, issue_deps))
    plugin.register_command("plan", _wrap_command_handler(plan_handler, issue_deps))
    plugin.register_command("prepare", _wrap_command_handler(prepare_handler, issue_deps))
    plugin.register_command("respond", _wrap_command_handler(respond_handler, issue_deps))
    plugin.register_command("track", _wrap_command_handler(track_handler, issue_deps))
    plugin.register_command("tracked", _wrap_command_handler(tracked_handler, issue_deps))
    plugin.register_command("untrack", _wrap_command_handler(untrack_handler, issue_deps))

    # Monitoring Commands
    plugin.register_command("active", _wrap_command_handler(active_handler, monitoring_deps))
    plugin.register_command("fuse", _wrap_command_handler(fuse_handler, monitoring_deps))
    plugin.register_command("logs", _wrap_command_handler(logs_handler, monitoring_deps))
    plugin.register_command("logsfull", _wrap_command_handler(logsfull_handler, monitoring_deps))
    plugin.register_command("status", _wrap_command_handler(status_handler, monitoring_deps))
    plugin.register_command("tail", _wrap_command_handler(tail_handler, monitoring_deps))
    plugin.register_command("tailstop", _wrap_command_handler(tailstop_handler, monitoring_deps))

    # Ops Commands
    plugin.register_command("agents", _wrap_command_handler(agents_handler, ops_deps))
    plugin.register_command("audit", _wrap_command_handler(audit_handler, ops_deps))
    plugin.register_command("direct", _wrap_command_handler(direct_handler, ops_deps))
    plugin.register_command("stats", _wrap_command_handler(stats_handler, ops_deps))

    # Workflow Control Commands
    plugin.register_command("continue", _wrap_command_handler(continue_handler, workflow_deps))
    plugin.register_command("forget", _wrap_command_handler(forget_handler, workflow_deps))
    plugin.register_command("kill", _wrap_command_handler(kill_handler, workflow_deps))
    plugin.register_command("pause", _wrap_command_handler(pause_handler, workflow_deps))
    plugin.register_command("reconcile", _wrap_command_handler(reconcile_handler, workflow_deps))
    plugin.register_command("reprocess", _wrap_command_handler(reprocess_handler, workflow_deps))
    plugin.register_command("resume", _wrap_command_handler(resume_handler, workflow_deps))
    plugin.register_command("stop", _wrap_command_handler(stop_handler, workflow_deps))
    plugin.register_command("wfstate", _wrap_command_handler(wfstate_handler, workflow_deps))

    # Catch-all text message handler
    from nexus.core.handlers.inbox_routing_handler import route_hands_free_text

    async def _message_handler(
        *,
        user_id: str,
        text: str,
        raw_event: Any = None,
        **_kwargs: Any,
    ) -> None:
        ctx = _build_ctx(user_id=user_id, text=text, args=[], raw_event=raw_event)
        await route_hands_free_text(ctx, routing_deps)

    plugin.register_message_handler(_message_handler)


if __name__ == "__main__":
    asyncio.run(main())
