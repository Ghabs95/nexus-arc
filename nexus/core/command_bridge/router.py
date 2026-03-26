"""Framework command router that can drive Nexus from chat plugins or HTTP callers."""

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from itertools import count
from typing import Any

from nexus.adapters.notifications.base import Button, Message
from nexus.adapters.notifications.interactive import InteractiveClientPlugin
from nexus.core.command_bridge.models import (
    AuditPayload,
    CommandRequest,
    CommandResult,
    ReplyRequest,
    RequesterContext,
    SessionContext,
    UiField,
    UiPayload,
    WorkflowRef,
)
from nexus.core.command_bridge.operator import BridgeOperatorService
from nexus.core.command_bridge.usage import (
    collect_bridge_usage_payload,
    usage_payload_from_bridge_event,
)
from nexus.core.command_contract import OPENCLAW_BRIDGE_COMMANDS
from nexus.core.command_visibility import is_command_visible
from nexus.core.config import PROJECT_CONFIG, get_project_display_names
from nexus.core.config import normalize_project_key as _normalize_project_key
from nexus.core.discord.discord_bridge_deps_service import (
    hands_free_bridge_deps,
    issue_bridge_deps,
    monitoring_bridge_deps,
    ops_bridge_deps,
    workflow_bridge_deps,
)
from nexus.core.handlers.chat_command_handlers import chat_agents_handler, chat_menu_handler
from nexus.core.handlers.hands_free_routing_handler import route_hands_free_text
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
from nexus.core.integrations.workflow_state_factory import get_workflow_state
from nexus.core.project.catalog import get_project_label, iter_project_keys, single_key
from nexus.core.telegram.telegram_issue_selection_service import parse_project_issue_args
from nexus.core.user_manager import get_user_manager

logger = logging.getLogger(__name__)

_PROJECTS_MAP = get_project_display_names()
_ISSUE_REF_RE = re.compile(r"(?P<project>[a-zA-Z0-9_-]+)#(?P<issue>\d+)")
_ISSUE_TOKEN_RE = re.compile(r"^#?(?P<issue>\d+)$")
_LONG_RUNNING_COMMANDS = {"continue", "implement", "pause", "plan", "prepare", "reprocess", "respond", "resume", "stop"}


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


@dataclass
class _RegisteredCommand:
    callback: Callable[..., Awaitable[None]]
    bridge_enabled: bool = True


class _CapturingInteractiveClient(InteractiveClientPlugin):
    """Interactive client used for HTTP execution where responses must be captured."""

    def __init__(self, platform_name: str) -> None:
        self._name = f"{platform_name}-interactive"
        self._commands: dict[str, Callable[..., Awaitable[None]]] = {}
        self._message_handler: Callable[..., Awaitable[None]] | None = None
        self._messages: list[dict[str, Any]] = []
        self._message_ids = count(1)

    @property
    def name(self) -> str:
        return self._name

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def register_command(self, command: str, callback: Callable) -> None:
        self._commands[str(command)] = callback

    def register_message_handler(self, callback: Callable) -> None:
        self._message_handler = callback

    async def send_interactive(self, user_id: str, message: Message) -> str:
        message_id = str(next(self._message_ids))
        self._messages.append(
            {
                "id": message_id,
                "user_id": str(user_id),
                "text": str(message.text or ""),
                "buttons": _buttons_to_labels(message.buttons),
                "edited": False,
            }
        )
        return message_id

    async def edit_interactive(self, user_id: str, message_id: str, message: Message) -> None:
        target = None
        for item in reversed(self._messages):
            if item["id"] == str(message_id):
                target = item
                break
        payload = {
            "id": str(message_id),
            "user_id": str(user_id),
            "text": str(message.text or ""),
            "buttons": _buttons_to_labels(message.buttons),
            "edited": True,
        }
        if target is None:
            self._messages.append(payload)
            return
        target.update(payload)

    def export_messages(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._messages]

    def final_text(self) -> str:
        if not self._messages:
            return ""
        return str(self._messages[-1].get("text", "") or "")


def _buttons_to_labels(buttons: list[Any] | None) -> list[list[str]]:
    if not isinstance(buttons, list):
        return []
    rows: list[list[str]] = []
    for row in buttons:
        if not isinstance(row, list):
            continue
        labels = [str(getattr(button, "label", "") or "").strip() for button in row]
        labels = [label for label in labels if label]
        if labels:
            rows.append(labels)
    return rows


class CommandExecutionContext:
    """Duck-typed interactive context usable by existing Nexus handlers."""

    def __init__(
        self,
        *,
        client: InteractiveClientPlugin,
        user_id: str,
        text: str,
        args: list[str] | None,
        raw_event: Any,
        user_state: dict[str, Any] | None,
        attachments: list[Any] | None,
    ) -> None:
        self.client = client
        self.user_id = str(user_id or "")
        self.text = str(text or "")
        self.args = list(args or [])
        self.raw_event = raw_event
        self.user_state = dict(user_state or {})
        self.attachments = attachments
        self.query = None
        self.chat_id = getattr(raw_event, "chat_id", None)

    @property
    def platform(self) -> str:
        return self.client.name.split("-")[0].lower()

    async def reply_text(
        self,
        text: str,
        buttons: list[list[Button]] | None = None,
        parse_mode: str | None = "Markdown",
        disable_web_page_preview: bool = True,
    ) -> str:
        del parse_mode, disable_web_page_preview
        return await self.client.send_interactive(
            self.user_id,
            Message(text=str(text or ""), buttons=buttons),  # type: ignore[arg-type]
        )

    async def edit_message_text(
        self,
        message_id: str,
        text: str,
        buttons: list[list[Button]] | None = None,
        parse_mode: str | None = "Markdown",
        disable_web_page_preview: bool = True,
    ) -> None:
        del parse_mode, disable_web_page_preview
        await self.client.edit_interactive(
            self.user_id,
            str(message_id),
            Message(text=str(text or ""), buttons=buttons),  # type: ignore[arg-type]
        )

    async def answer_callback_query(self, text: str | None = None) -> None:
        del text
        return None


