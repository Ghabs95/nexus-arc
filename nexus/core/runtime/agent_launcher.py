"""
Shared agent launching logic for inbox processor and webhook server.

This module provides a unified interface for launching AI agents
in response to workflow events, whether triggered by polling (inbox processor)
or webhooks (webhook server).
"""

import asyncio
import glob
import inspect
import json
import logging
import os
import re
import subprocess
import threading
import time

import yaml

from nexus.adapters.git.utils import build_issue_url
from nexus.core.audit_store import AuditStore
from nexus.core.auth.access_domain import (
    auth_enabled,
    build_execution_env,
    check_project_access,
    check_repo_access,
)
from nexus.core.auth.credential_store import get_issue_requester, get_issue_requester_by_url
from nexus.core.config import (
    BASE_DIR,
    NEXUS_WEBHOOK_INTERNAL_URL,
    ORCHESTRATOR_CONFIG,
    NEXUS_STORAGE_BACKEND,
    PROJECT_CONFIG,
    WEBHOOK_PORT,
    get_default_project,
    get_repo_branch,
    get_system_operations,
    get_repo,
    get_repos,
    get_nexus_dir_name,
    get_project_platform,
    get_tasks_logs_dir,
)
# Nexus Core framework imports
from nexus.core.guards import LaunchGuard
from nexus.core.inbox.inbox_repo_path_service import (
    extract_repo_from_issue_url as _extract_repo_from_issue_url,
)
from nexus.core.inbox.inbox_repo_path_service import (
    resolve_git_dir_for_repo as _resolve_git_dir_for_repo,
)
from nexus.core.integrations.notifications import notify_agent_completed, emit_alert
from nexus.core.orchestration.ai_orchestrator import get_orchestrator
from nexus.core.orchestration.plugin_runtime import get_profiled_plugin
from nexus.core.project.repo_utils import (
    iter_project_configs as _iter_project_configs,
)
from nexus.core.project.repo_utils import (
    project_repos_from_config as _project_repos,
)
from nexus.core.runtime_mode import is_postgres_backend
from nexus.core.state_manager import HostStateManager
from nexus.plugins.builtin.ai_runtime_plugin import ToolUnavailableError

logger = logging.getLogger(__name__)
_git_platform_cache = {}
_launch_policy_plugin = None


def _run_coro_sync(coro_factory):
    def _close_awaitable_if_needed(candidate: object | None) -> None:
        if candidate is None or not inspect.isawaitable(candidate):
            return
        close = getattr(candidate, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                return

    try:
        asyncio.get_running_loop()
        in_running_loop = True
    except RuntimeError:
        in_running_loop = False

    if not in_running_loop:
        candidate = None
        try:
            candidate = coro_factory()
            if not inspect.isawaitable(candidate):
                return candidate
            return asyncio.run(candidate)
        except Exception:
            _close_awaitable_if_needed(candidate)
            return None

    holder: dict[str, object | Exception | None] = {"value": None, "error": None}

    def _runner() -> None:
        candidate = None
        try:
            candidate = coro_factory()
            if not inspect.isawaitable(candidate):
                holder["value"] = candidate
                return
            holder["value"] = asyncio.run(candidate)
        except Exception as exc:
            _close_awaitable_if_needed(candidate)
            holder["error"] = exc

    import threading

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=10)
    if t.is_alive() or holder.get("error") is not None:
        return None
    return holder.get("value")


_NON_AGENT_TRIGGER_SOURCES = {
    "unknown",
    "git_webhook",
    "github_webhook",
    "gitlab_webhook",
    "push_completion",
    "pr_opened",
    "completion-scan",
    "orchestrator",
    "orphan-recovery",
    "dead-agent-retry",
    "orphan-timeout-retry",
    "timeout-retry",
}


def _completed_agent_from_trigger(trigger_source: str) -> str | None:
    """Return completed-agent label only when trigger_source is agent-like."""
    source = str(trigger_source or "").strip().lstrip("@").strip()
    if not source:
        return None
    if source.lower().startswith("manual-"):
        return "manual"
    normalized = source.lower()
    if normalized in _NON_AGENT_TRIGGER_SOURCES:
        return None
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}", source):
        return None
    return normalized


def _resolve_requester_token_for_issue(
    issue_number: str,
    repo: str,
    project_name: str | None,
) -> str | None:
    if not auth_enabled():
        return None
    requester_nexus_id = get_issue_requester(str(repo), str(issue_number))
    if not requester_nexus_id:
        # Fallback to URL-bound requester lookup for cases where repo-key
        # formatting differs from stored bindings.
        try:
            platform = str(get_project_platform(str(project_name or "")) or "").strip().lower()
            issue_url = build_issue_url(
                str(repo),
                str(issue_number),
                {"git_platform": platform or "github"},
            )
            requester_nexus_id = get_issue_requester_by_url(issue_url)
        except Exception:
            requester_nexus_id = None
    if not requester_nexus_id:
        return None
    user_env, env_error = build_execution_env(str(requester_nexus_id))
    if env_error:
        logger.warning(
            "Requester token unavailable for issue #%s repo=%s project=%s requester=%s: %s",
            issue_number,
            repo,
            project_name,
            requester_nexus_id,
            env_error,
        )
        return None
    platform = str(get_project_platform(str(project_name or "")) or "").strip().lower()
    if platform == "gitlab":
        return str(user_env.get("GITLAB_TOKEN") or user_env.get("GITHUB_TOKEN") or "").strip() or None
    return str(user_env.get("GITHUB_TOKEN") or user_env.get("GITLAB_TOKEN") or "").strip() or None


def _resolve_issue_requester_nexus_id(
    *,
    issue_url: str,
) -> str | None:
    """Resolve requester Nexus ID for an issue URL when available."""
    return _lookup_bound_issue_requester_nexus_id(issue_url)


def _lookup_bound_issue_requester_nexus_id(issue_url: str) -> str | None:
    """Resolve issue-bound requester Nexus ID from URL and repo/issue mapping."""
    issue_url_str = str(issue_url or "").strip()
    if not issue_url_str:
        return None

    try:
        resolved = str(get_issue_requester_by_url(issue_url_str) or "").strip() or None
    except Exception:
        resolved = None

    if resolved:
        return resolved

    issue_num_match = re.search(r"/issues/(\d+)(?:$|[/?#])", issue_url_str)
    issue_repo = _extract_repo_from_issue_url(issue_url_str)
    if not issue_num_match or not issue_repo:
        return None

    try:
        return str(get_issue_requester(str(issue_repo), str(issue_num_match.group(1))) or "").strip() or None
    except Exception:
        return None


def _get_git_platform_client(
    repo: str,
    project_name: str | None = None,
    token_override: str | None = None,
):
    """Return cached abstract git platform adapter for repository."""
    from nexus.core.orchestration.nexus_core_helpers import get_git_platform

    cache_key = f"{project_name or ''}:{repo}"
    if not token_override and cache_key in _git_platform_cache:
        return _git_platform_cache[cache_key]

    platform = get_git_platform(
        repo=repo,
        project_name=project_name or None,
        token_override=token_override,
    )
    if platform and not token_override:
        _git_platform_cache[cache_key] = platform
    return platform


def _resolve_project_from_task_file(task_file: str) -> str:
    """Resolve project key by matching task file path against project workspaces."""
    for project_key, project_cfg in _iter_project_configs(PROJECT_CONFIG, get_repos):
        workspace_abs = os.path.join(BASE_DIR, str(project_cfg["workspace"]))
        if task_file.startswith(workspace_abs):
            return project_key
    return ""


def _resolve_project_from_repo(repo_name: str) -> str:
    """Resolve project key from configured project repo mappings."""
    target = str(repo_name or "").strip()
    if not target:
        return ""
    for project_key, cfg in _iter_project_configs(PROJECT_CONFIG, get_repos):
        if target in _project_repos(project_key, cfg, get_repos):
            return project_key
    return ""


def _load_issue_body_from_project_repo(issue_number: str, preferred_repo: str | None = None):
    """Load issue body from the repo that matches task-file/project boundaries.

    Returns:
        ``(body, repo, task_file)`` when resolved, otherwise ``("", "", "")``.
    """
    issue_number = str(issue_number)
    candidate_repos: list[tuple[str, str]] = []
    for project_key, cfg in _iter_project_configs(PROJECT_CONFIG, get_repos):
        project_repos = _project_repos(project_key, cfg, get_repos)
        for repo_name in project_repos:
            pair = (project_key, repo_name)
            if pair not in candidate_repos:
                candidate_repos.append(pair)

    preferred = str(preferred_repo or "").strip()
    if preferred:
        preferred_pairs = [pair for pair in candidate_repos if pair[1] == preferred]
        other_pairs = [pair for pair in candidate_repos if pair[1] != preferred]
        candidate_repos = preferred_pairs + other_pairs

    for project_key, repo_name in candidate_repos:
        try:
            token_override = _resolve_requester_token_for_issue(
                issue_number,
                repo_name,
                project_key,
            )
            platform = _get_git_platform_client(
                repo_name,
                project_name=project_key,
                token_override=token_override,
            )
        except Exception as exc:
            logger.warning(
                "Skipping issue probe for issue #%s in %s (%s): %s",
                issue_number,
                repo_name,
                project_key,
                exc,
            )
            continue
        if not platform:
            continue

        issue = _run_coro_sync(lambda: platform.get_issue(issue_number))
        if not issue:
            continue

        body = str(getattr(issue, "body", "") or "")
        if not body:
            continue

        task_file_match = re.search(r"\*\*Task File:\*\*\s*`([^`]+)`", body)
        task_file = task_file_match.group(1) if task_file_match else ""
        if is_postgres_backend(NEXUS_STORAGE_BACKEND):
            # In DB-only mode, trust project->repo binding and avoid local task-file path resolution.
            return body, repo_name, task_file
        if not task_file:
            continue

        resolved_project_key = _resolve_project_from_task_file(task_file)
        if not resolved_project_key:
            continue

        project_cfg = PROJECT_CONFIG.get(resolved_project_key, {})
        expected_repos = _project_repos(resolved_project_key, project_cfg, get_repos)
        if repo_name not in expected_repos:
            continue

        return body, repo_name, task_file

    return "", "", ""


