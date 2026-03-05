from __future__ import annotations

from typing import Any


def truncate_for_log(value: Any, limit: int = 600) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def log_feature_ideation_success(
    logger: Any,
    *,
    provider: str,
    primary_success: bool,
    fallback_used: bool,
    item_count: int,
    project_key: str,
    agent_type: str,
) -> None:
    if logger is None:
        return

    message = (
        "Feature ideation success: provider=%s primary_success=%s fallback_used=%s "
        "items=%s project=%s agent_type=%s"
    )
    values = (
        str(provider),
        str(primary_success).lower(),
        str(fallback_used).lower(),
        int(item_count),
        str(project_key),
        str(agent_type),
    )

    log_info = getattr(logger, "info", None)
    if callable(log_info):
        log_info(message, *values)
        return

    log_warning = getattr(logger, "warning", None)
    if callable(log_warning):
        log_warning(message, *values)


def log_unauthorized_access(logger: Any, user_id: Any) -> None:
    if logger is None:
        return

    log_warning = getattr(logger, "warning", None)
    if callable(log_warning):
        log_warning("Unauthorized access attempt by ID: %s", user_id)


def log_unauthorized_callback_access(logger: Any, user_id: Any) -> None:
    if logger is None:
        return

    log_warning = getattr(logger, "warning", None)
    if callable(log_warning):
        log_warning("Unauthorized callback access attempt by ID: %s", user_id)