class CommandRouter:
    """Reusable command router shared by interactive plugins and the HTTP bridge."""

    def __init__(
        self,
        *,
        allowed_user_ids: list[int] | None = None,
        default_source_platform: str = "openclaw",
    ) -> None:
        self.allowed_user_ids = [int(item) for item in (allowed_user_ids or [])]
        self.default_source_platform = str(default_source_platform or "openclaw").strip().lower() or "openclaw"
        self._command_registry: dict[str, _RegisteredCommand] = {}
        self._projects = _PROJECTS_MAP
        self.workflow_deps = workflow_bridge_deps(
            allowed_user_ids=self.allowed_user_ids,
            prompt_project_selection=self._prompt_project_selection,
            ensure_project_issue=self._ensure_project_issue,
        )
        self.monitoring_deps = monitoring_bridge_deps(
            allowed_user_ids=self.allowed_user_ids,
            ensure_project=self._ensure_project,
            ensure_project_issue=self._ensure_project_issue,
        )
        self.ops_deps = ops_bridge_deps(
            allowed_user_ids=self.allowed_user_ids,
            prompt_project_selection=self._prompt_project_selection,
            ensure_project_issue=self._ensure_project_issue,
        )
        self.issue_deps = issue_bridge_deps(
            allowed_user_ids=self.allowed_user_ids,
            prompt_project_selection=self._prompt_project_selection,
            ensure_project_issue=self._ensure_project_issue,
        )
        self.hands_free_deps = hands_free_bridge_deps(
            requester_context_builder=self._build_requester_context_builder(
                self.default_source_platform
            )
        )
        self._override_requester_builders()
        self.operator_service = BridgeOperatorService(
            workflow_state_plugin_kwargs=self.workflow_deps.workflow_state_plugin_kwargs,
        )
        self._register_default_commands()

    def _build_requester_context_builder(self, platform_name: str) -> Callable[[int], dict[str, Any]]:
        return lambda user_id: {
            "platform": platform_name,
            "platform_user_id": str(user_id),
        }

    def _override_requester_builders(self) -> None:
        requester_builder = self._build_requester_context_builder(self.default_source_platform)
        self.workflow_deps.requester_context_builder = requester_builder
        self.ops_deps.requester_context_builder = requester_builder
        self.hands_free_deps.requester_context_builder = requester_builder
        feature_ideation_deps = getattr(self.hands_free_deps, "feature_ideation_deps", None)
        if feature_ideation_deps is not None:
            feature_ideation_deps.requester_context_builder = requester_builder

    def register_command(
        self,
        command: str,
        callback: Callable[..., Awaitable[None]],
        *,
        bridge_enabled: bool = True,
    ) -> None:
        self._command_registry[str(command).strip().lower()] = _RegisteredCommand(
            callback=callback,
            bridge_enabled=bridge_enabled,
        )

    def _bind_requester_identity(self, requester: RequesterContext) -> None:
        nexus_id = str(requester.nexus_id or "").strip()
        platform = str(requester.source_platform or self.default_source_platform).strip().lower()
        sender_id = str(requester.sender_id or "").strip()
        if not nexus_id or not platform or not sender_id:
            return

        try:
            user_manager = get_user_manager()
            candidate_user = user_manager.get_or_create_user_by_identity(
                platform=platform,
                platform_user_id=sender_id,
                username=str(requester.sender_name or "").strip() or None,
                first_name=str(requester.sender_name or "").strip() or None,
            )
            if str(candidate_user.nexus_id or "").strip() != nexus_id:
                user_manager.merge_users(nexus_id, candidate_user.nexus_id)
        except Exception as exc:
            logger.warning(
                "Failed to bind bridge requester identity nexus_id=%s platform=%s sender_id=%s: %s",
                nexus_id,
                platform,
                sender_id,
                exc,
            )

    def build_context(
        self,
        *,
        client: InteractiveClientPlugin,
        user_id: str,
        text: str,
        args: list[str] | None,
        raw_event: Any = None,
        user_state: dict[str, Any] | None = None,
        attachments: list[Any] | None = None,
    ) -> CommandExecutionContext:
        return CommandExecutionContext(
            client=client,
            user_id=user_id,
            text=text,
            args=self._normalize_arg_tokens(args or []),
            raw_event=raw_event,
            user_state=user_state,
            attachments=attachments,
        )

    def bind_plugin(self, plugin: InteractiveClientPlugin) -> None:
        plugin.register_command("chat", self._plugin_callback(plugin, "chat"))
        plugin.register_command("chatagents", self._plugin_callback(plugin, "chatagents"))
        plugin.register_command("assign", self._plugin_callback(plugin, "assign"))
        plugin.register_command("comments", self._plugin_callback(plugin, "comments"))
        plugin.register_command("implement", self._plugin_callback(plugin, "implement"))
        plugin.register_command("myissues", self._plugin_callback(plugin, "myissues"))
        plugin.register_command("new", self._plugin_callback(plugin, "new"))
        plugin.register_command("plan", self._plugin_callback(plugin, "plan"))
        plugin.register_command("prepare", self._plugin_callback(plugin, "prepare"))
        plugin.register_command("respond", self._plugin_callback(plugin, "respond"))
        plugin.register_command("track", self._plugin_callback(plugin, "track"))
        plugin.register_command("tracked", self._plugin_callback(plugin, "tracked"))
        plugin.register_command("untrack", self._plugin_callback(plugin, "untrack"))
        plugin.register_command("usage", self._plugin_callback(plugin, "usage"))
        if is_command_visible("active"):
            plugin.register_command("active", self._plugin_callback(plugin, "active"))
        plugin.register_command("fuse", self._plugin_callback(plugin, "fuse"))
        if is_command_visible("logs"):
            plugin.register_command("logs", self._plugin_callback(plugin, "logs"))
        if is_command_visible("logsfull"):
            plugin.register_command("logsfull", self._plugin_callback(plugin, "logsfull"))
        plugin.register_command("status", self._plugin_callback(plugin, "status"))
        if is_command_visible("tail"):
            plugin.register_command("tail", self._plugin_callback(plugin, "tail"))
        if is_command_visible("tailstop"):
            plugin.register_command("tailstop", self._plugin_callback(plugin, "tailstop"))
        plugin.register_command("agents", self._plugin_callback(plugin, "agents"))
        plugin.register_command("audit", self._plugin_callback(plugin, "audit"))
        plugin.register_command("direct", self._plugin_callback(plugin, "direct"))
        plugin.register_command("stats", self._plugin_callback(plugin, "stats"))
        plugin.register_command("summary", self._plugin_callback(plugin, "summary"))
        plugin.register_command("continue", self._plugin_callback(plugin, "continue"))
        plugin.register_command("forget", self._plugin_callback(plugin, "forget"))
        plugin.register_command("kill", self._plugin_callback(plugin, "kill"))
        plugin.register_command("pause", self._plugin_callback(plugin, "pause"))
        plugin.register_command("reconcile", self._plugin_callback(plugin, "reconcile"))
        plugin.register_command("reprocess", self._plugin_callback(plugin, "reprocess"))
        plugin.register_command("resume", self._plugin_callback(plugin, "resume"))
        plugin.register_command("stop", self._plugin_callback(plugin, "stop"))
        plugin.register_command("wfstate", self._plugin_callback(plugin, "wfstate"))

    async def execute(self, request: CommandRequest) -> CommandResult:
        command_name = str(request.command or "").strip().lower()
        spec = self._command_registry.get(command_name)
        if spec is None:
            return CommandResult(
                status="error",
                message=f"Unsupported command '{request.command}'.",
            )
        if command_name not in OPENCLAW_BRIDGE_COMMANDS or not spec.bridge_enabled:
            return CommandResult(
                status="error",
                message=f"Command '{command_name}' is not exposed on the command bridge.",
            )

        requester = request.requester if isinstance(request.requester, RequesterContext) else RequesterContext()
        context = request.context if isinstance(request.context, SessionContext) else SessionContext()
        self._bind_requester_identity(requester)
        client = _CapturingInteractiveClient(
            requester.source_platform or self.default_source_platform
        )
        args = self._normalize_arg_tokens(list(request.args or []))
        text = request.raw_text or " ".join([command_name, *args]).strip()
        raw_event = {
            "requester": requester.to_dict(),
            "context": context.to_dict(),
            "client": request.client.to_dict(),
            "correlation_id": request.correlation_id,
        }
        await spec.callback(
            client=client,
            user_id=requester.sender_id or "0",
            text=text,
            args=args,
            raw_event=raw_event,
            attachments=list(request.attachments or []),
        )
        project_key, issue_number = self._extract_project_and_issue(command_name, args)
        workflow_id = self._lookup_workflow_id(issue_number)
        usage = usage_payload_from_bridge_event(raw_event)
        if usage is None:
            usage = await collect_bridge_usage_payload(
                project_key=project_key,
                issue_number=issue_number,
                workflow_id=workflow_id,
            )
        status = "accepted" if workflow_id and command_name in _LONG_RUNNING_COMMANDS else "success"
        suggested_next_commands = self._suggested_next_commands(
            command_name=command_name,
            project_key=project_key,
            issue_number=issue_number,
            workflow_id=workflow_id,
        )
        message = client.final_text() or f"Executed {command_name}."
        return CommandResult(
            status=status,
            message=message,
            workflow_id=workflow_id,
            issue_number=issue_number,
            project_key=project_key,
            workflow=WorkflowRef(
                id=workflow_id,
                issue_number=issue_number,
                project_key=project_key,
            ),
            ui=UiPayload(
                title=_command_title(command_name),
                summary=message,
                fields=_build_ui_fields(
                    command_name=command_name,
                    project_key=project_key,
                    issue_number=issue_number,
                    workflow_id=workflow_id,
                    context=context,
                ),
                actions=suggested_next_commands,
            ),
            usage=usage,
            audit=AuditPayload(
                request_id=request.correlation_id,
                actor=requester.operator_id or requester.sender_id or requester.sender_name,
                session_id=requester.session_id,
            ),
            data={
                "messages": client.export_messages(),
                "requester": requester.to_dict(),
                "context": context.to_dict(),
                "client": request.client.to_dict(),
                "command": command_name,
                "args": args,
            },
            suggested_next_commands=suggested_next_commands,
        )

    async def route(self, request: CommandRequest) -> CommandResult:
        routed_command, routed_args, clarification = self._route_text_to_command(
            request.raw_text or " ".join(request.args or [])
        )
        if clarification:
            return CommandResult(
                status="clarification",
                message=clarification,
                ui=UiPayload(title="Clarification Needed", summary=clarification),
            )
        forwarded = CommandRequest(
            command=routed_command,
            args=routed_args,
            raw_text=request.raw_text,
            requester=request.requester,
            context=request.context,
            client=request.client,
            attachments=request.attachments,
            correlation_id=request.correlation_id,
        )
        return await self.execute(forwarded)

    async def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        workflow_id = str(workflow_id or "").strip()
        if not workflow_id:
            return {"ok": False, "error": "workflow_id is required"}
        payload = await self.operator_service.workflow_status(workflow_id=workflow_id)
        if not payload.get("ok"):
            return payload
        workflow = payload.get("workflow") if isinstance(payload.get("workflow"), dict) else {}
        issue_number = workflow.get("issue_number")
        project_key = workflow.get("project_key")
        usage = await collect_bridge_usage_payload(
            project_key=project_key,
            issue_number=issue_number,
            workflow_id=workflow_id,
        )
        payload["workflow_id"] = workflow_id
        payload["issue_number"] = issue_number
        payload["project_key"] = project_key
        payload["usage"] = usage.to_dict() if usage is not None else {}
        return payload

    async def get_runtime_health(self) -> dict[str, Any]:
        return await self.operator_service.runtime_health()

    async def get_workflow_summary(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
    ) -> dict[str, Any]:
        return await self.operator_service.workflow_summary(
            workflow_id=workflow_id,
            issue_number=issue_number,
        )

    async def get_workflow_diagnosis(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
    ) -> dict[str, Any]:
        return await self.operator_service.workflow_diagnosis(
            workflow_id=workflow_id,
            issue_number=issue_number,
        )

    async def get_active_workflows(self, *, limit: int = 20) -> dict[str, Any]:
        return await self.operator_service.active_workflows(limit=limit)

    async def get_recent_failures(self, *, limit: int = 20) -> dict[str, Any]:
        return await self.operator_service.recent_failures(limit=limit)

    async def get_git_identity_status(self) -> dict[str, Any]:
        return await self.operator_service.git_identity_status()

    async def explain_routing(
        self,
        *,
        project_key: str,
        task_type: str = "feature",
        workflow_id: str | None = None,
        issue_number: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        return await self.operator_service.routing_explain(
            project_key=project_key,
            task_type=task_type,
            workflow_id=workflow_id,
            issue_number=issue_number,
            agent_name=agent_name,
        )

    async def continue_workflow(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
        target_agent: str | None = None,
    ) -> dict[str, Any]:
        return await self.operator_service.continue_workflow(
            workflow_id=workflow_id,
            issue_number=issue_number,
            target_agent=target_agent,
        )

    async def retry_workflow_step(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
        target_agent: str,
    ) -> dict[str, Any]:
        return await self.operator_service.retry_step(
            workflow_id=workflow_id,
            issue_number=issue_number,
            target_agent=target_agent,
        )

    async def cancel_workflow(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
    ) -> dict[str, Any]:
        return await self.operator_service.cancel_workflow(
            workflow_id=workflow_id,
            issue_number=issue_number,
        )

    async def refresh_workflow_state(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
    ) -> dict[str, Any]:
        return await self.operator_service.refresh_state(
            workflow_id=workflow_id,
            issue_number=issue_number,
        )

    async def receive_reply(self, reply: ReplyRequest) -> CommandResult:
        """Accept an inbound reply forwarded by an OpenClaw plugin."""
        correlation_id = reply.correlation_id
        logger.info(
            "Received OpenClaw reply: correlation_id=%r sender_id=%r content_len=%d",
            correlation_id,
            reply.sender_id,
            len(reply.content),
        )
        return CommandResult(
            status="success",
            message="Reply received",
            data={
                "correlation_id": correlation_id,
                "received": True,
            },
        )

    def get_capabilities(self) -> dict[str, Any]:
        supported = sorted(
            command_name
            for command_name, spec in self._command_registry.items()
            if spec.bridge_enabled and command_name in OPENCLAW_BRIDGE_COMMANDS
        )
        long_running = sorted(command for command in supported if command in _LONG_RUNNING_COMMANDS)
        return {
            "ok": True,
            "version": "v1",
            "route_enabled": True,
            "supported_commands": supported,
            "long_running_commands": long_running,
            "clarification_hint": self._clarification_message(),
        }

    def _plugin_callback(
        self, plugin: InteractiveClientPlugin, command_name: str
    ) -> Callable[..., Awaitable[None]]:
        async def _callback(
            *,
            user_id: str,
            text: str,
            context: list[str] | None = None,
            raw_event: Any = None,
            **kwargs: Any,
        ) -> None:
            spec = self._command_registry[command_name]
            await spec.callback(
                client=plugin,
                user_id=str(user_id or ""),
                text=str(text or ""),
                args=list(context or []),
                raw_event=raw_event,
                attachments=kwargs.get("attachments"),
            )

        return _callback

    def _register_default_commands(self) -> None:
        self.register_command("chat", self._chat_bridge_callback())
        self.register_command("chatagents", self._wrap_command_handler(chat_agents_handler))
        self.register_command("assign", self._wrap_command_handler(assign_handler, self.issue_deps), bridge_enabled=False)
        self.register_command("comments", self._wrap_command_handler(comments_handler, self.issue_deps), bridge_enabled=False)
        self.register_command("implement", self._wrap_command_handler(implement_handler, self.issue_deps))
        self.register_command("myissues", self._wrap_command_handler(myissues_handler, self.issue_deps))
        self.register_command("new", self._wrap_command_handler(plan_handler, self.issue_deps))
        self.register_command("plan", self._wrap_command_handler(plan_handler, self.issue_deps))
        self.register_command("prepare", self._wrap_command_handler(prepare_handler, self.issue_deps))
        self.register_command("respond", self._wrap_command_handler(respond_handler, self.issue_deps))
        self.register_command("track", self._wrap_command_handler(track_handler, self.issue_deps))
        self.register_command("tracked", self._wrap_command_handler(tracked_handler, self.issue_deps))
        self.register_command("untrack", self._wrap_command_handler(untrack_handler, self.issue_deps))
        self.register_command("usage", self._usage_callback())
        self.register_command("active", self._wrap_command_handler(active_handler, self.monitoring_deps))
        self.register_command("fuse", self._wrap_command_handler(fuse_handler, self.monitoring_deps), bridge_enabled=False)
        self.register_command("logs", self._wrap_command_handler(logs_handler, self.monitoring_deps))
        self.register_command("logsfull", self._wrap_command_handler(logsfull_handler, self.monitoring_deps), bridge_enabled=False)
        self.register_command("status", self._wrap_command_handler(status_handler, self.monitoring_deps))
        self.register_command("tail", self._wrap_command_handler(tail_handler, self.monitoring_deps), bridge_enabled=False)
        self.register_command("tailstop", self._wrap_command_handler(tailstop_handler, self.monitoring_deps), bridge_enabled=False)
        self.register_command("agents", self._wrap_command_handler(agents_handler, self.ops_deps))
        self.register_command("audit", self._wrap_command_handler(audit_handler, self.ops_deps))
        self.register_command("direct", self._wrap_command_handler(direct_handler, self.ops_deps), bridge_enabled=False)
        self.register_command("stats", self._wrap_command_handler(stats_handler, self.ops_deps))
        self.register_command("summary", self._summary_callback())
        self.register_command("continue", self._wrap_command_handler(continue_handler, self.workflow_deps))
        self.register_command("forget", self._wrap_command_handler(forget_handler, self.workflow_deps), bridge_enabled=False)
        self.register_command("kill", self._wrap_command_handler(kill_handler, self.workflow_deps))
        self.register_command("pause", self._wrap_command_handler(pause_handler, self.workflow_deps))
        self.register_command("reconcile", self._wrap_command_handler(reconcile_handler, self.workflow_deps))
        self.register_command("reprocess", self._wrap_command_handler(reprocess_handler, self.workflow_deps))
        self.register_command("resume", self._wrap_command_handler(resume_handler, self.workflow_deps))
        self.register_command("stop", self._wrap_command_handler(stop_handler, self.workflow_deps))
        self.register_command("wfstate", self._wrap_command_handler(wfstate_handler, self.workflow_deps))

    def _wrap_command_handler(
        self,
        handler: Callable[..., Awaitable[None]],
        deps: Any | None = None,
    ) -> Callable[..., Awaitable[None]]:
        async def _callback(
            *,
            client: InteractiveClientPlugin,
            user_id: str,
            text: str,
            args: list[str] | None = None,
            raw_event: Any = None,
            attachments: list[Any] | None = None,
            user_state: dict[str, Any] | None = None,
        ) -> None:
            ctx = self.build_context(
                client=client,
                user_id=user_id,
                text=text,
                args=args,
                raw_event=raw_event,
                user_state=user_state,
                attachments=attachments,
            )
            if deps is None:
                await handler(ctx)
                return
            await handler(ctx, deps)

        return _callback

    def _chat_bridge_callback(self) -> Callable[..., Awaitable[None]]:
        async def _callback(
            *,
            client: InteractiveClientPlugin,
            user_id: str,
            text: str,
            args: list[str] | None = None,
            raw_event: Any = None,
            attachments: list[Any] | None = None,
            user_state: dict[str, Any] | None = None,
        ) -> None:
            normalized_args = self._normalize_arg_tokens(args or [])
            effective_user_state = dict(user_state or {})
            effective_user_state["chat_session_active"] = True
            if normalized_args:
                chat_text = " ".join(normalized_args).strip()
                ctx = self.build_context(
                    client=client,
                    user_id=user_id,
                    text=chat_text,
                    args=[],
                    raw_event=raw_event,
                    user_state=effective_user_state,
                    attachments=attachments,
                )
                await route_hands_free_text(ctx, self.hands_free_deps)
                return

            ctx = self.build_context(
                client=client,
                user_id=user_id,
                text=text,
                args=[],
                raw_event=raw_event,
                user_state=effective_user_state,
                attachments=attachments,
            )
            await chat_menu_handler(ctx)

        return _callback

    def _summary_callback(self) -> Callable[..., Awaitable[None]]:
        async def _callback(
            *,
            client: InteractiveClientPlugin,
            user_id: str,
            text: str,
            args: list[str] | None = None,
            raw_event: Any = None,
            attachments: list[Any] | None = None,
            user_state: dict[str, Any] | None = None,
        ) -> None:
            del text, attachments, user_state
            ctx = self.build_context(
                client=client,
                user_id=user_id,
                text="summary",
                args=args,
                raw_event=raw_event,
            )
            normalized_args = self._normalize_arg_tokens(ctx.args or [])
            project_key, issue_number = self._extract_project_and_issue("summary", normalized_args)
            workflow_id = None
            if not issue_number and len(normalized_args) == 1:
                token = str(normalized_args[0] or "").strip()
                if token and not _ISSUE_REF_RE.fullmatch(token) and not _ISSUE_TOKEN_RE.fullmatch(token):
                    workflow_id = token
            if not workflow_id and issue_number:
                workflow_id = self._lookup_workflow_id(issue_number) or None

            payload = await self.get_workflow_summary(workflow_id=workflow_id, issue_number=issue_number)
            if not payload.get("ok"):
                await ctx.reply_text(str(payload.get("error") or "Workflow summary unavailable"), parse_mode=None)
                return
            diagnosis = await self.get_workflow_diagnosis(workflow_id=workflow_id, issue_number=issue_number)
            lines = [str(payload.get("summary") or "Workflow summary unavailable")]
            if diagnosis.get("likely_cause"):
                lines.append(f"Likely cause: {diagnosis.get('likely_cause')}")
            actions = diagnosis.get("suggested_actions") or payload.get("suggested_actions") or []
            if actions:
                lines.append("Suggested actions: " + ", ".join(str(item) for item in actions))
            await ctx.reply_text("\n".join(lines), parse_mode=None)

        return _callback

    def _usage_callback(self) -> Callable[..., Awaitable[None]]:
        async def _callback(
            *,
            client: InteractiveClientPlugin,
            user_id: str,
            text: str,
            args: list[str] | None = None,
            raw_event: Any = None,
            attachments: list[Any] | None = None,
            user_state: dict[str, Any] | None = None,
        ) -> None:
            del text, attachments, user_state
            ctx = self.build_context(
                client=client,
                user_id=user_id,
                text="usage",
                args=args,
                raw_event=raw_event,
            )
            usage, summary = await self._resolve_usage_for_context(ctx)
            if usage is None:
                await ctx.reply_text(
                    summary
                    or "No Nexus ARC usage details are available for that command or workflow yet.",
                    parse_mode=None,
                )
                return
            if isinstance(raw_event, dict):
                raw_event["bridge_usage"] = usage.to_dict()
            await ctx.reply_text(summary, parse_mode=None)

        return _callback

    async def _prompt_project_selection(self, ctx: CommandExecutionContext, command: str) -> None:
        buttons = [
            [Button(label=get_project_label(project_key, self._projects), callback_data=f"command:{command}:{project_key}")]
            for project_key in self._iter_project_keys()
        ]
        await ctx.reply_text(
            f"Please select a project for `{command}` or provide one explicitly, for example: `/{command} <project> #123`.",
            buttons=buttons,
            parse_mode=None,
        )

    async def _ensure_project(self, ctx: CommandExecutionContext, command: str) -> str | None:
        args = self._normalize_arg_tokens(ctx.args or [])
        project_keys = self._iter_project_keys()
        if not args:
            candidate = single_key(project_keys)
            if candidate:
                return candidate
            await self._prompt_project_selection(ctx, command)
            return None
        raw = str(args[0] or "").strip().lower()
        if raw == "all":
            return "all"
        normalized = self._normalize_project_key(raw)
        if normalized in project_keys:
            return normalized
        await ctx.reply_text(f"❌ Unknown project '{raw}'.", parse_mode=None)
        return None

    async def _ensure_project_issue(
        self, ctx: CommandExecutionContext, command: str
    ) -> tuple[str | None, str | None, list[str]]:
        args = self._normalize_arg_tokens(ctx.args or [])
        project_keys = self._iter_project_keys()
        default_project = single_key(project_keys)
        project_key, issue_num, rest = parse_project_issue_args(
            args=args,
            normalize_project_key=self._normalize_project_key,
        )
        if project_key and issue_num:
            if project_key not in project_keys:
                await ctx.reply_text(f"❌ Unknown project '{project_key}'.", parse_mode=None)
                return None, None, []
            if not issue_num.isdigit():
                await ctx.reply_text("❌ Invalid issue number.", parse_mode=None)
                return None, None, []
            return project_key, issue_num, rest

        if len(args) == 1:
            token = str(args[0] or "").strip()
            issue_match = _ISSUE_TOKEN_RE.match(token)
            if issue_match:
                if default_project:
                    return default_project, issue_match.group("issue"), []
                await self._prompt_project_selection(ctx, command)
                return None, None, []
            maybe_project = self._normalize_project_key(token)
            if maybe_project and maybe_project in project_keys:
                await ctx.reply_text(
                    f"Please provide an issue number for `{maybe_project}`. Example: `/{command} {maybe_project} #123`.",
                    parse_mode=None,
                )
                return None, None, []

        if default_project:
            await ctx.reply_text(
                f"Please provide an issue number. Example: `/{command} {default_project} #123`.",
                parse_mode=None,
            )
            return None, None, []

        await self._prompt_project_selection(ctx, command)
        return None, None, []

    async def _resolve_usage_for_context(
        self, ctx: CommandExecutionContext
    ) -> tuple[Any | None, str]:
        args = self._normalize_arg_tokens(ctx.args or [])
        project_key, issue_number = self._extract_project_and_issue("usage", args)

        raw_event = ctx.raw_event if isinstance(ctx.raw_event, dict) else {}
        context_payload = raw_event.get("context") if isinstance(raw_event.get("context"), dict) else {}
        context = SessionContext.from_dict(context_payload if isinstance(context_payload, dict) else {})

        workflow_id = context.current_workflow_id or ""
        if not issue_number and context.current_issue_ref:
            issue_match = _ISSUE_REF_RE.fullmatch(str(context.current_issue_ref).strip())
            if issue_match:
                project_key = project_key or issue_match.group("project")
                issue_number = issue_match.group("issue")

        if not workflow_id and len(args) == 1:
            single = str(args[0] or "").strip()
            if single and not _ISSUE_REF_RE.fullmatch(single) and not _ISSUE_TOKEN_RE.fullmatch(single):
                workflow_id = single

        if not workflow_id and issue_number:
            workflow_id = self._lookup_workflow_id(issue_number) or ""

        usage = await collect_bridge_usage_payload(
            project_key=project_key,
            issue_number=issue_number,
            workflow_id=workflow_id or None,
        )
        if usage is None:
            if not issue_number and not workflow_id:
                return None, (
                    "Usage: /nexus usage <project> <issue#>\n"
                    "Or run it after selecting a project/issue with /nexus use, /nexus plan, or /nexus current."
                )
            target = workflow_id or (f"{project_key}#{issue_number}" if project_key and issue_number else issue_number or "")
            return None, f"No recent Nexus ARC usage details were found for {target}."

        summary_lines = ["Nexus ARC usage summary:"]
        if project_key:
            summary_lines.append(f"Project: {project_key}")
        if issue_number:
            summary_lines.append(f"Issue: {issue_number}")
        if workflow_id:
            summary_lines.append(f"Workflow: {workflow_id}")
        if usage.provider:
            summary_lines.append(f"Provider: {usage.provider}")
        if usage.model:
            summary_lines.append(f"Model: {usage.model}")
        if usage.input_tokens is not None:
            summary_lines.append(f"Input Tokens: {usage.input_tokens}")
        if usage.output_tokens is not None:
            summary_lines.append(f"Output Tokens: {usage.output_tokens}")
        if usage.metadata.get("total_tokens") is not None:
            summary_lines.append(f"Total Tokens: {usage.metadata.get('total_tokens')}")
        if usage.estimated_cost_usd is not None:
            summary_lines.append(f"Estimated Cost USD: {usage.estimated_cost_usd:.4f}")
        source = str(usage.metadata.get("source") or "").strip()
        if source:
            summary_lines.append(f"Source: {source}")
        return usage, "\n".join(summary_lines)

    def _normalize_arg_tokens(self, args: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in args:
            token = str(raw or "").strip()
            if not token:
                continue
            match = _ISSUE_REF_RE.fullmatch(token)
            if match:
                normalized.extend([match.group("project"), match.group("issue")])
                continue
            normalized.append(token)
        return normalized

    def _route_text_to_command(self, raw_text: str) -> tuple[str, list[str], str | None]:
        text = str(raw_text or "").strip()
        if not text:
            return "", [], self._clarification_message()
        tokens = text.split()
        first = tokens[0].lstrip("/").lower()
        if first in OPENCLAW_BRIDGE_COMMANDS:
            return first, self._normalize_arg_tokens(tokens[1:]), None

        lowered = text.lower()
        command = ""
        if re.search(r"\b(summary|summarize|summarise)\b.*\bworkflow\b", lowered) or re.search(r"\bworkflow\b.*\b(summary|summarize|summarise)\b", lowered) or re.search(r"\bwhy\b.*\bstuck\b", lowered):
            command = "summary"
        elif "workflow state" in lowered or "wfstate" in lowered:
            command = "wfstate"
        elif re.search(r"\bshow\b.*\blogs\b", lowered) or re.search(r"\blogs\b", lowered):
            command = "logs"
        elif re.search(r"\bactive\b", lowered):
            command = "active"
        elif re.search(r"\bstatus\b", lowered):
            command = "status"
        elif re.search(r"\baudit\b", lowered):
            command = "audit"
        elif re.search(r"\bstats?\b|\bstatistics\b|\bmetrics\b", lowered):
            command = "stats"
        elif re.search(r"\bagents?\b", lowered):
            command = "agents"
        elif re.search(r"\bpause\b", lowered):
            command = "pause"
        elif re.search(r"\bresume\b", lowered):
            command = "resume"
        elif re.search(r"\breconcile\b|\bsync\b.*\bsignals?\b|\brecover\b.*\bstate\b", lowered):
            command = "reconcile"
        elif re.search(r"\breprocess\b|\brestart\b.*\bworkflow\b|\brestart\b.*\bagent\b", lowered):
            command = "reprocess"
        elif re.search(r"\bkill\b.*\bagent\b|\bterminate\b.*\bagent\b", lowered):
            command = "kill"
        elif re.search(r"\bstop\b", lowered):
            command = "stop"
        elif re.search(r"\bcontinue\b", lowered):
            command = "continue"
        elif re.search(r"\bplan\b|\bplanning\b", lowered):
            command = "plan"
        elif re.search(r"\bnew\b|\bcreate\b.*\btask\b", lowered):
            command = "new"
        elif re.search(r"\bprepare\b", lowered):
            command = "prepare"
        elif re.search(r"\bimplement\b|\bimplementation\b", lowered):
            command = "implement"
        elif re.search(r"\brespond\b|\breply\b", lowered):
            command = "respond"
        elif re.search(r"\btracked\b", lowered):
            command = "tracked"
        elif re.search(r"\btrack\b", lowered):
            command = "track"
        elif re.search(r"\bmy issues\b|\bmyissues\b", lowered):
            command = "myissues"
        elif re.search(r"\busage\b|\bspend(?:ing)?\b|\bcost\b", lowered):
            command = "usage"

        if not command:
            return "", [], self._clarification_message()

        routed_args = self._extract_args_from_text(command, text)
        return command, routed_args, None

    def _extract_args_from_text(self, command: str, text: str) -> list[str]:
        match = _ISSUE_REF_RE.search(text)
        if match:
            return [match.group("project"), match.group("issue")]

        issue_match = re.search(r"\bissue\s+#?(?P<issue>\d+)\b", text, re.IGNORECASE)
        if issue_match:
            return [issue_match.group("issue")]

        project_keys = self._iter_project_keys()
        lowered = text.lower()
        for project_key in project_keys:
            if re.search(rf"\b{re.escape(project_key)}\b", lowered):
                if command in {"active", "status", "stats"}:
                    return [project_key]
                return [project_key]
        return []

    def _clarification_message(self) -> str:
        supported = ", ".join(sorted(OPENCLAW_BRIDGE_COMMANDS))
        return (
            "I could not map that request to a supported Nexus ARC command. "
            f"Supported bridge commands: {supported}."
        )

    def _extract_project_and_issue(
        self, command_name: str, args: list[str]
    ) -> tuple[str | None, str | None]:
        if command_name in {"active", "status", "stats"} and args:
            raw_project = str(args[0] or "").strip()
            normalized = self._normalize_project_key(raw_project)
            if normalized in self._iter_project_keys():
                issue_num = str(args[1]).lstrip("#") if len(args) > 1 and str(args[1]).lstrip("#").isdigit() else None
                return normalized, issue_num
        project_key, issue_num, _ = parse_project_issue_args(
            args=args,
            normalize_project_key=self._normalize_project_key,
        )
        if issue_num is None and args:
            token = str(args[0] or "").strip()
            issue_match = _ISSUE_TOKEN_RE.match(token)
            if issue_match:
                return single_key(self._iter_project_keys()), issue_match.group("issue")
        return project_key, issue_num

    def _lookup_workflow_id(self, issue_number: str | None) -> str | None:
        if not issue_number:
            return None
        return get_workflow_state().get_workflow_id(str(issue_number))

    def _issue_number_for_workflow_id(self, workflow_id: str) -> str | None:
        mappings = get_workflow_state().load_all_mappings()
        for issue_number, mapped_workflow_id in mappings.items():
            if str(mapped_workflow_id) == workflow_id:
                return str(issue_number)
        return None

    def _project_key_from_workflow_id(self, workflow_id: str) -> str | None:
        prefix = str(workflow_id or "").split("-", 1)[0].strip().lower()
        if prefix in self._iter_project_keys():
            return prefix
        return None

    def _suggested_next_commands(
        self,
        *,
        command_name: str,
        project_key: str | None,
        issue_number: str | None,
        workflow_id: str | None,
    ) -> list[str]:
        suggestions: list[str] = []
        if workflow_id:
            suggestions.append(f"/nexus wfstate {project_key or ''} #{issue_number or ''}".strip())
        elif project_key and issue_number and command_name in _LONG_RUNNING_COMMANDS:
            suggestions.append(f"/nexus wfstate {project_key} #{issue_number}")
        return suggestions

    def _iter_project_keys(self) -> list[str]:
        return iter_project_keys(project_config=PROJECT_CONFIG)

    @staticmethod
    def _normalize_project_key(value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_project_key(str(value))


def _command_title(command_name: str) -> str:
    text = str(command_name or "").strip().replace("_", " ")
    return " ".join(part.capitalize() for part in text.split()) or "Nexus ARC"


def _build_ui_fields(
    *,
    command_name: str,
    project_key: str | None,
    issue_number: str | None,
    workflow_id: str | None,
    context: SessionContext,
) -> list[UiField]:
    fields = [UiField("Command", command_name)]
    current_project = project_key or context.current_project
    current_issue_ref = context.current_issue_ref or (
        f"{project_key}#{issue_number}" if project_key and issue_number else None
    )
    if current_project:
        fields.append(UiField("Project", current_project))
    if current_issue_ref:
        fields.append(UiField("Issue", current_issue_ref))
    if workflow_id:
        fields.append(UiField("Workflow", workflow_id))
    if context.current_workflow_id and context.current_workflow_id != workflow_id:
        fields.append(UiField("Session Workflow", context.current_workflow_id))
    return fields
