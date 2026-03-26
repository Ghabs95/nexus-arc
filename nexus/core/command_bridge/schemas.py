"""Typed schema contracts for the Nexus command bridge."""

from __future__ import annotations

from typing import Any, TypedDict


class BridgeRequesterPayload(TypedDict, total=False):
    """Normalized caller identity forwarded by a bridge client."""

    source_platform: str
    operator_id: str
    sender_id: str
    sender_name: str
    channel_id: str
    channel_name: str
    session_id: str
    is_authorized_sender: bool | None
    roles: list[str]
    access_groups: list[str]
    metadata: dict[str, Any]


class BridgeSessionContextPayload(TypedDict, total=False):
    """Session-affinity hints supplied by the caller."""

    current_project: str | None
    current_workflow_id: str | None
    current_issue_ref: str | None
    metadata: dict[str, Any]


class BridgeClientPayload(TypedDict, total=False):
    """Bridge client metadata used for compatibility and rendering."""

    plugin_version: str
    render_mode: str
    metadata: dict[str, Any]


class BridgeExecuteRequestPayload(TypedDict, total=False):
    """Execute-command payload accepted by the bridge."""

    command: str
    args: list[str]
    raw_text: str
    requester: BridgeRequesterPayload
    context: BridgeSessionContextPayload
    client: BridgeClientPayload
    attachments: list[Any]
    correlation_id: str


class BridgeRouteRequestPayload(TypedDict, total=False):
    """Freeform route payload accepted by the bridge."""

    raw_text: str
    args: list[str]
    requester: BridgeRequesterPayload
    context: BridgeSessionContextPayload
    client: BridgeClientPayload
    attachments: list[Any]
    correlation_id: str


class BridgeWorkflowPayload(TypedDict, total=False):
    """Structured workflow reference returned by the bridge."""

    id: str | None
    issue_number: str | None
    project_key: str | None
    state: str | None


class BridgeUiFieldPayload(TypedDict):
    """Single UI field returned by the bridge."""

    label: str
    value: str


class BridgeUiPayload(TypedDict, total=False):
    """Structured UI rendering hints returned by the bridge."""

    title: str
    summary: str
    fields: list[BridgeUiFieldPayload]
    actions: list[str]
    metadata: dict[str, Any]


class BridgeUsagePayload(TypedDict, total=False):
    """Usage or cost metadata returned by the bridge."""

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    metadata: dict[str, Any]


class BridgeAuditPayload(TypedDict, total=False):
    """Audit metadata returned by the bridge."""

    request_id: str
    actor: str
    session_id: str
    metadata: dict[str, Any]


class BridgeCommandResultPayload(TypedDict, total=False):
    """Structured command response returned by the bridge."""

    status: str
    message: str
    workflow_id: str | None
    issue_number: str | None
    project_key: str | None
    workflow: BridgeWorkflowPayload
    ui: BridgeUiPayload
    usage: BridgeUsagePayload
    audit: BridgeAuditPayload
    data: dict[str, Any]
    suggested_next_commands: list[str]


class BridgeReplyRequestPayload(TypedDict, total=False):
    """Inbound reply payload sent by an OpenClaw plugin to Nexus."""

    correlation_id: str
    content: str
    sender_id: str
    session_id: str
    status: str
    metadata: dict[str, Any]
