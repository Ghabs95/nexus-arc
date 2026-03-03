"""Dynamic plugin hot-reload support for Nexus Core.

Provides :class:`HotReloadWatcher`, which monitors a directory for ``.py`` file
changes and reloads matching plugins into a :class:`PluginRegistry` without
restarting any core services.

Requires the ``watchdog`` package (``pip install nexus-arc[hotreload]``).
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import types
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class HandoffManager:
    """Track and resolve active agent delegations within a workflow.

    Thread-safe registry for :class:`~nexus.core.models.DelegationRequest`
    objects. Injected into :class:`~nexus.core.orchestrator.AIOrchestrator`
    as an optional dependency — existing code paths are unaffected when
    no ``HandoffManager`` is provided.

    Example::

        from nexus.plugins.plugin_runtime import HandoffManager
        from nexus.core.models import DelegationRequest

        manager = HandoffManager()
        req = DelegationRequest(
            lead_agent="developer",
            sub_agent="reviewer",
            issue_number="42",
            workflow_id="nexus-42-full",
            task_description="Review the PR",
        )
        manager.register(req)
        ...
        manager.complete(callback)
    """

    def __init__(self) -> None:
        # delegation_id → DelegationRequest
        self._active: dict[str, DelegationRequest] = {}  # noqa: F821
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, request: DelegationRequest) -> None:  # noqa: F821
        """Register *request* as an active delegation."""
        from nexus.core.models import DelegationStatus

        with self._lock:
            request.status = DelegationStatus.ACTIVE
            self._active[request.delegation_id] = request
        logger.debug(
            "Delegation registered: %s (%s → %s)",
            request.delegation_id,
            request.lead_agent,
            request.sub_agent,
        )

    def complete(
        self, callback: DelegationCallback  # noqa: F821
    ) -> DelegationRequest | None:  # noqa: F821
        """Mark the delegation identified by *callback* as completed.

        Returns the original :class:`~nexus.core.models.DelegationRequest` or
        ``None`` if the delegation is unknown.
        """
        from nexus.core.models import DelegationStatus

        with self._lock:
            request = self._active.pop(callback.delegation_id, None)
        if request is None:
            logger.warning(
                "complete() called for unknown delegation_id: %s",
                callback.delegation_id,
            )
            return None
        request.status = DelegationStatus.COMPLETED
        logger.debug("Delegation completed: %s", callback.delegation_id)
        return request

    def fail(self, delegation_id: str, error: str) -> None:
        """Mark delegation *delegation_id* as failed with *error*."""
        from nexus.core.models import DelegationStatus

        with self._lock:
            request = self._active.pop(delegation_id, None)
        if request is not None:
            request.status = DelegationStatus.FAILED
            logger.debug("Delegation failed: %s — %s", delegation_id, error)
        else:
            logger.warning("fail() called for unknown delegation_id: %s", delegation_id)

    def expire_stale(self) -> list[DelegationRequest]:  # noqa: F821
        """Expire delegations whose ``expires_at`` timestamp has passed.

        Returns the list of newly expired requests.
        """
        from nexus.core.models import DelegationStatus

        now = datetime.now(UTC).isoformat()
        expired: list = []
        with self._lock:
            stale_ids = [
                did
                for did, req in self._active.items()
                if req.expires_at is not None and req.expires_at < now
            ]
            for did in stale_ids:
                req = self._active.pop(did)
                req.status = DelegationStatus.EXPIRED
                expired.append(req)
        if expired:
            logger.info("Expired %d stale delegation(s)", len(expired))
        return expired

    def get(self, delegation_id: str) -> DelegationRequest | None:  # noqa: F821
        """Return the active delegation with *delegation_id*, or ``None``."""
        with self._lock:
            return self._active.get(delegation_id)

    def pending_for(
        self, lead_agent: str, workflow_id: str
    ) -> list[DelegationRequest]:  # noqa: F821
        """Return all active delegations for *lead_agent* in *workflow_id*."""
        with self._lock:
            return [
                req
                for req in self._active.values()
                if req.lead_agent == lead_agent and req.workflow_id == workflow_id
            ]


# Deferred import so that the rest of nexus-arc stays importable even when
# watchdog is not installed (hot-reload is an optional feature).
try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True
except ImportError:  # pragma: no cover
    _WATCHDOG_AVAILABLE = False
    FileSystemEvent = object  # type: ignore[assignment,misc]
    FileSystemEventHandler = object  # type: ignore[assignment,misc]
    Observer = None  # type: ignore[assignment,misc]


class HotReloadWatcher:
    """Watch a directory for ``.py`` file changes and reload plugins.

    The watcher is opt-in and must be explicitly started with :meth:`start`.
    It has no effect on existing code paths that do not use it.

    Example::

        from nexus.plugins.plugin_runtime import HotReloadWatcher
        watcher = HotReloadWatcher(registry, watch_dir="/my/plugins")
        watcher.start()
        ...
        watcher.stop()

    Plugin files must expose one of:
    - A ``register_plugins(registry)`` function (``RegistryContributor`` protocol), or
    - A top-level callable that accepts a :class:`PluginRegistry`.

    Args:
        registry: The :class:`~nexus.plugins.registry.PluginRegistry` to update.
        watch_dir: Path to the directory containing plugin ``.py`` files.
        poll_interval: Seconds between filesystem event checks (default 1.0).
    """

    def __init__(
        self,
        registry: PluginRegistry,  # noqa: F821 – forward ref
        watch_dir: str | Path,
        poll_interval: float = 1.0,
    ) -> None:
        if not _WATCHDOG_AVAILABLE:
            raise ImportError(
                "watchdog is required for hot-reload. "
                "Install it with: pip install 'nexus-arc[hotreload]'"
            )
        from nexus.plugins.registry import PluginRegistry  # local import avoids circulars

        if not isinstance(registry, PluginRegistry):
            raise TypeError(f"Expected PluginRegistry, got {type(registry).__name__}")

        self._registry = registry
        self._watch_dir = Path(watch_dir).resolve()
        self._poll_interval = poll_interval
        self._observer: Observer | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the filesystem observer in a background thread."""
        if self._observer is not None and self._observer.is_alive():
            logger.warning("HotReloadWatcher is already running")
            return

        handler = _PluginFileEventHandler(self._registry, self._watch_dir)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._watch_dir), recursive=False)
        self._observer.start()
        logger.info("HotReloadWatcher started, watching %s", self._watch_dir)

    def stop(self) -> None:
        """Stop the filesystem observer and clean up."""
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join()
        self._observer = None
        logger.info("HotReloadWatcher stopped")

    def is_running(self) -> bool:
        """Return True if the background observer thread is alive."""
        return self._observer is not None and self._observer.is_alive()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

