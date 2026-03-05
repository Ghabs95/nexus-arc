"""Environment parsing helpers for Nexus config."""

from __future__ import annotations

import os


def get_int_env(name: str, default: int) -> int:
    """Return integer environment variable value or fallback default."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def parse_int_list(name: str) -> list[int]:
    """Parse a comma-separated list of integer ids from env."""
    raw = os.getenv(name, "")
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


def parse_csv_list(name: str) -> list[str]:
    """Parse a comma-separated list of strings from env."""
    raw = os.getenv(name, "")
    return [str(item).strip() for item in str(raw).split(",") if str(item).strip()]


def env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    """Parse a float environment variable."""
    raw = os.getenv(name, str(default))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)
