"""NexusAgentRuntime — nexus-specific implementation of AgentRuntime.

Bridges ProcessOrchestrator (nexus-arc) with the concrete nexus host:
  - AI Agent CLI invocations (via agent_launcher.invoke_ai_agent)
  - HostStateManager-backed process tracking
  - AgentMonitor stuck/dead detection hooks
  - Telegram alerting
  - Workflow finalisation (close issue + PR)
"""

import asyncio
import glob
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from nexus.adapters.git.utils import build_issue_url
from nexus.core.completion import normalize_completion_comment_markdown
from nexus.core.process_orchestrator import AgentRuntime

logger = logging.getLogger(__name__)

RETRY_FUSE_MAX_ATTEMPTS = 3
RETRY_FUSE_WINDOW_SECONDS = 600
RETRY_FUSE_HARD_TRIP_COUNT = 2
RETRY_FUSE_HARD_WINDOW_SECONDS = 3600
DEFAULT_RECENT_AGENT_COMMENT_WINDOW_SECONDS = 900
_STEP_COMPLETE_HEADER_RE = re.compile(
    r"^\s*##\s+.+?\bcomplet(?:e|ed)\b\s*[-–—:]\s*`?@?([a-zA-Z0-9_-]+)`?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_STEP_ID_COMMENT_RE = re.compile(r"^\s*\*\*Step ID:\*\*\s*`?([a-zA-Z0-9_-]+)`?\s*$", re.MULTILINE)
_STEP_NUM_COMMENT_RE = re.compile(
    r"^\s*\*\*Step (?:Num|Number):\*\*\s*([0-9]+)\s*$",
    re.MULTILINE,
)
_ISSUE_OPEN_ERROR_LOG_COOLDOWN_SECONDS = 300
_last_issue_open_error_log_at: dict[tuple[str, str], float] = {}


def _runtime_token_override() -> str | None:
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN", "GLAB_TOKEN"):
        token = str(os.getenv(key, "")).strip()
        if token:
            return token
    return None


def _resolve_project_name_for_repo(repo: str) -> str | None:
    try:
        from nexus.core.config import PROJECT_CONFIG, get_default_project, get_repo, get_repos
        from nexus.core.project.repo_utils import resolve_project_name_for_repo as _resolve_shared
    except Exception:
        return None

    return _resolve_shared(
        repo,
        project_config=PROJECT_CONFIG,
        get_default_project=get_default_project,
        get_repo=get_repo,
        get_project_repos=get_repos,
    )


def _resolve_requester_token_override(
    issue_number: str,
    repo: str,
    project_name: str | None = None,
) -> str | None:
    env_token = _runtime_token_override()
    try:
        from nexus.core.auth.access_domain import auth_enabled, build_execution_env
        from nexus.core.auth.credential_store import get_issue_requester, get_issue_requester_by_url
    except Exception:
        auth_flag = str(os.getenv("NEXUS_AUTH_ENABLED", "false")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if auth_flag:
            return None
        return env_token

    auth_is_enabled = bool(auth_enabled())
    if not auth_is_enabled:
        return env_token

    normalized_repo = str(repo or "").strip()
    normalized_issue = str(issue_number or "").strip()
    if not normalized_repo or not normalized_issue:
        return None

    requester_nexus_id = None
    try:
        requester_nexus_id = get_issue_requester(normalized_repo, normalized_issue)
    except Exception:
        requester_nexus_id = None

    if not requester_nexus_id:
        candidates: list[str] = []
        try:
            project_cfg = {}
            if project_name:
                from nexus.core.config import PROJECT_CONFIG

                raw_cfg = (PROJECT_CONFIG or {}).get(str(project_name), {})
                if isinstance(raw_cfg, dict):
                    project_cfg = raw_cfg
            candidates.append(build_issue_url(normalized_repo, normalized_issue, project_cfg))
        except Exception:
            pass
        # Legacy/default-host URL probes for bindings saved by URL.
        candidates.append(
            build_issue_url(
                normalized_repo,
                normalized_issue,
                {"git_platform": "github"},
            )
        )
        candidates.append(
            build_issue_url(
                normalized_repo,
                normalized_issue,
                {"git_platform": "gitlab", "gitlab_base_url": "https://gitlab.com"},
            )
        )

        seen: set[str] = set()
        for issue_url in candidates:
            url = str(issue_url or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            try:
                requester_nexus_id = get_issue_requester_by_url(url)
            except Exception:
                requester_nexus_id = None
            if requester_nexus_id:
                break

    if not requester_nexus_id:
        logger.warning(
            "Requester binding missing for issue #%s repo=%s project=%s; refusing service-token fallback.",
            normalized_issue,
            normalized_repo,
            project_name,
        )
        return None

    user_env, env_error = build_execution_env(str(requester_nexus_id))
    if env_error:
        logger.warning(
            "Requester token unavailable for issue #%s repo=%s project=%s requester=%s: %s",
            normalized_issue,
            normalized_repo,
            project_name,
            requester_nexus_id,
            env_error,
        )
        return None

    platform = "github"
    try:
        if project_name:
            from nexus.core.config import get_project_platform

            platform = str(get_project_platform(str(project_name)) or "github").strip().lower()
    except Exception:
        platform = "github"

    preferred_keys = (
        ("GITLAB_TOKEN", "GLAB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")
        if platform == "gitlab"
        else ("GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN", "GLAB_TOKEN")
    )
    for key in preferred_keys:
        token = str(user_env.get(key) or "").strip()
        if token:
            return token

    logger.warning(
        "Requester token missing for issue #%s repo=%s project=%s requester=%s after credential resolution.",
        normalized_issue,
        normalized_repo,
        project_name,
        requester_nexus_id,
    )
    return None


def _extract_http_status_code(exc: Exception) -> int | None:
    """Best-effort extraction of HTTP status from provider exceptions."""
    for attr in ("status_code", "status", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def _run_coro_sync(coro_factory: Callable[[], Coroutine[Any, Any, object]]) -> object | None:
    """Run an async coroutine from sync code, even if a loop is already running."""
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

    holder: dict[str, object] = {"value": None, "error": None}

    def _runner() -> None:
        try:
            holder["value"] = asyncio.run(coro_factory())
        except Exception as inner_exc:
            holder["error"] = inner_exc

    worker = threading.Thread(target=_runner, daemon=True)
    worker.start()
    worker.join(timeout=10)
    if worker.is_alive() or holder["error"] is not None:
        return None
    return holder["value"]


def get_recent_agent_comment_window_seconds() -> int:
    """Return duplicate-suppression window (seconds) from env or default."""
    raw = os.getenv(
        "NEXUS_RECENT_AGENT_COMMENT_WINDOW_SECONDS",
        str(DEFAULT_RECENT_AGENT_COMMENT_WINDOW_SECONDS),
    )
    try:
        value = int(str(raw).strip())
        if value < 60:
            return DEFAULT_RECENT_AGENT_COMMENT_WINDOW_SECONDS
        return value
    except Exception:
        return DEFAULT_RECENT_AGENT_COMMENT_WINDOW_SECONDS


def get_retry_fuse_status(issue_number: str, now_ts: float | None = None) -> dict[str, object]:
    """Return retry-fuse status snapshot for an issue.

    The status is read from launched-agents state and computed without mutating
    persisted data.
    """
    now = now_ts if now_ts is not None else time.time()
    issue_key = str(issue_number)

    try:
        from nexus.core.state_manager import HostStateManager

        launched = HostStateManager.load_launched_agents(recent_only=False)
    except Exception:
        launched = {}

    entry = launched.get(issue_key, {}) if isinstance(launched, dict) else {}
    if not isinstance(entry, dict):
        entry = {}

    fuse = entry.get("retry_fuse", {})
    if not isinstance(fuse, dict):
        fuse = {}

    trip_times_raw = entry.get("retry_fuse_trip_times", [])
    if not isinstance(trip_times_raw, list):
        trip_times_raw = []
    trip_times = [
        float(ts)
        for ts in trip_times_raw
        if isinstance(ts, (int, float)) and (now - float(ts)) <= RETRY_FUSE_HARD_WINDOW_SECONDS
    ]

    window_start = float(fuse.get("window_start", 0) or 0)
    attempts = int(fuse.get("attempts", 0) or 0)
    window_elapsed_seconds = max(0.0, now - window_start) if window_start > 0 else None
    window_remaining_seconds = (
        max(0.0, RETRY_FUSE_WINDOW_SECONDS - window_elapsed_seconds)
        if window_elapsed_seconds is not None
        else None
    )

    return {
        "issue": issue_key,
        "exists": bool(fuse),
        "agent": str(fuse.get("agent", "")).strip().lower() or None,
        "attempts": attempts,
        "max_attempts": RETRY_FUSE_MAX_ATTEMPTS,
        "window_start": window_start or None,
        "window_elapsed_seconds": window_elapsed_seconds,
        "window_remaining_seconds": window_remaining_seconds,
        "window_seconds": RETRY_FUSE_WINDOW_SECONDS,
        "tripped": bool(fuse.get("tripped", False)),
        "hard_tripped": bool(fuse.get("hard_tripped", False)),
        "tripped_at": float(fuse.get("tripped_at", 0) or 0) or None,
        "alerted": bool(fuse.get("alerted", False)),
        "trip_count_in_hard_window": len(trip_times),
        "hard_trip_threshold": RETRY_FUSE_HARD_TRIP_COUNT,
        "hard_window_seconds": RETRY_FUSE_HARD_WINDOW_SECONDS,
        "trip_times": trip_times,
    }


class NexusAgentRuntime(AgentRuntime):
    """AgentRuntime implementation for the nexus host application.

    Args:
        finalize_fn: Callable ``(issue_number, repo, last_agent, project_name) -> None``
            that closes the issue and creates a PR when a workflow completes.
            Typically :func:`inbox_processor._finalize_workflow`.
        resolve_project: ``(file_path) -> project_name | None`` resolver used
            in :meth:`is_process_running` and forwarded via the orchestrator
            callbacks.  Can be ``None`` when not needed.
        resolve_repo: ``(project_name, issue_number) -> repo_string`` resolver.
            Falls back to the ``"nexus"`` project repo when omitted.
    """

    def __init__(
        self,
        finalize_fn: Callable,
        resolve_project: Callable | None = None,
        resolve_repo: Callable | None = None,
    ) -> None:
        self._finalize_fn = finalize_fn
        self._resolve_project = resolve_project
        self._resolve_repo = resolve_repo

    # ------------------------------------------------------------------
    # AgentRuntime interface
    # ------------------------------------------------------------------

    def launch_agent(
        self,
        issue_number: str,
        agent_type: str,
        *,
        trigger_source: str = "orchestrator",
        exclude_tools: list[str] | None = None,
        repo_override: str | None = None,
    ) -> tuple[int | None, str | None]:
        state = self.get_workflow_state(str(issue_number))
        if state in {"COMPLETED", "FAILED", "CANCELLED"}:
            logger.info(
                "Skipping launch for issue #%s (%s): workflow is terminal (%s)",
                issue_number,
                agent_type,
                state,
            )
            return None, "workflow-terminal"

        from nexus.core.runtime.agent_launcher import launch_next_agent

        return launch_next_agent(
            issue_number=issue_number,
            next_agent=agent_type,
            trigger_source=trigger_source,
            exclude_tools=exclude_tools,
            repo_override=repo_override,
        )

    def load_launched_agents(self, recent_only: bool = True) -> dict[str, dict]:
        from nexus.core.state_manager import HostStateManager

        return HostStateManager.load_launched_agents(recent_only=recent_only)

    def save_launched_agents(self, data: dict[str, dict]) -> None:
        from nexus.core.state_manager import HostStateManager

        HostStateManager.save_launched_agents(data)

    def clear_launch_guard(self, issue_number: str) -> None:
        from nexus.core.runtime.agent_launcher import clear_launch_guard

        clear_launch_guard(issue_number)

    def should_retry(self, issue_number: str, agent_type: str) -> bool:
        normalized_agent = str(agent_type or "").strip().lstrip("@").strip().lower()
        issue_key = str(issue_number)

        state = self.get_workflow_state(issue_key)
        if state in {"STOPPED", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"}:
            return False

        issue_open = self._is_issue_open_for_retry(issue_key)
        if issue_open is False:
            return False

        try:
            from nexus.core.runtime.agent_monitor import AgentMonitor

            if not AgentMonitor.should_retry(issue_key, normalized_agent):
                return False
        except Exception:
            pass

        if not self._autofix_retry_allowed(issue_key, normalized_agent):
            return False

        try:
            from nexus.core.state_manager import HostStateManager

            launched = HostStateManager.load_launched_agents(recent_only=False)
            entry = launched.get(issue_key, {}) if isinstance(launched, dict) else {}
            if not isinstance(entry, dict):
                entry = {}

            now = time.time()
            fuse = entry.get("retry_fuse", {})
            if not isinstance(fuse, dict):
                fuse = {}

            agent_in_fuse = str(fuse.get("agent", "")).strip().lower()
            window_start = float(fuse.get("window_start", 0) or 0)
            attempts = int(fuse.get("attempts", 0) or 0)
            tripped = bool(fuse.get("tripped", False))
            alerted = bool(fuse.get("alerted", False))
            hard_tripped = bool(fuse.get("hard_tripped", False))

            trip_times = entry.get("retry_fuse_trip_times", [])
            if not isinstance(trip_times, list):
                trip_times = []
            trip_times = [
                float(ts)
                for ts in trip_times
                if isinstance(ts, (int, float))
                and (now - float(ts)) <= RETRY_FUSE_HARD_WINDOW_SECONDS
            ]

            # Reset rolling window for different agent or expired time window.
            if (
                agent_in_fuse != normalized_agent
                or window_start <= 0
                or (now - window_start) > RETRY_FUSE_WINDOW_SECONDS
            ):
                window_start = now
                attempts = 0
                tripped = False
                alerted = False
                hard_tripped = False

            if tripped or hard_tripped:
                logger.error(
                    "Retry fuse already tripped for issue #%s agent %s",
                    issue_number,
                    normalized_agent,
                )
                return False

            attempts += 1
            fuse = {
                "agent": normalized_agent,
                "window_start": window_start,
                "attempts": attempts,
                "tripped": False,
                "alerted": alerted,
                "hard_tripped": False,
            }

            if attempts >= RETRY_FUSE_MAX_ATTEMPTS:
                fuse["tripped"] = True
                fuse["tripped_at"] = now

                trip_times.append(now)
                hard_trip = len(trip_times) >= RETRY_FUSE_HARD_TRIP_COUNT
                fuse["hard_tripped"] = hard_trip

                entry["retry_fuse"] = fuse
                entry["retry_fuse_trip_times"] = trip_times
                launched[issue_key] = entry
                HostStateManager.save_launched_agents(launched)

                if not alerted:
                    try:
                        from nexus.core.audit_store import AuditStore
                        from nexus.core.config import (
                            NEXUS_CORE_STORAGE_DIR,
                            NEXUS_STORAGE_DSN,
                            NEXUS_WORKFLOW_BACKEND,
                        )
                        from nexus.core.orchestration.plugin_runtime import (
                            get_workflow_state_plugin,
                        )

                        from nexus.core.integrations.workflow_state_factory import get_workflow_state

                        workflow_plugin = get_workflow_state_plugin(
                            storage_dir=NEXUS_CORE_STORAGE_DIR,
                            storage_type=(
                                "postgres" if NEXUS_WORKFLOW_BACKEND == "postgres" else "file"
                            ),
                            storage_config=(
                                {"connection_string": NEXUS_STORAGE_DSN}
                                if NEXUS_WORKFLOW_BACKEND == "postgres" and NEXUS_STORAGE_DSN
                                else {}
                            ),
                            issue_to_workflow_id=lambda n: get_workflow_state().get_workflow_id(n),
                            clear_pending_approval=lambda n: get_workflow_state().clear_pending_approval(
                                n
                            ),
                            audit_log=AuditStore.audit_log,
                            cache_key="workflow:state-engine:retry-fuse",
                        )
                        if workflow_plugin:
                            try:
                                import asyncio

                                asyncio.run(
                                    workflow_plugin.pause_workflow(
                                        issue_key,
                                        reason=(
                                            "Auto-paused: retry fuse tripped "
                                            f"for {normalized_agent}"
                                        ),
                                    )
                                )
                            except Exception as pause_exc:
                                logger.warning(
                                    "Failed to auto-pause workflow for issue #%s after retry fuse trip: %s",
                                    issue_number,
                                    pause_exc,
                                )
                    except Exception as exc:
                        logger.warning(
                            "Retry fuse pause hook setup failed for issue #%s: %s",
                            issue_number,
                            exc,
                        )

                    self.send_alert(
                        (
                            "🛑 **Retry Fuse Hard-Stop**\n\n"
                            f"Issue: #{issue_number}\n"
                            f"Agent: {normalized_agent}\n"
                            f"Status: Fuse tripped {len(trip_times)} times within "
                            f"{RETRY_FUSE_HARD_WINDOW_SECONDS // 60} minutes. "
                            "Workflow auto-chain is hard-stopped and requires manual intervention.\n\n"
                            "Review logs/comments, then use /reprocess or /resume when ready."
                        )
                        if hard_trip
                        else (
                            "🛑 **Retry Fuse Tripped**\n\n"
                            f"Issue: #{issue_number}\n"
                            f"Agent: {normalized_agent}\n"
                            f"Status: Auto-chain paused after {attempts} retry attempts "
                            f"within {RETRY_FUSE_WINDOW_SECONDS // 60} minutes.\n\n"
                            "Use /reprocess or /resume after investigating logs."
                        )
                    )

                    fuse["alerted"] = True
                    entry["retry_fuse"] = fuse
                    entry["retry_fuse_trip_times"] = trip_times
                    launched[issue_key] = entry
                    HostStateManager.save_launched_agents(launched)

                logger.error(
                    "Retry fuse tripped for issue #%s agent %s (%d attempts in window, %d trips in hard window)",
                    issue_number,
                    normalized_agent,
                    attempts,
                    len(trip_times),
                )
                return False

            entry["retry_fuse"] = fuse
            launched[issue_key] = entry
            HostStateManager.save_launched_agents(launched)
        except Exception as exc:
            logger.debug(
                "Retry fuse bookkeeping failed for issue #%s (%s); falling back to monitor retry logic",
                issue_number,
                exc,
            )

        # Fuse didn't trip — allow the retry.
        # (The actual retry-budget decision lives in the fuse logic above.)
        return True

    def _autofix_retry_allowed(self, issue_number: str, agent_type: str) -> bool:
        """Guard retries when recent autofix history shows repeated failures."""
        try:
            from nexus.core.audit_store import AuditStore

            issue_int = int(str(issue_number))
        except Exception:
            return True

        try:
            events = AuditStore.get_autofix_events(
                issue_int,
                agent_type=agent_type,
                limit=12,
            )
        except Exception as exc:
            logger.debug(
                "Autofix retry-guard lookup failed for issue #%s agent %s: %s",
                issue_number,
                agent_type,
                exc,
            )
            return True

        if not events:
            return True

        recent_types = [str(item.get("event_type") or "").strip().upper() for item in events]
        if any(event_type == "AUTOFIX_VALIDATED" for event_type in recent_types[:4]):
            return True

        failure_streak = 0
        for event_type in recent_types:
            if event_type == "AUTOFIX_FAILED":
                failure_streak += 1
                continue
            break

        recent_window = recent_types[:6]
        repeated_recent_failures = recent_window.count("AUTOFIX_FAILED") >= 4 and (
            "AUTOFIX_VALIDATED" not in recent_window
        )
        if failure_streak < 3 and not repeated_recent_failures:
            return True

        reason = (
            f"Blocked retry for issue #{issue_number} agent {agent_type}: "
            f"autofix failure streak={failure_streak}, recent={recent_window}"
        )
        logger.warning(reason)
        try:
            AuditStore.audit_log(
                issue_int,
                "AUTOFIX_RETRY_GUARD_BLOCKED",
                reason,
            )
        except Exception:
            pass
        return False

    def _is_issue_open_for_retry(self, issue_number: str) -> bool | None:
        """Best-effort issue status check used by retry gating."""
        try:
            from nexus.core.config import get_default_project, get_repo
            from nexus.core.integrations.workflow_state_factory import get_workflow_state

            workflow_id = get_workflow_state().get_workflow_id(str(issue_number))
            project_hint = ""
            if workflow_id:
                project_hint = str(workflow_id).split("-", 1)[0].strip().lower()

            repo_candidates: list[str] = []
            if project_hint:
                try:
                    repo_candidates.append(get_repo(project_hint))
                except Exception:
                    pass

            try:
                default_project = get_default_project()
                repo_candidates.append(get_repo(default_project))
            except Exception:
                pass

            seen: set[str] = set()
            for repo in repo_candidates:
                repo_name = str(repo or "").strip()
                if not repo_name or repo_name in seen:
                    continue
                seen.add(repo_name)
                status = self.is_issue_open(str(issue_number), repo_name)
                if status is False:
                    # Fail-open when no Git credentials are configured; in that
                    # state we cannot reliably distinguish "closed" from
                    # "unreachable/unauthorized".
                    project_name = _resolve_project_name_for_repo(repo_name)
                    token_override = _resolve_requester_token_override(
                        str(issue_number),
                        repo_name,
                        project_name,
                    )
                    if not token_override:
                        continue
                    return False
                if status is True:
                    return True
        except Exception:
            return None

        return None

    def send_alert(self, message: str) -> bool:
        from nexus.core.integrations.notifications import emit_alert

        text = str(message or "")
        severity = "warning"
        if "**Agent Transition**" in text or text.lstrip().startswith("🔗"):
            severity = "info"
        return bool(emit_alert(text, severity=severity, source="agent_runtime"))

    def _extract_completion_comment_signature(self, body: str) -> tuple[str, str, int] | None:
        normalized_body = normalize_completion_comment_markdown(body)
        complete_match = _STEP_COMPLETE_HEADER_RE.search(normalized_body)
        step_id_match = _STEP_ID_COMMENT_RE.search(normalized_body)
        step_num_match = _STEP_NUM_COMMENT_RE.search(normalized_body)
        if not complete_match or not step_id_match or not step_num_match:
            return None

        completed_agent = str(complete_match.group(1) or "").strip().lstrip("@").strip("`").lower()
        step_id = str(step_id_match.group(1) or "").strip().lower()
        try:
            step_num = int(step_num_match.group(1))
        except (TypeError, ValueError):
            return None
        if not completed_agent or not step_id or step_num <= 0:
            return None
        return completed_agent, step_id, step_num

    def _has_recent_duplicate_completion_comment(
        self,
        platform,
        issue_number: str,
        body: str,
    ) -> bool:
        expected_signature = self._extract_completion_comment_signature(body)
        if expected_signature is None:
            return False

        try:
            import asyncio

            comments = asyncio.run(platform.get_comments(str(issue_number)))
        except Exception as exc:
            logger.warning(
                "Could not inspect existing comments for issue #%s: %s",
                issue_number,
                exc,
            )
            return False

        now = datetime.now(UTC)
        window_seconds = get_recent_agent_comment_window_seconds()
        for comment in reversed(comments or []):
            comment_body = str(getattr(comment, "body", "") or "")
            if "_Automated comment from Nexus._" in comment_body:
                continue
            current_signature = self._extract_completion_comment_signature(comment_body)
            if current_signature is None or current_signature != expected_signature:
                continue

            created_at = getattr(comment, "created_at", None)
            if not isinstance(created_at, datetime):
                return True

            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)

            age_seconds = (now - created_at).total_seconds()
            if age_seconds <= window_seconds:
                return True

        return False

    def post_completion_comment(self, issue_number: str, repo: str, body: str) -> bool:
        from nexus.core.orchestration.nexus_core_helpers import get_git_platform

        try:
            normalized_body = normalize_completion_comment_markdown(body)
            project_name = _resolve_project_name_for_repo(str(repo))
            platform = get_git_platform(
                repo,
                project_name=project_name,
                token_override=_resolve_requester_token_override(
                    str(issue_number),
                    str(repo),
                    project_name,
                ),
            )
            import asyncio

            if self._has_recent_duplicate_completion_comment(
                platform,
                issue_number,
                normalized_body,
            ):
                logger.info(
                    "Skipping automated completion comment for issue #%s: "
                    "duplicate completion comment for the same step already exists",
                    issue_number,
                )
                return True

            asyncio.run(platform.add_comment(str(issue_number), normalized_body))
            return True
        except Exception as exc:
            logger.error(
                "Failed to post completion comment for issue #%s in %s: %s",
                issue_number,
                repo,
                exc,
            )
            return False

    def is_issue_open(self, issue_number: str, repo: str) -> bool | None:
        """Return issue open/closed status for auto-completion guardrails."""
        try:
            import asyncio

            from nexus.core.orchestration.nexus_core_helpers import get_git_platform

            project_name = _resolve_project_name_for_repo(str(repo))
            platform = get_git_platform(
                repo,
                project_name=project_name,
                token_override=_resolve_requester_token_override(
                    str(issue_number),
                    str(repo),
                    project_name,
                ),
            )
            if not platform:
                return None

            try:
                asyncio.get_running_loop()
                in_running_loop = True
            except RuntimeError:
                in_running_loop = False

            if not in_running_loop:
                details = asyncio.run(platform.get_issue(str(issue_number)))
            else:
                holder: dict[str, object] = {"value": None, "error": None}

                def _runner() -> None:
                    try:
                        holder["value"] = asyncio.run(platform.get_issue(str(issue_number)))
                    except Exception as inner_exc:
                        holder["error"] = inner_exc

                worker = threading.Thread(target=_runner, daemon=True)
                worker.start()
                worker.join(timeout=10)
                if worker.is_alive():
                    return None
                if holder["error"] is not None:
                    raise holder["error"]  # type: ignore[misc]
                details = holder["value"]

            if not details:
                return False

            state_value = ""
            if isinstance(details, dict):
                state_value = str(details.get("state", "")).strip().lower()
            else:
                state_value = str(getattr(details, "state", "")).strip().lower()

            if state_value == "closed":
                return False
            if state_value == "open":
                return True
            logger.warning(
                "is_issue_open returned unexpected state for issue #%s in %s: raw_state=%r",
                issue_number,
                repo,
                state_value,
            )
            return None
        except Exception as exc:
            error_text = str(exc).lower()
            if "404" in error_text or "not found" in error_text:
                return False
            status_code = _extract_http_status_code(exc)
            key = (str(issue_number), str(repo))
            now = time.time()
            last_log_at = _last_issue_open_error_log_at.get(key, 0.0)
            if (now - last_log_at) >= _ISSUE_OPEN_ERROR_LOG_COOLDOWN_SECONDS:
                _last_issue_open_error_log_at[key] = now
                logger.warning(
                    "is_issue_open check failed for issue #%s in %s: %s(%s) status=%s. "
                    "Likely causes: missing/expired token, missing repo permission, API rate limit, or network failure.",
                    issue_number,
                    repo,
                    type(exc).__name__,
                    str(exc),
                    status_code if status_code is not None else "unknown",
                )
            else:
                logger.debug(
                    "is_issue_open check failed for issue #%s in %s (suppressed): %s(%s)",
                    issue_number,
                    repo,
                    type(exc).__name__,
                    exc,
                )
            return None

    def audit_log(self, issue_number: str, event: str, details: str = "") -> None:
        from nexus.core.audit_store import AuditStore

        AuditStore.audit_log(int(issue_number), event, details or None)

    def finalize_workflow(
        self,
        issue_number: str,
        repo: str,
        last_agent: str,
        project_name: str,
    ) -> dict:
        self._finalize_fn(issue_number, repo, last_agent, project_name)
        return {}

    # ------------------------------------------------------------------
    # Optional hooks (override base-class no-ops)
    # ------------------------------------------------------------------

    def get_workflow_state(self, issue_number: str) -> str | None:
        """Read workflow state from workflow backend via workflow plugin."""
        from nexus.core.config import NEXUS_CORE_STORAGE_DIR, NEXUS_STORAGE_DSN, NEXUS_WORKFLOW_BACKEND
        from nexus.core.orchestration.plugin_runtime import get_workflow_state_plugin
        from nexus.core.integrations.workflow_state_factory import get_workflow_state

        workflow_id = get_workflow_state().get_workflow_id(str(issue_number))
        normalized = ""
        if workflow_id:
            workflow_plugin = get_workflow_state_plugin(
                storage_dir=NEXUS_CORE_STORAGE_DIR,
                storage_type=("postgres" if NEXUS_WORKFLOW_BACKEND == "postgres" else "file"),
                storage_config=(
                    {"connection_string": NEXUS_STORAGE_DSN}
                    if NEXUS_WORKFLOW_BACKEND == "postgres" and NEXUS_STORAGE_DSN
                    else {}
                ),
                issue_to_workflow_id=lambda n: get_workflow_state().get_workflow_id(n),
                clear_pending_approval=lambda n: get_workflow_state().clear_pending_approval(n),
                cache_key="workflow:state-engine:runtime-state",
            )

            async def _load_workflow() -> object:
                engine = workflow_plugin._get_engine()
                return await engine.get_workflow(workflow_id)

            workflow = _run_coro_sync(_load_workflow)
            if workflow:
                state_obj = getattr(workflow, "state", None)
                normalized = str(getattr(state_obj, "value", state_obj or "")).strip().lower()

        if not normalized:
            payload = self._load_workflow_payload_from_file(
                issue_number=str(issue_number),
                workflow_id=str(workflow_id or ""),
                storage_dir=NEXUS_CORE_STORAGE_DIR,
            )
            normalized = str((payload or {}).get("state", "")).strip().lower()
        if normalized in {"paused", "cancelled", "completed", "failed"}:
            return normalized.upper()
        return None

    def should_retry_dead_agent(self, issue_number: str, agent_type: str) -> bool:
        """Allow dead-agent retry only when workflow still has a matching RUNNING step."""
        expected_agent = self.get_expected_running_agent(issue_number)
        if not expected_agent:
            return False

        requested = str(agent_type or "").strip().lower().lstrip("@")
        expected = str(expected_agent or "").strip().lower().lstrip("@")
        return bool(requested and expected and requested == expected)

    def _is_remote_issue_open(self, payload: dict, issue_number: str) -> bool | None:
        """Best-effort check whether the source issue still exists and is open.

        Returns:
            True when confirmed open, False when confirmed closed/missing,
            None when status cannot be determined.
        """
        try:
            from nexus.core.orchestration.nexus_core_helpers import get_git_platform
        except Exception:
            return None

        metadata = payload.get("metadata") if isinstance(payload, dict) else None
        if not isinstance(metadata, dict):
            return None

        issue_url = str(metadata.get("issue_url", "")).strip()
        project_name = str(metadata.get("project", "")).strip() or None

        repo_name = ""
        issue_num = str(issue_number)
        if issue_url:
            parsed = urlparse(issue_url)
            path_parts = [part for part in parsed.path.strip("/").split("/") if part]
            if "issues" in path_parts:
                idx = path_parts.index("issues")
                if idx >= 2:
                    repo_name = f"{path_parts[idx - 2]}/{path_parts[idx - 1]}"
                if idx + 1 < len(path_parts):
                    issue_num = path_parts[idx + 1]

        try:
            platform = get_git_platform(
                repo=repo_name or None,
                project_name=project_name,
                token_override=_resolve_requester_token_override(
                    str(issue_num),
                    str(repo_name),
                    project_name,
                ),
            )
            details = platform.get_issue(str(issue_num), ["state"]) if platform else None
            if not details:
                return False
            state = str(details.get("state", "")).strip().lower()
            if state == "closed":
                return False
            if state:
                return True
            return None
        except Exception as exc:
            error_text = str(exc).lower()
            if "404" in error_text or "not found" in error_text:
                return False
            return None

    def get_expected_running_agent(self, issue_number: str) -> str | None:
        """Return the current RUNNING step agent type from workflow storage."""
        from nexus.core.config import NEXUS_CORE_STORAGE_DIR, NEXUS_STORAGE_DSN, NEXUS_WORKFLOW_BACKEND
        from nexus.core.orchestration.plugin_runtime import get_workflow_state_plugin
        from nexus.core.integrations.workflow_state_factory import get_workflow_state

        workflow_id = get_workflow_state().get_workflow_id(str(issue_number))
        if workflow_id:
            workflow_plugin = get_workflow_state_plugin(
                storage_dir=NEXUS_CORE_STORAGE_DIR,
                storage_type=("postgres" if NEXUS_WORKFLOW_BACKEND == "postgres" else "file"),
                storage_config=(
                    {"connection_string": NEXUS_STORAGE_DSN}
                    if NEXUS_WORKFLOW_BACKEND == "postgres" and NEXUS_STORAGE_DSN
                    else {}
                ),
                issue_to_workflow_id=lambda n: get_workflow_state().get_workflow_id(n),
                clear_pending_approval=lambda n: get_workflow_state().clear_pending_approval(n),
                cache_key="workflow:state-engine:runtime-expected-agent",
            )

            async def _load_workflow() -> object:
                engine = workflow_plugin._get_engine()
                return await engine.get_workflow(workflow_id)

            workflow = _run_coro_sync(_load_workflow)
            if workflow:
                state_obj = getattr(workflow, "state", None)
                state_str = str(getattr(state_obj, "value", state_obj or "")).strip().lower()
                if state_str in {"completed", "failed", "cancelled"}:
                    return None

                for step in list(getattr(workflow, "steps", []) or []):
                    status_obj = getattr(step, "status", None)
                    status = str(getattr(status_obj, "value", status_obj or "")).strip().upper()
                    if status != "RUNNING":
                        continue
                    agent = getattr(step, "agent", None)
                    name = str(getattr(agent, "name", "") or "").strip()
                    display_name = str(getattr(agent, "display_name", "") or "").strip()
                    if name:
                        return name
                    if display_name:
                        return display_name

        payload = self._load_workflow_payload_from_file(
            issue_number=str(issue_number),
            workflow_id=str(workflow_id or ""),
            storage_dir=NEXUS_CORE_STORAGE_DIR,
        )
        state = str((payload or {}).get("state", "")).strip().lower()
        if state in {"completed", "failed", "cancelled"}:
            return None
        for step in (payload or {}).get("steps", []):
            if not isinstance(step, dict):
                continue
            if str(step.get("status", "")).strip().upper() != "RUNNING":
                continue
            agent = step.get("agent")
            if isinstance(agent, dict):
                name = str(agent.get("name", "")).strip()
                display_name = str(agent.get("display_name", "")).strip()
                if name:
                    return name
                if display_name:
                    return display_name
        return None

    def _load_workflow_payload_from_file(
        self,
        *,
        issue_number: str,
        workflow_id: str,
        storage_dir: str,
    ) -> dict[str, Any]:
        """Best-effort compatibility fallback for file-based workflow payload reads."""
        candidate_ids = [str(workflow_id or "").strip(), str(issue_number or "").strip()]
        seen: set[str] = set()
        for cid in candidate_ids:
            if not cid or cid in seen:
                continue
            seen.add(cid)
            wf_file = os.path.join(storage_dir, "workflows", f"{cid}.json")
            try:
                with open(wf_file, encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict):
                    return payload
            except Exception:
                continue
        return {}

    def get_agent_timeout_seconds(
        self,
        issue_number: str,
        agent_type: str | None = None,
    ) -> int | None:
        """Return workflow-defined timeout for issue/agent when available."""
        import json

        from nexus.core.config import NEXUS_CORE_STORAGE_DIR
        from nexus.core.integrations.workflow_state_factory import get_workflow_state

        workflow_id = get_workflow_state().get_workflow_id(str(issue_number))
        if not workflow_id:
            return None

        wf_file = os.path.join(NEXUS_CORE_STORAGE_DIR, "workflows", f"{workflow_id}.json")
        try:
            with open(wf_file) as f:
                payload = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

        steps = payload.get("steps", [])
        if not isinstance(steps, list):
            return None

        requested = str(agent_type or "").strip().lower()

        def _step_agent_name(step: dict) -> str:
            agent = step.get("agent")
            if not isinstance(agent, dict):
                return ""
            return str(agent.get("name", "")).strip().lower()

        def _timeout_from_step(step: dict) -> int | None:
            step_timeout = step.get("timeout")
            if isinstance(step_timeout, (int, float)) and int(step_timeout) > 0:
                return int(step_timeout)
            agent = step.get("agent")
            if not isinstance(agent, dict):
                return None
            agent_timeout = agent.get("timeout")
            if isinstance(agent_timeout, (int, float)) and int(agent_timeout) > 0:
                return int(agent_timeout)
            return None

        prioritized_steps = []
        if requested:
            prioritized_steps.extend(
                step
                for step in steps
                if isinstance(step, dict)
                and str(step.get("status", "")).strip().upper() == "RUNNING"
                and _step_agent_name(step) == requested
            )
        prioritized_steps.extend(
            step
            for step in steps
            if isinstance(step, dict) and str(step.get("status", "")).strip().upper() == "RUNNING"
        )
        if requested:
            prioritized_steps.extend(
                step
                for step in steps
                if isinstance(step, dict) and _step_agent_name(step) == requested
            )

        for step in prioritized_steps:
            timeout = _timeout_from_step(step)
            if timeout:
                return timeout

        return None

    def is_process_running(self, issue_number: str) -> bool:
        """Return True if an agent process is still active for this issue."""
        try:
            from nexus.core.orchestration.plugin_runtime import get_runtime_ops_plugin

            ops = get_runtime_ops_plugin(cache_key="runtime-ops:inbox")
            if ops:
                if bool(ops.is_issue_process_running(issue_number)):
                    return True
        except Exception as exc:
            logger.debug(f"is_process_running check failed for #{issue_number}: {exc}")

        # Fallback: rely on persisted launch PID when command-line pattern
        # matching is insufficient (e.g., wrapped CLIs or prompt-only context).
        try:
            from nexus.core.state_manager import HostStateManager

            launched = HostStateManager.load_launched_agents(recent_only=False) or {}
            entry = launched.get(str(issue_number), {})
            if isinstance(entry, dict):
                pid = entry.get("pid")
                if isinstance(pid, int) and pid > 0:
                    try:
                        os.kill(int(pid), 0)
                        return True
                    except Exception:
                        pass
        except Exception as exc:
            logger.debug("PID fallback process check failed for #%s: %s", issue_number, exc)
        return False

    def check_log_timeout(
        self,
        issue_number: str,
        log_file: str,
        timeout_seconds: int | None = None,
    ) -> tuple[bool, int | None]:
        from nexus.core.runtime.agent_monitor import AgentMonitor

        resolved_timeout = timeout_seconds or self.get_agent_timeout_seconds(issue_number)
        return AgentMonitor.check_timeout(
            issue_number,
            log_file,
            timeout_seconds=resolved_timeout,
        )

    def kill_process(self, pid: int) -> bool:
        """Delegate to AgentMonitor.kill_agent for consistent kill + cleanup."""
        from nexus.core.runtime.agent_monitor import AgentMonitor

        # AgentMonitor.kill_agent expects an issue_num string but uses it only
        # for logging; pass empty string when we don't have it readily.
        return bool(AgentMonitor.kill_agent(pid, ""))

    def notify_timeout(self, issue_number: str, agent_type: str, will_retry: bool) -> None:
        normalized = str(agent_type or "").strip().lstrip("@").strip().lower()
        if not normalized or normalized in {"unknown", "none", "n/a"}:
            logger.warning(
                "Skipping timeout notification for issue #%s: unresolved agent type (%r)",
                issue_number,
                agent_type,
            )
            return

        try:
            from nexus.core.integrations.notifications import notify_agent_timeout

            notify_agent_timeout(issue_number, agent_type, will_retry, project="nexus")
        except Exception as exc:
            logger.warning(f"notify_timeout failed for #{issue_number} / {agent_type}: {exc}")

    def get_latest_issue_log(self, issue_number: str) -> str | None:
        """Return latest task session log path for an issue, if present."""
        try:
            from nexus.core.config import BASE_DIR, get_nexus_dir_name

            nexus_dir_name = get_nexus_dir_name()
            pattern = os.path.join(
                BASE_DIR,
                "**",
                nexus_dir_name,
                "tasks",
                "*",
                "logs",
                f"*_{issue_number}_*.log",
            )
            matches = glob.glob(pattern, recursive=True)
            if not matches:
                return None
            return max(matches, key=os.path.getmtime)
        except Exception:
            return None


def get_expected_running_agent_from_workflow(issue_number: str) -> str | None:
    """Compatibility helper for callers that need expected running agent lookup."""
    runtime = NexusAgentRuntime(finalize_fn=lambda *_args, **_kwargs: None)
    return runtime.get_expected_running_agent(str(issue_number))
