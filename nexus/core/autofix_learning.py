"""Autofix learning helpers.

This module provides lightweight primitives to capture and retrieve autofix
attempt signals from audit history.
"""

from __future__ import annotations

import re
from typing import Any

_AUTOFIX_KEYWORDS = (
    "autofix",
    "fix",
    "fixed",
    "fixing",
    "patch",
    "repair",
    "hotfix",
)

_PATH_PATTERN = re.compile(r"(?:[a-zA-Z]:)?[/~]?[\w.\-/]+(?:\.[A-Za-z0-9_]+)?")
_NUMBER_PATTERN = re.compile(r"\b\d+\b")
_HEX_PATTERN = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)
_SPACE_PATTERN = re.compile(r"\s+")


def build_error_fingerprint(error: str, *, max_length: int = 160) -> str:
    """Build a stable-ish fingerprint from raw error text."""
    text = str(error or "").strip().lower()
    if not text:
        return "unknown-error"

    # Remove highly variable tokens before truncation.
    text = _HEX_PATTERN.sub("<hex>", text)
    text = _NUMBER_PATTERN.sub("<num>", text)
    text = _PATH_PATTERN.sub("<path>", text)
    text = _SPACE_PATTERN.sub(" ", text).strip(" .,:;-")
    if not text:
        return "unknown-error"
    return text[:max_length]


def is_autofix_candidate(
    *,
    step_name: str,
    agent_type: str,
    outputs: dict[str, Any] | None,
    error: str | None,
) -> bool:
    """Return True when a step likely represents autofix behavior."""
    step = str(step_name or "").strip().lower()
    agent = str(agent_type or "").strip().lower()

    if any(keyword in step for keyword in _AUTOFIX_KEYWORDS):
        return True

    # Explicit fix/patch signal from outputs.
    if isinstance(outputs, dict):
        key_blob = " ".join(str(k).lower() for k in outputs.keys())
        if "fix" in key_blob or "patch" in key_blob:
            return True

    # Developer/debug steps that surfaced an error are strong candidates.
    if error and agent in {"developer", "debug"}:
        return True

    return False


def build_autofix_payload(
    *,
    step_num: int,
    step_name: str,
    agent_type: str,
    error: str | None,
    retry_count: int,
    retry_planned: bool,
    outputs: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build normalized payload for audit log entries."""
    strategy = ""
    if isinstance(outputs, dict):
        strategy = str(outputs.get("fix_strategy") or outputs.get("strategy") or "").strip()

    payload: dict[str, Any] = {
        "step_num": int(step_num),
        "step_name": str(step_name or ""),
        "agent_type": str(agent_type or ""),
        "retry_count": int(retry_count),
        "retry_planned": bool(retry_planned),
    }
    if strategy:
        payload["strategy"] = strategy
    if error:
        payload["error_fingerprint"] = build_error_fingerprint(error)
        payload["error_excerpt"] = str(error)[:300]
    return payload


def find_similar_autofix_attempts(
    audit_events: list[dict[str, Any]],
    *,
    agent_type: str | None = None,
    error_fingerprint: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Return recent autofix events filtered by agent and/or error fingerprint."""
    if not audit_events:
        return []

    normalized_agent = str(agent_type or "").strip().lower()
    normalized_fp = str(error_fingerprint or "").strip().lower()
    results: list[dict[str, Any]] = []

    for event in reversed(audit_events):
        event_type = str(event.get("event_type") or "").strip().upper()
        if event_type not in {"AUTOFIX_ATTEMPTED", "AUTOFIX_VALIDATED", "AUTOFIX_FAILED"}:
            continue

        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        current_agent = str(data.get("agent_type") or "").strip().lower()
        current_fp = str(data.get("error_fingerprint") or "").strip().lower()

        if normalized_agent and current_agent != normalized_agent:
            continue
        if normalized_fp and current_fp != normalized_fp:
            continue

        results.append(event)
        if len(results) >= max(1, limit):
            break

    return results
