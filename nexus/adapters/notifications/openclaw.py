"""OpenClaw notification channel — pushes Nexus workflow events to the OpenClaw command bridge.

This allows Nexus ARC running in ``NEXUS_RUNTIME_MODE=openclaw`` to deliver
step completions, alerts, and workflow updates directly to the OpenClaw agent
session (e.g. a Telegram chat), without requiring a dedicated Nexus Telegram bot.

Configuration (env vars or project_config.yaml plugin block):
    NEXUS_OPENCLAW_BRIDGE_URL     Base URL of the OpenClaw gateway (default: http://127.0.0.1:18789)
    NEXUS_OPENCLAW_BRIDGE_TOKEN   Bearer token for the OpenClaw hooks endpoint
    NEXUS_OPENCLAW_SENDER_ID      Telegram/channel chat ID to deliver notifications to
    NEXUS_OPENCLAW_CHANNEL        Optional channel hint (e.g. "telegram")

Requires hooks to be enabled in openclaw.json:
    {
      "hooks": {
        "enabled": true,
        "token": "<same as NEXUS_OPENCLAW_BRIDGE_TOKEN>",
        "path": "/hooks",
        "allowedAgentIds": ["main"]
      }
    }
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexus.core.command_bridge.reply_security import issue_reply_token

from nexus.adapters.notifications.base import Message, NotificationChannel
from nexus.core.models import Severity

try:
    import aiohttp

    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False

logger = logging.getLogger(__name__)

_DEFAULT_BRIDGE_URL = "http://127.0.0.1:18789"
_SEVERITY_EMOJI = {
    Severity.INFO: "ℹ️",
    Severity.WARNING: "⚠️",
    Severity.ERROR: "🚨",
    Severity.CRITICAL: "🔴",
}


@dataclass
class WorkflowNotificationPayload:
    """Typed envelope for rich Nexus → OpenClaw workflow notifications."""

    schema_version: str = "workflow_notification.v1"
    event_type: str = "workflow_progressed"
    workflow_id: str = ""
    project_key: str = ""
    repo: str = ""
    issue_number: str = ""
    pr_number: str = ""
    pr_url: str = ""
    current_step: str = ""
    step_id: str = ""
    step_num: int = 0
    step_name: str = ""
    workflow_phase: str = ""
    agent_type: str = ""
    severity: str = "info"
    summary: str = ""
    blocked_reason: str = ""
    key_findings: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)
    session_key: str = ""
    correlation_token: str = field(default_factory=lambda: f"ocwf-{uuid.uuid4().hex}")
    reply_token: str = ""
    reply_token_expires_at_utc: str = ""
    timestamp_utc: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_metadata(self) -> dict[str, Any]:
        workflow_ref = {
            "id": self.workflow_id or None,
            "issue_number": self.issue_number or None,
            "project_key": self.project_key or None,
            "state": self.workflow_phase or None,
        }
        actions = [
            {"name": action, "label": action.replace("_", " ").title()}
            for action in self.suggested_actions
            if str(action).strip()
        ]
        return {
            "source": "nexus",
            "kind": "workflow_notification",
            "schema_version": self.schema_version,
            "event_type": self.event_type,
            "workflow": workflow_ref,
            "payload": {
                "repo": self.repo,
                "pr_number": self.pr_number,
                "pr_url": self.pr_url,
                "current_step": self.current_step,
                "step_id": self.step_id,
                "step_num": self.step_num,
                "step_name": self.step_name,
                "workflow_phase": self.workflow_phase,
                "agent_type": self.agent_type,
                "severity": self.severity,
                "summary": self.summary,
                "blocked_reason": self.blocked_reason,
                "key_findings": list(self.key_findings or []),
                "suggested_actions": list(self.suggested_actions or []),
                "timestamp_utc": self.timestamp_utc,
            },
            "actions": actions,
            "routing": {
                "session_key": self.session_key,
                "correlation_token": self.correlation_token,
                "reply_hint": {
                    "workflow_id": self.workflow_id,
                    "session_key": self.session_key,
                    "event_type": self.event_type,
                    "current_step": self.current_step,
                    "correlation_token": self.correlation_token,
                    "reply_token": self.reply_token,
                    "reply_token_expires_at_utc": self.reply_token_expires_at_utc,
                    "allowed_actions": list(self.suggested_actions or []),
                },
            },
        }


def _require_aiohttp() -> None:
    if not _AIOHTTP_AVAILABLE:
        raise ImportError(
            "aiohttp is required for OpenClawNotificationChannel. "
            "Install it with: pip install aiohttp"
        )


def _normalize_event_type(event_type: str) -> str:
    mapping = {
        "workflow.started": "workflow_started",
        "workflow.completed": "workflow_completed",
        "workflow.failed": "workflow_failed",
        "workflow.cancelled": "workflow_blocked",
        "workflow.approval_required": "workflow_waiting_human",
        "step.completed": "workflow_progressed",
        "step.failed": "workflow_blocked",
        "system.alert": "workflow_blocked",
        "agent.timeout": "workflow_blocked",
    }
    key = str(event_type or "").strip()
    return mapping.get(key, key.replace(".", "_") or "workflow_progressed")


def _extract_issue_number(value: str) -> str:
    text = str(value or "")
    match = re.search(r"(?:^|[^\d])(\d{1,10})(?:$|[^\d])", text)
    return match.group(1) if match else ""


def _infer_project_key(workflow_id: str, fallback: str = "") -> str:
    raw = str(fallback or "").strip()
    if raw:
        return raw
    candidate = str(workflow_id or "").strip()
    if not candidate:
        return ""
    if "-" in candidate:
        return candidate.split("-", 1)[0]
    return ""


def _resolve_session_key(
    workflow_id: str,
    project_key: str = "",
    *,
    configured_session_key: str = "",
    issue_number: str = "",
) -> str:
    if str(configured_session_key or "").strip():
        return str(configured_session_key).strip()
    if str(workflow_id or "").strip():
        prefix = f"nexus:{project_key}:workflow" if project_key else "nexus:workflow"
        return f"{prefix}:{workflow_id}"
    if str(issue_number or "").strip():
        prefix = f"nexus:{project_key}:issue" if project_key else "nexus:issue"
        return f"{prefix}:{issue_number}"
    return ""


class OpenClawNotificationChannel(NotificationChannel):
    """Sends Nexus notifications to an OpenClaw agent session via the OpenClaw bridge.

    When Nexus is running in ``openclaw`` runtime mode this channel replaces (or
    supplements) the Telegram bot, so workflow step completions, alerts, and
    human-handoff prompts all arrive in the user's primary OpenClaw chat.

    Args:
        bridge_url: OpenClaw bridge URL.  Defaults to ``NEXUS_OPENCLAW_BRIDGE_URL`` env var.
        auth_token: Bearer token for the bridge.  Defaults to ``NEXUS_OPENCLAW_BRIDGE_TOKEN``.
        sender_id:  Target session/chat ID.  Defaults to ``NEXUS_OPENCLAW_SENDER_ID``.
        channel:    Optional channel hint forwarded in the payload (e.g. ``"telegram"``).
        timeout:    HTTP request timeout in seconds.
    """

    def __init__(
        self,
        bridge_url: str | None = None,
        auth_token: str | None = None,
        sender_id: str | None = None,
        channel: str | None = None,
        session_key: str | None = None,
        timeout: int = 10,
    ):
        _require_aiohttp()
        self._bridge_url = (
            (bridge_url or os.getenv("NEXUS_OPENCLAW_BRIDGE_URL") or _DEFAULT_BRIDGE_URL).rstrip("/")
        )
        self._auth_token = auth_token or os.getenv("NEXUS_OPENCLAW_BRIDGE_TOKEN") or ""
        self._sender_id = sender_id or os.getenv("NEXUS_OPENCLAW_SENDER_ID") or ""
        self._channel = channel or os.getenv("NEXUS_OPENCLAW_CHANNEL") or "telegram"
        self._session_key = session_key or os.getenv("NEXUS_OPENCLAW_SESSION_KEY") or ""
        self._timeout_seconds = timeout
        self._reply_secret = os.getenv("NEXUS_OPENCLAW_REPLY_SECRET") or self._auth_token
        self._reply_ttl_seconds = int(os.getenv("NEXUS_OPENCLAW_REPLY_TTL_SECONDS") or "900")
        self._sessions_by_loop: dict[object, aiohttp.ClientSession] = {}

    @property
    def name(self) -> str:
        return "openclaw"

    async def send_message(self, user_id: str, message: Message) -> str:
        emoji = _SEVERITY_EMOJI.get(message.severity, "ℹ️")
        text = f"{emoji} **[Nexus]** {message.text}"
        payload = self._build_payload(text, target_user=user_id or self._sender_id)
        ok = await self._post(payload)
        if not ok:
            logger.error(
                "Failed to send OpenClaw notification for user '%s' via bridge '%s'",
                user_id or self._sender_id,
                self._bridge_url,
            )
            return ""
        return "openclaw:ok"

    async def update_message(self, message_id: str, new_text: str) -> None:
        payload = self._build_payload(f"ℹ️ **[Nexus update]** {new_text}")
        await self._post(payload)

    async def send_alert(self, message: str, severity: Severity) -> None:
        emoji = _SEVERITY_EMOJI.get(severity, "⚠️")
        text = f"{emoji} **[Nexus alert]** {message}"
        payload = self._build_payload(text)
        await self._post(payload)

    async def request_input(self, user_id: str, prompt: str) -> str:
        text = f"💬 **[Nexus needs input]** {prompt}"
        payload = self._build_payload(text, target_user=user_id or self._sender_id)
        await self._post(payload)
        return ""

    async def send_workflow_notification(
        self,
        *,
        event_type: str,
        workflow_id: str,
        summary: str,
        severity: str = "info",
        project_key: str = "",
        repo: str = "",
        issue_number: str = "",
        pr_number: str = "",
        pr_url: str = "",
        current_step: str = "",
        step_id: str = "",
        step_num: int = 0,
        step_name: str = "",
        workflow_phase: str = "",
        agent_type: str = "",
        blocked_reason: str = "",
        key_findings: list[str] | None = None,
        suggested_actions: list[str] | None = None,
        correlation_token: str | None = None,
        session_key: str | None = None,
        target_user: str | None = None,
    ) -> bool:
        """Send a rich workflow notification envelope to OpenClaw."""
        resolved_project = _infer_project_key(workflow_id, project_key)
        resolved_issue = str(issue_number or "").strip() or _extract_issue_number(workflow_id)
        resolved_session_key = _resolve_session_key(
            workflow_id,
            resolved_project,
            configured_session_key=session_key or self._session_key,
            issue_number=resolved_issue,
        )
        correlation_value = str(correlation_token or "").strip() or f"ocwf-{uuid.uuid4().hex}"
        allowed_actions = [str(item).strip() for item in (suggested_actions or []) if str(item).strip()]
        reply_token = ""
        reply_token_expiry = ""
        if self._reply_secret and correlation_value:
            reply_token, claims = issue_reply_token(
                secret=self._reply_secret,
                correlation_id=correlation_value,
                workflow_id=str(workflow_id or "").strip(),
                session_key=resolved_session_key,
                sender_id=str(target_user or self._sender_id or "").strip(),
                allowed_actions=allowed_actions,
                ttl_seconds=self._reply_ttl_seconds,
            )
            reply_token_expiry = datetime.fromtimestamp(claims.expires_at, UTC).isoformat()
        payload_model = WorkflowNotificationPayload(
            event_type=_normalize_event_type(event_type),
            workflow_id=str(workflow_id or "").strip(),
            project_key=resolved_project,
            repo=str(repo or "").strip(),
            issue_number=resolved_issue,
            pr_number=str(pr_number or "").strip(),
            pr_url=str(pr_url or "").strip(),
            current_step=str(current_step or "").strip(),
            step_id=str(step_id or "").strip(),
            step_num=int(step_num or 0),
            step_name=str(step_name or "").strip(),
            workflow_phase=str(workflow_phase or "").strip(),
            agent_type=str(agent_type or "").strip(),
            severity=str(severity or "info").strip().lower() or "info",
            summary=str(summary or "").strip(),
            blocked_reason=str(blocked_reason or "").strip(),
            key_findings=[str(item).strip() for item in (key_findings or []) if str(item).strip()],
            suggested_actions=allowed_actions,
            session_key=resolved_session_key,
            correlation_token=correlation_value,
            reply_token=reply_token,
            reply_token_expires_at_utc=reply_token_expiry,
        )
        text = self._render_workflow_notification(payload_model)
        payload = self._build_payload(
            text,
            target_user=target_user or self._sender_id,
            metadata=payload_model.to_metadata(),
            session_key=resolved_session_key,
        )
        return await self._post(payload)

    def _build_payload(
        self,
        text: str,
        target_user: str | None = None,
        *,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "message": text,
            "name": "Nexus",
            "deliver": True,
            "channel": self._channel or "telegram",
            "wakeMode": "now",
        }
        if metadata:
            payload["metadata"] = metadata
        resolved_session_key = str(session_key or self._session_key or "").strip()
        if resolved_session_key:
            payload["sessionKey"] = resolved_session_key
        recipient = target_user or self._sender_id
        if recipient:
            payload["to"] = recipient
        return payload

    def _render_workflow_notification(self, item: WorkflowNotificationPayload) -> str:
        icon = {
            "workflow_started": "🚀",
            "workflow_progressed": "🔄",
            "workflow_waiting_human": "⏳",
            "workflow_blocked": "⚠️",
            "workflow_failed": "❌",
            "workflow_completed": "✅",
            "review_requested": "👀",
            "deployment_succeeded": "🚀",
            "deployment_failed": "💥",
        }.get(item.event_type, "📌")
        workflow_ref = f"#{item.issue_number}" if item.issue_number else item.workflow_id or "workflow"
        status_ref = item.current_step or item.workflow_phase or item.event_type.replace("_", " ")
        lines = [f"{icon} **Workflow {workflow_ref} · {status_ref}**"]
        if item.summary:
            lines.append(item.summary)
        context_bits: list[str] = []
        if item.project_key:
            context_bits.append(f"Project: `{item.project_key}`")
        if item.repo:
            context_bits.append(f"Repo: `{item.repo}`")
        if item.agent_type:
            context_bits.append(f"Agent: `{item.agent_type}`")
        if item.step_num or item.step_name:
            step_label = item.step_name or item.current_step
            if item.step_num and step_label:
                context_bits.append(f"Step: `{item.step_num}` · {step_label}")
            elif step_label:
                context_bits.append(f"Step: {step_label}")
        if item.pr_number:
            context_bits.append(f"PR: `#{item.pr_number}`")
        elif item.pr_url:
            context_bits.append(f"PR: {item.pr_url}")
        if context_bits:
            lines.extend(context_bits)
        if item.blocked_reason:
            lines.append(f"Blocked: {item.blocked_reason}")
        if item.key_findings:
            lines.append("Findings: " + "; ".join(item.key_findings[:3]))
        if item.suggested_actions:
            action_labels = ", ".join(action.replace("_", " ") for action in item.suggested_actions[:4])
            lines.append(f"Actions: {action_labels}")
        return "\n".join(lines)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        return headers

    def _get_session(self) -> "aiohttp.ClientSession":
        import asyncio

        current_loop = asyncio.get_running_loop()
        session = self._sessions_by_loop.get(current_loop)
        if session is not None and session.closed:
            self._sessions_by_loop.pop(current_loop, None)
            session = None

        if session is None:
            timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
            session = aiohttp.ClientSession(timeout=timeout)
            self._sessions_by_loop[current_loop] = session

        return session

    async def aclose(self) -> None:
        for session in list(self._sessions_by_loop.values()):
            if not session.closed:
                await session.close()
        self._sessions_by_loop = {}

    async def _post(self, payload: dict[str, Any]) -> bool:
        url = f"{self._bridge_url}/hooks/agent"
        try:
            session = self._get_session()
            async with session.post(url, json=payload, headers=self._headers()) as resp:
                if resp.status < 300:
                    logger.debug("OpenClaw notification delivered (status=%s)", resp.status)
                    return True
                body = await resp.text()
                logger.warning(
                    "OpenClaw notification failed: HTTP %s — %s", resp.status, body[:200]
                )
                return False
        except Exception as exc:
            logger.warning("OpenClaw notification error: %s", exc)
            return False