def _get_launch_policy_plugin():
    """Return shared agent launch policy plugin instance."""
    global _launch_policy_plugin
    if _launch_policy_plugin:
        return _launch_policy_plugin

    try:
        default_project = str(get_default_project() or "").strip()
    except Exception:
        default_project = ""

    default_system_operations = get_system_operations(default_project) if default_project else {}

    completion_backend = "postgres" if is_postgres_backend(NEXUS_STORAGE_BACKEND) else "filesystem"
    webhook_url = NEXUS_WEBHOOK_INTERNAL_URL or f"http://127.0.0.1:{WEBHOOK_PORT}"

    plugin = get_profiled_plugin(
        "agent_launch_policy",
        overrides={
            "system_operations": default_system_operations,
            "system_operations_resolver": get_system_operations,
            "default_project_name": default_project,
            "completion_backend": completion_backend,
            "webhook_url": webhook_url,
        },
        cache_key="agent-launch:policy",
    )
    if plugin:
        _launch_policy_plugin = plugin
    return plugin


def _merge_excluded_tools(*tool_lists) -> list[str]:
    """Merge tool names preserving first-seen order."""
    merged: list[str] = []
    for tools in tool_lists:
        if not tools:
            continue
        for tool in tools:
            value = str(tool or "").strip().lower()
            if value and value not in merged:
                merged.append(value)
    return merged


def _tool_is_rate_limited(orchestrator, tool_name: str) -> bool:
    """Return True when orchestrator has an active rate-limit cooldown for tool."""
    try:
        rate_limits = getattr(orchestrator, "_rate_limits", {})
        if not isinstance(rate_limits, dict):
            return False
        info = rate_limits.get(str(tool_name or "").strip().lower())
        if not isinstance(info, dict):
            return False
        until = float(info.get("until", 0) or 0)
        return until > time.time()
    except Exception:
        return False


def _persist_issue_excluded_tools(issue_num: str, tools: list[str]) -> None:
    """Persist issue-level excluded tools in launched_agents state."""
    if not issue_num or issue_num == "unknown":
        return
    merged = _merge_excluded_tools(tools)
    if not merged:
        return

    launched_agents = HostStateManager.load_launched_agents(recent_only=False)
    previous_entry = launched_agents.get(str(issue_num), {})
    if not isinstance(previous_entry, dict):
        previous_entry = {}

    entry = dict(previous_entry)
    entry["exclude_tools"] = _merge_excluded_tools(previous_entry.get("exclude_tools", []), merged)
    launched_agents[str(issue_num)] = entry
    HostStateManager.save_launched_agents(launched_agents)


