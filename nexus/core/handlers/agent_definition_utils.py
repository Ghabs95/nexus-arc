"""Shared helpers for reading agent definition files."""

from __future__ import annotations

import os
from typing import Any

import yaml


def load_agent_yaml(path: str) -> dict[str, Any] | None:
    """Load a YAML file and return a mapping payload, or None on parse/read failures."""
    try:
        with open(path, encoding="utf-8") as file_handle:
            data = yaml.safe_load(file_handle)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    return data


def extract_agent_identity(path: str) -> tuple[str, str]:
    """Return (agent_name, agent_type) parsed from an agent YAML definition.

    Returns empty strings when fields are unavailable. Name fallback uses filename stem.
    """
    data = load_agent_yaml(path)
    if not isinstance(data, dict):
        return "", ""

    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    spec = data.get("spec") if isinstance(data.get("spec"), dict) else {}

    agent_name = (
        str(metadata.get("name") or "").strip()
        or str(spec.get("agent_type") or "").strip()
        or os.path.splitext(os.path.basename(path))[0]
    )
    agent_type = str(spec.get("agent_type") or "").strip().lower()

    return agent_name, agent_type
