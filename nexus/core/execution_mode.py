"""Helpers for persistent issue execution-mode metadata."""

from __future__ import annotations

import re
from collections.abc import Iterable

PLANNING_EXECUTION_MODE = "planning"
_EXECUTION_MODE_ALIASES = {
    "plan": PLANNING_EXECUTION_MODE,
    "planning": PLANNING_EXECUTION_MODE,
}
_EXECUTION_MODE_PATTERN = re.compile(
    r"\*\*Execution Mode:\*\*\s*`?([A-Za-z0-9_-]+)`?",
    re.IGNORECASE,
)


def normalize_execution_mode(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return _EXECUTION_MODE_ALIASES.get(normalized)


def parse_execution_mode_from_text(text: str | None) -> str | None:
    if not text:
        return None
    match = _EXECUTION_MODE_PATTERN.search(str(text))
    if not match:
        return None
    return normalize_execution_mode(str(match.group(1) or ""))


def infer_execution_mode_from_labels(labels: Iterable[object] | None) -> str | None:
    if not labels:
        return None
    for label in labels:
        if str(label or "").strip().lower() == "agent:plan-requested":
            return PLANNING_EXECUTION_MODE
    return None
