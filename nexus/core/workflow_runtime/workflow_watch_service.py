"""Live workflow watch service for Telegram `/watch` command."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from nexus.core.config import WEBHOOK_PORT
from nexus.core.state_manager import HostStateManager

try:
    import socketio
except Exception:  # pragma: no cover - optional dependency fallback
    socketio = None

logger = logging.getLogger(__name__)

_DEFAULT_NAMESPACE = "/visualizer"
_DEFAULT_THROTTLE_SECONDS = 2.0


@dataclass
class WatchSubscription:
    chat_id: int
    user_id: int
    project_key: str
    issue_num: str
    workflow_id: str = ""
    mermaid_enabled: bool = False
    last_event_at: float = 0.0
    last_event_key: str = ""
    last_sent_at: float = 0.0
    updated_at: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.chat_id}:{self.user_id}"


class WorkflowWatchService:
    """Tracks workflow watch subscriptions and relays live visualizer events."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscriptions: dict[str, WatchSubscription] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sender: Any = None
        self._snapshot_fetcher: Callable[[str, str], dict[str, Any]] | None = None
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._client_started = False
        self._load_subscriptions()

    def is_enabled(self) -> bool:
        return os.getenv("NEXUS_TELEGRAM_WATCH_ENABLED", "true").strip().lower() == "true"

    def bind_runtime(self, *, loop: asyncio.AbstractEventLoop, sender: Any) -> None:
        """Bind async runtime pieces used to deliver Telegram messages."""
        self._loop = loop
        self._sender = sender

    def bind_snapshot_fetcher(self, fetcher: Callable[[str, str], dict[str, Any]]) -> None:
        """Bind helper to fetch current workflow state snapshots."""
        self._snapshot_fetcher = fetcher

    def ensure_started(self) -> None:
        """Start the Socket.IO listener worker when needed."""
        if not self.is_enabled() or socketio is None:
            return
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._stop_event.clear()
            self._worker = threading.Thread(
                target=self._run_socket_worker,
                daemon=True,
                name="telegram-watch-listener",
            )
            self._worker.start()

    def start_watch(
        self,
        *,
        chat_id: int,
        user_id: int,
        project_key: str,
        issue_num: str,
        mermaid_enabled: bool = False,
    ) -> dict[str, Any]:
        """Create or replace a watch session for a chat/user."""
        now = time.time()
        sub = WatchSubscription(
            chat_id=chat_id,
            user_id=user_id,
            project_key=str(project_key),
            issue_num=str(issue_num),
            mermaid_enabled=bool(mermaid_enabled),
            updated_at=now,
        )
        with self._lock:
            replaced = sub.key in self._subscriptions
            self._subscriptions[sub.key] = sub
            self._save_subscriptions()
        self.ensure_started()
        self._send_initial_snapshot(sub)
        return {"ok": True, "replaced": replaced, "subscription": asdict(sub)}

    def stop_watch(
        self,
        *,
        chat_id: int,
        user_id: int,
        project_key: str | None = None,
        issue_num: str | None = None,
    ) -> int:
        """Stop current watch for a chat/user (optionally matching project/issue)."""
        removed = 0
        key = f"{chat_id}:{user_id}"
        with self._lock:
            current = self._subscriptions.get(key)
            if not current:
                return 0
            if project_key and current.project_key != str(project_key):
                return 0
            if issue_num and current.issue_num != str(issue_num):
                return 0
            self._subscriptions.pop(key, None)
            removed = 1
            self._save_subscriptions()
        return removed

    def set_mermaid(self, *, chat_id: int, user_id: int, enabled: bool) -> bool:
        """Toggle Mermaid updates for the active watch session."""
        key = f"{chat_id}:{user_id}"
        with self._lock:
            current = self._subscriptions.get(key)
            if not current:
                return False
            current.mermaid_enabled = bool(enabled)
            current.updated_at = time.time()
            self._subscriptions[key] = current
            self._save_subscriptions()
        return True

    def get_status(self, *, chat_id: int, user_id: int) -> dict[str, Any] | None:
        """Return active watch status for chat/user."""
        key = f"{chat_id}:{user_id}"
        with self._lock:
            current = self._subscriptions.get(key)
            if not current:
                return None
            return asdict(current)

    def _send_initial_snapshot(self, sub: WatchSubscription) -> None:
        """Send a one-time status snapshot to the chat if fetcher is available."""
        if not self._snapshot_fetcher:
            return
        try:
            snapshot = self._snapshot_fetcher(sub.issue_num, sub.project_key)
            if not snapshot:
                return
            state = str(snapshot.get("workflow_state", "unknown"))
            step = str(snapshot.get("current_step", "?/?"))
            step_name = str(snapshot.get("current_step_name", "unknown"))
            agent = str(snapshot.get("current_agent", "unknown"))
            msg = (
                f"👀 Watching workflow #{sub.issue_num} ({sub.project_key})\n"
                f"Status: {state}\n"
                f"Step: {step} ({step_name})\n"
                f"Agent: {agent}"
            )
            self._send_message(sub.chat_id, msg)
        except Exception as exc:
            logger.warning("Failed to send initial snapshot for #%s: %s", sub.issue_num, exc)

    def _send_reconnect_snapshots(self) -> None:
        """Send a recovery status snapshot to all active subscribers on reconnect."""
        if not self._snapshot_fetcher:
            return
        with self._lock:
            subs = list(self._subscriptions.values())
        for sub in subs:
            # Reconnect recovery: providing current state in case events were missed
            self._send_initial_snapshot(sub)

    def _load_subscriptions(self) -> None:
        raw = HostStateManager.load_workflow_watch_subscriptions()
        if not isinstance(raw, dict):
            return
        loaded: dict[str, WatchSubscription] = {}
        for key, item in raw.items():
            if not isinstance(item, dict):
                continue
            try:
                sub = WatchSubscription(
                    chat_id=int(item.get("chat_id")),
                    user_id=int(item.get("user_id")),
                    project_key=str(item.get("project_key", "")),
                    issue_num=str(item.get("issue_num", "")),
                    workflow_id=str(item.get("workflow_id", "")),
                    mermaid_enabled=bool(item.get("mermaid_enabled", False)),
                    last_event_at=float(item.get("last_event_at", 0.0)),
                    last_event_key=str(item.get("last_event_key", "")),
                    last_sent_at=float(item.get("last_sent_at", 0.0)),
                    updated_at=float(item.get("updated_at", 0.0)),
                )
            except Exception:
                continue
            if not sub.issue_num:
                continue
            loaded[key] = sub
        self._subscriptions = loaded

    def _save_subscriptions(self) -> None:
        payload = {key: asdict(value) for key, value in self._subscriptions.items()}
        HostStateManager.save_workflow_watch_subscriptions(payload)

    def _socket_url(self) -> str:
        default_url = f"http://127.0.0.1:{WEBHOOK_PORT}"
        return os.getenv("NEXUS_TELEGRAM_WATCH_URL", default_url).strip() or default_url

    def _socket_namespace(self) -> str:
        raw = os.getenv("NEXUS_TELEGRAM_WATCH_NAMESPACE", "").strip()
        if not raw:
            return _DEFAULT_NAMESPACE
        if not raw.startswith("/"):
            raw = "/" + raw
        return raw

    def _run_socket_worker(self) -> None:
        if socketio is None:  # pragma: no cover - import fallback
            logger.warning("Workflow watch disabled: python-socketio client not available")
            return

        namespace = self._socket_namespace()
        url = self._socket_url()
        backoff = 1.0

        client = socketio.Client(
            reconnection=True,
            reconnection_attempts=0,
            reconnection_delay=1,
            reconnection_delay_max=30,
            logger=False,
            engineio_logger=False,
        )

        transports = None
        try:
            import websocket  # type: ignore  # noqa: F401
        except Exception:
            transports = ["polling"]
            logger.warning(
                "websocket-client not installed; workflow watch will use polling transport"
            )

        @client.on("connect", namespace=namespace)
        def _on_connect() -> None:
            nonlocal backoff
            backoff = 1.0
            self._client_started = True
            logger.info("Telegram watch connected to %s%s", url, namespace)
            self._send_reconnect_snapshots()

        @client.on("disconnect", namespace=namespace)
        def _on_disconnect() -> None:
            logger.warning("Telegram watch disconnected from %s%s", url, namespace)

        @client.on("step_status_changed", namespace=namespace)
        def _on_step_status_changed(data: dict[str, Any]) -> None:
            self._handle_event("step_status_changed", data)

        @client.on("workflow_completed", namespace=namespace)
        def _on_workflow_completed(data: dict[str, Any]) -> None:
            self._handle_event("workflow_completed", data)

        @client.on("mermaid_diagram", namespace=namespace)
        def _on_mermaid_diagram(data: dict[str, Any]) -> None:
            self._handle_event("mermaid_diagram", data)

        while not self._stop_event.is_set():
            try:
                client.connect(url, namespaces=[namespace], wait_timeout=5, transports=transports)
                while client.connected and not self._stop_event.wait(0.25):
                    pass
            except Exception as exc:
                logger.warning("Telegram watch connect/reconnect failed: %s", exc)
            finally:
                if client.connected:
                    try:
                        client.disconnect()
                    except Exception:
                        pass

            if self._stop_event.wait(backoff):
                break
            backoff = min(backoff * 2, 30.0)

    def _handle_event(self, event_type: str, payload: dict[str, Any]) -> None:
        issue = str((payload or {}).get("issue", "")).strip()
        if not issue:
            return
        workflow_id = str((payload or {}).get("workflow_id", "")).strip()

        now = time.time()
        remove_keys: list[str] = []
        to_send: list[tuple[int, str]] = []

        with self._lock:
            for key, sub in self._subscriptions.items():
                if not self._subscription_matches_event(
                    subscription=sub,
                    issue=issue,
                    workflow_id=workflow_id,
                    payload=payload,
                ):
                    continue

                message = self._build_message(event_type, payload, sub)
                if not message:
                    continue

                event_key = self._event_key(event_type, payload)
                if event_key and event_key == sub.last_event_key:
                    continue

                should_throttle = event_type in {"step_status_changed", "mermaid_diagram"}
                if should_throttle and now - sub.last_sent_at < _DEFAULT_THROTTLE_SECONDS:
                    continue

                sub.last_event_key = event_key
                sub.last_event_at = now
                sub.last_sent_at = now
                sub.updated_at = now
                if workflow_id and not sub.workflow_id:
                    sub.workflow_id = workflow_id
                self._subscriptions[key] = sub
                to_send.append((sub.chat_id, message))

                if event_type == "workflow_completed":
                    remove_keys.append(key)

            for key in remove_keys:
                self._subscriptions.pop(key, None)

            if to_send or remove_keys:
                self._save_subscriptions()

        for chat_id, message in to_send:
            self._send_message(chat_id, message)

    def _event_key(self, event_type: str, payload: dict[str, Any]) -> str:
        workflow_id = str(payload.get("workflow_id", ""))
        if event_type == "step_status_changed":
            return (
                f"{event_type}:{payload.get('issue')}:{payload.get('step_id')}:"
                f"{payload.get('status')}:{workflow_id}"
            )
        if event_type == "workflow_completed":
            return f"{event_type}:{payload.get('issue')}:{payload.get('status')}:{workflow_id}"
        if event_type == "mermaid_diagram":
            diagram = str(payload.get("diagram", ""))
            digest = hashlib.sha1(diagram.encode("utf-8")).hexdigest()[:16]
            return f"{event_type}:{payload.get('issue')}:{workflow_id}:{digest}"
        return f"{event_type}:{payload.get('issue')}"

    def _subscription_matches_event(
        self,
        *,
        subscription: WatchSubscription,
        issue: str,
        workflow_id: str,
        payload: dict[str, Any],
    ) -> bool:
        if subscription.issue_num != issue:
            return False

        payload_project = str((payload or {}).get("project_key", "")).strip()
        if payload_project and payload_project != subscription.project_key:
            return False

        if subscription.workflow_id:
            if workflow_id and workflow_id != subscription.workflow_id:
                return False
            return True

        if workflow_id and subscription.project_key:
            expected_prefix = f"{subscription.project_key}-{subscription.issue_num}"
            return workflow_id == expected_prefix or workflow_id.startswith(f"{expected_prefix}-")

        return True

    def _build_message(
        self, event_type: str, payload: dict[str, Any], subscription: WatchSubscription
    ) -> str | None:
        issue = str(payload.get("issue", subscription.issue_num))
        if event_type == "step_status_changed":
            step_id = str(payload.get("step_id", "unknown"))
            agent_type = str(payload.get("agent_type", "agent"))
            status = str(payload.get("status", "unknown"))
            return f"▶️ #{issue} {agent_type} · {step_id} → {status}"

        if event_type == "workflow_completed":
            status = str(payload.get("status", "unknown"))
            summary = str(payload.get("summary", "")).strip()
            if summary:
                return f"✅ Workflow #{issue} completed: {status} — {summary}"
            return f"✅ Workflow #{issue} completed: {status}"

        if event_type == "mermaid_diagram" and subscription.mermaid_enabled:
            return f"🧭 Workflow #{issue} diagram updated."

        return None

    def _send_message(self, chat_id: int, text: str) -> None:
        if not self._loop or not self._sender:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._sender(chat_id, text), self._loop)

            def _done_callback(done: Any) -> None:
                try:
                    done.result()
                except Exception as exc:
                    logger.warning("Workflow watch send failed for chat %s: %s", chat_id, exc)

            future.add_done_callback(_done_callback)
        except Exception as exc:
            logger.warning("Workflow watch schedule send failed for chat %s: %s", chat_id, exc)


_service_singleton: WorkflowWatchService | None = None
_service_lock = threading.Lock()


def get_workflow_watch_service() -> WorkflowWatchService:
    """Return process-wide singleton watch service."""
    global _service_singleton
    if _service_singleton is None:
        with _service_lock:
            if _service_singleton is None:
                _service_singleton = WorkflowWatchService()
    return _service_singleton
