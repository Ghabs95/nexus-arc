import json
import os
from collections.abc import Callable
from typing import Any


def load_json_state_file(*, path: str, logger, warn_only: bool = False) -> dict[str, Any]:
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


def save_json_state_file(
    *,
    path: str,
    data: dict[str, Any],
    logger,
    warn_only: bool = False,
) -> None:
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
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
