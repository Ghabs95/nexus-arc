from typing import Mapping


def iter_project_keys(*, project_config: dict) -> list[str]:
    return sorted(
        str(key)
        for key, cfg in project_config.items()
        if isinstance(cfg, dict) and cfg.get("workspace")
    )


def single_key(keys: list[str]) -> str | None:
    return keys[0] if len(keys) == 1 else None


def get_single_project_key(*, project_config: dict) -> str | None:
    return single_key(iter_project_keys(project_config=project_config))


def get_project_label(project_key: str, projects_map: Mapping[str, str]) -> str:
    return str(projects_map.get(project_key, project_key))


def get_project_workspace(*, project_key: str, project_config: dict) -> str:
    cfg = project_config.get(project_key, {})
    if isinstance(cfg, dict):
        workspace = cfg.get("workspace")
        if isinstance(workspace, str) and workspace.strip():
            return workspace.strip()
    return project_key
