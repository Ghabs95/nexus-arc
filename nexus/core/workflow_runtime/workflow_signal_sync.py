"""Shared helpers for workflow/comment/local completion signal reconciliation."""

from __future__ import annotations

import glob
import json
import os
import re
from datetime import datetime
from typing import Any

from nexus.core.completion import budget_completion_payload
from nexus.core.storage.capabilities import get_storage_capabilities

_STEP_COMPLETE_COMMENT_RE = re.compile(
    r"^\s*##\s+.+?\bcomplete\b\s*[-–—:]\s*`?@?([a-zA-Z0-9_-]+)`?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_READY_FOR_COMMENT_RE = re.compile(
    r"\bready\s+for\s+(?:\*\*)?`?@?([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)


def _local_completions_enabled() -> bool:
    return get_storage_capabilities().local_completions


def normalize_agent_reference(agent_ref: str) -> str:
    """Normalize agent references used in comments/completion files."""
    value = str(agent_ref or "").strip()
    value = value.lstrip("@").strip()
    return value.strip("`").strip()


def extract_structured_completion_signals(comments: list[dict]) -> list[dict[str, str]]:
    """Extract (completed_agent -> next_agent) transitions from structured comments."""
    signals: list[dict[str, str]] = []
    for comment in comments or []:
        body = str(comment.get("body", "") or "")

        complete_match = _STEP_COMPLETE_COMMENT_RE.search(body)
        ready_match = _READY_FOR_COMMENT_RE.search(body)
        if not (complete_match and ready_match):
            continue

        completed_agent = normalize_agent_reference(complete_match.group(1)).lower()
        next_agent = normalize_agent_reference(ready_match.group(1)).lower()
        if not completed_agent or not next_agent:
            continue

        signals.append(
            {
                "comment_id": str(comment.get("id", "") or ""),
                "created": str(comment.get("created") or comment.get("createdAt") or ""),
                "completed_agent": completed_agent,
                "next_agent": next_agent,
            }
        )

    return signals


def read_latest_local_completion(
    base_dir: str,
    nexus_dir_name: str,
    issue_num: str,
) -> dict[str, Any] | None:
    """Return latest local completion summary metadata for an issue."""
    if not _local_completions_enabled():
        raise RuntimeError("read_latest_local_completion is disabled in postgres mode")
    pattern = os.path.join(
        base_dir,
        "**",
        nexus_dir_name,
        "tasks",
        "*",
        "completions",
        f"completion_summary_{issue_num}.json",
    )
    matches = glob.glob(pattern, recursive=True)
    if not matches:
        return None

    latest = max(matches, key=os.path.getmtime)
    try:
        with open(latest, encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return None
    payload = budget_completion_payload(payload)

    return {
        "path": latest,
        "mtime": datetime.fromtimestamp(os.path.getmtime(latest)).isoformat(),
        "agent_type": normalize_agent_reference(str(payload.get("agent_type", ""))).lower(),
        "next_agent": normalize_agent_reference(str(payload.get("next_agent", ""))).lower(),
        "status": str(payload.get("status", "") or ""),
    }


def write_local_completion_from_signal(
    base_dir: str,
    nexus_dir_name: str,
    project_key: str,
    issue_num: str,
    signal: dict[str, str],
    *,
    key_findings: list[str] | None = None,
) -> str:
    """Persist completion summary from a reconciled signal."""
    if not _local_completions_enabled():
        raise RuntimeError("write_local_completion_from_signal is disabled in postgres mode")
    completions_dir = os.path.join(
        base_dir,
        nexus_dir_name,
        "tasks",
        project_key,
        "completions",
    )
    os.makedirs(completions_dir, exist_ok=True)

    completion_path = os.path.join(completions_dir, f"completion_summary_{issue_num}.json")
    payload: dict[str, Any] = {
        "status": "complete",
        "agent_type": signal["completed_agent"],
        "summary": (
            f"Reconciled from Git comment {signal.get('comment_id', '')}: "
            f"{signal['completed_agent']} -> {signal['next_agent']}"
        ),
        "key_findings": key_findings
        or [
            "Workflow/comment/local completion drift reconciled",
            f"Source comment id: {signal.get('comment_id', 'n/a')}",
        ],
        "next_agent": signal["next_agent"],
    }

    with open(completion_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    return completion_path
