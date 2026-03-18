"""Workflow control service helpers used by Telegram command handlers."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from nexus.adapters.git.utils import build_issue_url, resolve_repo
from nexus.core.completion import budget_completion_payload
from nexus.core.config import NEXUS_STORAGE_BACKEND
from nexus.core.integrations.workflow_state_factory import get_storage_backend
from nexus.core.prompt_budget import apply_prompt_budget
from nexus.core.runtime_mode import is_postgres_backend

logger = logging.getLogger(__name__)

_PROMPT_MAX_CHARS = int(os.getenv("AI_PROMPT_MAX_CHARS", "16000"))
_CONTEXT_SUMMARY_MAX_CHARS = int(os.getenv("AI_CONTEXT_SUMMARY_MAX_CHARS", "1200"))
_TERMINAL_AGENT_REFERENCES = frozenset(
    {
        "none",
        "n/a",
        "null",
        "done",
        "end",
        "finish",
        "complete",
        "",
    }
)


def _run_coro_sync(coro_factory):
    try:
        asyncio.get_running_loop()
        in_running_loop = True
    except RuntimeError:
        in_running_loop = False

    if not in_running_loop:
        try:
            return asyncio.run(coro_factory())
        except Exception:
            return None

    holder: dict[str, Any] = {"value": None, "error": None}

    def _runner() -> None:
        try:
            holder["value"] = asyncio.run(coro_factory())
        except Exception as exc:
            holder["error"] = exc

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=10)
    if t.is_alive() or holder.get("error") is not None:
        return None
    return holder.get("value")


def _read_latest_completion_from_storage(issue_num: str) -> dict[str, Any] | None:
    async def _load():
        backend = get_storage_backend()
        items = await backend.list_completions(str(issue_num))
        return items[0] if items else None

    payload = _run_coro_sync(_load)
    if not isinstance(payload, dict):
        return None
    normalized = budget_completion_payload(payload)

    return {
        "agent_type": str(normalized.get("agent_type") or normalized.get("_agent_type") or "")
        .strip()
        .lower(),
        "next_agent": str(normalized.get("next_agent") or "").strip().lower(),
        "status": str(normalized.get("status") or "").strip().lower(),
        "is_workflow_done": bool(normalized.get("is_workflow_done", False)),
        "summary": normalized,
    }


def prepare_continue_context(
    *,
    issue_num: str,
    project_key: str,
    rest_tokens: list[str],
    base_dir: str,
    project_config: dict[str, dict[str, Any]],
    default_repo: str,
    find_task_file_by_issue: Callable[[str], str | None],
    get_issue_details: Callable[[str, str | None], dict[str, Any] | None],
    resolve_project_config_from_task: Callable[[str], tuple[str | None, dict[str, Any] | None]],
    get_runtime_ops_plugin: Callable[..., Any],
    scan_for_completions: Callable[[str], list[Any]],
    normalize_agent_reference: Callable[[str | None], str | None],
    get_expected_running_agent_from_workflow: Callable[[str], str | None],
    get_sop_tier_from_issue: Callable[[str, str | None], str | None],
    get_sop_tier: Callable[[str], tuple[str, Any, Any]],
    requester_nexus_id: str | None = None,
) -> dict[str, Any]:
    """Build context for /continue and return either a terminal state or launch payload."""

    def _normalize_routable_agent(value: Any) -> str | None:
        normalized = normalize_agent_reference(value)
        if not normalized:
            return None
        normalized_text = str(normalized).strip()
        if normalized_text.lower() in _TERMINAL_AGENT_REFERENCES:
            return None
        return normalized_text

    def _extract_repo_from_text(text: str) -> str | None:
        if not text:
            return None
        # Generic Git platform issue URL pattern
        git_match = re.search(r"https?://[^/]+/([^/]+/[^/]+)/(?:issues|merge_requests|pulls|pull)/\d+", text)
        if git_match:
            return git_match.group(1)
        gl_match = re.search(r"https?://[^/]+/([^\s]+)/-/issues/\d+", text)
        if gl_match:
            return gl_match.group(1)
        return None

    def _repo_candidates(preferred_config: dict[str, Any] | None = None) -> list[str]:
        candidates: list[str] = []

        def _add_repo(value: str | None) -> None:
            repo_value = str(value or "").strip()
            if repo_value and repo_value not in candidates:
                candidates.append(repo_value)

        cfgs: list[dict[str, Any]] = []
        if isinstance(preferred_config, dict):
            cfgs.append(preferred_config)

        project_cfg = project_config.get(project_key)
        if isinstance(project_cfg, dict) and project_cfg not in cfgs:
            cfgs.append(project_cfg)

        for cfg in cfgs:
            _add_repo(resolve_repo(cfg, default_repo))
            repo_list = cfg.get("git_repos")
            if isinstance(repo_list, list):
                for repo_name in repo_list:
                    _add_repo(str(repo_name or ""))

        # Only fall back to global default when no project-specific repos were discovered.
        if not candidates:
            _add_repo(default_repo)
        return candidates

    def _load_issue(
        preferred_config: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        for candidate_repo in _repo_candidates(preferred_config):
            try:
                issue_details = get_issue_details(
                    issue_num,
                    candidate_repo,
                    requester_nexus_id=requester_nexus_id,
                )
            except TypeError:
                issue_details = get_issue_details(issue_num, candidate_repo)
            if issue_details:
                return issue_details, candidate_repo
        return None, None

    def _looks_like_agent_ref(token: str) -> bool:
        value = str(token or "").strip()
        if not value:
            return False
        return bool(re.fullmatch(r"@?[A-Za-z0-9][A-Za-z0-9_-]*", value))

    forced_agent = None
    filtered_rest: list[str] = []
    for token in rest_tokens or []:
        if token.lower().startswith("from:"):
            forced_agent = token[5:].strip()
        else:
            filtered_rest.append(token)

    if not forced_agent and filtered_rest and _looks_like_agent_ref(filtered_rest[0]):
        forced_agent = filtered_rest[0]
        filtered_rest = filtered_rest[1:]

    continuation_prompt = (
        " ".join(filtered_rest) if filtered_rest else "Please continue with the next step."
    )
    continuation_budget = apply_prompt_budget(
        continuation_prompt,
        max_chars=min(_PROMPT_MAX_CHARS, 3000),
        summary_max_chars=min(_CONTEXT_SUMMARY_MAX_CHARS, 800),
    )
    continuation_prompt = str(continuation_budget["text"])

    runtime_ops = get_runtime_ops_plugin(cache_key="runtime-ops:telegram")
    pid = runtime_ops.find_agent_pid_for_issue(issue_num) if runtime_ops else None
    if pid:
        return {
            "status": "already_running",
            "message": (
                f"⚠️ Agent is already running for issue #{issue_num} (PID: {pid}).\n\n"
                f"Use /kill {issue_num} first if you want to restart it."
            ),
        }

    task_file = (
        None if is_postgres_backend(NEXUS_STORAGE_BACKEND) else find_task_file_by_issue(issue_num)
    )
    details = None
    repo = None

    if not task_file:
        details, repo = _load_issue()
        if not details:
            checked = ", ".join(_repo_candidates())
            return {
                "status": "error",
                "message": (f"❌ Could not load issue #{issue_num}.\n" f"Checked repos: {checked}"),
            }
        body = details.get("body", "")
        if not is_postgres_backend(NEXUS_STORAGE_BACKEND):
            match = re.search(r"Task File:\s*`([^`]+)`", body)
            task_file = match.group(1) if match else None

    project_name = None
    config: dict[str, Any] | None = None
    content = ""
    task_type = "feature"
    local_issue_fallback = False

    if (not is_postgres_backend(NEXUS_STORAGE_BACKEND)) and task_file and os.path.exists(task_file):
        project_name, config = resolve_project_config_from_task(task_file)
        if not config or not config.get("agents_dir"):
            fallback_config = project_config.get(project_key)
            if isinstance(fallback_config, dict) and fallback_config.get("agents_dir"):
                config = fallback_config
                project_name = project_key

        if not config or not config.get("agents_dir"):
            name = project_name or "unknown"
            return {"status": "error", "message": f"❌ No agents config for project '{name}'."}

        with open(task_file, encoding="utf-8") as handle:
            content = handle.read()

        type_match = re.search(r"\*\*Type:\*\*\s*(.+)", content)
        task_type = type_match.group(1).strip().lower() if type_match else "feature"
    else:
        fallback_config = project_config.get(project_key)
        if not isinstance(fallback_config, dict):
            return {
                "status": "error",
                "message": f"❌ Task file not found for issue #{issue_num} and no project config fallback.",
            }
        if not fallback_config.get("agents_dir") or not fallback_config.get("workspace"):
            return {
                "status": "error",
                "message": f"❌ Project config for '{project_key}' is missing agents_dir/workspace.",
            }

        config = fallback_config
        project_name = project_key

        if not details:
            details, repo = _load_issue(config)
        if not details:
            checked = ", ".join(_repo_candidates(config))
            return {
                "status": "error",
                "message": (f"❌ Could not load issue #{issue_num}.\n" f"Checked repos: {checked}"),
            }

        title = str(details.get("title") or "").strip()
        body = str(details.get("body") or "").strip()
        if title and body:
            content = f"# {title}\n\n{body}"
        elif title:
            content = f"# {title}"
        elif body:
            content = body
        else:
            content = f"Issue #{issue_num}"

        labels = details.get("labels")
        if isinstance(labels, list):
            for label in labels:
                if isinstance(label, dict):
                    name = str(label.get("name") or "").strip().lower()
                else:
                    name = str(label or "").strip().lower()
                if name.startswith("type:"):
                    candidate = name.split(":", 1)[1].strip()
                    if candidate:
                        task_type = candidate
                        break

    repo = repo or resolve_repo(config, default_repo)
    if not details:
        details, repo = _load_issue(config)
        if not details:
            repo_from_content = _extract_repo_from_text(content)
            if repo_from_content:
                repo = repo_from_content
                details = {"state": "open", "labels": []}
                local_issue_fallback = True
                logger.warning(
                    "Continue issue #%s: remote issue lookup failed; using local task context with repo=%s",
                    issue_num,
                    repo,
                )
            else:
                checked = ", ".join(_repo_candidates(config))
                return {
                    "status": "error",
                    "message": (
                        f"❌ Could not load issue #{issue_num}.\n" f"Checked repos: {checked}"
                    ),
                }

    if details.get("state", "").lower() == "closed":
        return {"status": "error", "message": f"⚠️ Issue #{issue_num} is closed."}

    agent_type = None
    agent_from_completion = False
    resumed_from = None
    workflow_already_done = False
    sync_workflow_to_agent = False

    if not is_postgres_backend(NEXUS_STORAGE_BACKEND):
        try:
            completions = scan_for_completions(base_dir)
            issue_completions = [c for c in completions if c.issue_number == str(issue_num)]
            if issue_completions:
                latest = max(issue_completions, key=lambda c: os.path.getmtime(c.file_path))
                if getattr(latest.summary, "is_workflow_done", False):
                    workflow_already_done = True
                    resumed_from = latest.summary.agent_type
                else:
                    raw_next = latest.summary.next_agent
                    normalized = _normalize_routable_agent(raw_next)
                    if normalized:
                        agent_type = normalized
                        agent_from_completion = True
                        resumed_from = latest.summary.agent_type
                        logger.info(
                            "Continue issue #%s: last step was %s, resuming with next_agent=%s",
                            issue_num,
                            resumed_from,
                            agent_type,
                        )
                    elif str(raw_next or "").strip():
                        logger.warning(
                            "Continue issue #%s: ignoring invalid recovered next_agent from completion: %r",
                            issue_num,
                            raw_next,
                        )
        except Exception as exc:
            logger.warning("Could not scan completions for issue #%s: %s", issue_num, exc)
    else:
        latest_completion = _read_latest_completion_from_storage(str(issue_num))
        if latest_completion:
            status = str(latest_completion.get("status") or "").strip().lower()
            normalized = _normalize_routable_agent(latest_completion.get("next_agent"))
            has_next_agent = bool(normalized)
            terminal_statuses = {"done", "workflow_done", "workflow_complete", "closed"}

            if latest_completion.get("is_workflow_done") or (
                status in terminal_statuses and not has_next_agent
            ):
                workflow_already_done = True
                resumed_from = latest_completion.get("agent_type") or resumed_from
            elif has_next_agent:
                agent_type = normalized
                agent_from_completion = True
                resumed_from = latest_completion.get("agent_type") or resumed_from
            elif str(latest_completion.get("next_agent") or "").strip():
                logger.warning(
                    "Continue issue #%s: ignoring invalid recovered next_agent from storage: %r",
                    issue_num,
                    latest_completion.get("next_agent"),
                )

    if forced_agent:
        agent_type = normalize_agent_reference(forced_agent) or forced_agent
        agent_from_completion = False
        workflow_already_done = False
        logger.info("Continue issue #%s: overriding agent to %s (from: arg)", issue_num, agent_type)

    if workflow_already_done and not forced_agent:
        if details.get("state", "").lower() == "open":
            return {
                "status": "workflow_done_open",
                "repo": repo,
                "resumed_from": resumed_from,
                "project_name": project_name or project_key,
            }
        return {
            "status": "workflow_done_closed",
            "message": (
                f"✅ Workflow for issue #{issue_num} is already complete and closed.\n"
                f"Last agent: `{resumed_from}`\n\n"
                f"Use /continue {project_key} {issue_num} from:<agent> to re-run a specific step."
            ),
        }

    if not agent_type:
        agent_type_match = re.search(r"\*\*Agent Type:\*\*\s*(.+)", content)
        raw_agent_type = agent_type_match.group(1).strip() if agent_type_match else "triage"
        agent_type = _normalize_routable_agent(raw_agent_type) or "triage"
        if raw_agent_type != agent_type:
            logger.warning(
                "Continue issue #%s: ignoring invalid fallback agent reference from issue/task content: %r",
                issue_num,
                raw_agent_type,
            )
        logger.info(
            "Continue issue #%s: no prior completion found, starting with %s",
            issue_num,
            agent_type,
        )

    expected_running_agent = get_expected_running_agent_from_workflow(str(issue_num))
    normalized_expected = (
        normalize_agent_reference(expected_running_agent) if expected_running_agent else None
    )
    normalized_requested = normalize_agent_reference(agent_type) if agent_type else None
    if (
        not forced_agent
        and normalized_expected
        and normalized_requested
        and normalized_expected != normalized_requested
    ):
        normalized_resumed_from = normalize_agent_reference(resumed_from) if resumed_from else None
        if (
            agent_from_completion
            and normalized_resumed_from
            and normalized_expected == normalized_resumed_from
        ):
            logger.info(
                "Continue issue #%s: workflow pointer still at completed step '%s'; "
                "proceeding with recovered next_agent '%s'",
                issue_num,
                expected_running_agent,
                agent_type,
            )
            sync_workflow_to_agent = True
        else:
            logger.warning(
                "Continue issue #%s: requested agent '%s' does not match workflow RUNNING step '%s'; "
                "auto-aligning to workflow state",
                issue_num,
                agent_type,
                expected_running_agent,
            )
            agent_type = normalized_expected
            normalized_requested = normalized_expected

    normalized_final = _normalize_routable_agent(agent_type) if agent_type else None
    if not agent_type and normalized_expected:
        agent_type = normalized_expected
    elif not normalized_final and normalized_expected:
        logger.warning(
            "Continue issue #%s: discarding unresolved agent reference %r; using workflow RUNNING step %s",
            issue_num,
            agent_type,
            normalized_expected,
        )
        agent_type = normalized_expected
    elif normalized_final:
        agent_type = normalized_final
    else:
        logger.warning(
            "Continue issue #%s: discarding unresolved agent reference %r; falling back to triage",
            issue_num,
            agent_type,
        )
        agent_type = "triage"

    label_tier = None
    if not local_issue_fallback:
        label_tier = get_sop_tier_from_issue(issue_num, project_name or project_key)
    if label_tier:
        tier_name = label_tier
    else:
        tier_name, _, _ = get_sop_tier(task_type)

    issue_url = build_issue_url(repo, issue_num, config)
    log_subdir = project_name or project_key
    raw_content = content
    content_budget = apply_prompt_budget(
        content,
        max_chars=_PROMPT_MAX_CHARS,
        summary_max_chars=_CONTEXT_SUMMARY_MAX_CHARS,
    )
    content = str(content_budget["text"])
    if content_budget["summarized"] or content_budget["truncated"]:
        logger.info(
            "Continue context budget applied: issue=%s original=%s final=%s summarized=%s truncated=%s",
            issue_num,
            content_budget["original_chars"],
            content_budget["final_chars"],
            content_budget["summarized"],
            content_budget["truncated"],
        )

    return {
        "status": "ready",
        "issue_num": issue_num,
        "repo": repo,
        "agent_type": agent_type,
        "forced_agent_override": bool(forced_agent),
        "sync_workflow_to_agent": sync_workflow_to_agent,
        "resumed_from": resumed_from,
        "continuation_prompt": continuation_prompt,
        "agents_abs": os.path.join(base_dir, config["agents_dir"]),
        "workspace_abs": os.path.join(base_dir, config["workspace"]),
        "issue_url": issue_url,
        "tier_name": tier_name,
        "content": content,
        "raw_content": raw_content,
        "log_subdir": log_subdir,
    }


def kill_issue_agent(
    *, issue_num: str, get_runtime_ops_plugin: Callable[..., Any]
) -> dict[str, Any]:
    """Kill a running issue agent and report outcome."""
    runtime_ops = get_runtime_ops_plugin(cache_key="runtime-ops:telegram")
    pid = runtime_ops.find_agent_pid_for_issue(issue_num) if runtime_ops else None

    if not pid:
        return {
            "status": "not_running",
            "message": f"⚠️ No running agent found for issue #{issue_num}.",
        }

    if not runtime_ops or not runtime_ops.kill_process(pid, force=False):
        return {"status": "error", "message": f"Failed to stop process {pid}", "pid": pid}

    time.sleep(1)
    new_pid = runtime_ops.find_agent_pid_for_issue(issue_num) if runtime_ops else None
    if new_pid:
        if not runtime_ops or not runtime_ops.kill_process(pid, force=True):
            return {"status": "error", "message": f"Failed to force kill process {pid}", "pid": pid}
        return {"status": "killed", "pid": pid}

    return {"status": "stopped", "pid": pid}
