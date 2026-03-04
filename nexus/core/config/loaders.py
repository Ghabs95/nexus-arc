"""Loader helpers for Telegram bot configuration files."""

from __future__ import annotations

import os
from typing import Any, Callable

import yaml


def load_project_config_yaml(path: str) -> dict[str, Any]:
    """Load project config YAML file and ensure a mapping is returned."""
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("PROJECT_CONFIG must be a YAML mapping")
    return data


def load_and_validate_project_config(
    *,
    base_dir: str,
    cache: dict[str, Any],
    validator: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Load PROJECT_CONFIG using env path with simple cache invalidation."""
    project_config_path = os.getenv("PROJECT_CONFIG_PATH")
    if not project_config_path:
        raise ValueError(
            "PROJECT_CONFIG_PATH environment variable is required. "
            "It must point to a YAML file with project configuration."
        )

    cached_path = cache.get("path")
    if cached_path != project_config_path:
        cache["value"] = None
        cache["path"] = project_config_path

    cached_value = cache.get("value")
    if isinstance(cached_value, dict):
        return cached_value

    resolved_config_path = (
        project_config_path
        if os.path.isabs(project_config_path)
        else os.path.join(base_dir, project_config_path)
    )

    try:
        loaded = load_project_config_yaml(resolved_config_path)
    except FileNotFoundError:
        raise FileNotFoundError(f"PROJECT_CONFIG file not found: {resolved_config_path}")
    except Exception as exc:
        raise ValueError(f"Failed to load PROJECT_CONFIG from {resolved_config_path}: {exc}")

    validator(loaded)
    cache["value"] = loaded
    return loaded
