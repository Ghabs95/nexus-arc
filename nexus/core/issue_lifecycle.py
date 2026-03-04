"""Issue lifecycle helpers extracted from inbox_processor."""

import asyncio
import contextlib
import logging
import os
import re
from datetime import UTC, datetime, timedelta

from nexus.core.auth.access_domain import auth_enabled, build_execution_env

logger = logging.getLogger(__name__)
_SOURCE_MARKER_PREFIX = "nexus-inbox-source:"


def get_project_platform(project_name: str) -> str:
    """Resolve project platform from host app configuration."""
    try:
        from nexus.core.config import get_project_platform as _host_get_project_platform
    except Exception as exc:
        raise RuntimeError("Host get_project_platform is not available") from exc
    return str(_host_get_project_platform(project_name) or "").strip().lower()


def get_git_platform(
    repo_key: str,
    *,
    project_name: str,
    token_override: str | None = None,
):
    """Resolve provider adapter from host orchestration helper."""
    try:
        from nexus.core.orchestration.nexus_core_helpers import get_git_platform as _host_get_git_platform
    except Exception as exc:
        raise RuntimeError("Host get_git_platform is not available") from exc
    return _host_get_git_platform(repo_key, project_name=project_name, token_override=token_override)


def _normalize_title(value: str) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _normalize_dedupe_key(value: str | None) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _build_source_marker(value: str) -> str:
    return f"<!-- {_SOURCE_MARKER_PREFIX} {value} -->"


def _extract_source_marker(body: str) -> str:
    if not body:
        return ""
    match = re.search(rf"{re.escape(_SOURCE_MARKER_PREFIX)}\s*([^>]+)", str(body), re.IGNORECASE)
    if not match:
        return ""
    raw = str(match.group(1) or "").strip()
    raw = re.sub(r"\s*-+\s*$", "", raw).strip()
    return _normalize_dedupe_key(raw)


def _find_recent_duplicate_issue(
    *,
    issues: list,
    title: str,
    required_labels: list[str],
    max_age_hours: float,
    dedupe_key: str | None = None,
):
    if max_age_hours <= 0:
        return None

    cutoff = datetime.now(tz=UTC) - timedelta(hours=max_age_hours)
    normalized_title = _normalize_title(title)
    normalized_dedupe_key = _normalize_dedupe_key(dedupe_key)
    required = {str(label).strip().lower() for label in required_labels if str(label).strip()}

    for issue in issues or []:
        created_at = getattr(issue, "created_at", None)
        if not isinstance(created_at, datetime):
            continue
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if created_at < cutoff:
            continue

        if normalized_dedupe_key:
            issue_marker = _extract_source_marker(str(getattr(issue, "body", "") or ""))
            if issue_marker and issue_marker == normalized_dedupe_key:
                return issue

        issue_title = _normalize_title(getattr(issue, "title", ""))
        if issue_title != normalized_title:
            continue

        issue_labels = {
            str(label).strip().lower()
            for label in (getattr(issue, "labels", None) or [])
            if str(label).strip()
        }
        if not required.issubset(issue_labels):
            continue
        return issue
    return None


