import asyncio
import logging
import os
import signal
import sys
from typing import Any

from nexus.adapters.notifications.interactive import InteractiveClientPlugin
from nexus.adapters.registry import AdapterRegistry

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO, force=True
)
logger = logging.getLogger(__name__)

# Add src to path for local imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import ORCHESTRATOR_CONFIG, PROJECT_CONFIG

# Centralized dependency injection factories
from dependencies import (
    get_callback_handler_deps,
    get_feature_ideation_handler_deps,
    get_hands_free_routing_handler_deps,
    get_issue_handler_deps,
    get_monitoring_handler_deps,
    get_ops_handler_deps,
    get_workflow_handler_deps,
)
from nexus.core.handlers.chat_command_handlers import (
    chat_agents_handler,
    chat_callback_handler,
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
    registry = AdapterRegistry.get_instance()

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
            plugin = registry.get_interactive_client(plugin_type, config)
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

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # 6. Start all plugins concurrently
    logger.info(f"Starting {len(plugins)} interactive plugins...")

    start_tasks = []
    for client_id, plugin in plugins.items():
        # Depending on the plugin, this might block (like telegram long-polling)
        # or it might just setup background tasks.
        start_tasks.append(asyncio.create_task(plugin.start()))

    try:
        # Wait until we get a stop signal or one of the bots hard-crashes
        done, pending = await asyncio.wait(
            [stop_event.wait()] + start_tasks, return_when=asyncio.FIRST_COMPLETED
        )
        logger.info("Main loop terminated. Stopping plugins...")
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
    feature_deps = get_feature_ideation_handler_deps()
    routing_deps = get_hands_free_routing_handler_deps()
    callback_deps = get_callback_handler_deps()

    # Chat Commands
    plugin.register_command_handler("chat", lambda ctx: chat_menu_handler(ctx, ops_deps))
    plugin.register_command_handler("chatagents", lambda ctx: chat_agents_handler(ctx, ops_deps))

    # Issue Commands
    plugin.register_command_handler("assign", lambda ctx: assign_handler(ctx, issue_deps))
    plugin.register_command_handler("comments", lambda ctx: comments_handler(ctx, issue_deps))
    plugin.register_command_handler("implement", lambda ctx: implement_handler(ctx, issue_deps))
    plugin.register_command_handler("myissues", lambda ctx: myissues_handler(ctx, issue_deps))
    plugin.register_command_handler("plan", lambda ctx: plan_handler(ctx, issue_deps))
    plugin.register_command_handler("prepare", lambda ctx: prepare_handler(ctx, issue_deps))
    plugin.register_command_handler("respond", lambda ctx: respond_handler(ctx, issue_deps))
    plugin.register_command_handler("track", lambda ctx: track_handler(ctx, issue_deps))
    plugin.register_command_handler("tracked", lambda ctx: tracked_handler(ctx, issue_deps))
    plugin.register_command_handler("untrack", lambda ctx: untrack_handler(ctx, issue_deps))

    # Monitoring Commands
    plugin.register_command_handler("active", lambda ctx: active_handler(ctx, monitoring_deps))
    plugin.register_command_handler("fuse", lambda ctx: fuse_handler(ctx, monitoring_deps))
    plugin.register_command_handler("logs", lambda ctx: logs_handler(ctx, monitoring_deps))
    plugin.register_command_handler("logsfull", lambda ctx: logsfull_handler(ctx, monitoring_deps))
    plugin.register_command_handler("status", lambda ctx: status_handler(ctx, monitoring_deps))
    plugin.register_command_handler("tail", lambda ctx: tail_handler(ctx, monitoring_deps))
    plugin.register_command_handler("tailstop", lambda ctx: tailstop_handler(ctx, monitoring_deps))

    # Ops Commands
    plugin.register_command_handler("agents", lambda ctx: agents_handler(ctx, ops_deps))
    plugin.register_command_handler("audit", lambda ctx: audit_handler(ctx, ops_deps))
    plugin.register_command_handler("direct", lambda ctx: direct_handler(ctx, ops_deps))
    plugin.register_command_handler("stats", lambda ctx: stats_handler(ctx, ops_deps))

    # Workflow Control Commands
    plugin.register_command_handler("continue", lambda ctx: continue_handler(ctx, workflow_deps))
    plugin.register_command_handler("forget", lambda ctx: forget_handler(ctx, workflow_deps))
    plugin.register_command_handler("kill", lambda ctx: kill_handler(ctx, workflow_deps))
    plugin.register_command_handler("pause", lambda ctx: pause_handler(ctx, workflow_deps))
    plugin.register_command_handler("reconcile", lambda ctx: reconcile_handler(ctx, workflow_deps))
    plugin.register_command_handler("reprocess", lambda ctx: reprocess_handler(ctx, workflow_deps))
    plugin.register_command_handler("resume", lambda ctx: resume_handler(ctx, workflow_deps))
    plugin.register_command_handler("stop", lambda ctx: stop_handler(ctx, workflow_deps))
    plugin.register_command_handler("wfstate", lambda ctx: wfstate_handler(ctx, workflow_deps))

    # Catch-all text message handler
    from nexus.core.handlers.inbox_routing_handler import route_hands_free_text

    plugin.register_message_handler(lambda ctx: route_hands_free_text(ctx, routing_deps))

    # Core callback query dispatcher
    plugin.register_callback_handler(
        lambda ctx: dispatch_callback(ctx, callback_deps, chat_callback_handler, feature_deps)
    )


async def dispatch_callback(ctx, callback_deps, chat_handler, feature_deps) -> None:
    """Route unified callback queries to specific handler modules."""
    data = ctx.query.data

    if data.startswith("chat:"):
        await chat_handler(ctx, callback_deps)
    elif data.startswith("feat:"):
        from nexus.core.handlers.feature_ideation_handlers import feature_callback_handler

        await feature_callback_handler(ctx, feature_deps)
    elif (
        data.startswith("pickcmd:")
        or data.startswith("pickissue:")
        or data.startswith("flow:close")
    ):
        from nexus.core.handlers.callback_command_handlers import core_callback_router

        await core_callback_router(ctx, callback_deps)
    else:
        # Let's see if it's an action (logs_, respond_, etc)
        from nexus.core.handlers.callback_command_handlers import inline_keyboard_handler

        await inline_keyboard_handler(ctx, callback_deps)


if __name__ == "__main__":
    asyncio.run(main())
