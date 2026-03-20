"""Console entrypoint for running the Nexus command bridge."""

from __future__ import annotations

from nexus.core.command_bridge import (
    CommandBridgeConfig,
    CommandRouter,
    run_command_bridge_server,
)
from nexus.core.config import (
    NEXUS_COMMAND_BRIDGE_ALLOWED_SENDER_IDS,
    NEXUS_COMMAND_BRIDGE_ALLOWED_SOURCES,
    NEXUS_COMMAND_BRIDGE_AUTH_TOKEN,
    NEXUS_COMMAND_BRIDGE_HOST,
    NEXUS_COMMAND_BRIDGE_PORT,
)


def run_command_bridge(
    *,
    host: str = NEXUS_COMMAND_BRIDGE_HOST,
    port: int = NEXUS_COMMAND_BRIDGE_PORT,
    auth_token: str = NEXUS_COMMAND_BRIDGE_AUTH_TOKEN,
) -> None:
    """Start the command bridge using current config defaults."""
    router = CommandRouter(allowed_user_ids=[])
    run_command_bridge_server(
        router,
        config=CommandBridgeConfig(
            host=host,
            port=port,
            auth_token=auth_token,
            allowed_sources=NEXUS_COMMAND_BRIDGE_ALLOWED_SOURCES,
            allowed_sender_ids=NEXUS_COMMAND_BRIDGE_ALLOWED_SENDER_IDS,
        ),
    )


def main() -> None:
    """Console-script entrypoint for ``nexus-bridge``."""
    run_command_bridge()