def create_issue(
    *,
    title: str,
    body: str,
    project: str,
    workflow_label: str,
    task_type: str,
    repo_key: str,
    dedupe_key: str | None = None,
    requester_nexus_id: str | None = None,
) -> str:
    """Create a provider-backed issue and apply labels with fallback behavior."""
    type_label = f"type:{task_type}"
    project_label = f"project:{project}"
    labels = [project_label, type_label, workflow_label]
    normalized_dedupe_key = _normalize_dedupe_key(dedupe_key)
    issue_body = str(body or "")
    if normalized_dedupe_key:
        source_marker = _build_source_marker(normalized_dedupe_key)
        if source_marker not in issue_body:
            issue_body = f"{issue_body}\n\n{source_marker}"

    try:
        token_override = None
        if auth_enabled() and requester_nexus_id:
            user_env, env_error = build_execution_env(str(requester_nexus_id))
            if env_error:
                raise RuntimeError(f"Requester credentials unavailable: {env_error}")
            platform = str(get_project_platform(project) or "").strip().lower()
            token_override = (
                user_env.get("GITLAB_TOKEN") if platform == "gitlab" else user_env.get("GITHUB_TOKEN")
            )

        platform = get_git_platform(
            repo_key,
            project_name=project,
            token_override=token_override,
        )
        issue_obj = None
        dedupe_hours = 24.0
        with contextlib.suppress(Exception):
            dedupe_hours = max(0.0, float(os.getenv("NEXUS_ISSUE_DEDUPE_HOURS", "24")))

        with contextlib.suppress(Exception):
            open_issues = asyncio.run(platform.list_open_issues(limit=200, labels=[project_label]))
            duplicate = _find_recent_duplicate_issue(
                issues=open_issues,
                title=title,
                required_labels=labels,
                max_age_hours=dedupe_hours,
                dedupe_key=normalized_dedupe_key,
            )
            if duplicate:
                logger.warning(
                    "Duplicate issue create suppressed for project '%s': title='%s' -> #%s",
                    project,
                    title,
                    getattr(duplicate, "number", "unknown"),
                )
                return str(getattr(duplicate, "url"))

        try:
            issue_obj = asyncio.run(
                platform.create_issue(title=title, body=issue_body, labels=labels)
            )
        except Exception as create_with_labels_exc:
            logger.warning(
                "Issue create with labels failed for project '%s': %s. Retrying without labels.",
                project,
                create_with_labels_exc,
            )
            issue_obj = asyncio.run(
                platform.create_issue(title=title, body=issue_body, labels=None)
            )

        if not issue_obj:
            raise RuntimeError("GitPlatform.create_issue returned no issue")

        issue_num = str(issue_obj.number)
        label_specs: list[tuple[str, str, str]] = []
        for label in labels:
            if label.startswith("workflow:"):
                label_specs.append((label, "0E8A16", "Workflow tier"))
            elif label.startswith("type:"):
                label_specs.append((label, "1D76DB", "Task type"))
            else:
                label_specs.append((label, "5319E7", "Project key"))

        for label, color, description in label_specs:
            with contextlib.suppress(Exception):
                asyncio.run(platform.ensure_label(label, color=color, description=description))

        with contextlib.suppress(Exception):
            asyncio.run(platform.update_issue(issue_num, labels=labels))

        logger.info("Issue created via GitPlatform adapter")
        return issue_obj.url
    except Exception as exc:
        raise RuntimeError(f"Git platform issue create failed: {exc}") from exc


def rename_task_file_and_sync_issue_body(
    *,
    task_file_path: str,
    issue_url: str,
    issue_body: str,
    project_name: str,
    repo_key: str,
    requester_nexus_id: str | None = None,
) -> str:
    """Rename local task file to include issue number and sync issue body task-file path."""
    issue_num = str(issue_url).rstrip("/").split("/")[-1]
    old_basename = os.path.basename(task_file_path)
    new_basename = re.sub(r"_(\d+)\.md$", f"_{issue_num}.md", old_basename)
    if new_basename == old_basename:
        return task_file_path

    renamed_path = os.path.join(os.path.dirname(task_file_path), new_basename)
    os.rename(task_file_path, renamed_path)
    logger.info("Renamed task file: %s -> %s", old_basename, new_basename)

    corrected_body = issue_body.replace(task_file_path, renamed_path)
    try:
        token_override = None
        if auth_enabled() and requester_nexus_id:
            user_env, env_error = build_execution_env(str(requester_nexus_id))
            if env_error:
                raise RuntimeError(f"Requester credentials unavailable: {env_error}")
            platform = str(get_project_platform(project_name) or "").strip().lower()
            token_override = (
                user_env.get("GITLAB_TOKEN") if platform == "gitlab" else user_env.get("GITHUB_TOKEN")
            )

        platform = get_git_platform(
            repo_key,
            project_name=project_name,
            token_override=token_override,
        )
        asyncio.run(platform.update_issue(issue_num, body=corrected_body))
    except Exception as update_exc:
        logger.warning(
            "Failed to update issue body after task file rename for issue #%s: %s",
            issue_num,
            update_exc,
        )
    return renamed_path
