import json
import os
import threading
from collections.abc import Callable
from typing import Any


def _normalize_backend(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"database", "postgres", "postgresql"}:
        return "database"
    return "filesystem"


def _effective_state_backend(storage_backend: str | None = None) -> str:
    host_state_backend = str(os.getenv("NEXUS_HOST_STATE_BACKEND", "")).strip()
    if host_state_backend:
        return _normalize_backend(host_state_backend)
    if storage_backend:
        return _normalize_backend(storage_backend)
    return _normalize_backend(os.getenv("NEXUS_STORAGE_BACKEND", "filesystem"))


def _host_state_key(path: str, state_key: str | None = None) -> str:
    if state_key:
        return str(state_key).strip()
    return os.path.splitext(os.path.basename(path))[0]


def _run_coro_sync(coro_factory: Callable[[], Any], *, timeout_seconds: float = 10) -> Any:
    import asyncio

    try:
        asyncio.get_running_loop()
        in_running_loop = True
    except RuntimeError:
        in_running_loop = False

    if not in_running_loop:
        return asyncio.run(coro_factory())

    holder: dict[str, Any] = {"value": None, "error": None}

    def _runner() -> None:
        try:
            holder["value"] = asyncio.run(coro_factory())
        except Exception as exc:  # pragma: no cover - defensive bridge
            holder["error"] = exc

    worker = threading.Thread(target=_runner, daemon=True)
    worker.start()
    worker.join(timeout=timeout_seconds)
    if worker.is_alive():
        raise TimeoutError("Timed out running async host-state operation in worker thread")
    if holder["error"] is not None:
        raise holder["error"]
    return holder["value"]


def _get_host_state_backend(logger):
    try:
        from nexus.core.integrations.workflow_state_factory import get_storage_backend

        return get_storage_backend()
    except Exception as exc:
        logger.warning("Failed to initialize database host-state backend: %s", exc)
        return None


def _load_local_json(*, path: str, logger, warn_only: bool = False) -> dict[str, Any]:
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
                return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        if warn_only:
            logger.warning("Failed to read state file %s: %s", path, exc)
        else:
            logger.error("Error loading state file %s: %s", path, exc)
    return {}


def load_json_state_file(
    *,
    path: str,
    logger,
    warn_only: bool = False,
    storage_backend: str | None = None,
    state_key: str | None = None,
    migrate_local_on_empty: bool = True,
) -> dict[str, Any]:
    if _effective_state_backend(storage_backend) == "database":
        backend = _get_host_state_backend(logger)
        if backend is not None:
            key = _host_state_key(path, state_key)
            try:
                payload = _run_coro_sync(lambda: backend.load_host_state(key))
                if isinstance(payload, dict):
                    return payload
                if payload is not None:
                    logger.warning(
                        "Ignoring unexpected host-state payload type for %s: %s",
                        key,
                        type(payload).__name__,
                    )
                if migrate_local_on_empty:
                    local_payload = _load_local_json(path=path, logger=logger, warn_only=warn_only)
                    if local_payload:
                        _run_coro_sync(lambda: backend.save_host_state(key, local_payload))
                        logger.info(
                            "Bootstrapped host-state key '%s' from local file %s",
                            key,
                            path,
                        )
                    return local_payload
                return {}
            except Exception as exc:
                log_fn = logger.warning if warn_only else logger.error
                log_fn("Failed to load database host-state key %s: %s", key, exc)

    return _load_local_json(path=path, logger=logger, warn_only=warn_only)


def save_json_state_file(
    *,
    path: str,
    data: dict[str, Any],
    logger,
    warn_only: bool = False,
    storage_backend: str | None = None,
    state_key: str | None = None,
) -> None:
    normalized = data if isinstance(data, dict) else {}
    if _effective_state_backend(storage_backend) == "database":
        backend = _get_host_state_backend(logger)
        if backend is not None:
            key = _host_state_key(path, state_key)
            try:
                _run_coro_sync(lambda: backend.save_host_state(key, normalized))
                return
            except Exception as exc:
                log_fn = logger.warning if warn_only else logger.error
                log_fn("Failed to save database host-state key %s: %s", key, exc)

    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, indent=2)
    except Exception as exc:
        if warn_only:
            logger.warning("Failed to save state file %s: %s", path, exc)
        else:
            logger.error("Error saving state file %s: %s", path, exc)


def get_completion_replay_window_seconds(
    *, getenv: Callable[[str, str], str], default_seconds: int = 1800
) -> int:
    raw = getenv("NEXUS_COMPLETION_REPLAY_WINDOW_SECONDS", str(default_seconds))
    try:
        value = int(str(raw).strip())
        return max(0, value)
    except Exception:
        return default_seconds
