"""Backend enum normalization helpers.

Framework-owned helpers so core and examples can share strict backend validation.
"""

from __future__ import annotations

STORAGE_BACKEND_ALIASES: dict[str, str] = {
    "file": "filesystem",
    "fs": "filesystem",
    "filesystem": "filesystem",
    "postgres": "postgres",
    "postgresql": "postgres",
    "database": "postgres",
}

RATE_LIMIT_BACKEND_ALIASES: dict[str, str] = {
    "file": "filesystem",
    "fs": "filesystem",
    "filesystem": "filesystem",
    "redis": "redis",
    "database": "database",
    "postgres": "database",
    "postgresql": "database",
}

VALID_STORAGE_BACKENDS: set[str] = {"filesystem", "postgres"}
VALID_RATE_LIMIT_BACKENDS: set[str] = {"filesystem", "database", "redis"}


def normalize_backend_enum(
    raw_value: str | None,
    *,
    env_name: str,
    default: str,
    allowed: set[str],
    aliases: dict[str, str],
) -> str:
    candidate = str(raw_value or "").strip().lower()
    if not candidate:
        return default

    normalized = aliases.get(candidate, candidate)
    if normalized in allowed:
        return normalized

    allowed_text = ", ".join(sorted(allowed))
    raise ValueError(
        f"{env_name} has invalid value '{candidate}'. Allowed values: {allowed_text}. "
        "Aliases accepted: file/fs->filesystem, postgresql/database->postgres."
    )
