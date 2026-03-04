import glob
import os
import re

from nexus.core.project.catalog import (
    get_single_project_key as _shared_get_single_project_key,
)


def resolve_project_root_from_task_path(task_file: str) -> str:
    normalized = os.path.abspath(task_file).replace("\\", "/")
    match = re.search(r"^(.*)/\.nexus/tasks/[^/]+/", normalized)
    if match:
        return os.path.normpath(match.group(1))
    if "/.nexus/" in normalized:
        return os.path.normpath(normalized.split("/.nexus/", 1)[0])
    return os.path.dirname(os.path.dirname(os.path.dirname(normalized)))


def extract_issue_number_from_file(*, file_path: str, logger) -> str | None:
    try:
        with open(file_path) as handle:
            content = handle.read()
        match = re.search(r"\*\*Issue:\*\*\s*https?://[^\s`]+/(?:-/)?issues/(\d+)", content)
        if match:
            return str(match.group(1))
    except Exception as exc:
        logger.warning("Failed to read issue number from %s: %s", file_path, exc)
    return None


def find_task_logs(
    *,
    task_file: str,
    logger,
    resolve_project_root_from_task_path_fn,
    extract_project_from_nexus_path,
    get_tasks_logs_dir,
) -> list[str]:
    if not task_file:
        return []
    try:
        project_root = resolve_project_root_from_task_path_fn(task_file)
        project_key = extract_project_from_nexus_path(task_file)
        if not project_key:
            return []
        logs_dir = get_tasks_logs_dir(project_root, project_key)
        if not os.path.isdir(logs_dir):
            return []
        return glob.glob(os.path.join(logs_dir, "**", "*.log"), recursive=True)
    except Exception as exc:
        logger.warning("Failed to list task logs: %s", exc)
        return []


