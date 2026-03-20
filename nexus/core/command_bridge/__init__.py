"""Framework command bridge for external command surfaces like OpenClaw."""

from nexus.core.command_bridge.http import (
    CommandBridgeConfig,
    create_command_bridge_app,
    run_command_bridge_server,
)
from nexus.core.command_bridge.models import CommandRequest, CommandResult, RequesterContext
from nexus.core.command_bridge.router import CommandRouter
from nexus.core.command_bridge.schemas import (
    BridgeAuditPayload,
    BridgeClientPayload,
    BridgeCommandResultPayload,
    BridgeExecuteRequestPayload,
    BridgeRequesterPayload,
    BridgeSessionContextPayload,
    BridgeUiFieldPayload,
    BridgeUiPayload,
    BridgeUsagePayload,
    BridgeWorkflowPayload,
)

__all__ = [
    "BridgeAuditPayload",
    "BridgeClientPayload",
    "BridgeCommandResultPayload",
    "BridgeExecuteRequestPayload",
    "BridgeRequesterPayload",
    "BridgeSessionContextPayload",
    "BridgeUiFieldPayload",
    "BridgeUiPayload",
    "BridgeUsagePayload",
    "BridgeWorkflowPayload",
    "CommandBridgeConfig",
    "CommandRequest",
    "CommandResult",
    "CommandRouter",
    "RequesterContext",
    "create_command_bridge_app",
    "run_command_bridge_server",
]
