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
    r"^\s*##\s+.+?\bcomplet(?:e|ed)\b\s*[-–—:]\s*`?@?([a-zA-Z0-9_-]+)`?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_READY_FOR_COMMENT_RE = re.compile(
    r"\bready\s+for\s+(?:\*\*)?`?@?([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)
_STEP_ID_COMMENT_RE = re.compile(r"^\s*\*\*Step ID:\*\*\s*`?([a-zA-Z0-9_-]+)`?\s*$", re.MULTILINE)
_STEP_NUM_COMMENT_RE = re.compile(
    r"^\s*\*\*Step (?:Num|Number):\*\*\s*([0-9]+)\s*$",
    re.MULTILINE,
)
_CHECKLIST_DONE_STEP_RE = re.compile(
    r"^\s*-\s*\[x\]\s*([0-9]+)\.\s+\*\*([^*]+)\*\*\s*[-–—:]\s*`?@?([a-zA-Z0-9_-]+)`?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_INSTRUCTION_TEMPLATE_MARKERS = (
    "<agent_type from workflow steps",
    "<step name>",
    "<finding 1>",
    "<finding 2>",
    "<one-line summary of what you did>",
    "@<display name>",
)


def _coerce_step_num(value: Any) -> int:
    try:
        step_num = int(value)
    except (TypeError, ValueError):
        return 0
    return step_num if step_num > 0 else 0


def _local_completions_enabled() -> bool:
    return get_storage_capabilities().local_completions


def normalize_agent_reference(agent_ref: str) -> str:
    """Normalize agent references used in comments/completion files."""
    value = str(agent_ref or "").strip()
    value = value.lstrip("@").strip()
    value = value.strip("`").strip()
    lowered = value.lower()
    if not lowered:
        return ""
    if ("<" in lowered and ">" in lowered) or any(
        marker in lowered for marker in _INSTRUCTION_TEMPLATE_MARKERS
    ):
        return ""
    return value


def _normalize_step_id_from_label(label: str) -> str:
    value = str(label or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_")


def _derive_step_from_checklist(body: str, completed_agent: str) -> tuple[str, int]:
    fallback_step_id = ""
    fallback_step_num = 0
    for match in _CHECKLIST_DONE_STEP_RE.finditer(body or ""):
        try:
            candidate_num = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if candidate_num <= 0:
            continue
        candidate_label = str(match.group(2) or "")
        candidate_agent = normalize_agent_reference(match.group(3)).lower()
        candidate_step_id = _normalize_step_id_from_label(candidate_label)
        if not candidate_step_id:
            continue
        fallback_step_id = candidate_step_id
        fallback_step_num = candidate_num
        if candidate_agent == completed_agent:
            return candidate_step_id, candidate_num
    return fallback_step_id, fallback_step_num


def extract_structured_completion_signals(comments: list[dict]) -> list[dict[str, str]]:
    """Extract strict completion transitions from structured comments."""
    signals: list[dict[str, str]] = []
    for comment in comments or []:
        body = str(comment.get("body", "") or "")

        complete_match = _STEP_COMPLETE_COMMENT_RE.search(body)
        ready_match = _READY_FOR_COMMENT_RE.search(body)
        step_id_match = _STEP_ID_COMMENT_RE.search(body)
        step_num_match = _STEP_NUM_COMMENT_RE.search(body)
        if not complete_match:
            continue

        completed_agent = normalize_agent_reference(complete_match.group(1)).lower()
        next_agent = normalize_agent_reference(ready_match.group(1)).lower() if ready_match else "none"
        step_id = normalize_agent_reference(step_id_match.group(1)).lower() if step_id_match else ""
        try:
            step_num = int(step_num_match.group(1)) if step_num_match else 0
        except (TypeError, ValueError):
            step_num = 0
        if (not step_id or step_num <= 0) and completed_agent:
            fallback_step_id, fallback_step_num = _derive_step_from_checklist(body, completed_agent)
            if not step_id:
                step_id = fallback_step_id
            if step_num <= 0:
                step_num = fallback_step_num
        if not completed_agent or not next_agent or not step_id or step_num <= 0:
            continue

        signals.append(
            {
                "comment_id": str(comment.get("id", "") or ""),
                "created": str(comment.get("created") or comment.get("createdAt") or ""),
                "completed_agent": completed_agent,
                "next_agent": next_agent,
                "step_id": step_id,
                "step_num": str(step_num),
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
        "step_id": normalize_agent_reference(str(payload.get("step_id", ""))).lower(),
        "step_num": _coerce_step_num(payload.get("step_num", 0)),
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
        "step_id": signal["step_id"],
        "step_num": _coerce_step_num(signal["step_num"]),
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
