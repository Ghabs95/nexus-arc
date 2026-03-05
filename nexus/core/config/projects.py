from typing import Callable


def get_default_project(get_project_config: Callable[[], dict]) -> str:
    """Return default project key from project config."""
    config = get_project_config()
    if isinstance(config.get("nexus"), dict):
        return "nexus"

    for key, value in config.items():
        if isinstance(value, dict) and value.get("workspace"):
            return str(key)

    raise ValueError("No project with repository configuration found in PROJECT_CONFIG")


def get_track_short_projects(
    get_project_registry_fn: Callable[[], dict[str, dict[str, object]]],
) -> list[str]:
    """Return short project keys suitable for /track commands."""
    derived: list[str] = []
    for short_key, payload in get_project_registry_fn().items():
        code = str(payload.get("code", "")).strip().lower()
        if not code:
            continue
        if short_key != code and short_key.replace("-", "").replace("_", "").isalnum():
            derived.append(short_key)

    unique: list[str] = []
    for item in derived:
        if item not in unique:
            unique.append(item)
    return unique


def get_workflow_profile(
    get_project_config: Callable[[], dict],
    project: str = "nexus",
) -> str:
    """Resolve workflow definition path/profile for a project."""
    config = get_project_config()

    workflow_value = ""
    project_cfg = config.get(project)
    if isinstance(project_cfg, dict):
        workflow_value = str(project_cfg.get("workflow_definition_path", "")).strip()

    if not workflow_value:
        workflow_value = str(config.get("workflow_definition_path", "")).strip()

    return workflow_value or "ghabs_org_workflow"
