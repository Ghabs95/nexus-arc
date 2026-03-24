"""Framework command bridge for external command surfaces like OpenClaw."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS: dict[str, tuple[str, str]] = {
    "BridgeAuditPayload": ("nexus.core.command_bridge.schemas", "BridgeAuditPayload"),
    "BridgeClientPayload": ("nexus.core.command_bridge.schemas", "BridgeClientPayload"),
    "BridgeCommandResultPayload": (
        "nexus.core.command_bridge.schemas",
        "BridgeCommandResultPayload",
    ),
    "BridgeExecuteRequestPayload": (
        "nexus.core.command_bridge.schemas",
        "BridgeExecuteRequestPayload",
    ),
    "BridgeRequesterPayload": ("nexus.core.command_bridge.schemas", "BridgeRequesterPayload"),
    "BridgeSessionContextPayload": (
        "nexus.core.command_bridge.schemas",
        "BridgeSessionContextPayload",
    ),
    "BridgeUiFieldPayload": ("nexus.core.command_bridge.schemas", "BridgeUiFieldPayload"),
    "BridgeUiPayload": ("nexus.core.command_bridge.schemas", "BridgeUiPayload"),
    "BridgeUsagePayload": ("nexus.core.command_bridge.schemas", "BridgeUsagePayload"),
    "BridgeWorkflowPayload": ("nexus.core.command_bridge.schemas", "BridgeWorkflowPayload"),
    "CommandBridgeConfig": ("nexus.core.command_bridge.http", "CommandBridgeConfig"),
    "CommandRequest": ("nexus.core.command_bridge.models", "CommandRequest"),
    "CommandResult": ("nexus.core.command_bridge.models", "CommandResult"),
    "CommandRouter": ("nexus.core.command_bridge.router", "CommandRouter"),
    "RequesterContext": ("nexus.core.command_bridge.models", "RequesterContext"),
    "create_command_bridge_app": ("nexus.core.command_bridge.http", "create_command_bridge_app"),
    "run_command_bridge_server": ("nexus.core.command_bridge.http", "run_command_bridge_server"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'nexus.core.command_bridge' has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value

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
