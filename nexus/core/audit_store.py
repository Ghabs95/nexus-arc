"""Dedicated audit storage utilities for Nexus.

Keeps audit read/write concerns separate from generic state management.
Delegates to nexus-arc for standardized storage.
"""

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar, cast

from nexus.adapters.storage.file import FileStorage
from nexus.adapters.storage.structured_log import StructuredLogAuditBackend
from nexus.core.storage.audit import WorkflowAuditStore

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

def _run_coro_sync(
    coro_factory: Callable[[], Coroutine[Any, Any, _T]],
    *,
    timeout_seconds: float = 10,
) -> _T:
    """Run an async call from sync code, even if a loop is already running."""
    try:
        asyncio.get_running_loop()
        in_running_loop = True
    except RuntimeError:
        in_running_loop = False

    if not in_running_loop:
        return asyncio.run(coro_factory())

    value_holder: Any = None
    error_holder: Exception | None = None

    def _runner() -> None:
        nonlocal value_holder, error_holder
        try:
            value_holder = asyncio.run(coro_factory())
        except Exception as exc:  # pragma: no cover - defensive bridge
            error_holder = exc

    worker = threading.Thread(target=_runner, daemon=True)
    worker.start()
    worker.join(timeout=timeout_seconds)
    if worker.is_alive():
        raise TimeoutError("Timed out running async audit operation in worker thread")
    if error_holder is not None:
        raise error_holder
    return cast(_T, value_holder)


class AuditStore:
    """Read/write audit events using nexus-arc implementation."""

    _core_store = None
    _storage_dir_override: str | None = None

    @classmethod
    def configure(cls, *, storage_dir: str | None = None, reset: bool = False) -> None:
        """Configure storage parameters and optional singleton reset."""
        if storage_dir is not None:
            cls._storage_dir_override = storage_dir
        if reset:
            cls._core_store = None

    @classmethod
    def reset(cls) -> None:
        """Reset singleton core audit store (mainly for tests)."""
        cls._core_store = None
        cls._storage_dir_override = None

    @classmethod
    def _resolve_storage_dir(cls) -> str:
        if cls._storage_dir_override:
            return cls._storage_dir_override
        from nexus.core.config import get_runtime_settings

        return get_runtime_settings().nexus_core_storage_dir

    @classmethod
    def _get_core_store(cls):
        if cls._core_store is None:
            base_storage = FileStorage(base_path=cls._resolve_storage_dir())
            storage = StructuredLogAuditBackend(
                backend=base_storage,
                logger_name="nexus.audit",
                extra_labels={"app": "nexus", "env": "prod"},
            )
            cls._core_store = WorkflowAuditStore(storage=storage)
        return cls._core_store

    @staticmethod
    def audit_log(issue_num: int, event: str, details: str | None = None) -> None:
        """Log an audit event in nexus-arc JSONL format."""
        from nexus.core.integrations.workflow_state_factory import get_workflow_state

        workflow_id = get_workflow_state().get_workflow_id(str(issue_num)) or "_nexus_system"

        store = AuditStore._get_core_store()
        try:
            _run_coro_sync(
                lambda: store.log(
                    workflow_id=workflow_id,
                    event_type=event,
                    data={"issue_number": issue_num, "details": details},
                )
            )
            logger.debug(f"Audit: #{issue_num} {event}")
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")

    @staticmethod
    def get_audit_history(issue_num: int, limit: int = 50) -> list[dict]:
        """Get recent audit events for an issue."""
        from nexus.core.integrations.workflow_state_factory import get_workflow_state

        workflow_id = get_workflow_state().get_workflow_id(str(issue_num))
        if not workflow_id:
            return []

        store = AuditStore._get_core_store()
        try:
            events = _run_coro_sync(lambda: store.get_workflow_history(workflow_id, limit=limit))
            return [
                {
                    "workflow_id": e.workflow_id,
                    "timestamp": e.timestamp.isoformat(),
                    "event_type": e.event_type,
                    "data": e.data,
                    "user_id": e.user_id,
                }
                for e in events[-limit:]
            ]
        except Exception as e:
            logger.error(f"Failed to read audit log: {e}")
            return []