def _read_tail(path: str, max_chars: int = 4000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            data = handle.read()
        if len(data) <= max_chars:
            return data
        return data[-max_chars:]
    except Exception:
        return ""


def _log_indicates_any_quota_failure(log_text: str) -> bool:
    """Check log content for quota/rate-limit failure markers from any provider."""
    text = str(log_text or "").lower()
    if not text:
        return False
    quota_markers = (
        "402",
        "429",
        "quota",
        "rate limit",
        "ratelimit",
        "too many requests",
        "status: 429",
        "status 429",
        "exhausted your capacity",
        "retryablequotaerror",
        "insufficient_quota",
        "no capacity available",
    )
    progress_markers = (
        "total session time",
        "total usage est",
        "api time spent",
        "retrying after",
        "attempt 1 failed",
        "attempt 2 failed",
        "retry",
    )
    return any(marker in text for marker in quota_markers) and any(
        marker in text for marker in progress_markers
    )


def _find_latest_agent_log(workspace_dir: str, project_key: str | None, issue_num: str, tool_name: str = "*") -> str:
    """Find the most recent log file for a specific issue and tool."""
    project = str(project_key or "nexus")
    log_dir = get_tasks_logs_dir(workspace_dir, project)
    pattern = os.path.join(log_dir, f"{tool_name}_{issue_num}_*.log")
    candidates = glob.glob(pattern)
    if not candidates:
        return ""
    return max(candidates, key=os.path.getmtime)


def _normalize_agent_reference(value: object) -> str:
    return str(value or "").strip().lstrip("@").strip().lower()


def _iter_json_objects(text: str):
    decoder = json.JSONDecoder()
    index = 0
    content = str(text or "")
    length = len(content)
    while index < length:
        start = content.find("{", index)
        if start < 0:
            break
        try:
            payload, consumed = decoder.raw_decode(content[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        index = start + max(consumed, 1)
        if isinstance(payload, dict):
            yield payload


def _iter_shell_data_json_objects(text: str):
    """Extract JSON payloads from multiline `curl ... -d '{...}'` shell blocks."""
    lines = str(text or "").splitlines()
    index = 0
    total = len(lines)

    while index < total:
        line = lines[index]
        marker_pos = line.find("-d '{")
        if marker_pos < 0:
            index += 1
            continue

        brace_pos = line.find("{", marker_pos)
        if brace_pos < 0:
            index += 1
            continue

        payload_lines = [line[brace_pos:]]
        cursor = index + 1
        while cursor < total:
            payload_lines.append(lines[cursor])
            if lines[cursor].rstrip().endswith("}'"):
                break
            cursor += 1
        index = cursor + 1

        raw_payload = "\n".join(payload_lines)
        normalized_payload = re.sub(r"}'\s*$", "}", raw_payload, count=1)
        # Shell escaping for apostrophes inside single-quoted -d payload.
        normalized_payload = normalized_payload.replace("'\\''", "'")
        normalized_payload = normalized_payload.replace("'\"'\"'", "'")

        try:
            parsed = json.loads(normalized_payload)
        except Exception:
            continue
        if isinstance(parsed, dict):
            yield parsed


def _extract_completion_payload_from_log_text(
    log_text: str,
    *,
    issue_num: str,
    agent_type: str,
) -> dict | None:
    """Best-effort extraction of completion JSON printed by an agent CLI."""

    def _looks_like_instruction_template(value: object) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        lowered = text.lower()
        markers = (
            "<one-line summary of what you did>",
            "<finding 1>",
            "<finding 2>",
            "<step name>",
            "<agent_type from workflow steps",
            "@<display name>",
            "<placeholder>",
        )
        return any(marker in lowered for marker in markers)

    def _is_template_payload(candidate: dict) -> bool:
        if _looks_like_instruction_template(candidate.get("summary")):
            return True
        if _looks_like_instruction_template(candidate.get("next_agent")):
            return True
        if _looks_like_instruction_template(candidate.get("comment_markdown")):
            return True
        findings = candidate.get("key_findings")
        if isinstance(findings, list):
            for item in findings:
                if _looks_like_instruction_template(item):
                    return True
        return False

    expected_issue = str(issue_num or "").strip()
    expected_agent = _normalize_agent_reference(agent_type)
    latest_match: dict | None = None

    for candidate in list(_iter_json_objects(log_text)) + list(_iter_shell_data_json_objects(log_text)):
        step_id = str(candidate.get("step_id") or "").strip()
        try:
            step_num = int(candidate.get("step_num") or 0)
        except (TypeError, ValueError):
            step_num = 0
        if not step_id or step_num <= 0:
            continue

        payload_issue = str(candidate.get("issue_number") or "").strip()
        if payload_issue and expected_issue and payload_issue != expected_issue:
            continue

        payload_agent = _normalize_agent_reference(candidate.get("agent_type"))
        if payload_agent and expected_agent and payload_agent != expected_agent:
            continue

        if "summary" not in candidate and "comment_markdown" not in candidate:
            continue
        if _is_template_payload(candidate):
            continue

        payload = dict(candidate)
        payload["issue_number"] = expected_issue
        payload["agent_type"] = expected_agent or payload_agent or "unknown"
        payload["step_id"] = step_id
        payload["step_num"] = step_num
        payload.setdefault("status", "complete")
        latest_match = payload

    return latest_match


def _hydrate_workflow_context_for_completion(issue_num: str, payload: dict) -> dict:
    hydrated = dict(payload or {})
    if hydrated.get("workflow_id"):
        return hydrated

    try:
        from nexus.core.orchestration.nexus_core_helpers import get_workflow_status

        status = _run_coro_sync(lambda: get_workflow_status(str(issue_num)))
    except Exception:
        status = None

    if isinstance(status, dict):
        workflow_id = str(status.get("workflow_id") or "").strip()
        if workflow_id:
            hydrated["workflow_id"] = workflow_id
    return hydrated


def _recover_completion_from_agent_log(
    *,
    issue_num: str,
    agent_type: str,
    workspace_dir: str,
    project_key: str | None,
    tool_name: str,
    log_path: str,
    issue_url: str = "",
) -> str | None:
    """Recover completion payload from agent stdout log when POST was not executed."""
    if not is_postgres_backend(NEXUS_STORAGE_BACKEND):
        return None
    if not log_path or not os.path.exists(log_path):
        return None

    tail = _read_tail(log_path, max_chars=600000)
    payload = _extract_completion_payload_from_log_text(
        tail,
        issue_num=str(issue_num),
        agent_type=str(agent_type),
    )
    if not payload:
        return None

    payload = _hydrate_workflow_context_for_completion(str(issue_num), payload)
    if project_key:
        payload.setdefault("_project", str(project_key))

    def _resolve_recovery_repo() -> str:
        from_issue = _extract_repo_from_issue_url(str(issue_url or ""))
        if from_issue:
            return str(from_issue)
        if project_key:
            try:
                return str(get_repo(str(project_key)))
            except Exception:
                return ""
        return ""

    def _resolve_comment_body(completion_payload: dict) -> str:
        from nexus.core.completion import (
            CompletionSummary,
            build_completion_comment,
            normalize_completion_comment_markdown,
        )

        markdown = normalize_completion_comment_markdown(
            completion_payload.get("comment_markdown") or ""
        )
        if markdown:
            return markdown
        summary = CompletionSummary.from_dict(completion_payload)
        return build_completion_comment(summary)

    def _normalize_comment_text(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip()).lower()

    def _completion_comment_exists(
        platform,
        *,
        target_step_id: str,
        target_step_num: int,
        expected_comment_body: str = "",
    ) -> bool:
        comments = _run_coro_sync(lambda: platform.get_comments(str(issue_num)))
        if not isinstance(comments, list):
            return False
        step_id_norm = str(target_step_id or "").strip()
        step_num_int = int(target_step_num)
        body_norm_expected = _normalize_comment_text(expected_comment_body)
        exact_body_required = len(body_norm_expected) >= 64

        step_id_re = re.compile(
            rf"step\s*id\s*:\s*`?\s*{re.escape(step_id_norm)}\s*`?",
            re.IGNORECASE,
        )
        step_num_re = re.compile(
            rf"step\s*num(?:ber)?\s*:\s*{step_num_int}\b",
            re.IGNORECASE,
        )

        for comment in reversed(comments):
            if isinstance(comment, dict):
                body = str(comment.get("body") or "")
            else:
                body = str(getattr(comment, "body", "") or "")
            body_norm = _normalize_comment_text(body)

            if exact_body_required and body_norm == body_norm_expected:
                return True
            if exact_body_required and body_norm_expected in body_norm:
                return True

            if step_id_re.search(body) and step_num_re.search(body):
                return True
        return False

    def _post_recovered_completion_comment(completion_payload: dict) -> bool:
        repo = _resolve_recovery_repo()
        if not repo:
            return False
        step_id = str(completion_payload.get("step_id") or "").strip()
        try:
            step_num = int(completion_payload.get("step_num") or 0)
        except (TypeError, ValueError):
            step_num = 0
        if not step_id or step_num <= 0:
            return False

        token_override = _resolve_requester_token_for_issue(
            str(issue_num),
            str(repo),
            str(project_key or ""),
        )
        platform = _get_git_platform_client(
            str(repo),
            project_name=str(project_key or ""),
            token_override=token_override,
        )
        if not platform:
            return False
        comment_body = _resolve_comment_body(completion_payload)
        if not comment_body:
            return False
        if _completion_comment_exists(
            platform,
            target_step_id=step_id,
            target_step_num=step_num,
            expected_comment_body=comment_body,
        ):
            return True

        posted = _run_coro_sync(lambda: platform.add_comment(str(issue_num), comment_body))
        return posted is not None

    try:
        from nexus.core.completion_store import CompletionStore
        from nexus.core.integrations.workflow_state_factory import get_storage_backend

        store = CompletionStore(
            backend="postgres",
            storage=get_storage_backend(),
            base_dir=BASE_DIR,
            nexus_dir=get_nexus_dir_name(),
        )
        dedup_key = store.save(str(issue_num), str(agent_type), payload)
        logger.warning(
            "Recovered completion payload from %s log for issue #%s (%s) dedup=%s",
            tool_name,
            issue_num,
            agent_type,
            dedup_key,
        )
        comment_ok = _post_recovered_completion_comment(payload)
        if not comment_ok:
            logger.warning(
                "Recovered completion for issue #%s but could not confirm comment delivery",
                issue_num,
            )
        return str(dedup_key)
    except Exception as exc:
        logger.warning(
            "Failed recovering completion payload from log for issue #%s (%s): %s",
            issue_num,
            agent_type,
            exc,
        )
        return None


def _start_completion_recovery_watchdog(
    *,
    issue_num: str,
    pid: int,
    agent_type: str,
    workspace_dir: str,
    project_key: str | None,
    tool_name: str,
    issue_url: str,
) -> None:
    """After launch, watch for process exit and salvage completion payload from logs."""

    def _pid_alive(candidate: int) -> bool:
        try:
            os.kill(int(candidate), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return False

    def _worker() -> None:
        deadline = time.time() + 1200
        while time.time() < deadline:
            launched = HostStateManager.load_launched_agents(recent_only=False)
            current = launched.get(str(issue_num), {})
            current_pid = int((current or {}).get("pid", 0) or 0) if isinstance(current, dict) else 0
            if current_pid != int(pid):
                return

            if _pid_alive(pid):
                time.sleep(0.8)
                continue

            # Give the log tee thread a short flush window.
            time.sleep(1.0)
            log_path = _find_latest_agent_log(
                workspace_dir,
                project_key,
                str(issue_num),
                tool_name=str(tool_name or "*"),
            )
            if not log_path:
                log_path = _find_latest_agent_log(
                    workspace_dir,
                    project_key,
                    str(issue_num),
                    tool_name="*",
                )

            dedup_key = _recover_completion_from_agent_log(
                issue_num=str(issue_num),
                agent_type=str(agent_type),
                workspace_dir=str(workspace_dir),
                project_key=project_key,
                tool_name=str(tool_name or "agent"),
                log_path=str(log_path or ""),
                issue_url=str(issue_url or ""),
            )
            if dedup_key:
                try:
                    AuditStore.audit_log(
                        int(issue_num),
                        "COMPLETION_RECOVERED_FROM_LOG",
                        f"Recovered completion from {tool_name} log (dedup={dedup_key})",
                    )
                except Exception:
                    pass
            return

    threading.Thread(
        target=_worker,
        name=f"completion-recovery-{issue_num}",
        daemon=True,
    ).start()


def _start_agent_quota_watchdog(
    *,
    issue_num: str,
    pid: int,
    agent_type: str,
    prompt: str,
    workspace_dir: str,
    agents_dir: str,
    base_dir: str,
    log_subdir: str | None,
    agent_env: dict[str, str] | None,
    orchestrator,
    exclude_tools: list[str] | None,
    tier_name: str,
    mode: str,
    workflow_name: str,
    tool_name: str,
) -> None:
    """Watchdog that monitors agent logs for quota failures and triggers fallback."""
    def _worker() -> None:
        logger.info(
            "AI Agent watchdog started for %s on issue #%s (pid=%s, agent=%s)",
            tool_name,
            issue_num,
            pid,
            agent_type,
        )

        def _on_quota_detected(*, reason: str, terminate_pid: bool) -> None:
            logger.warning(
                "AI Agent watchdog detected quota/rate-limit for %s on issue #%s (pid=%s, reason=%s).",
                tool_name,
                issue_num,
                pid,
                reason,
            )
            if terminate_pid:
                try:
                    os.kill(int(pid), 15)
                    logger.info("Watchdog terminated pid=%s for issue #%s", pid, issue_num)
                except Exception:
                    pass

            merged_exclusions = _merge_excluded_tools(exclude_tools or [], [tool_name])
            _persist_issue_excluded_tools(issue_num, [tool_name])

            logger.warning(
                "Quota detected for %s on issue #%s (pid=%s). Auto-fallback with exclusions=%s",
                tool_name,
                issue_num,
                pid,
                merged_exclusions,
            )

            emit_alert(
                (
                    f"⚠️ {tool_name.capitalize()} quota detected for issue #{issue_num}. "
                    "Auto-switching to fallback provider."
                ),
                severity="warning",
                source="agent_launcher",
                issue_number=str(issue_num),
                project_key=str(log_subdir or "nexus"),
            )

            try:
                logger.info(
                    "Watchdog invoking fallback chain for issue #%s (exclude=%s)",
                    issue_num,
                    merged_exclusions,
                )
                pid_new, tool_new = orchestrator.invoke_agent(
                    agent_prompt=prompt,
                    workspace_dir=workspace_dir,
                    agents_dir=agents_dir,
                    base_dir=base_dir,
                    issue_url=build_issue_url(
                        get_repo(str(log_subdir or "nexus")),
                        str(issue_num),
                        PROJECT_CONFIG.get(str(log_subdir or "nexus")),
                    ),
                    agent_name=agent_type,
                    project_name=str(log_subdir or "nexus"),
                    preferred_tool=None,
                    exclude_tools=merged_exclusions,
                    log_subdir=log_subdir,
                    env=agent_env,
                )
                if pid_new:
                    new_tool = str(getattr(tool_new, "value", tool_new))
                    launched_agents = HostStateManager.load_launched_agents(recent_only=False)
                    prev = launched_agents.get(str(issue_num), {})
                    if not isinstance(prev, dict):
                        prev = {}
                    requester_nexus_id = str(prev.get("requester_nexus_id") or "").strip() or None
                    launched_agents[str(issue_num)] = {
                        **prev,
                        "timestamp": time.time(),
                        "pid": int(pid_new or 0),
                        "requester_nexus_id": requester_nexus_id,
                        "tier": tier_name,
                        "mode": mode,
                        "tool": new_tool,
                        "agent_type": agent_type,
                        "exclude_tools": _merge_excluded_tools(
                            prev.get("exclude_tools", []), merged_exclusions
                        ),
                    }
                    HostStateManager.save_launched_agents(launched_agents)
                    record_agent_launch(issue_num, agent_type=agent_type, pid=int(pid_new or 0))
                    AuditStore.audit_log(
                        int(issue_num),
                        "AGENT_LAUNCHED",
                        f"Auto-fallback launched {new_tool} after {tool_name.capitalize()} quota (workflow: {workflow_name}/{tier_name}, mode: {mode}, PID: {pid_new})",
                        user_id=requester_nexus_id,
                    )
                    logger.info(
                        "✅ Auto-fallback launch succeeded for issue #%s with %s (PID: %s)",
                        issue_num,
                        new_tool,
                        pid_new,
                    )
                    # Start a new watchdog for the fallback process
                    _start_agent_quota_watchdog(
                        issue_num=issue_num,
                        pid=int(pid_new),
                        tool_name=new_tool,
                        agent_type=agent_type,
                        prompt=prompt,
                        workspace_dir=workspace_dir,
                        agents_dir=agents_dir,
                        base_dir=base_dir,
                        log_subdir=log_subdir,
                        agent_env=agent_env,
                        orchestrator=orchestrator,
                        exclude_tools=merged_exclusions,
                        tier_name=tier_name,
                        mode=mode,
                        workflow_name=workflow_name,
                    )
                else:
                    logger.error(
                        "Auto-fallback launch returned no PID for issue #%s after %s quota",
                        issue_num,
                        tool_name.capitalize(),
                    )
                    emit_alert(
                        (
                            f"❌ No AI providers available after {tool_name.capitalize()} quota on issue #{issue_num}. "
                            "All fallback providers are unavailable or rate-limited."
                        ),
                        severity="error",
                        source="agent_launcher",
                        issue_number=str(issue_num),
                        project_key=str(log_subdir or "nexus"),
                    )
                    AuditStore.audit_log(
                        int(issue_num),
                        "AGENT_LAUNCH_FAILED",
                        f"No fallback providers available after {tool_name.capitalize()} quota watchdog trigger",
                    )
            except ToolUnavailableError as exc:
                logger.error(
                    "%s watchdog fallback exhausted providers for issue #%s: %s",
                    tool_name.capitalize(),
                    issue_num,
                    exc,
                )
                emit_alert(
                    (
                        f"❌ No AI providers available for issue #{issue_num} "
                        f"after {tool_name.capitalize()} quota: {exc}"
                    ),
                    severity="error",
                    source="agent_launcher",
                    issue_number=str(issue_num),
                    project_key=str(log_subdir or "nexus"),
                )
                AuditStore.audit_log(
                    int(issue_num),
                    "AGENT_LAUNCH_FAILED",
                    f"Fallback exhausted after {tool_name.capitalize()} quota: {exc}",
                )
            except Exception as exc:
                logger.error(
                    "Auto-fallback launch failed for issue #%s after %s quota: %s",
                    issue_num,
                    tool_name.capitalize(),
                    exc,
                    exc_info=True,
                )

        # Loop for up to 5 minutes
        deadline = time.time() + 300
        while time.time() < deadline:
            # Verify we are still the active process for this issue
            launched = HostStateManager.load_launched_agents(recent_only=False)
            current = launched.get(str(issue_num), {})
            if not isinstance(current, dict) or int(current.get("pid", 0) or 0) != int(pid):
                return

            log_path = _find_latest_agent_log(workspace_dir, log_subdir, issue_num, tool_name=tool_name)
            if log_path and os.path.exists(log_path):
                tail = _read_tail(log_path)
                if _log_indicates_any_quota_failure(tail):
                    _on_quota_detected(reason="Detected in logs", terminate_pid=True)
                    return

    t = threading.Thread(
        target=_worker,
        name=f"agent-watchdog-{issue_num}",
        daemon=True,
    )
    t.start()


def _attach_post_launch_watchdog(
    *,
    tool_name: str,
    issue_num: str,
    pid: int,
    agent_type: str,
    prompt: str,
    workspace_dir: str,
    agents_dir: str,
    base_dir: str,
    log_subdir: str | None,
    agent_env: dict[str, str] | None,
    orchestrator,
    exclude_tools: list[str] | None,
    tier_name: str,
    mode: str,
    workflow_name: str,
    issue_url: str,
) -> None:
    normalized_tool = str(tool_name or "").strip().lower()
    _start_agent_quota_watchdog(
        issue_num=issue_num,
        pid=pid,
        tool_name=normalized_tool,
        agent_type=agent_type,
        prompt=prompt,
        workspace_dir=workspace_dir,
        agents_dir=agents_dir,
        base_dir=base_dir,
        log_subdir=log_subdir,
        agent_env=agent_env,
        orchestrator=orchestrator,
        exclude_tools=exclude_tools,
        tier_name=tier_name,
        mode=mode,
        workflow_name=workflow_name,
    )
    _start_completion_recovery_watchdog(
        issue_num=issue_num,
        pid=pid,
        agent_type=agent_type,
        workspace_dir=workspace_dir,
        project_key=log_subdir,
        tool_name=normalized_tool or "*",
        issue_url=issue_url,
    )


def _pgrep_and_logfile_guard(issue_id: str, agent_type: str) -> bool:
    """Custom guard: returns True (allow) if no running process AND no recent log.

    Check 1: pgrep for running supported agent process on this issue
    Check 2: recent log files (within last 2 minutes)
    """
    # Check 1: Running processes
    try:
        # Search for any process mentioning issues/{issue_id}
        check_result = subprocess.run(
            [
                "pgrep",
                "-af",
                f"issues/{issue_id}[^0-9]|issues/{issue_id}$",
            ],
            text=True,
            capture_output=True,
            timeout=5,
        )
        # Limit matches to those that look like an agent invocation (containing .agent.md or similar)
        if check_result.stdout and (".agent.md" in check_result.stdout or "agent" in check_result.stdout.lower()):
            logger.info(f"⏭️ Agent already running for issue #{issue_id} (PID found)")
            return False
    except Exception:
        pass

    # Check 2: Recent log files (within last 2 minutes)
    nexus_dir_name = get_nexus_dir_name()
    recent_logs = glob.glob(
        os.path.join(
            BASE_DIR,
            "**",
            nexus_dir_name,
            "tasks",
            "logs",
            "**",
            f"*_{issue_id}_*.log",  # Use wildcard for tool_name
        ),
        recursive=True,
    )
    if recent_logs:
        recent_logs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        latest_log_age = time.time() - os.path.getmtime(recent_logs[0])
        if latest_log_age < 120:
            logger.info(f"⏭️ Recent log file for issue #{issue_id} ({latest_log_age:.0f}s old)")
            return False

    return True  # allow launch


# Module-level singleton — LaunchGuard with 120s cooldown + pgrep/logfile custom guard.
_launch_guard = LaunchGuard(
    cooldown_seconds=120,
    custom_guard=_pgrep_and_logfile_guard,
)


def is_recent_launch(issue_number: str, agent_type: str = "*") -> bool:
    """Check if an agent was recently launched for this issue.

    Delegates to nexus-arc's LaunchGuard (cooldown + pgrep + logfile checks).
    Returns True if launched within cooldown window.
    """
    normalized_agent = str(agent_type or "*").strip() or "*"
    return not _launch_guard.can_launch(str(issue_number), agent_type=normalized_agent)


def record_agent_launch(issue_number: str, agent_type: str = "*", pid: int = None) -> None:
    """Record a successful agent launch in the LaunchGuard."""
    normalized_agent = str(agent_type or "*").strip() or "*"
    _launch_guard.record_launch(str(issue_number), agent_type=normalized_agent, pid=pid)


def clear_launch_guard(issue_number: str) -> int:
    """Clear the LaunchGuard for an issue, allowing an immediate relaunch.

    Used by the dead-agent retry path to bypass the cooldown window when
    we intentionally want to relaunch a crashed agent.

    Returns:
        Number of cleared records.
    """
    return _launch_guard.clear(str(issue_number))


def _resolve_workflow_path(project_name: str = None) -> str:
    """Resolve workflow definition path for project or global config."""
    workflow_path = ""
    if project_name:
        project_cfg = PROJECT_CONFIG.get(project_name, {})
        if isinstance(project_cfg, dict):
            workflow_path = project_cfg.get("workflow_definition_path", "")

    if not workflow_path:
        workflow_path = PROJECT_CONFIG.get("workflow_definition_path", "")

    if workflow_path and not os.path.isabs(workflow_path):
        workflow_path = os.path.join(BASE_DIR, workflow_path)

    return workflow_path


def _is_git_repo(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _resolve_worktree_base_repo(workspace_dir: str, issue_url: str) -> str:
    """Resolve git repo root used for worktree provisioning.

    For mono-workspace setups where ``workspace_dir`` is a parent folder (e.g. ``/home/.../ghabs``),
    use repo name from issue URL to find nested checkout (e.g. ``/home/.../ghabs/nexus-arc``).
    """
    base = str(workspace_dir or "").strip()
    if _is_git_repo(base):
        return base

    repo_slug = _extract_repo_from_issue_url(str(issue_url or ""))
    repo_candidates: list[str] = []
    if repo_slug:
        repo_candidates.append(repo_slug)
        tail = repo_slug.split("/")[-1].strip()
        if tail and tail not in repo_candidates:
            repo_candidates.append(tail)

    for candidate in repo_candidates:
        nested = os.path.join(base, candidate)
        if _is_git_repo(nested):
            return nested

    return base


def _resolve_worktree_targets(
    *,
    project_name: str | None,
    issue_repo_slug: str,
    fallback_repo_dir: str,
) -> dict[str, str]:
    """Resolve repositories that should receive issue worktrees for this launch."""
    targets: dict[str, str] = {}
    normalized_project = str(project_name or "").strip()
    if normalized_project:
        try:
            configured_repos = get_repos(normalized_project)
        except Exception:
            configured_repos = []

        for repo_slug in configured_repos:
            repo_key = str(repo_slug or "").strip()
            if not repo_key:
                continue
            try:
                repo_dir = _resolve_git_dir_for_repo(
                    project_name=normalized_project,
                    repo_name=repo_key,
                    project_config=PROJECT_CONFIG,
                    base_dir=BASE_DIR,
                )
            except Exception:
                repo_dir = None
            if repo_dir and _is_git_repo(str(repo_dir)):
                targets[repo_key] = str(repo_dir)

    if targets:
        return targets

    fallback = str(fallback_repo_dir or "").strip()
    if not fallback or not _is_git_repo(fallback):
        return {}

    repo_key = str(issue_repo_slug or "").strip()
    if repo_key:
        return {repo_key: fallback}

    fallback_name = os.path.basename(fallback.rstrip(os.sep)) or "repo"
    return {fallback_name: fallback}


def _select_primary_worktree(
    *,
    issue_repo_slug: str,
    fallback_repo_dir: str,
    targets: dict[str, str],
    provisioned: dict[str, str],
) -> str:
    """Select primary worktree path used as launched process working directory."""
    issue_repo = str(issue_repo_slug or "").strip()
    if issue_repo and issue_repo in provisioned:
        return provisioned[issue_repo]

    fallback_abs = os.path.abspath(str(fallback_repo_dir or "").strip())
    for repo_key, repo_dir in targets.items():
        if os.path.abspath(str(repo_dir)) == fallback_abs and repo_key in provisioned:
            return provisioned[repo_key]

    if len(provisioned) == 1:
        return next(iter(provisioned.values()))

    return str(fallback_repo_dir)


def _coerce_optional_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _normalize_step_ref(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower())
    return normalized.strip("-")


def _resolve_step_requires_worktree(
    *,
    workflow_path: str,
    tier_name: str,
    workflow_name: str,
    step_id: str,
    step_num: int,
) -> bool | None:
    path = str(workflow_path or "").strip()
    if not path or not os.path.isfile(path):
        return None

    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except Exception as exc:
        logger.debug("Could not parse workflow definition (%s): %s", path, exc)
        return None

    if not isinstance(data, dict):
        return None
    data["__yaml_path"] = path

    try:
        from nexus.core.workflow_engine.workflow_definition_loader import resolve_workflow_steps_list
    except Exception:
        return None

    workflow_type_candidates: list[str] = []
    for candidate in (
        tier_name,
        f"workflow:{tier_name}" if tier_name else "",
        workflow_name,
        f"workflow:{workflow_name}" if workflow_name else "",
        "",
    ):
        candidate_str = str(candidate or "").strip()
        if candidate_str in workflow_type_candidates:
            continue
        workflow_type_candidates.append(candidate_str)

    steps: list[dict] = []
    for workflow_type in workflow_type_candidates:
        try:
            resolved = resolve_workflow_steps_list(data, workflow_type)
        except Exception:
            continue
        if isinstance(resolved, list) and resolved:
            steps = resolved
            break

    if not steps:
        return None

    normalized_target = _normalize_step_ref(step_id)
    if normalized_target:
        for step in steps:
            if not isinstance(step, dict):
                continue
            refs = {
                str(step.get("id") or "").strip(),
                str(step.get("name") or "").strip(),
            }
            normalized_refs = {_normalize_step_ref(ref) for ref in refs if ref}
            if normalized_target in normalized_refs or step_id in refs:
                return _coerce_optional_bool(step.get("requires_worktree"))

    if step_num > 0 and step_num <= len(steps):
        selected_step = steps[step_num - 1]
        if isinstance(selected_step, dict):
            return _coerce_optional_bool(selected_step.get("requires_worktree"))

    return None


def _resolve_step_context_policy(
    *,
    workflow_path: str,
    tier_name: str,
    workflow_name: str,
    next_agent_type: str,
) -> dict:
    """Return the context-fetch policy defined on the next workflow step.

    Looks up the step whose ``agent_type`` matches *next_agent_type* in the
    workflow definition at *workflow_path* and returns the resolved policy
    fields as a dict.  Falls back to sensible defaults when the workflow file
    is absent, unparseable, or the step has no explicit policy.

    Returned keys (always present):
        ``context_policy``       – ``"minimal"``, ``"standard"``, or ``"deep"``
        ``require_api_discovery`` – ``bool``
        ``include_audit_history`` – ``bool``
        ``audit_limit``           – ``int``
    """
    _DEFAULTS = {
        "context_policy": "standard",
        "require_api_discovery": True,
        "include_audit_history": True,
        "audit_limit": 25,
    }

    path = str(workflow_path or "").strip()
    if not path or not os.path.isfile(path):
        return dict(_DEFAULTS)

    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except Exception as exc:
        logger.debug("Could not parse workflow definition (%s): %s", path, exc)
        return dict(_DEFAULTS)

    if not isinstance(data, dict):
        return dict(_DEFAULTS)
    data["__yaml_path"] = path

    try:
        from nexus.core.workflow_engine.workflow_definition_loader import resolve_workflow_steps_list
    except Exception:
        return dict(_DEFAULTS)

    workflow_type_candidates: list[str] = []
    for candidate in (
        tier_name,
        f"workflow:{tier_name}" if tier_name else "",
        workflow_name,
        f"workflow:{workflow_name}" if workflow_name else "",
        "",
    ):
        candidate_str = str(candidate or "").strip()
        if candidate_str in workflow_type_candidates:
            continue
        workflow_type_candidates.append(candidate_str)

    steps: list[dict] = []
    for workflow_type in workflow_type_candidates:
        try:
            resolved = resolve_workflow_steps_list(data, workflow_type)
        except Exception:
            continue
        if isinstance(resolved, list) and resolved:
            steps = resolved
            break

    if not steps:
        return dict(_DEFAULTS)

    target_agent = _normalize_step_ref(next_agent_type)
    matched_step: dict | None = None
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_agent = _normalize_step_ref(str(step.get("agent_type") or ""))
        if step_agent and step_agent == target_agent:
            matched_step = step
            break

    if matched_step is None:
        return dict(_DEFAULTS)

    raw_policy = str(matched_step.get("context_policy") or "").strip().lower()
    context_policy = raw_policy if raw_policy in {"minimal", "standard", "deep"} else "standard"

    # Derive sensible defaults from the policy level when explicit flags absent.
    policy_defaults = {
        "minimal": {"require_api_discovery": False, "include_audit_history": False, "audit_limit": 0},
        "standard": {"require_api_discovery": True, "include_audit_history": True, "audit_limit": 25},
        "deep": {"require_api_discovery": True, "include_audit_history": True, "audit_limit": 50},
    }[context_policy]

    require_api_discovery = matched_step.get("require_api_discovery")
    if not isinstance(require_api_discovery, bool):
        require_api_discovery = policy_defaults["require_api_discovery"]

    include_audit_history = matched_step.get("include_audit_history")
    if not isinstance(include_audit_history, bool):
        include_audit_history = policy_defaults["include_audit_history"]

    raw_limit = matched_step.get("audit_limit")
    if isinstance(raw_limit, int) and raw_limit > 0:
        audit_limit = raw_limit
    else:
        audit_limit = policy_defaults["audit_limit"]

    return {
        "context_policy": context_policy,
        "require_api_discovery": require_api_discovery,
        "include_audit_history": include_audit_history,
        "audit_limit": audit_limit,
    }


def _should_provision_worktree(
    *,
    issue_num: str,
    agent_type: str,
    step_requires_worktree: bool | None = None,
) -> bool:
    """Return whether the current agent launch should provision an isolated worktree."""
    if str(issue_num or "").strip() in {"", "unknown"}:
        return False
    if isinstance(step_requires_worktree, bool):
        return step_requires_worktree
    agent_norm = str(agent_type or "").strip().lower()
    # Triage routes work and often runs before impacted repository selection is known.
    if agent_norm == "triage":
        return False
    return True


def _slugify_branch_component(text: str, max_len: int = 48) -> str:
    value = str(text or "").strip().lower()
    value = re.sub(r"['`]", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    if not value:
        return ""
    return value[: max(8, int(max_len))].strip("-")


def _extract_issue_title_from_task_content(task_content: str) -> str:
    text = str(task_content or "")
    patterns = [
        r"(?im)^#\s*Issue\s*#\d+\s*:\s*(.+)$",
        r"(?im)^\*\*Title:\*\*\s*(.+)$",
        r"(?im)^Title:\s*(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return str(match.group(1) or "").strip()

    for raw_line in text.splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        candidate = re.sub(r"^#+\s*", "", line).strip()
        if candidate and not candidate.lower().startswith("issue:"):
            return candidate
    return ""


def _extract_issue_type_from_task_content(task_content: str) -> str:
    text = str(task_content or "")
    match = re.search(r"(?im)^\*\*Type:\*\*\s*([A-Za-z0-9_-]+)\s*$", text)
    if match:
        return str(match.group(1) or "").strip().lower()
    return ""


def _branch_prefix_for_issue(*, issue_type: str, tier_name: str) -> str:
    issue_type_norm = str(issue_type or "").strip().lower()
    tier_norm = str(tier_name or "").strip().lower()
    if tier_norm == "fast-track" or issue_type_norm in {"bug", "fix", "hotfix"}:
        return "fix"
    if issue_type_norm in {"doc", "docs", "documentation"}:
        return "docs"
    if issue_type_norm in {"chore", "support", "maintenance", "refactor"}:
        return "chore"
    return "feat"


def _derive_issue_branch_name(*, issue_number: str, task_content: str, tier_name: str) -> str:
    issue_num = str(issue_number or "").strip()
    issue_type = _extract_issue_type_from_task_content(task_content)
    prefix = _branch_prefix_for_issue(issue_type=issue_type, tier_name=tier_name)
    title = _extract_issue_title_from_task_content(task_content)
    slug = _slugify_branch_component(title, max_len=52)
    if not slug:
        return f"{prefix}/issue-{issue_num}"
    suffix = f"issue-{issue_num}"
    if slug.endswith(suffix):
        return f"{prefix}/{slug}"
    return f"{prefix}/{slug}-{suffix}"


def _build_agent_search_dirs(agents_dir: str) -> list:
    """Build the ordered list of directories to search for agent YAML files.

    Starts with the project-specific *agents_dir*, then appends the shared
    org-level agents directory configured via ``shared_agents_dir`` in config.
    """
    dirs = [agents_dir]
    shared = PROJECT_CONFIG.get("shared_agents_dir", "")
    if shared:
        shared_abs = os.path.join(BASE_DIR, shared) if not os.path.isabs(shared) else shared
        if shared_abs != agents_dir:
            dirs.append(shared_abs)
    return dirs


from nexus.core.execution import ExecutionEngine, find_agent_definition


def _resolve_skill_name(agent_type: str) -> str:
    """Normalize agent name for workspace skill directory."""
    return re.sub(r"[^a-z0-9]+", "_", agent_type.lower()).strip("_")


def _ensure_agent_definition(
    agents_dir: str, agent_type: str, workspace_dir: str | None = None
) -> bool:
    """Ensure an agent definition exists and sync workspace skills if needed."""
    search_dirs = _build_agent_search_dirs(agents_dir)
    yaml_path = find_agent_definition(agent_type, search_dirs)

    if not yaml_path:
        msg = f"Missing agent YAML for agent_type '{agent_type}' in {search_dirs}"
        logger.error(msg)
        emit_alert(msg, severity="error", source="agent_launcher")
        return False

    agent_md_path = os.path.splitext(yaml_path)[0] + ".agent.md"

    # Generate instructions if missing or outdated
    needs_sync = False
    if not os.path.exists(agent_md_path) or os.path.getmtime(agent_md_path) < os.path.getmtime(
        yaml_path
    ):
        try:
            from nexus.translators.to_copilot import translate_agent_to_copilot as translate_agent_to_md

            md_content = translate_agent_to_md(yaml_path)
            if md_content:
                with open(agent_md_path, "w", encoding="utf-8") as handle:
                    handle.write(md_content)
                logger.info(f"✅ Generated agent instructions: {agent_md_path}")
                needs_sync = True
            else:
                return False
        except Exception as e:
            logger.error(f"Translator error for {yaml_path}: {e}")
            return False
    else:
        needs_sync = True

    # Sync to workspace skill
    if needs_sync and workspace_dir:
        try:
            with open(agent_md_path, encoding="utf-8") as f:
                content = f.read()
            ExecutionEngine.sync_workspace_skill(workspace_dir, agent_type, content)
        except Exception as e:
            logger.warning(f"Failed to sync workspace skill for {agent_type}: {e}")

    return True


def get_sop_tier_from_issue(issue_number, project="nexus", repo_override: str | None = None):
    """Get workflow tier from issue labels.

    Delegates to nexus-arc's GitPlatform.get_workflow_type_from_issue().

    Args:
        issue_number: Git issue number
        project: Project name to determine repo

    Returns: tier_name (full/shortened/fast-track) or None
    """

    from nexus.core.orchestration.nexus_core_helpers import get_git_platform

    repo = str(repo_override or "")
    try:
        repo = repo or get_repo(project)
        token_override = _resolve_requester_token_for_issue(
            str(issue_number),
            repo,
            project,
        )
        issue = _run_coro_sync(
            lambda: get_git_platform(
                repo,
                project_name=project,
                token_override=token_override,
            ).get_issue(str(issue_number))
        )
        if not issue:
            return None
        labels = {str(label).lower() for label in (issue.labels or [])}
        if "workflow:fast-track" in labels:
            return "fast-track"
        if "workflow:shortened" in labels:
            return "shortened"
        if "workflow:full" in labels:
            return "full"
        return None
    except Exception as e:
        logger.error(f"Failed to get tier from issue #{issue_number} in {project} ({repo}): {e}")
        return None


def get_workflow_name(tier_name):
    """Returns the workflow slash-command name for the tier."""
    policy = _get_launch_policy_plugin()
    if policy and hasattr(policy, "get_workflow_name"):
        return policy.get_workflow_name(tier_name)
    if tier_name in {"fast-track", "shortened"}:
        return "bug_fix"
    return "new_feature"


def _resolve_issue_step_context(issue_url: str) -> tuple[str, int]:
    """Resolve active workflow step context for a Git issue URL."""
    issue_match = re.search(r"/issues/(\d+)(?:$|[/?#])", str(issue_url or ""))
    if not issue_match:
        return "", 0

    issue_number = str(issue_match.group(1))
    try:
        from nexus.core.orchestration.nexus_core_helpers import get_workflow_status

        status = _run_coro_sync(lambda: get_workflow_status(issue_number))
    except Exception:
        status = None

    if not isinstance(status, dict):
        return "", 0

    step_id = str(
        status.get("current_step_id")
        or status.get("current_step_name")
        or ""
    ).strip()
    step_num_raw = status.get("current_step_num", status.get("current_step"))
    try:
        step_num = int(step_num_raw)
    except (TypeError, ValueError):
        step_num = 0
    if step_num <= 0:
        step_num = 0
    return step_id, step_num


def invoke_ai_agent(
    agents_dir,
    workspace_dir,
    issue_url,
    tier_name,
    task_content,
    continuation=False,
    continuation_prompt=None,
    preferred_tool=None,
    exclude_tools=None,
    log_subdir=None,
    agent_type="triage",
    project_name=None,
    requester_nexus_id=None,
):
    """Invokes an AI agent on the agents directory to process the task.

    Uses orchestrator to determine best tool (Agent CLI) with fallback support.
    Runs asynchronously (Popen) since agent execution can take several minutes.

    Args:
        agents_dir: Path to agents directory
        workspace_dir: Path to workspace directory
        issue_url: Git issue URL
        tier_name: Workflow tier (full/shortened/fast-track)
        task_content: Task description
        continuation: If True, this is a continuation of previous work
        continuation_prompt: Custom prompt for continuation
        preferred_tool: If provided, prefer this tool (e.g. 'gemini', 'claude'); otherwise use best available (default: None)
        agent_type: Agent type to route to (triage, design, analysis, etc.)
        project_name: Project name for resolving workflow definition

    Returns:
        Tuple of (PID of launched process or None if failed, tool_used: str)
    """
    pid = None
    tool_name: str | None = None
    workflow_name = get_workflow_name(tier_name)
    workflow_path = _resolve_workflow_path(project_name)
    step_id, step_num = _resolve_issue_step_context(issue_url)
    step_requires_worktree = _resolve_step_requires_worktree(
        workflow_path=workflow_path,
        tier_name=tier_name,
        workflow_name=workflow_name,
        step_id=step_id,
        step_num=step_num,
    )
    if not step_id or step_num <= 0:
        logger.warning(
            "Active workflow step context unresolved for issue=%s agent=%s "
            "(step_id=%s step_num=%s); completion validation may fail.",
            issue_url,
            agent_type,
            step_id or "<none>",
            step_num,
        )
    policy = _get_launch_policy_plugin()
    if policy and hasattr(policy, "build_agent_prompt"):
        prompt = policy.build_agent_prompt(
            issue_url=issue_url,
            tier_name=tier_name,
            task_content=task_content,
            agent_type=agent_type,
            continuation=continuation,
            continuation_prompt=continuation_prompt,
            workflow_path=workflow_path,
            nexus_dir=get_nexus_dir_name(),
            project_name=project_name,
            repo_path=workspace_dir,
            step_id=step_id,
            step_num=step_num,
        )
    else:
        prompt = (
            f"You are a {agent_type} agent.\n\n"
            f"Issue: {issue_url}\n"
            f"Tier: {tier_name}\n"
            f"Workflow: /{workflow_name}\n\n"
            f"Task details:\n{task_content}"
        )

    mode = "continuation" if continuation else "initial"
    logger.info(f"🤖 Launching {agent_type} agent in {agents_dir} (mode: {mode})")
    logger.info(f"   Workspace: {workspace_dir}")
    logger.info(f"   Workflow: /{workflow_name} (tier: {tier_name})")

    if not _ensure_agent_definition(agents_dir, agent_type, workspace_dir):
        return None, None

    # Use orchestrator to launch agent
    orchestrator = get_orchestrator(ORCHESTRATOR_CONFIG)
    auth_is_enabled = bool(auth_enabled())
    provided_requester_nexus_id = str(requester_nexus_id or "").strip() or None
    bound_requester_nexus_id = _lookup_bound_issue_requester_nexus_id(str(issue_url or ""))
    if (
        auth_is_enabled
        and provided_requester_nexus_id
        and bound_requester_nexus_id
        and provided_requester_nexus_id != bound_requester_nexus_id
    ):
        logger.error(
            "Requester identity mismatch for issue launch (issue=%s provided=%s bound=%s). Refusing launch.",
            issue_url,
            provided_requester_nexus_id,
            bound_requester_nexus_id,
        )
        return None, None

    if auth_is_enabled and not bound_requester_nexus_id:
        logger.error(
            "Auth is enabled but issue requester binding is missing for launch (issue=%s provided=%s). Refusing launch.",
            issue_url,
            provided_requester_nexus_id or "<none>",
        )
        return None, None

    effective_requester_nexus_id = _resolve_issue_requester_nexus_id(issue_url=str(issue_url or ""))

    # Resolve project-specific API token
    from nexus.core.orchestration.nexus_core_helpers import _get_project_config

    project_cfg = _get_project_config().get(project_name, {}) if project_name else {}
    git_platform = project_cfg.get("git_platform", "github")
    default_token_var = "GITLAB_TOKEN" if git_platform == "gitlab" else "GITHUB_TOKEN"
    token_var = project_cfg.get("git_token_var_name", default_token_var)
    token = os.getenv(token_var) if not auth_is_enabled else None

    agent_env = None
    if auth_is_enabled:
        if effective_requester_nexus_id:
            if project_name:
                allowed_project, error_project = check_project_access(
                    str(effective_requester_nexus_id),
                    str(project_name),
                )
                if not allowed_project:
                    logger.error(
                        "Requester project access denied: requester=%s project=%s error=%s",
                        effective_requester_nexus_id,
                        project_name,
                        error_project,
                    )
                    return None, None
                try:
                    repo_name = get_repo(str(project_name))
                except Exception:
                    repo_name = ""
                if repo_name:
                    allowed_repo, error_repo = check_repo_access(
                        str(effective_requester_nexus_id),
                        repo_name,
                        str(project_name or ""),
                    )
                    if not allowed_repo:
                        logger.error(
                            "Requester repo access denied: requester=%s repo=%s error=%s",
                            effective_requester_nexus_id,
                            repo_name,
                            error_repo,
                        )
                        return None, None

            resolved_env, env_error = build_execution_env(str(effective_requester_nexus_id))
            if env_error:
                logger.error(
                    "Requester credential resolution failed for nexus_id=%s: %s",
                    effective_requester_nexus_id,
                    env_error,
                )
                return None, None
            # Propagate requester context into provider invokers for masked auth diagnostics.
            resolved_env["NEXUS_REQUESTER_ID"] = str(effective_requester_nexus_id)
            agent_env = resolved_env
        else:
            logger.error(
                "Auth is enabled but no requester identity is available for launch "
                "(project=%s issue=%s).",
                project_name,
                issue_url,
            )
            return None, None

    if agent_env is None and token and not auth_is_enabled:
        # Backward-compat fallback for non-auth runtime paths.
        agent_env = {"GITHUB_TOKEN": token, "GITLAB_TOKEN": token}

    try:
        from nexus.core.workspace import WorktreeProvisionError, WorkspaceManager

        # Extract issue number for tracking
        issue_match = re.search(r"/issues/(\d+)", issue_url or "")
        issue_num = issue_match.group(1) if issue_match else "unknown"
        issue_repo_slug = _extract_repo_from_issue_url(str(issue_url or ""))

        worktree_base_repo = _resolve_worktree_base_repo(workspace_dir, issue_url)
        isolated_workspace = worktree_base_repo

        should_provision_worktree = _should_provision_worktree(
            issue_num=issue_num,
            agent_type=agent_type,
            step_requires_worktree=step_requires_worktree,
        )

        if should_provision_worktree:
            # Extract branch name from issue body if available
            target_branch = None
            if issue_url:
                try:
                    preferred_repo = _extract_repo_from_issue_url(str(issue_url or "")) or None
                    body, _, _ = _load_issue_body_from_project_repo(
                        issue_num,
                        preferred_repo=preferred_repo,
                    )
                    if body:
                        branch_match = re.search(r"Target Branch:\s*`([^`]+)`", body)
                        if branch_match:
                            target_branch = branch_match.group(1)
                            logger.info(f"Using target branch from issue: {target_branch}")
                except Exception as e:
                    logger.warning(f"Could not extract target branch: {e}")

            branch_for_worktree = str(target_branch or "").strip() or _derive_issue_branch_name(
                issue_number=issue_num,
                task_content=task_content,
                tier_name=tier_name,
            )
            logger.info(
                "Using worktree branch for issue #%s: %s",
                issue_num,
                branch_for_worktree,
            )

            worktree_targets = _resolve_worktree_targets(
                project_name=project_name,
                issue_repo_slug=issue_repo_slug,
                fallback_repo_dir=worktree_base_repo,
            )
            if not worktree_targets:
                logger.warning(
                    "Skipping worktree provisioning for issue %s: no git repositories resolved "
                    "(base=%s)",
                    issue_num,
                    worktree_base_repo,
                )
            try:
                provisioned_paths: dict[str, str] = {}
                failures: dict[str, str] = {}
                for target_repo, target_repo_dir in worktree_targets.items():
                    start_ref = None
                    if project_name:
                        try:
                            base_branch = get_repo_branch(project_name, target_repo)
                        except Exception:
                            base_branch = "main"
                        normalized_base = str(base_branch or "").strip() or "main"
                        start_ref = f"origin/{normalized_base}"

                    try:
                        created_worktree = WorkspaceManager.provision_worktree(
                            target_repo_dir,
                            issue_num,
                            branch_name=branch_for_worktree,
                            start_ref=start_ref,
                        )
                        provisioned_paths[target_repo] = created_worktree
                        removed_helpers = WorkspaceManager.sanitize_worktree_helper_scripts(
                            created_worktree
                        )
                        if removed_helpers:
                            logger.warning(
                                "Removed deprecated helper scripts before agent launch for issue #%s",
                                issue_num,
                            )
                    except WorktreeProvisionError as target_exc:
                        failures[target_repo] = str(target_exc)
                        logger.warning(
                            "Worktree provisioning failed for issue #%s repo=%s: %s",
                            issue_num,
                            target_repo,
                            target_exc,
                        )

                issue_repo_key = str(issue_repo_slug or "").strip()
                if issue_repo_key and issue_repo_key in worktree_targets and issue_repo_key not in provisioned_paths:
                    raise WorktreeProvisionError(
                        f"{issue_repo_key}: {failures.get(issue_repo_key, 'provisioning failed')}"
                    )
                if not provisioned_paths and worktree_targets:
                    detail = "; ".join(f"{repo}: {reason}" for repo, reason in failures.items())
                    raise WorktreeProvisionError(detail or "no worktree could be provisioned")

                if provisioned_paths:
                    isolated_workspace = _select_primary_worktree(
                        issue_repo_slug=issue_repo_slug,
                        fallback_repo_dir=worktree_base_repo,
                        targets=worktree_targets,
                        provisioned=provisioned_paths,
                    )
                if failures and provisioned_paths:
                    logger.warning(
                        "Provisioned issue worktrees for issue #%s with partial failures: %s",
                        issue_num,
                        "; ".join(f"{repo}: {reason}" for repo, reason in failures.items()),
                    )
            except WorktreeProvisionError as exc:
                failure_reason = str(exc)
                logger.error(
                    "Worktree provisioning failed for issue #%s (project=%s): %s",
                    issue_num,
                    project_name or log_subdir or "nexus",
                    failure_reason,
                )
                paused = False
                try:
                    from nexus.core.orchestration.nexus_core_helpers import pause_workflow

                    paused = bool(
                        _run_coro_sync(
                            lambda: pause_workflow(
                                str(issue_num),
                                reason="Paused: isolated worktree provisioning failed",
                            )
                        )
                    )
                except Exception as pause_exc:
                    logger.warning(
                        "Could not pause workflow for issue #%s after worktree failure: %s",
                        issue_num,
                        pause_exc,
                    )

                if paused:
                    pause_note = " Workflow has been paused."
                else:
                    pause_note = " Workflow pause attempt failed; please run /pause manually."

                emit_alert(
                    (
                        f"❌ Could not create isolated worktree for issue #{issue_num}."
                        f"{pause_note}"
                    ),
                    severity="error",
                    source="agent_launcher",
                    issue_number=str(issue_num),
                    project_key=str(project_name or log_subdir or "nexus"),
                )
                try:
                    AuditStore.audit_log(
                        int(issue_num),
                        "WORKTREE_PROVISION_FAILED",
                        failure_reason,
                    )
                except Exception:
                    pass
                return None, None
        elif issue_num != "unknown":
            logger.info(
                "Skipping worktree provisioning for issue %s agent=%s (not required for this step)",
                issue_num,
                agent_type,
            )

        pid, tool_used = orchestrator.invoke_agent(
            agent_prompt=prompt,
            workspace_dir=isolated_workspace,
            agents_dir=agents_dir,
            base_dir=BASE_DIR,
            issue_url=issue_url,
            agent_name=agent_type,
            project_name=project_name,
            preferred_tool=preferred_tool,
            exclude_tools=exclude_tools,
            log_subdir=log_subdir,
            env=agent_env,
        )

        tool_name = str(getattr(tool_used, "value", tool_used))
        logger.info(f"🚀 Agent launched with {tool_name} (PID: {pid})")

        # Save to launched agents tracker and emit audit, but never fail launch
        # response if bookkeeping has an internal error.
        if issue_num != "unknown":
            try:
                dynamic_exclusions = []
                rate_limits = getattr(orchestrator, "_rate_limits", {})
                if isinstance(rate_limits, dict):
                    for tool, info in rate_limits.items():
                        until = float((info or {}).get("until", 0) or 0)
                        if until > time.time():
                            dynamic_exclusions.append(tool)

                launched_agents = HostStateManager.load_launched_agents(recent_only=False)
                previous_entry = launched_agents.get(str(issue_num), {})
                if not isinstance(previous_entry, dict):
                    previous_entry = {}

                entry = dict(previous_entry)
                entry.update(
                    {
                        "timestamp": time.time(),
                        "pid": pid,
                        "project": project_name,
                        "requester_nexus_id": effective_requester_nexus_id,
                        "tier": tier_name,
                        "mode": mode,
                        "tool": tool_name,
                        "agent_type": agent_type,
                        "exclude_tools": _merge_excluded_tools(
                            previous_entry.get("exclude_tools", []),
                            list(exclude_tools) if exclude_tools else [],
                            dynamic_exclusions,
                        ),
                    }
                )
                launched_agents[str(issue_num)] = entry
                HostStateManager.save_launched_agents(launched_agents)

                record_agent_launch(issue_num, agent_type=agent_type, pid=pid)

                AuditStore.audit_log(
                    int(issue_num),
                    "AGENT_LAUNCHED",
                    f"Launched {tool_name} agent in {os.path.basename(agents_dir)} "
                    f"(workflow: {workflow_name}/{tier_name}, mode: {mode}, PID: {pid})",
                    user_id=effective_requester_nexus_id,
                )

                _attach_post_launch_watchdog(
                    tool_name=str(tool_name),
                    issue_num=str(issue_num),
                    pid=int(pid),
                    agent_type=str(agent_type),
                    prompt=str(prompt),
                    workspace_dir=str(isolated_workspace),
                    agents_dir=str(agents_dir),
                    base_dir=str(BASE_DIR),
                    log_subdir=str(log_subdir or project_name or "nexus"),
                    agent_env=agent_env,
                    orchestrator=orchestrator,
                    exclude_tools=list(exclude_tools) if exclude_tools else [],
                    tier_name=str(tier_name),
                    mode=str(mode),
                    workflow_name=str(workflow_name),
                    issue_url=str(issue_url or ""),
                )
            except Exception as bookkeeping_exc:
                logger.warning(
                    "Agent launch bookkeeping failed for issue #%s after successful launch: %s",
                    issue_num,
                    bookkeeping_exc,
                )

        return pid, tool_name

    except ToolUnavailableError as e:
        logger.error(f"❌ All AI tools unavailable: {e}")

        issue_match = re.search(r"/issues/(\d+)", issue_url or "")
        issue_num = issue_match.group(1) if issue_match else "unknown"
        message = str(e).lower()
        rate_limits = getattr(orchestrator, "_rate_limits", {})
        if isinstance(rate_limits, dict):
            for tool, info in rate_limits.items():
                until = float((info or {}).get("until", 0) or 0)
                if until > time.time():
                    _persist_issue_excluded_tools(issue_num, [tool])
                    logger.info(
                        "Persisted issue-level exclusion for %s on issue #%s due to rate-limit failure",
                        tool,
                        issue_num,
                    )
        if issue_num != "unknown":
            AuditStore.audit_log(
                int(issue_num), "AGENT_LAUNCH_FAILED", f"All tools unavailable: {str(e)}"
            )

        return None, None
    except Exception as e:
        logger.error(f"❌ Failed to launch agent: {e}")

        if pid and tool_name:
            logger.warning(
                "Returning successful launch despite post-launch exception (PID: %s, tool: %s)",
                pid,
                tool_name,
            )
            return pid, tool_name

        issue_match = re.search(r"/issues/(\d+)", issue_url or "")
        issue_num = issue_match.group(1) if issue_match else "unknown"
        if issue_num != "unknown":
            AuditStore.audit_log(int(issue_num), "AGENT_LAUNCH_FAILED", f"Exception: {str(e)}")

        return None, None


def launch_next_agent(
    issue_number,
    next_agent,
    trigger_source="unknown",
    exclude_tools=None,
    repo_override: str | None = None,
    requester_nexus_id: str | None = None,
):
    """
    Launch the next agent in the workflow chain.

    This is the main entry point used by both inbox_processor and webhook_server.

    Args:
        issue_number: Git issue number (string or int)
        next_agent: Name of the agent to launch (e.g., "Atlas", "Architect")
        trigger_source: Where the trigger came from ("git_webhook", "log_file", "git_comment")
        exclude_tools: List of tool names to exclude from this launch attempt.
        repo_override: Preferred repository full_name for resolving issue metadata.
        requester_nexus_id: Optional requester identity to enforce issue-owner matching.

    Returns:
        ``(pid, tool_name)`` on success.
        ``(None, "duplicate-suppressed")`` when a duplicate launch is intentionally skipped.
        ``(None, None)`` on failure.
    """
    issue_number = str(issue_number)
    logger.info(
        f"🔗 Launching next agent @{next_agent} for issue #{issue_number} (trigger: {trigger_source})"
    )

    # Check for duplicate launches
    if is_recent_launch(issue_number, next_agent):
        logger.info(f"⏭️ Skipping duplicate launch for issue #{issue_number} agent @{next_agent}")
        return None, "duplicate-suppressed"

    # Get issue details from the repo matching this issue's task-file project
    try:
        body, resolved_repo, resolved_task_file = _load_issue_body_from_project_repo(
            issue_number,
            preferred_repo=repo_override,
        )
        if not body:
            logger.error(
                f"Failed to resolve issue #{issue_number} from configured project repositories"
            )
            return None, None
    except Exception as e:
        logger.error(f"Failed to resolve/process issue #{issue_number} body: {e}")
        return None, None

    # Find task-file metadata (optional in postgres mode)
    task_file_match = re.search(r"\*\*Task File:\*\*\s*`([^`]+)`", body)
    task_file = task_file_match.group(1) if task_file_match else (resolved_task_file or "")
    if not task_file and not is_postgres_backend(NEXUS_STORAGE_BACKEND):
        logger.warning(f"No task file in issue #{issue_number}")
        return None, None

    if task_file and resolved_task_file and resolved_task_file != task_file:
        logger.warning(
            f"Issue #{issue_number} task file mismatch between resolution and body parse: "
            f"{resolved_task_file} vs {task_file}"
        )
    normalized_task_file = task_file.replace("\\", "/")
    is_shared_active_task_file = "/active/" in normalized_task_file
    task_file_exists = False if is_postgres_backend(NEXUS_STORAGE_BACKEND) else os.path.exists(task_file)
    if not is_postgres_backend(NEXUS_STORAGE_BACKEND) and not task_file_exists and not is_shared_active_task_file:
        logger.warning(f"Task file not found: {task_file}")
        return None, None

    # Get project config
    project_root = ""
    config = {}
    if is_postgres_backend(NEXUS_STORAGE_BACKEND):
        project_root = _resolve_project_from_repo(resolved_repo or "")
        config = PROJECT_CONFIG.get(project_root, {}) if project_root else {}
    else:
        for key, cfg in PROJECT_CONFIG.items():
            if not isinstance(cfg, dict):
                continue
            workspace = cfg.get("workspace")
            if workspace and task_file:
                workspace_abs = os.path.join(BASE_DIR, str(workspace))
                if task_file.startswith(workspace_abs):
                    project_root = key
                    config = cfg
                    break

    if not project_root or not config.get("agents_dir"):
        logger.warning(
            f"No project config for issue #{issue_number} (repo={resolved_repo or 'unknown'}, task_file={task_file or 'n/a'})"
        )
        return None, None

    expected_repos = _project_repos(project_root, config, get_repos)
    if resolved_repo and expected_repos and resolved_repo not in expected_repos:
        logger.error(
            f"Project boundary violation for issue #{issue_number}: "
            f"resolved repo {resolved_repo}, project repos {expected_repos}"
        )
        return None, None

    # Read task content
    # Use issue body as authoritative task snapshot when Task File points to the shared
    # active path, which can be overwritten by later issues and cause cross-issue bleed.
    task_content = body
    if is_postgres_backend(NEXUS_STORAGE_BACKEND):
        logger.info(
            "Postgres mode: using issue body task snapshot for issue #%s (task-file metadata: %s)",
            issue_number,
            (task_file or "missing"),
        )
    elif is_shared_active_task_file:
        logger.info(
            "Using issue body task snapshot for issue #%s (shared active task file: %s)",
            issue_number,
            task_file,
        )
    elif task_file_exists:
        try:
            with open(task_file) as f:
                task_content = f.read()
        except Exception as e:
            logger.warning(
                "Failed to read task file %s for issue #%s, falling back to issue body: %s",
                task_file,
                issue_number,
                e,
            )

    # Get workflow tier: launched_agents tracker → issue labels → halt if unknown
    from nexus.core.state_manager import HostStateManager

    repo = resolved_repo or get_repo(project_root)
    tracker_tier = HostStateManager.get_last_tier_for_issue(issue_number)
    label_tier = get_sop_tier_from_issue(issue_number, project_root, repo_override=repo)
    tier_name = label_tier or tracker_tier
    if not tier_name:
        logger.error(
            f"Cannot determine workflow tier for issue #{issue_number}: "
            "no tracker entry and no workflow: label."
        )
        return None, None

    # Merge caller-provided exclude_tools with any persisted ones from previous runs.
    launched_agents = HostStateManager.load_launched_agents(recent_only=False)
    persisted = launched_agents.get(str(issue_number), {}).get("exclude_tools", [])
    merged_exclusions = _merge_excluded_tools(persisted, exclude_tools or [])
    if merged_exclusions:
        exclude_tools = merged_exclusions
        logger.info(
            f"Using merged exclude_tools for issue #{issue_number}: {exclude_tools}"
        )

    issue_url = build_issue_url(repo, str(issue_number), PROJECT_CONFIG.get(project_root or "nexus"))
    agents_abs = os.path.join(BASE_DIR, config["agents_dir"])
    workspace_abs = os.path.join(BASE_DIR, config["workspace"])

    # Resolve per-step context policy from the workflow YAML (or use env/defaults as fallback).
    workflow_path = _resolve_workflow_path(project_root)
    workflow_name = get_workflow_name(tier_name)
    context_policy_cfg = _resolve_step_context_policy(
        workflow_path=workflow_path,
        tier_name=tier_name,
        workflow_name=workflow_name,
        next_agent_type=next_agent,
    )
    context_policy = context_policy_cfg["context_policy"]
    require_api_discovery = context_policy_cfg["require_api_discovery"]
    include_audit_history = context_policy_cfg["include_audit_history"]
    audit_limit = context_policy_cfg["audit_limit"]
    logger.debug(
        "Context policy for @%s issue #%s: policy=%s discovery=%s audit=%s limit=%d",
        next_agent, issue_number, context_policy, require_api_discovery, include_audit_history, audit_limit,
    )

    # Build discovery guard sentence (injected only when required by policy).
    discovery_guard = (
        "Before starting work, call GET /api/v1 to discover supported routes, then "
        "GET /api/v1/workflow-context/<issue_number> for canonical context. "
        "Do not invent or probe undocumented endpoints. "
        if require_api_discovery
        else ""
    )

    # Build audit-history instruction (injected only when policy requests it).
    if include_audit_history and audit_limit > 0:
        audit_instruction = (
            f"The context endpoint returns audit history (up to {audit_limit} events). "
            "Review it to understand prior decisions before acting. "
        )
    else:
        audit_instruction = ""

    if context_policy == "minimal":
        continuation_prompt = (
            f"You are {next_agent}. Previous step complete. "
            "Retrieve current workflow state from GET /api/v1/workflow-context/<issue_number>, "
            "perform your task, and post a concise status update ending with: 'Ready for `@NextAgent`'"
        )
    elif context_policy == "deep":
        continuation_prompt = (
            f"You are a {next_agent} agent. The previous workflow step is complete.\n\n"
            f"Your task: Begin your step in the workflow.\n"
            f"{discovery_guard}"
            "Read canonical workflow context via GET /api/v1/workflow-context/<issue_number> "
            "(do not guess endpoints).\n"
            f"{audit_instruction}"
            "Use Git comments only as supplemental human context.\n"
            "Then perform your assigned work and post a status update.\n"
            "End with a completion marker like: 'Ready for `@NextAgent`'"
        )
    else:  # standard (default)
        continuation_prompt = (
            f"You are {next_agent}. Previous step complete. "
            f"{discovery_guard}"
            "Read /api/v1/workflow-context/<issue_number> for canonical context "
            "(do not guess endpoints), "
            f"{audit_instruction}"
            "use issue comments as supplemental context, "
            "perform your task, and post a concise status update ending with: 'Ready for `@NextAgent`'"
        )

    # Launch
    pid, tool_used = invoke_ai_agent(
        agents_dir=agents_abs,
        workspace_dir=workspace_abs,
        issue_url=issue_url,
        tier_name=tier_name,
        task_content=task_content,
        continuation=True,
        continuation_prompt=continuation_prompt,
        exclude_tools=exclude_tools,
        log_subdir=project_root,
        agent_type=next_agent,
        project_name=project_root,
        requester_nexus_id=requester_nexus_id,
    )

    if pid:
        logger.info(
            f"✅ Successfully launched @{next_agent} for issue #{issue_number} "
            f"(PID: {pid}, tool: {tool_used})"
        )
        completed_agent = _completed_agent_from_trigger(trigger_source)
        if completed_agent:
            try:
                notify_agent_completed(
                    issue_number=str(issue_number),
                    completed_agent=completed_agent,
                    next_agent=next_agent,
                    project=project_root,
                )
            except Exception as e:
                logger.warning(f"Failed to send notification: {e}")
        return pid, tool_used
    else:
        logger.error(f"❌ Failed to launch @{next_agent} for issue #{issue_number}")
        return None, None
