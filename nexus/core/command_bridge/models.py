"""Typed request and response models for the Nexus command bridge."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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


@dataclass
class RequesterContext:
    """Metadata about the caller that originated a command."""

    source_platform: str = "openclaw"
    nexus_id: str = ""
    auth_authority: str = ""
    operator_id: str = ""
    sender_id: str = ""
    sender_name: str = ""
    channel_id: str = ""
    channel_name: str = ""
    session_id: str = ""
    is_authorized_sender: bool | None = None
    roles: list[str] = field(default_factory=list)
    access_groups: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "RequesterContext":
        data = payload if isinstance(payload, dict) else {}
        return cls(
            source_platform=str(data.get("source_platform", "openclaw") or "openclaw"),
            nexus_id=str(data.get("nexus_id", "") or ""),
            auth_authority=str(data.get("auth_authority", "") or ""),
            operator_id=str(data.get("operator_id", "") or ""),
            sender_id=str(data.get("sender_id", "") or ""),
            sender_name=str(data.get("sender_name", "") or ""),
            channel_id=str(data.get("channel_id", "") or ""),
            channel_name=str(data.get("channel_name", "") or ""),
            session_id=str(data.get("session_id", "") or ""),
            is_authorized_sender=(
                bool(data["is_authorized_sender"])
                if "is_authorized_sender" in data and data.get("is_authorized_sender") is not None
                else None
            ),
            roles=_string_list(data.get("roles")),
            access_groups=_string_list(data.get("access_groups")),
            metadata=dict(data.get("metadata", {}) or {}),
        )

    def to_dict(self) -> BridgeRequesterPayload:
        return {
            "source_platform": self.source_platform,
            "nexus_id": self.nexus_id,
            "auth_authority": self.auth_authority,
            "operator_id": self.operator_id,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "session_id": self.session_id,
            "is_authorized_sender": self.is_authorized_sender,
            "roles": list(self.roles or []),
            "access_groups": list(self.access_groups or []),
            "metadata": dict(self.metadata or {}),
        }

    def to_audit_context(self) -> dict[str, Any]:
        payload = {
            "platform": self.source_platform,
            "nexus_id": self.nexus_id,
            "auth_authority": self.auth_authority,
            "operator_id": self.operator_id,
            "platform_user_id": self.sender_id,
            "platform_user_name": self.sender_name,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "session_id": self.session_id,
            "authorized_sender": self.is_authorized_sender,
            "roles": list(self.roles or []),
            "access_groups": list(self.access_groups or []),
        }
        if self.metadata:
            payload["source_metadata"] = dict(self.metadata)
        return payload

    def to_requester_context(self) -> dict[str, Any]:
        payload = {
            "platform": self.source_platform,
            "platform_user_id": self.sender_id,
        }
        nexus_id = str(self.nexus_id or "").strip()
        if nexus_id:
            payload["nexus_id"] = nexus_id
        auth_authority = str(self.auth_authority or "").strip().lower()
        if auth_authority:
            payload["auth_authority"] = auth_authority
        session_id = str(self.session_id or "").strip()
        if session_id:
            payload["session_id"] = session_id
        if self.roles:
            payload["roles"] = list(self.roles)
        if self.access_groups:
            payload["access_groups"] = list(self.access_groups)
        return payload


@dataclass
class SessionContext:
    """Session-affinity hints supplied by the bridge client."""

    current_project: str | None = None
    current_workflow_id: str | None = None
    current_issue_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "SessionContext":
        data = payload if isinstance(payload, dict) else {}
        metadata = dict(data.get("metadata", {}) or {})
        return cls(
            current_project=_string_or_none(data.get("current_project")),
            current_workflow_id=_string_or_none(data.get("current_workflow_id")),
            current_issue_ref=_string_or_none(data.get("current_issue_ref")),
            metadata=metadata,
        )

    def to_dict(self) -> BridgeSessionContextPayload:
        return {
            "current_project": self.current_project,
            "current_workflow_id": self.current_workflow_id,
            "current_issue_ref": self.current_issue_ref,
            "metadata": dict(self.metadata or {}),
        }


@dataclass
class ClientContext:
    """Bridge client metadata used for compatibility and rendering."""

    plugin_version: str = ""
    render_mode: str = "text"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ClientContext":
        data = payload if isinstance(payload, dict) else {}
        return cls(
            plugin_version=str(data.get("plugin_version", "") or ""),
            render_mode=str(data.get("render_mode", "text") or "text"),
            metadata=dict(data.get("metadata", {}) or {}),
        )

    def to_dict(self) -> BridgeClientPayload:
        return {
            "plugin_version": self.plugin_version,
            "render_mode": self.render_mode,
            "metadata": dict(self.metadata or {}),
        }


@dataclass
class CommandRequest:
    """Normalized command payload accepted by the router and HTTP bridge."""

    command: str = ""
    args: list[str] = field(default_factory=list)
    raw_text: str = ""
    requester: RequesterContext = field(default_factory=RequesterContext)
    context: SessionContext = field(default_factory=SessionContext)
    client: ClientContext = field(default_factory=ClientContext)
    attachments: list[Any] = field(default_factory=list)
    correlation_id: str = ""

    @classmethod
    def from_dict(cls, payload: BridgeExecuteRequestPayload | dict[str, Any] | None) -> "CommandRequest":
        data = payload if isinstance(payload, dict) else {}
        args = data.get("args", [])
        attachments = data.get("attachments", [])
        return cls(
            command=str(data.get("command", "") or ""),
            args=[str(item or "") for item in args] if isinstance(args, list) else [],
            raw_text=str(data.get("raw_text", "") or ""),
            requester=RequesterContext.from_dict(data.get("requester")),
            context=SessionContext.from_dict(data.get("context")),
            client=ClientContext.from_dict(data.get("client")),
            attachments=list(attachments or []) if isinstance(attachments, list) else [],
            correlation_id=str(data.get("correlation_id", "") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "args": list(self.args or []),
            "raw_text": self.raw_text,
            "requester": self.requester.to_dict(),
            "context": self.context.to_dict(),
            "client": self.client.to_dict(),
            "attachments": list(self.attachments or []),
            "correlation_id": self.correlation_id,
        }


@dataclass
class WorkflowRef:
    """Structured workflow reference returned by the bridge."""

    id: str | None = None
    issue_number: str | None = None
    project_key: str | None = None
    state: str | None = None

    def to_dict(self) -> BridgeWorkflowPayload:
        return {
            "id": self.id,
            "issue_number": self.issue_number,
            "project_key": self.project_key,
            "state": self.state,
        }


@dataclass
class UiField:
    """Single UI field returned by the bridge."""

    label: str
    value: str

    def to_dict(self) -> BridgeUiFieldPayload:
        return {"label": self.label, "value": self.value}


@dataclass
class UiPayload:
    """Structured UI rendering hints returned by the bridge."""

    title: str = ""
    summary: str = ""
    fields: list[UiField] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> BridgeUiPayload:
        return {
            "title": self.title,
            "summary": self.summary,
            "fields": [field.to_dict() for field in self.fields],
            "actions": list(self.actions or []),
            "metadata": dict(self.metadata or {}),
        }


@dataclass
class UsagePayload:
    """Usage or cost metadata returned by the bridge."""

    provider: str = ""
    model: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> BridgeUsagePayload:
        return {
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "metadata": dict(self.metadata or {}),
        }


@dataclass
class AuditPayload:
    """Audit metadata returned by the bridge."""

    request_id: str = ""
    actor: str = ""
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> BridgeAuditPayload:
        return {
            "request_id": self.request_id,
            "actor": self.actor,
            "session_id": self.session_id,
            "metadata": dict(self.metadata or {}),
        }


@dataclass
class CommandResult:
    """Structured command execution result returned by the bridge."""

    status: str
    message: str
    workflow_id: str | None = None
    issue_number: str | None = None
    project_key: str | None = None
    workflow: WorkflowRef | None = None
    ui: UiPayload | None = None
    usage: UsagePayload | None = None
    audit: AuditPayload | None = None
    data: dict[str, Any] = field(default_factory=dict)
    suggested_next_commands: list[str] = field(default_factory=list)

    def to_dict(self) -> BridgeCommandResultPayload:
        workflow = self.workflow or WorkflowRef(
            id=self.workflow_id,
            issue_number=self.issue_number,
            project_key=self.project_key,
        )
        ui = self.ui or UiPayload(
            summary=self.message,
            actions=list(self.suggested_next_commands or []),
        )
        return {
            "status": self.status,
            "message": self.message,
            "workflow_id": self.workflow_id or workflow.id,
            "issue_number": self.issue_number or workflow.issue_number,
            "project_key": self.project_key or workflow.project_key,
            "workflow": workflow.to_dict(),
            "ui": ui.to_dict(),
            "usage": self.usage.to_dict() if self.usage is not None else {},
            "audit": self.audit.to_dict() if self.audit is not None else {},
            "data": dict(self.data or {}),
            "suggested_next_commands": list(self.suggested_next_commands or []),
        }


def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def requester_context_from_raw_event(raw_event: Any) -> dict[str, Any]:
    requester_payload = None
    if isinstance(raw_event, dict):
        requester_payload = raw_event.get("requester")
    elif hasattr(raw_event, "requester"):
        requester_payload = getattr(raw_event, "requester", None)

    if isinstance(requester_payload, RequesterContext):
        return requester_payload.to_requester_context()
    if isinstance(requester_payload, dict):
        return RequesterContext.from_dict(requester_payload).to_requester_context()
    return {}


def requester_nexus_id_from_raw_event(raw_event: Any) -> str | None:
    requester_context = requester_context_from_raw_event(raw_event)
    nexus_id = str(requester_context.get("nexus_id") or "").strip()
    return nexus_id or None
