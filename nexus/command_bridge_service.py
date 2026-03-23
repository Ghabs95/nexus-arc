"""Console entrypoint for running the Nexus command bridge."""

from __future__ import annotations

def run_command_bridge(
    *,
    host: str | None = None,
    port: int | None = None,
    auth_token: str | None = None,
) -> None:
    """Start the command bridge using current config defaults."""
    from nexus.core.command_bridge import (
        CommandBridgeConfig,
        CommandRouter,
        run_command_bridge_server,
    )
    from nexus.core.config import (
        NEXUS_AUTH_AUTHORITY,
        NEXUS_COMMAND_BRIDGE_ALLOWED_SENDER_IDS,
        NEXUS_COMMAND_BRIDGE_ALLOWED_SOURCES,
        NEXUS_COMMAND_BRIDGE_AUTH_TOKEN,
        NEXUS_COMMAND_BRIDGE_HOST,
        NEXUS_COMMAND_BRIDGE_PORT,
        NEXUS_RUNTIME_MODE,
    )
    from nexus.core.config.runtime import bridge_requires_authorized_sender

    router = CommandRouter(allowed_user_ids=[])
    run_command_bridge_server(
        router,
        config=CommandBridgeConfig(
            host=host or NEXUS_COMMAND_BRIDGE_HOST,
            port=port or NEXUS_COMMAND_BRIDGE_PORT,
            auth_token=auth_token if auth_token is not None else NEXUS_COMMAND_BRIDGE_AUTH_TOKEN,
            allowed_sources=NEXUS_COMMAND_BRIDGE_ALLOWED_SOURCES,
            allowed_sender_ids=NEXUS_COMMAND_BRIDGE_ALLOWED_SENDER_IDS,
            require_authorized_sender=bridge_requires_authorized_sender(
                NEXUS_AUTH_AUTHORITY,
                NEXUS_RUNTIME_MODE,
            ),
        ),
    )


def main() -> None:
    """Console-script entrypoint for ``nexus-bridge``."""
    run_command_bridge()