if _WATCHDOG_AVAILABLE:

    class _PluginFileEventHandler(FileSystemEventHandler):
        """Handles file-system events and triggers registry refresh."""

        def __init__(self, registry: PluginRegistry, watch_dir: Path) -> None:  # noqa: F821
            super().__init__()
            self._registry = registry
            self._watch_dir = watch_dir

        # watchdog calls on_modified for both file modifications and moves
        def on_modified(self, event: FileSystemEvent) -> None:
            if getattr(event, "is_directory", False):
                return
            src: str = getattr(event, "src_path", "")
            if src.endswith(".py"):
                self._reload_plugin_file(Path(src))

        on_created = on_modified  # also handle newly dropped files

        def _reload_plugin_file(self, plugin_path: Path) -> None:
            """Load a plugin file and update the registry."""
            module = _load_module_from_path(plugin_path)
            if module is None:
                return

            # Try RegistryContributor protocol first
            register_fn = getattr(module, "register_plugins", None)
            if callable(register_fn):
                try:
                    register_fn(self._registry)
                    logger.info("Hot-reloaded plugin file: %s", plugin_path)
                except Exception as exc:
                    logger.warning("Failed to register plugins from %s: %s", plugin_path, exc)
                return

            # Fallback: module itself is callable
            if callable(module):
                try:
                    module(self._registry)
                    logger.info("Hot-reloaded plugin file (callable): %s", plugin_path)
                except Exception as exc:
                    logger.warning("Failed to load callable plugin %s: %s", plugin_path, exc)
                return

            logger.warning(
                "Plugin file %s does not expose register_plugins() and is not callable; skipping",
                plugin_path,
            )

else:
    # Stub so tests can import the module without watchdog installed
    class _PluginFileEventHandler:  # type: ignore[no-redef]
        pass


def _load_module_from_path(path: Path) -> types.ModuleType | None:
    """Load a Python module from *path* using a fresh module object.

    Uses ``importlib.util.spec_from_file_location`` + ``module_from_spec`` so
    each load is fully isolated — no stale references leak through
    ``sys.modules``.
    """
    module_name = f"_nexus_hotreload_{path.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            logger.warning("Cannot create module spec from %s", path)
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        return module
    except Exception as exc:
        logger.warning("Error loading plugin module %s: %s", path, exc)
        return None