def read_log_matches(
    *, log_path: str, issue_num: str, logger, issue_url: str | None = None, max_lines: int = 20
) -> list[str]:
    if not log_path or not os.path.exists(log_path):
        return []
    matches: list[str] = []
    needle = f"#{issue_num}"
    try:
        with open(log_path, encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if needle in line or (issue_url and issue_url in line):
                    matches.append(line.rstrip())
    except Exception as exc:
        logger.warning("Failed to read log file %s: %s", log_path, exc)
        return []
    return matches[-max_lines:] if max_lines else matches


def search_logs_for_issue(
    *,
    issue_num: str,
    telegram_bot_log_file: str | None,
    logs_dir: str | None,
    logger,
    read_log_matches_fn,
) -> list[str]:
    log_paths: list[str] = []
    if telegram_bot_log_file:
        log_paths.append(telegram_bot_log_file)
    if logs_dir and os.path.isdir(logs_dir):
        log_paths.extend(
            os.path.join(logs_dir, name) for name in os.listdir(logs_dir) if name.endswith(".log")
        )

    seen: set[str] = set()
    results: list[str] = []
    for path in log_paths:
        if path in seen:
            continue
        seen.add(path)
        results.extend(read_log_matches_fn(path, issue_num, max_lines=10))
    return results


def read_latest_log_tail(
    *, task_file: str, logger, find_task_logs_fn, max_lines: int = 20
) -> list[str]:
    log_files = find_task_logs_fn(task_file)
    if not log_files:
        return []
    log_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    latest = log_files[0]
    try:
        with open(latest, encoding="utf-8", errors="ignore") as handle:
            lines = handle.readlines()
        return [f"[{os.path.basename(latest)}] {line.rstrip()}" for line in lines[-max_lines:]]
    except Exception as exc:
        logger.warning("Failed to read latest log file %s: %s", latest, exc)
        return []


def find_issue_log_files(
    *,
    issue_num: str,
    task_file: str | None,
    base_dir: str,
    nexus_dir_name: str,
    extract_project_from_nexus_path,
    resolve_project_root_from_task_path_fn,
    get_tasks_logs_dir,
) -> list[str]:
    matches: list[str] = []
    if task_file:
        project_root = resolve_project_root_from_task_path_fn(task_file)
        project_key = extract_project_from_nexus_path(task_file)
        if project_key:
            logs_dir = get_tasks_logs_dir(project_root, project_key)
            if os.path.isdir(logs_dir):
                matches.extend(
                    glob.glob(os.path.join(logs_dir, "**", f"*_{issue_num}_*.log"), recursive=True)
                )
    if matches:
        return matches

    matches.extend(
        glob.glob(
            os.path.join(
                base_dir, "**", nexus_dir_name, "tasks", "*", "logs", "**", f"*_{issue_num}_*.log"
            ),
            recursive=True,
        )
    )
    matches.extend(
        glob.glob(
            os.path.join(
                base_dir,
                "**",
                nexus_dir_name,
                "worktrees",
                "*",
                nexus_dir_name,
                "tasks",
                "*",
                "logs",
                "**",
                f"*_{issue_num}_*.log",
            ),
            recursive=True,
        )
    )

    unique: list[str] = []
    seen: set[str] = set()
    for path in matches:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def read_latest_log_full(*, task_file: str, logger, find_task_logs_fn) -> list[str]:
    log_files = find_task_logs_fn(task_file)
    if not log_files:
        return []
    log_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    latest = log_files[0]
    try:
        with open(latest, encoding="utf-8", errors="ignore") as handle:
            lines = handle.readlines()
        return [f"[{os.path.basename(latest)}] {line.rstrip()}" for line in lines]
    except Exception as exc:
        logger.warning("Failed to read latest log file %s: %s", latest, exc)
        return []


def resolve_project_config_from_task(
    *, task_file: str, project_config: dict, base_dir: str
) -> tuple[str | None, dict | None]:
    if not task_file:
        return None, None
    task_path = os.path.abspath(task_file)

    if "/.nexus/" in task_path:
        project_root = task_path.split("/.nexus/")[0]
        for key, cfg in project_config.items():
            if not isinstance(cfg, dict):
                continue
            workspace = cfg.get("workspace")
            if not workspace:
                continue
            workspace_abs = os.path.abspath(os.path.join(base_dir, str(workspace)))
            if project_root == workspace_abs or project_root.startswith(workspace_abs + os.sep):
                return key, cfg

    for key, cfg in project_config.items():
        if not isinstance(cfg, dict):
            continue
        agents_dir = cfg.get("agents_dir")
        if not agents_dir:
            continue
        agents_abs = os.path.abspath(os.path.join(base_dir, str(agents_dir)))
        if task_path.startswith(agents_abs + os.sep):
            return key, cfg
    return None, None


def iter_project_keys(*, project_config: dict) -> list[str]:
    keys: list[str] = []
    for key, cfg in project_config.items():
        if not isinstance(cfg, dict):
            continue
        repo = cfg.get("git_repo")
        repo_list = cfg.get("git_repos")
        has_primary = isinstance(repo, str) and bool(repo.strip())
        has_multi = isinstance(repo_list, list) and any(
            isinstance(item, str) and item.strip() for item in repo_list
        )
        if has_primary or has_multi:
            keys.append(key)
    return keys


get_single_project_key = _shared_get_single_project_key


def get_project_root(*, project_key: str, project_config: dict, base_dir: str) -> str | None:
    cfg = project_config.get(project_key)
    if not isinstance(cfg, dict):
        return None
    workspace = cfg.get("workspace")
    if not workspace:
        return None
    return os.path.join(base_dir, str(workspace))


def get_project_logs_dir(
    *, project_key: str, project_config: dict, base_dir: str, get_tasks_logs_dir
) -> str | None:
    project_root = get_project_root(
        project_key=project_key, project_config=project_config, base_dir=base_dir
    )
    if not project_root:
        return None
    logs_dir = get_tasks_logs_dir(project_root, project_key)
    return logs_dir if os.path.isdir(logs_dir) else None


def extract_project_from_nexus_path(
    *, path: str, normalize_project_key, iter_project_keys_fn
) -> str | None:
    if not path or "/.nexus/" not in path:
        return None
    normalized = path.replace("\\", "/")
    match = re.search(r"/\.nexus/(?:tasks|inbox)/([^/]+)/", normalized)
    if not match:
        return None
    project_key = normalize_project_key(match.group(1))
    if project_key and project_key in iter_project_keys_fn():
        return project_key
    return None
