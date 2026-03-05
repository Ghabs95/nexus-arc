from typing import Callable


def get_project_registry(get_project_config: Callable[[], dict]) -> dict[str, dict[str, object]]:
    """Return normalized short-key project registry from PROJECT_CONFIG['projects']."""
    config = get_project_config()
    raw_registry = config.get("projects")
    if not isinstance(raw_registry, dict):
        return {}

    registry: dict[str, dict[str, object]] = {}
    for short_key, payload in raw_registry.items():
        normalized_short = str(short_key).strip().lower()
        if not normalized_short or not isinstance(payload, dict):
            continue

        code = str(payload.get("code", "")).strip().lower()
        if not code:
            continue
        raw_aliases = payload.get("aliases", [])
        aliases = []
        if isinstance(raw_aliases, list):
            aliases = [
                str(alias).strip().lower()
                for alias in raw_aliases
                if isinstance(alias, str) and str(alias).strip()
            ]

        registry[normalized_short] = {
            "code": code,
            "aliases": aliases,
        }

    return registry


def get_project_aliases(
    get_project_config: Callable[[], dict],
    get_project_registry_fn: Callable[[], dict[str, dict[str, object]]],
) -> dict[str, str]:
    """Return normalized aliases resolved from PROJECT_CONFIG['projects']."""
    config = get_project_config()
    aliases: dict[str, str] = {}

    for short_key, payload in get_project_registry_fn().items():
        code = str(payload.get("code", "")).strip().lower()
        if not code:
            continue
        aliases[short_key] = code
        raw_aliases = payload.get("aliases", [])
        if isinstance(raw_aliases, list):
            for alias in raw_aliases:
                normalized = str(alias).strip().lower()
                if normalized:
                    aliases[normalized] = code

    for key, value in config.items():
        if isinstance(value, dict) and value.get("workspace"):
            canonical = str(key).strip().lower()
            if canonical:
                aliases.setdefault(canonical, canonical)

    return aliases


def normalize_project_key(
    get_project_aliases_fn: Callable[[], dict[str, str]],
    project: str,
) -> str | None:
    """Normalize a project key using configured aliases."""
    candidate = str(project or "").strip().lower()
    if not candidate:
        return None
    aliases = get_project_aliases_fn()
    return aliases.get(candidate, candidate)
