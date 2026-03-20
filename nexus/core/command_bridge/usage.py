"""Usage collection helpers for command bridge responses."""

from __future__ import annotations

import glob
import json
import os
import re
from json import JSONDecodeError
from typing import Any

from nexus.core.command_bridge.models import UsagePayload
from nexus.core.config import BASE_DIR, PROJECT_CONFIG, get_tasks_logs_dir
from nexus.core.config import normalize_project_key as _normalize_project_key
from nexus.core.integrations.workflow_state_factory import get_storage_backend

_PROMPT_TOKEN_RE = re.compile(r"(?:prompt|input)[_\s-]*tokens?['\"=:,\s]+(?P<value>\d+)", re.IGNORECASE)
_COMPLETION_TOKEN_RE = re.compile(
    r"(?:completion|output)[_\s-]*tokens?['\"=:,\s]+(?P<value>\d+)",
    re.IGNORECASE,
)
_TOTAL_TOKEN_RE = re.compile(r"total[_\s-]*tokens?['\"=:,\s]+(?P<value>\d+)", re.IGNORECASE)
_ESTIMATED_COST_RE = re.compile(
    r"(?:estimated[_\s-]*cost(?:[_\s-]*usd)?|total usage est(?:imate)?)[^0-9$-]*\$?(?P<value>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_PROVIDER_RE = re.compile(r"(?:provider(?:_used)?|tool)['\"=:,\s]+(?P<value>[A-Za-z0-9._:-]+)", re.IGNORECASE)
_MODEL_RE = re.compile(r"model['\"=:,\s]+(?P<value>[A-Za-z0-9._:-]+)", re.IGNORECASE)


async def collect_bridge_usage_payload(
    *,
    project_key: str | None = None,
    issue_number: str | None = None,
    workflow_id: str | None = None,
) -> UsagePayload | None:
    """Return the best available recent usage payload for a bridge result."""

    normalized_project = _coerce_project_key(project_key) or _project_key_from_workflow_id(workflow_id)
    issue_token = str(issue_number or "").strip() or None
    if not issue_token:
        return None

    usage = await _load_usage_from_completion_storage(issue_token)
    if usage is not None:
        return usage

    if normalized_project:
        return _load_usage_from_recent_log(normalized_project, issue_token)
    return None


def usage_payload_from_bridge_event(
    payload: dict[str, Any] | Any,
    *,
    source: str = "bridge_event",
) -> UsagePayload | None:
    """Coerce handler-provided bridge metadata into a :class:`UsagePayload`."""

    data = payload if isinstance(payload, dict) else {}
    explicit = data.get("bridge_usage")
    if isinstance(explicit, dict):
        usage = _usage_from_mapping(
            explicit,
            source=source,
        )
        if usage is not None:
            return usage
    return _usage_from_mapping(data, source=source)


async def _load_usage_from_completion_storage(issue_number: str) -> UsagePayload | None:
    try:
        backend = get_storage_backend()
        items = await backend.list_completions(str(issue_number))
    except Exception:
        return None
    if not isinstance(items, list) or not items:
        return None

    for item in items:
        if not isinstance(item, dict):
            continue
        usage = _usage_from_mapping(
            item,
            source="completion_storage",
            extra_metadata={"issue_number": str(issue_number)},
        )
        if usage is not None:
            return usage
    return None


def _load_usage_from_recent_log(project_key: str, issue_number: str) -> UsagePayload | None:
    project_cfg = PROJECT_CONFIG.get(project_key)
    if not isinstance(project_cfg, dict):
        return None

    workspace = str(project_cfg.get("workspace") or "").strip()
    if not workspace:
        return None
    workspace_dir = workspace if os.path.isabs(workspace) else os.path.join(BASE_DIR, workspace)
    log_dir = get_tasks_logs_dir(workspace_dir, project_key)
    pattern = os.path.join(log_dir, f"*_{issue_number}_*.log")
    candidates = glob.glob(pattern)
    if not candidates:
        return None

    latest = max(candidates, key=os.path.getmtime)
    try:
        with open(latest, encoding="utf-8", errors="replace") as handle:
            content = handle.read()
    except Exception:
        return None

    if len(content) > 20000:
        content = content[-20000:]
    return _usage_from_log_text(
        content,
        log_path=latest,
        project_key=project_key,
        issue_number=issue_number,
    )


def _usage_from_log_text(
    text: str,
    *,
    log_path: str,
    project_key: str,
    issue_number: str,
) -> UsagePayload | None:
    best: UsagePayload | None = None

    for payload in _iter_json_objects(text):
        candidate = _usage_from_mapping(
            payload,
            source="agent_log_json",
            extra_metadata={
                "project_key": project_key,
                "issue_number": issue_number,
                "log_path": log_path,
            },
        )
        if _usage_score(candidate) >= _usage_score(best):
            best = candidate

    regex_candidate = _usage_from_regex(
        text,
        source="agent_log_text",
        extra_metadata={
            "project_key": project_key,
            "issue_number": issue_number,
            "log_path": log_path,
        },
    )
    if _usage_score(regex_candidate) >= _usage_score(best):
        best = regex_candidate

    return best


def _usage_from_mapping(
    payload: dict[str, Any] | Any,
    *,
    source: str,
    extra_metadata: dict[str, Any] | None = None,
) -> UsagePayload | None:
    data = payload if isinstance(payload, dict) else {}
    if not data:
        return None

    metadata_payload = data.get("metadata")
    metadata = metadata_payload if isinstance(metadata_payload, dict) else {}
    usage_payload = data.get("usage")
    usage = usage_payload if isinstance(usage_payload, dict) else {}

    provider = _first_text(
        data.get("provider_used"),
        data.get("provider"),
        metadata.get("provider_used"),
        metadata.get("provider"),
        data.get("tool"),
    )
    model = _first_text(
        data.get("model"),
        metadata.get("model"),
        usage.get("model"),
    )
    input_tokens = _first_int(
        data.get("input_tokens"),
        usage.get("input_tokens"),
        data.get("prompt_tokens"),
        usage.get("prompt_tokens"),
    )
    output_tokens = _first_int(
        data.get("output_tokens"),
        usage.get("output_tokens"),
        data.get("completion_tokens"),
        usage.get("completion_tokens"),
    )
    total_tokens = _first_int(
        data.get("total_tokens"),
        usage.get("total_tokens"),
    )
    estimated_cost_usd = _first_float(
        data.get("estimated_cost_usd"),
        usage.get("estimated_cost_usd"),
        data.get("estimated_cost"),
        usage.get("estimated_cost"),
    )

    if not any(
        (
            provider,
            model,
            input_tokens is not None,
            output_tokens is not None,
            total_tokens is not None,
            estimated_cost_usd is not None,
        )
    ):
        return None

    payload_metadata: dict[str, Any] = {"source": source}
    if total_tokens is not None:
        payload_metadata["total_tokens"] = total_tokens
    if extra_metadata:
        payload_metadata.update(extra_metadata)

    return UsagePayload(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=estimated_cost_usd,
        metadata=payload_metadata,
    )


def _usage_from_regex(
    text: str,
    *,
    source: str,
    extra_metadata: dict[str, Any] | None = None,
) -> UsagePayload | None:
    provider = _match_group(_PROVIDER_RE, text)
    model = _match_group(_MODEL_RE, text)
    input_tokens = _match_int(_PROMPT_TOKEN_RE, text)
    output_tokens = _match_int(_COMPLETION_TOKEN_RE, text)
    total_tokens = _match_int(_TOTAL_TOKEN_RE, text)
    estimated_cost_usd = _match_float(_ESTIMATED_COST_RE, text)

    if not any(
        (
            provider,
            model,
            input_tokens is not None,
            output_tokens is not None,
            total_tokens is not None,
            estimated_cost_usd is not None,
        )
    ):
        return None

    payload_metadata: dict[str, Any] = {"source": source}
    if total_tokens is not None:
        payload_metadata["total_tokens"] = total_tokens
    if extra_metadata:
        payload_metadata.update(extra_metadata)

    return UsagePayload(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=estimated_cost_usd,
        metadata=payload_metadata,
    )


def _iter_json_objects(text: str):
    decoder = json.JSONDecoder()
    index = 0
    content = str(text or "")
    length = len(content)
    while index < length:
        start = content.find("{", index)
        if start < 0:
            break
        try:
            payload, consumed = decoder.raw_decode(content[start:])
        except JSONDecodeError:
            index = start + 1
            continue
        index = start + max(consumed, 1)
        if isinstance(payload, dict):
            yield payload


def _usage_score(value: UsagePayload | None) -> int:
    if value is None:
        return -1
    score = 0
    if value.provider:
        score += 1
    if value.model:
        score += 1
    if value.input_tokens is not None:
        score += 2
    if value.output_tokens is not None:
        score += 2
    if value.estimated_cost_usd is not None:
        score += 2
    if value.metadata.get("total_tokens") is not None:
        score += 1
    return score


def _coerce_project_key(value: str | None) -> str | None:
    if value is None:
        return None
    return _normalize_project_key(str(value))


def _project_key_from_workflow_id(workflow_id: str | None) -> str | None:
    prefix = str(workflow_id or "").split("-", 1)[0].strip()
    return _coerce_project_key(prefix) if prefix else None


def _match_group(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return str(match.group("value")).strip() if match else ""


def _match_int(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    return _first_int(match.group("value")) if match else None


def _match_float(pattern: re.Pattern[str], text: str) -> float | None:
    match = pattern.search(text)
    return _first_float(match.group("value")) if match else None


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _first_int(*values: Any) -> int | None:
    for value in values:
        try:
            if value is None or value == "":
                continue
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_float(*values: Any) -> float | None:
    for value in values:
        try:
            if value is None or value == "":
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return None
