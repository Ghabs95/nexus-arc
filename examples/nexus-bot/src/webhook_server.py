#!/usr/bin/env python3
"""
Nexus Webhook Server - Receives and processes Git webhook events

This service replaces the polling-based Git comment checking with
real-time webhook event processing for faster response times.

Event handlers:
- issues.opened: Convert Git issue to markdown task in .nexus/inbox/<project>/ for triage
- issue_comment.created: Detect workflow completion and chain to next agent
- pull_request.opened/synchronized: Notify about new PRs
- pull_request_review.submitted: Notify about PR reviews
"""

import asyncio
import html
import logging
import os
import time
from datetime import UTC, datetime
from hmac import compare_digest
from urllib.parse import quote

import requests
from flask import Flask, jsonify, make_response, redirect, request, send_from_directory
from werkzeug.exceptions import NotFound

from nexus.core.config.bootstrap import initialize_runtime

initialize_runtime(configure_logging=False)

try:
    from flask_socketio import SocketIO
except ImportError:

    class SocketIO:  # type: ignore[no-redef]
        """Minimal fallback used when flask-socketio is unavailable (tests/dev)."""

        def __init__(self, app, *args, **kwargs):
            self.app = app

        def emit(self, *args, **kwargs):
            return None

        def run(self, app, *args, **kwargs):
            return app.run(*args, **kwargs)

from nexus.core.project.repo_utils import project_repos_from_config as _project_repos
from nexus.core.workspace import WorkspaceManager

from nexus.core.config import (
    BASE_DIR,
    DISCORD_TOKEN,
    NEXUS_ACCESS_SYNC_INTERVAL_MINUTES,
    NEXUS_AUTH_ENABLED,
    NEXUS_AUTH_SESSION_TTL_SECONDS,
    NEXUS_PUBLIC_BASE_URL,
    NEXUS_STORAGE_BACKEND,
    NEXUS_CORE_STORAGE_DIR,
    NEXUS_STORAGE_DSN,
    NEXUS_WORKFLOW_BACKEND,
    PROJECT_CONFIG,
    WEBHOOK_PORT,
    WEBHOOK_SECRET,
    TELEGRAM_TOKEN,
    get_default_project,
    get_repos,
    get_inbox_dir,
    get_inbox_storage_backend,
    get_tasks_active_dir,
)
from nexus.core.integrations.inbox_queue import enqueue_task
from nexus.core.integrations.notifications import (
    emit_alert,
    send_notification,
)
from nexus.core.orchestration.plugin_runtime import (
    get_webhook_policy_plugin,
    get_workflow_state_plugin,
)
from nexus.core.orchestration.nexus_core_helpers import get_git_platform, get_workflow_definition_path
from nexus.core.orchestration.nexus_core_helpers import (
    can_access_issue_context,
    get_workflow_context,
    resolve_project_name_for_repo,
)
from nexus.core.runtime.agent_launcher import launch_next_agent
from nexus.core.user_manager import get_user_manager
from nexus.core.webhook.issue_service import handle_issue_opened_event as _handle_issue_opened_event
from nexus.core.webhook.comment_service import (
    handle_issue_comment_event as _handle_issue_comment_event,
)
from nexus.core.webhook.pr_service import (
    evaluate_issue_close_for_pr_merge,
    handle_pull_request_event as _handle_pull_request_event,
)
from nexus.core.webhook.pr_review_service import (
    handle_pull_request_review_event as _handle_pull_request_review_event,
)
from nexus.core.webhook.http_service import process_webhook_request as _process_webhook_request
from nexus.core.auth import (
    build_setup_completed_chat_message as _svc_build_setup_completed_chat_message,
)
from nexus.core.auth import (
    complete_github_oauth as _svc_complete_github_oauth,
)
from nexus.core.auth import (
    complete_gitlab_oauth as _svc_complete_gitlab_oauth,
)
from nexus.core.auth import (
    format_login_session_ref as _svc_format_login_session_ref,
)
from nexus.core.auth import (
    get_provider_account_login_status as _svc_get_provider_account_login_status,
)
from nexus.core.auth import (
    get_session_and_setup_status as _svc_get_session_and_setup_status,
)
from nexus.core.auth import (
    refresh_stale_access_grants as _svc_refresh_stale_access_grants,
)
from nexus.core.auth import (
    relay_provider_account_login_callback as _svc_relay_provider_account_login_callback,
)
from nexus.core.auth import (
    resolve_login_session_id as _svc_resolve_login_session_id,
)
from nexus.core.auth import (
    start_provider_account_login as _svc_start_provider_account_login,
)
from nexus.core.auth import (
    submit_provider_account_login_code as _svc_submit_provider_account_login_code,
)
from nexus.core.auth import (
    start_oauth_flow as _svc_start_oauth_flow,
)
from nexus.core.auth import (
    store_ai_provider_keys as _svc_store_ai_provider_keys,
)
from nexus.core.auth.credential_store import get_auth_session as _svc_get_auth_session
from nexus.core.auth.credential_store import get_auth_session_by_state as _svc_get_auth_session_by_state
from nexus.core.runtime_mode import is_issue_process_running

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
    force=True,
)
logger = logging.getLogger(__name__)

def _resolve_visualizer_static_folder() -> str:
    module_static = os.path.join(os.path.dirname(__file__), "static")
    fallback_static = "/app/src/static"
    candidates = (module_static, fallback_static)
    for candidate in candidates:
        visualizer_file = os.path.join(candidate, "visualizer.html")
        if os.path.isfile(visualizer_file):
            return candidate
    return module_static


_VISUALIZER_STATIC_FOLDER = _resolve_visualizer_static_folder()
if not os.path.isfile(os.path.join(_VISUALIZER_STATIC_FOLDER, "visualizer.html")):
    logger.warning(
        "Visualizer asset missing; expected visualizer.html under %s",
        _VISUALIZER_STATIC_FOLDER,
    )

app = Flask(__name__, static_folder=_VISUALIZER_STATIC_FOLDER)
_socketio_async_mode = str(os.getenv("NEXUS_SOCKETIO_ASYNC_MODE", "eventlet")).strip().lower()
if _socketio_async_mode not in {"threading", "eventlet", "gevent", "gevent_uwsgi"}:
    _socketio_async_mode = "threading"
socketio = SocketIO(app, async_mode=_socketio_async_mode, cors_allowed_origins="*")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "true" if default else "false")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _cli_auth_mode() -> str:
    mode = str(os.getenv("NEXUS_CLI_AUTH_MODE", "account")).strip().lower()
    return mode if mode in {"account", "api-key", "auto"} else "account"


def _allow_api_keys_in_web_setup() -> bool:
    return _cli_auth_mode() != "account"


def _oauth_provider_configured(provider: str) -> bool:
    normalized = str(provider or "").strip().lower()
    if normalized == "github":
        return bool(
            str(os.getenv("NEXUS_GITHUB_CLIENT_ID", "")).strip()
            and str(os.getenv("NEXUS_GITHUB_CLIENT_SECRET", "")).strip()
        )
    if normalized == "gitlab":
        return bool(
            str(os.getenv("NEXUS_GITLAB_CLIENT_ID", "")).strip()
            and str(os.getenv("NEXUS_GITLAB_CLIENT_SECRET", "")).strip()
        )
    return False


def _configured_oauth_providers() -> list[str]:
    providers: list[str] = []
    if _oauth_provider_configured("github"):
        providers.append("github")
    if _oauth_provider_configured("gitlab"):
        providers.append("gitlab")
    return providers


def _default_oauth_provider(providers: list[str]) -> str:
    if "gitlab" in providers:
        return "gitlab"
    if "github" in providers:
        return "github"
    return ""

# Track processed events to avoid duplicates
processed_events = set()
_last_acl_sync_at = 0.0
_WEB_SESSION_COOKIE_NAME = "nexus_web_session"
_WEB_SESSION_COOKIE_SECURE = NEXUS_PUBLIC_BASE_URL.lower().startswith("https://")
_WEB_SESSION_COOKIE_MAX_AGE_SECONDS = max(300, int(NEXUS_AUTH_SESSION_TTL_SECONDS))
_VISUALIZER_ENABLED = str(os.getenv("NEXUS_VISUALIZER_ENABLED", "true")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_VISUALIZER_SHARED_TOKEN = str(os.getenv("NEXUS_VISUALIZER_SHARED_TOKEN", "")).strip()
_VISUALIZER_SHARED_TOKEN_COOKIE_NAME = "nexus_visualizer_token"
_VISUALIZER_SHARED_TOKEN_HEADER = "X-Nexus-Visualizer-Token"


def _run_acl_sync_if_due() -> None:
    global _last_acl_sync_at
    if not NEXUS_AUTH_ENABLED:
        return
    interval_seconds = max(60, int(NEXUS_ACCESS_SYNC_INTERVAL_MINUTES) * 60)
    now = time.time()
    if _last_acl_sync_at and (now - _last_acl_sync_at) < interval_seconds:
        return
    try:
        result = _svc_refresh_stale_access_grants(limit=200)
        _last_acl_sync_at = now
        if int(result.get("processed") or 0) > 0:
            logger.info("Auth ACL sync: %s", result)
    except Exception as exc:
        logger.warning("Periodic ACL sync failed: %s", exc)


def _safe_next_path(raw_path: str | None, *, default: str = "/visualizer") -> str:
    candidate = str(raw_path or "").strip()
    if candidate.startswith("/") and not candidate.startswith("//"):
        return candidate
    return default


def _parse_iso_utc(raw_value: str | None) -> datetime | None:
    value = str(raw_value or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _extract_session_id_from_request() -> str:
    for candidate in (
        request.args.get("session"),
        request.headers.get("X-Nexus-Session"),
        request.cookies.get(_WEB_SESSION_COOKIE_NAME),
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return ""


def _resolve_ready_web_session(session_id: str | None = None) -> tuple[str, dict | None]:
    candidate = str(session_id or _extract_session_id_from_request() or "").strip()
    if not candidate:
        return "", None
    try:
        payload = _svc_get_session_and_setup_status(candidate)
    except Exception as exc:
        logger.warning("Failed to resolve auth session %s: %s", candidate, exc)
        return "", None
    if not isinstance(payload, dict) or not payload.get("exists"):
        return "", None

    expires_at = _parse_iso_utc(payload.get("expires_at"))
    if expires_at and expires_at <= datetime.now(tz=UTC):
        return "", None
    if str(payload.get("status") or "").strip().lower() == "expired":
        return "", None

    setup = payload.get("setup")
    if not isinstance(setup, dict) or not bool(setup.get("ready")):
        return "", None
    resolved_session_id = str(payload.get("session_id") or "").strip() or candidate
    return resolved_session_id, payload


def _set_web_session_cookie(response, session_id: str) -> None:
    response.set_cookie(
        _WEB_SESSION_COOKIE_NAME,
        str(session_id),
        max_age=_WEB_SESSION_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=_WEB_SESSION_COOKIE_SECURE,
        samesite="Lax",
        path="/",
    )


def _clear_web_session_cookie(response) -> None:
    response.set_cookie(
        _WEB_SESSION_COOKIE_NAME,
        "",
        expires=0,
        max_age=0,
        httponly=True,
        secure=_WEB_SESSION_COOKIE_SECURE,
        samesite="Lax",
        path="/",
    )


def _extract_visualizer_shared_token_from_request() -> str:
    auth_header = str(request.headers.get("Authorization", "")).strip()
    bearer_token = ""
    if auth_header.lower().startswith("bearer "):
        bearer_token = auth_header[7:].strip()
    for candidate in (
        request.headers.get(_VISUALIZER_SHARED_TOKEN_HEADER),
        bearer_token,
        request.cookies.get(_VISUALIZER_SHARED_TOKEN_COOKIE_NAME),
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return ""


def _has_valid_visualizer_shared_token(raw_token: str | None = None) -> bool:
    expected = str(_VISUALIZER_SHARED_TOKEN or "").strip()
    if not expected:
        return False
    candidate = str(raw_token or _extract_visualizer_shared_token_from_request() or "").strip()
    if not candidate:
        return False
    return bool(compare_digest(candidate, expected))


def _set_visualizer_shared_token_cookie(response, token: str) -> None:
    response.set_cookie(
        _VISUALIZER_SHARED_TOKEN_COOKIE_NAME,
        str(token),
        max_age=_WEB_SESSION_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=_WEB_SESSION_COOKIE_SECURE,
        samesite="Lax",
        path="/",
    )


def _clear_visualizer_shared_token_cookie(response) -> None:
    response.set_cookie(
        _VISUALIZER_SHARED_TOKEN_COOKIE_NAME,
        "",
        expires=0,
        max_age=0,
        httponly=True,
        secure=_WEB_SESSION_COOKIE_SECURE,
        samesite="Lax",
        path="/",
    )


def _resolve_visualizer_access() -> tuple[bool, str, str]:
    if not _VISUALIZER_ENABLED:
        return False, "disabled", ""
    if _VISUALIZER_SHARED_TOKEN and _has_valid_visualizer_shared_token():
        return True, "token", ""
    if NEXUS_AUTH_ENABLED:
        session_id, _payload = _resolve_ready_web_session()
        if session_id:
            return True, "session", session_id
        return False, "login-required", ""
    if _VISUALIZER_SHARED_TOKEN:
        return False, "token-required", ""
    return False, "unconfigured", ""


def _visualizer_login_redirect(next_path: str = "/visualizer"):
    safe_next = quote(_safe_next_path(next_path), safe="/")
    return redirect(f"/?next={safe_next}", code=302)


def _render_login_page(*, message_html: str = "", session_hint: str = ""):
    providers = _configured_oauth_providers()
    preferred_provider = _default_oauth_provider(providers) or "github"
    session_value = html.escape(str(session_hint or "").strip(), quote=True)
    ordered_providers = (
        providers
        if preferred_provider == "github"
        else sorted(providers, key=lambda item: (item != "gitlab", item))
    )
    provider_options = "".join(
        f'<option value="{name}">{name.title()}</option>' for name in ordered_providers
    )
    provider_disabled_attr = "" if ordered_providers else " disabled"
    submit_disabled_attr = "" if ordered_providers else " disabled"
    missing_provider_note = (
        ""
        if ordered_providers
        else (
            "<p><small>No OAuth providers are fully configured yet. "
            "Set both client ID and client secret for GitHub and/or GitLab.</small></p>"
        )
    )
    if not provider_options:
        provider_options = '<option value="">No OAuth providers configured</option>'
    body = f"""
<p>Authenticate using the same OAuth onboarding used by Telegram/Discord <code>/login</code>.</p>
{message_html}
<form method="get" action="/auth/start" autocomplete="off">
  <label for="session"><strong>Session Reference</strong></label>
  <input id="session" name="session" type="text" value="{session_value}" placeholder="Paste the session reference from /login" spellcheck="false" autocapitalize="off" autocorrect="off" required />
  <label for="provider"><strong>Provider</strong></label>
  <select id="provider" name="provider" style="width:100%; padding:0.6rem; border-radius:8px; border:1px solid #94a3b8;"{provider_disabled_attr}>
    {provider_options}
  </select>
  <button type="submit" class="form-submit"{submit_disabled_attr}>Continue Login</button>
</form>
{missing_provider_note}
<p><small>Tip: run <code>/login</code> in Telegram or Discord, then paste the session reference shown in chat.</small></p>
"""
    return _render_auth_message("Nexus Login", body, status_code=200)


def _render_visualizer_token_page(*, message_html: str = "", next_path: str = "/visualizer"):
    safe_next = html.escape(_safe_next_path(next_path), quote=True)
    body = f"""
<p>Visualizer access requires the shared token configured in <code>NEXUS_VISUALIZER_SHARED_TOKEN</code>.</p>
{message_html}
<form method="post" action="/visualizer/access" autocomplete="off">
  <input type="hidden" name="next" value="{safe_next}" />
  <label for="token"><strong>Visualizer Token</strong></label>
  <input id="token" name="token" type="password" placeholder="Paste shared token" spellcheck="false" autocapitalize="off" autocorrect="off" required />
  <button type="submit" class="form-submit">Open Visualizer</button>
</form>
<p><small>Alternative: send header <code>{_VISUALIZER_SHARED_TOKEN_HEADER}</code> or <code>Authorization: Bearer ...</code>.</small></p>
"""
    return _render_auth_message("Visualizer Access", body, status_code=200)


def _collect_visualizer_snapshot() -> list[dict]:
    """Return a best-effort snapshot of mapped workflows for visualizer bootstrap."""
    try:
        from nexus.core.integrations.workflow_state_factory import get_workflow_state
        from nexus.core.mermaid_render_service import build_mermaid_diagram
        from nexus.core.orchestration.plugin_runtime import get_workflow_state_plugin

        workflow_state = get_workflow_state()
        mappings = workflow_state.load_all_mappings() or {}
        if not isinstance(mappings, dict) or not mappings:
            return []

        workflow_plugin = get_workflow_state_plugin(
            storage_dir=NEXUS_CORE_STORAGE_DIR,
            storage_type=("postgres" if NEXUS_WORKFLOW_BACKEND == "postgres" else "file"),
            storage_config=(
                {"connection_string": NEXUS_STORAGE_DSN}
                if NEXUS_WORKFLOW_BACKEND == "postgres" and NEXUS_STORAGE_DSN
                else {}
            ),
            issue_to_workflow_id=lambda n: workflow_state.get_workflow_id(n),
            clear_pending_approval=lambda n: workflow_state.clear_pending_approval(n),
            cache_key="workflow:state-engine:visualizer-snapshot",
        )

        async def _load() -> list[dict]:
            records: list[dict] = []
            for issue_num, workflow_id in sorted(mappings.items(), key=lambda kv: str(kv[0])):
                try:
                    status = await workflow_plugin.get_workflow_status(str(issue_num))
                except Exception:
                    status = None

                records.append(
                    {
                        "issue": str(issue_num),
                        "workflow_id": str(workflow_id),
                        "status": status or {},
                        "diagram": (
                            build_mermaid_diagram(
                                list((status or {}).get("steps") or []),
                                str(issue_num),
                            )
                            if isinstance(status, dict) and status.get("steps")
                            else ""
                        ),
                    }
                )
            return records

        return asyncio.run(_load())
    except Exception as exc:
        logger.warning("Failed to collect visualizer snapshot: %s", exc)
        return []


# Register SocketIO emitter with HostStateManager for real-time transition broadcasting
try:
    from nexus.core.state_manager import set_socketio_emitter

    set_socketio_emitter(lambda event, data: socketio.emit(event, data, namespace="/visualizer"))
    logger.info("✅ SocketIO emitter registered with HostStateManager")
except Exception as _e:
    logger.warning(f"⚠️ Could not register SocketIO emitter: {_e}")


@socketio.on("connect", namespace="/visualizer")
def _visualizer_socket_connect():
    allowed, mode, _session_id = _resolve_visualizer_access()
    if not allowed:
        logger.warning("Visualizer Socket.IO rejected: access mode=%s", mode)
        return False
    logger.info("Visualizer Socket.IO client connected")


@socketio.on("disconnect", namespace="/visualizer")
def _visualizer_socket_disconnect():
    logger.info("Visualizer Socket.IO client disconnected")


def _get_webhook_policy():
    """Get framework webhook policy plugin."""
    return get_webhook_policy_plugin(cache_key="git-webhook-policy:webhook")


def _repo_to_project_key(repo_name: str) -> str:
    """Best-effort mapping from repository full_name to configured project key."""
    policy = _get_webhook_policy()
    return policy.resolve_project_key(
        repo_name,
        PROJECT_CONFIG,
        default_project=get_default_project(),
    )


def _effective_review_mode(repo_name: str) -> str:
    """Resolve effective merge review mode for a repo.

    Returns one of: manual, auto.
    """
    policy = _get_webhook_policy()
    return policy.resolve_review_mode(repo_name, PROJECT_CONFIG, default_mode="manual")


def _resolve_git_dir_for_repo(repo_name: str) -> str | None:
    project_key = _repo_to_project_key(repo_name)
    project_cfg = PROJECT_CONFIG.get(project_key, {})
    if not isinstance(project_cfg, dict):
        return None

    workspace_rel = str(project_cfg.get("workspace", "") or "").strip()
    if not workspace_rel:
        return None

    workspace_abs = os.path.join(BASE_DIR, workspace_rel)
    repo_basename = str(repo_name or "").strip().split("/")[-1]
    if not repo_basename:
        return None

    # Either workspace is the repo root, or repo lives under workspace/{repo}.
    if os.path.isdir(os.path.join(workspace_abs, ".git")):
        if os.path.basename(workspace_abs.rstrip(os.sep)) == repo_basename:
            return workspace_abs

    candidate = os.path.join(workspace_abs, repo_basename)
    if os.path.isdir(os.path.join(candidate, ".git")):
        return candidate
    return None


def _cleanup_worktree_for_issue(repo_name: str, issue_number: str) -> bool:
    git_dir = _resolve_git_dir_for_repo(repo_name)
    if not git_dir:
        logger.info(
            "Skipping webhook worktree cleanup for issue #%s: could not resolve git dir for %s",
            issue_number,
            repo_name,
        )
        return False

    return bool(
        WorkspaceManager.cleanup_worktree_safe(
            base_repo_path=git_dir,
            issue_number=str(issue_number),
            is_issue_agent_running=lambda value: is_issue_process_running(
                value, cache_key="runtime-ops:webhook"
            ),
            require_clean=True,
        )
    )


def _close_issue_for_pr_merge(repo_name: str, issue_number: str) -> bool:
    project_key = _repo_to_project_key(repo_name)
    try:
        workflow_plugin = _get_runtime_workflow_plugin()
        workflow_status = None
        if workflow_plugin and hasattr(workflow_plugin, "get_workflow_status"):
            workflow_status = asyncio.run(workflow_plugin.get_workflow_status(str(issue_number)))
        allowed, reason = evaluate_issue_close_for_pr_merge(workflow_status)
        if not allowed:
            logger.info(
                "Skipping webhook PR-merge issue close for issue #%s in %s: %s",
                issue_number,
                repo_name,
                reason,
            )
            return False

        platform = get_git_platform(repo_name, project_name=project_key)
        return bool(
            asyncio.run(
                platform.close_issue(
                    str(issue_number),
                    comment=f"Closed automatically after PR/MR merge in `{repo_name}`.",
                )
            )
        )
    except Exception as exc:
        logger.warning(
            "Failed webhook PR-merge issue close for issue #%s in %s: %s",
            issue_number,
            repo_name,
            exc,
        )
        return False


def _notify_lifecycle(message: str, *, dedup_key: str | None = None) -> bool:
    """Send lifecycle notification via abstract notifier, fallback to Telegram alert."""
    if send_notification(message):
        return True
    return emit_alert(
        message,
        severity="info",
        source="webhook_server",
        dedup_key=dedup_key,
    )


def _get_runtime_workflow_plugin():
    """Build workflow-state plugin for webhook-triggered manual resets."""
    from nexus.core.integrations.workflow_state_factory import get_workflow_state

    workflow_state = get_workflow_state()
    return get_workflow_state_plugin(
        storage_dir=NEXUS_CORE_STORAGE_DIR,
        storage_type=("postgres" if NEXUS_WORKFLOW_BACKEND == "postgres" else "file"),
        storage_config=(
            {"connection_string": NEXUS_STORAGE_DSN}
            if NEXUS_WORKFLOW_BACKEND == "postgres" and NEXUS_STORAGE_DSN
            else {}
        ),
        issue_to_workflow_id=lambda n: workflow_state.get_workflow_id(n),
        issue_to_workflow_map_setter=lambda n, w: workflow_state.map_issue(n, w),
        workflow_definition_path_resolver=get_workflow_definition_path,
        clear_pending_approval=lambda n: workflow_state.clear_pending_approval(n),
        cache_key="workflow:state-engine:webhook-runtime",
    )


def _reset_workflow_to_agent(issue_number: str, agent_ref: str) -> bool:
    """Realign workflow RUNNING step before manual webhook-driven launch."""
    try:
        workflow_plugin = _get_runtime_workflow_plugin()
    except Exception as exc:
        logger.warning(
            "Could not initialize workflow plugin for manual override reset issue #%s -> %s: %s",
            issue_number,
            agent_ref,
            exc,
        )
        return False

    try:
        return bool(
            asyncio.run(workflow_plugin.reset_to_agent_for_issue(str(issue_number), str(agent_ref)))
        )
    except Exception as exc:
        logger.warning(
            "Manual override reset failed for issue #%s -> %s: %s",
            issue_number,
            agent_ref,
            exc,
        )
        return False


def verify_signature(payload_body, signature_header, gitlab_token_header=None):
    """Verify Git webhook signature/token."""
    policy = _get_webhook_policy()
    verified = bool(
        policy.verify_signature(payload_body, signature_header, WEBHOOK_SECRET, gitlab_token_header)
    )
    if not WEBHOOK_SECRET and verified:
        logger.warning("⚠️ WEBHOOK_SECRET not configured - accepting all requests (INSECURE!)")
    if not verified:
        logger.error("❌ Signature verification failed")
    return verified


def handle_issue_opened(payload, event):
    """
    Handle issues.opened events.

    Converts Git issue into an inbox task (Postgres queue or filesystem inbox)
    for the inbox processor to route to the appropriate agent based on type.

    Agent types (abstract roles):
    - triage: Initial issue analysis and classification
    - escalation: High-priority/urgent issues (escalate to senior agent)
    - debug: Bug analysis and root cause

    The actual agent implementing each type is defined in the workflow YAML.
    """
    policy = _get_webhook_policy()
    return _handle_issue_opened_event(
        event=event,
        logger=logger,
        policy=policy,
        notify_lifecycle=_notify_lifecycle,
        emit_alert=emit_alert,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
        project_repos=_project_repos,
        get_repos=get_repos,
        get_tasks_active_dir=get_tasks_active_dir,
        get_inbox_dir=get_inbox_dir,
        get_inbox_storage_backend=get_inbox_storage_backend,
        enqueue_task=enqueue_task,
        cleanup_worktree_for_issue=_cleanup_worktree_for_issue,
    )


def handle_issue_comment(payload, event):
    """
    Handle issue_comment events.

    Detects workflow completion markers in comments and chains to next agent.
    """
    policy = _get_webhook_policy()
    from nexus.core.workflow_runtime.workflow_pr_monitor_service import check_and_notify_pr

    return _handle_issue_comment_event(
        event=event,
        logger=logger,
        policy=policy,
        processed_events=processed_events,
        launch_next_agent=launch_next_agent,
        check_and_notify_pr=check_and_notify_pr,
        reset_workflow_to_agent=_reset_workflow_to_agent,
    )


def handle_pull_request(payload, event):
    """Handle pull_request events (opened, synchronized, etc.)."""
    policy = _get_webhook_policy()
    return _handle_pull_request_event(
        event=event,
        logger=logger,
        policy=policy,
        notify_lifecycle=_notify_lifecycle,
        effective_review_mode=_effective_review_mode,
        launch_next_agent=launch_next_agent,
        cleanup_worktree_for_issue=_cleanup_worktree_for_issue,
        close_issue_for_issue=_close_issue_for_pr_merge,
    )


def handle_pull_request_review(payload, event):
    """Handle pull_request_review events."""
    return _handle_pull_request_review_event(event=event, logger=logger)


def _render_auth_message(title: str, body: str, *, status_code: int = 200):
    html = f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>
      body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; color: #0f172a; }}
      main {{ max-width: 720px; margin: 0 auto; }}
      .card {{ border: 1px solid #cbd5e1; border-radius: 12px; padding: 1rem 1.2rem; background: #f8fafc; }}
      code {{ background: #e2e8f0; padding: 0.1rem 0.3rem; border-radius: 4px; }}
      input[type=text], input[type=password] {{ width: 100%; padding: 0.6rem; border-radius: 8px; border: 1px solid #94a3b8; box-sizing: border-box; }}
      .field-row {{ display: grid; grid-template-columns: 1fr 110px; gap: 0.6rem; align-items: center; }}
      .field-action-spacer {{ visibility: hidden; }}
      button {{ background: #0f766e; color: white; border: none; padding: 0.7rem 1rem; border-radius: 8px; cursor: pointer; }}
      .inline-reset {{ margin-top: 0; width: 100%; }}
      .form-submit {{ margin-top: 0.8rem; }}
      @media (max-width: 720px) {{
        .field-row {{ grid-template-columns: 1fr; }}
      }}
      small {{ color: #475569; }}
    </style>
  </head>
  <body>
    <main>
      <h1>{title}</h1>
      <div class="card">{body}</div>
    </main>
  </body>
</html>"""
    return html, status_code, {"Content-Type": "text/html; charset=utf-8"}


def _render_ai_key_form(
    *,
    session_id: str,
    show_api_key_inputs: bool,
    copilot_checked: bool,
    show_copilot_option: bool,
    copilot_token_set: bool,
    codex_key_set: bool,
    gemini_key_set: bool,
    claude_key_set: bool,
    codex_account_enabled: bool,
    gemini_account_enabled: bool,
    claude_account_enabled: bool,
    copilot_account_enabled: bool,
    existing_keys_note: str,
) -> str:
    checked_attr = " checked" if copilot_checked else ""
    codex_account_attr = " checked" if codex_account_enabled else ""
    gemini_account_attr = " checked" if gemini_account_enabled else ""
    claude_account_attr = " checked" if claude_account_enabled else ""
    copilot_account_attr = " checked" if copilot_account_enabled else ""
    api_key_mode_notice = ""
    if show_api_key_inputs:
        codex_field = """
  <label for="codex_api_key"><strong>Codex/OpenAI API Key (optional)</strong></label>
  <div class="field-row">
    <input id="codex_api_key" name="codex_api_key" type="password" placeholder="sk-..." autocomplete="new-password" spellcheck="false" autocapitalize="off" autocorrect="off" />
    <button type="button" class="field-action-spacer" disabled>Reset</button>
  </div>
""" if not codex_key_set else """
  <label><strong>Codex/OpenAI API Key (optional)</strong></label>
  <div id="codex_api_key_saved" class="field-row">
    <input type="text" value="********" disabled />
    <button type="button" class="inline-reset" onclick="enableProviderField('codex_api_key')">Reset</button>
  </div>
  <div id="codex_api_key_editor" style="display:none;">
    <div class="field-row">
      <input id="codex_api_key" name="codex_api_key" type="password" placeholder="Leave empty to clear, or paste new key" autocomplete="new-password" spellcheck="false" autocapitalize="off" autocorrect="off" disabled />
      <button type="button" class="field-action-spacer" disabled>Reset</button>
    </div>
    <small>Leave empty and save to clear this key.</small>
  </div>
"""
        gemini_field = """
  <label for="gemini_api_key"><strong>Gemini API Key (optional)</strong></label>
  <div class="field-row">
    <input id="gemini_api_key" name="gemini_api_key" type="password" placeholder="AIza..." autocomplete="new-password" spellcheck="false" autocapitalize="off" autocorrect="off" />
    <button type="button" class="field-action-spacer" disabled>Reset</button>
  </div>
""" if not gemini_key_set else """
  <label><strong>Gemini API Key (optional)</strong></label>
  <div id="gemini_api_key_saved" class="field-row">
    <input type="text" value="********" disabled />
    <button type="button" class="inline-reset" onclick="enableProviderField('gemini_api_key')">Reset</button>
  </div>
  <div id="gemini_api_key_editor" style="display:none;">
    <div class="field-row">
      <input id="gemini_api_key" name="gemini_api_key" type="password" placeholder="Leave empty to clear, or paste new key" autocomplete="new-password" spellcheck="false" autocapitalize="off" autocorrect="off" disabled />
      <button type="button" class="field-action-spacer" disabled>Reset</button>
    </div>
    <small>Leave empty and save to clear this key.</small>
  </div>
"""
        claude_field = """
  <label for="claude_api_key"><strong>Claude API Key (optional)</strong></label>
  <div class="field-row">
    <input id="claude_api_key" name="claude_api_key" type="password" placeholder="sk-ant-..." autocomplete="new-password" spellcheck="false" autocapitalize="off" autocorrect="off" />
    <button type="button" class="field-action-spacer" disabled>Reset</button>
  </div>
""" if not claude_key_set else """
  <label><strong>Claude API Key (optional)</strong></label>
  <div id="claude_api_key_saved" class="field-row">
    <input type="text" value="********" disabled />
    <button type="button" class="inline-reset" onclick="enableProviderField('claude_api_key')">Reset</button>
  </div>
  <div id="claude_api_key_editor" style="display:none;">
    <div class="field-row">
      <input id="claude_api_key" name="claude_api_key" type="password" placeholder="Leave empty to clear, or paste new key" autocomplete="new-password" spellcheck="false" autocapitalize="off" autocorrect="off" disabled />
      <button type="button" class="field-action-spacer" disabled>Reset</button>
    </div>
    <small>Leave empty and save to clear this key.</small>
  </div>
"""
        copilot_token_field = """
  <label for="copilot_github_token"><strong>Copilot Token (optional)</strong></label>
  <div class="field-row">
    <input id="copilot_github_token" name="copilot_github_token" type="password" placeholder="ghp_..." autocomplete="new-password" spellcheck="false" autocapitalize="off" autocorrect="off" />
    <button type="button" class="field-action-spacer" disabled>Reset</button>
  </div>
""" if not copilot_token_set else """
  <label><strong>Copilot Token (optional)</strong></label>
  <div id="copilot_github_token_saved" class="field-row">
    <input type="text" value="********" disabled />
    <button type="button" class="inline-reset" onclick="enableProviderField('copilot_github_token')">Reset</button>
  </div>
  <div id="copilot_github_token_editor" style="display:none;">
    <div class="field-row">
      <input id="copilot_github_token" name="copilot_github_token" type="password" placeholder="Leave empty to clear, or paste new token" autocomplete="new-password" spellcheck="false" autocapitalize="off" autocorrect="off" disabled />
      <button type="button" class="field-action-spacer" disabled>Reset</button>
    </div>
    <small>Leave empty and save to clear this token.</small>
  </div>
"""
    else:
        codex_field = ""
        gemini_field = ""
        claude_field = ""
        copilot_token_field = ""
        api_key_mode_notice = (
            '<p><small>API key/token fields are hidden because '
            '<code>NEXUS_CLI_AUTH_MODE=account</code>.</small></p>'
        )
    account_mode_html = f"""
  <hr />
  <p><strong>Provider Account Login (No API Key)</strong><br /><small>Enable these toggles if you connected provider account login for this workspace.</small></p>
  <div style="margin:0.4rem 0 0.8rem;">
    <button type="button" onclick="startProviderConnect('codex')">Connect Codex Account</button>
    <button type="button" onclick="startProviderConnect('gemini')">Connect Gemini Account</button>
    <button type="button" onclick="startProviderConnect('claude')">Connect Claude Account</button>
    <small id="provider_connect_status" style="display:block; margin-top:0.4rem;">This opens a browser-based device flow and saves the result to your session.</small>
  </div>
  <div id="gemini_code_section" style="display:none; margin:0.45rem 0 0.8rem;">
    <label for="gemini_authorization_code"><small>Gemini authorization code</small></label>
    <input id="gemini_authorization_code" type="text" placeholder="Paste the code shown after Google sign-in" spellcheck="false" autocapitalize="off" autocorrect="off" />
    <button type="button" style="margin-top:0.45rem; margin-bottom:0.25rem;" onclick="submitProviderAuthCode('gemini')">Submit Gemini Authorization Code</button>
    <small style="display:block;">If prompted with "Enter the authorization code", paste it here.</small>
  </div>
  <div id="gemini_callback_section" style="display:none; margin:0.2rem 0 0.8rem;">
    <label for="gemini_callback_url"><small>Gemini localhost callback URL (fallback)</small></label>
    <input id="gemini_callback_url" type="text" placeholder="http://localhost:41725/oauth2callback?..." spellcheck="false" autocapitalize="off" autocorrect="off" />
    <button type="button" style="margin-top:0.45rem; margin-bottom:0.25rem;" onclick="relayProviderCallback('gemini')">Submit Gemini Callback URL</button>
    <small style="display:block;">Use this only if Gemini redirects to localhost and login remains pending.</small>
  </div>
  <label style="display:block; margin-top:0.4rem;">
    <input type="checkbox" name="use_codex_account" value="1"{codex_account_attr} />
    Use Codex account login
  </label>
  <label style="display:block; margin-top:0.4rem;">
    <input type="checkbox" name="use_gemini_account" value="1"{gemini_account_attr} />
    Use Gemini account login
  </label>
  <label style="display:block; margin-top:0.4rem;">
    <input type="checkbox" name="use_claude_account" value="1"{claude_account_attr} />
    Use Claude account login
  </label>
"""
    copilot_html = (
        f"""
  <label style="display:block; margin-top:0.8rem;">
    <input type="checkbox" name="use_copilot" value="1"{checked_attr} />
    Use Copilot with a linked GitHub account (no separate Copilot API key)
  </label>
  <label style="display:block; margin-top:0.4rem;">
    <input type="checkbox" name="use_copilot_account" value="1"{copilot_account_attr} />
    Use Copilot account mode (requires linked GitHub OAuth)
  </label>
"""
        if show_copilot_option
        else ""
    )
    return f"""
<form method="post" action="/auth/ai-keys" autocomplete="off">
  <input type="hidden" name="session_id" value="{session_id}" />
  {codex_field}
  {gemini_field}
  {claude_field}
  {copilot_token_field}
  {api_key_mode_notice}
  {account_mode_html}
  {copilot_html}
  <button type="submit" class="form-submit">Save Setup</button>
  <p><small>{existing_keys_note}</small></p>
</form>
<script>
function enableProviderField(fieldName) {{
  var saved = document.getElementById(fieldName + "_saved");
  var editor = document.getElementById(fieldName + "_editor");
  if (saved) saved.style.display = "none";
  if (editor) {{
    editor.style.display = "block";
    var input = editor.querySelector('input[name="' + fieldName + '"]');
    if (input) input.disabled = false;
  }}
}}
var providerConnectPoll = null;
var providerConnectOpenedUrls = Object.create(null);
var providerConnectPopups = Object.create(null);
function providerDisplayName(provider) {{
  var key = String(provider || "").trim().toLowerCase();
  if (key === "gemini") return "Gemini";
  if (key === "claude") return "Claude";
  return "Codex";
}}
function setProviderAccountToggle(provider, enabled) {{
  var key = String(provider || "").trim().toLowerCase();
  var fieldName = "";
  if (key === "gemini") fieldName = "use_gemini_account";
  else if (key === "claude") fieldName = "use_claude_account";
  else if (key === "codex") fieldName = "use_codex_account";
  if (!fieldName) return;
  var input = document.querySelector('input[name="' + fieldName + '"]');
  if (input) input.checked = !!enabled;
}}
function _escapeHtml(value) {{
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}}
function setProviderConnectStatus(text) {{
  var el = document.getElementById("provider_connect_status");
  if (el) el.textContent = String(text || "");
}}
function setProviderConnectStatusWithLink(message, verifyUrl, userCode) {{
  var el = document.getElementById("provider_connect_status");
  if (!el) return;
  var url = String(verifyUrl || "").trim();
  if (!url) {{
    setProviderConnectStatus(message);
    return;
  }}
  var safeMessage = _escapeHtml(message || "Open the verification link and confirm login.");
  var safeUrl = _escapeHtml(url);
  var safeCode = _escapeHtml(String(userCode || "").trim());
  var codeHtml = safeCode ? ("<br />Code: <code>" + safeCode + "</code>") : "";
  el.innerHTML =
    safeMessage
    + ' <a href="' + safeUrl + '" target="_blank" rel="noopener noreferrer">Open verification link</a>.'
    + codeHtml;
}}
function _isVisible(el) {{
  return !!el && String(el.style.display || "") !== "none";
}}
function setGeminiProviderSections(opts) {{
  var showCode = !!(opts && opts.showCode);
  var showCallback = !!(opts && opts.showCallback);
  var codeSection = document.getElementById("gemini_code_section");
  var callbackSection = document.getElementById("gemini_callback_section");
  if (codeSection) codeSection.style.display = showCode ? "block" : "none";
  if (callbackSection) callbackSection.style.display = showCallback ? "block" : "none";
}}
function _providerConnectPendingUrl(provider, sessionId) {{
  var key = encodeURIComponent(String(provider || "").trim().toLowerCase() || "codex");
  var sid = encodeURIComponent(String(sessionId || "").trim());
  return "/auth/provider-connect/pending?provider=" + key + "&session_id=" + sid;
}}
function primeProviderPopup(provider, sessionId) {{
  try {{
    var existing = providerConnectPopups[provider];
    if (existing && !existing.closed) return;
    var pendingUrl = _providerConnectPendingUrl(provider, sessionId);
    var popup = window.open(pendingUrl, "_blank");
    if (!popup) return;
    providerConnectPopups[provider] = popup;
  }} catch (err) {{
    return;
  }}
}}
function maybeOpenProviderVerifyUrl(provider, verifyUrl) {{
  var url = String(verifyUrl || "").trim();
  if (!url) return false;
  var dedupeKey = String(provider || "") + "::" + url;
  if (providerConnectOpenedUrls[dedupeKey]) return true;
  providerConnectOpenedUrls[dedupeKey] = true;
  try {{
    var popup = providerConnectPopups[provider];
    if (popup && !popup.closed) {{
      popup.location.href = url;
      return true;
    }}
  }} catch (err) {{
    // Fall through and try regular open.
  }}
  try {{
    var opened = window.open(url, "_blank", "noopener,noreferrer");
    return !!opened;
  }} catch (err) {{
    return false;
  }}
}}
window.addEventListener("message", function(event) {{
  try {{
    if (!event || event.origin !== window.location.origin) return;
    var payload = event.data;
    if (!payload || payload.source !== "nexus-provider-connect") return;
    var provider = String(payload.provider || "").trim().toLowerCase();
    var state = String(payload.state || "").trim().toLowerCase();
    var message = String(payload.message || "").trim();
    if (payload.verify_url) {{
      maybeOpenProviderVerifyUrl(provider, String(payload.verify_url));
    }}
    if (state === "connected") {{
      setProviderAccountToggle(provider, true);
      if (provider === "gemini") {{
        setGeminiProviderSections({{ showCode: false, showCallback: false }});
      }}
      setProviderConnectStatus(providerDisplayName(provider) + " account connected. Save Setup to keep the toggle.");
      if (providerConnectPoll) {{
        clearTimeout(providerConnectPoll);
        providerConnectPoll = null;
      }}
      var popup = providerConnectPopups[provider];
      if (popup && !popup.closed) {{
        try {{ popup.close(); }} catch (err) {{ /* ignore */ }}
      }}
      return;
    }}
    if (message) {{
      setProviderConnectStatus(message);
    }}
  }} catch (err) {{
    return;
  }}
}});
async function pollProviderConnect(provider, sessionId) {{
  try {{
    var response = await fetch("/auth/provider-connect/status?provider=" + encodeURIComponent(provider) + "&session_id=" + encodeURIComponent(sessionId));
    var payload = await response.json();
    var state = String(payload.state || "");
    if (String(provider || "").toLowerCase() === "gemini") {{
      var callbackInput = document.getElementById("gemini_callback_url");
      if (payload.callback_url_hint && callbackInput && !String(callbackInput.value || "").trim()) {{
        callbackInput.value = String(payload.callback_url_hint);
      }}
      var callbackVisible = _isVisible(document.getElementById("gemini_callback_section"));
      var messageLower = String(payload.message || "").toLowerCase();
      var shouldShowCallback = callbackVisible
        || !!payload.callback_url_hint
        || messageLower.indexOf("localhost") >= 0;
      setGeminiProviderSections({{
        showCode: state !== "connected",
        showCallback: state !== "connected" && shouldShowCallback
      }});
    }}
    if (payload.verify_url) {{
      var openedFromPoll = maybeOpenProviderVerifyUrl(provider, payload.verify_url);
      setProviderConnectStatusWithLink(
        openedFromPoll
          ? "Verification tab opened."
          : "If no browser tab opened automatically, use this link:",
        payload.verify_url,
        payload.user_code
      );
    }} else if (payload.message) {{
      setProviderConnectStatus(String(payload.message));
    }}
    if (state === "connected") {{
      setProviderAccountToggle(provider, true);
      setProviderConnectStatus(providerDisplayName(provider) + " account connected. Save Setup to keep the toggle.");
      var popup = providerConnectPopups[provider];
      if (popup && !popup.closed) {{
        try {{ popup.close(); }} catch (err) {{ /* ignore */ }}
      }}
      if (providerConnectPoll) {{
        clearTimeout(providerConnectPoll);
        providerConnectPoll = null;
      }}
      return;
    }}
    if (
      state === "failed"
      || state === "connected_but_not_saved"
      || state === "rate_limited"
      || state === "interactive_required"
      || state === "unsupported_provider"
      || state === "invalid_session"
    ) {{
      if (providerConnectPoll) {{
        clearTimeout(providerConnectPoll);
        providerConnectPoll = null;
      }}
      return;
    }}
    providerConnectPoll = setTimeout(function() {{ pollProviderConnect(provider, sessionId); }}, 1500);
  }} catch (err) {{
    setProviderConnectStatus("Could not poll provider-connect status. Retry.");
  }}
}}
async function startProviderConnect(provider) {{
  var sessionInput = document.querySelector('input[name="session_id"]');
  var sessionId = sessionInput ? String(sessionInput.value || "").trim() : "";
  if (!sessionId) {{
    setProviderConnectStatus("Missing session id.");
    return;
  }}
  var providerKey = String(provider || "").toLowerCase();
  if (providerKey === "codex" || providerKey === "gemini" || providerKey === "claude") {{
    primeProviderPopup(provider, sessionId);
  }}
  if (providerKey === "gemini") {{
    setGeminiProviderSections({{ showCode: true, showCallback: false }});
  }} else {{
    setGeminiProviderSections({{ showCode: false, showCallback: false }});
  }}
  setProviderConnectStatus("Starting " + providerDisplayName(provider) + " account connection...");
  try {{
    var response = await fetch("/auth/provider-connect/start", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ provider: provider, session_id: sessionId }})
    }});
    var payload = await response.json();
    if (!response.ok) {{
      setProviderConnectStatus(String(payload.message || "Could not start provider connect."));
      return;
    }}
    if (payload.verify_url) {{
      var openedFromStart = maybeOpenProviderVerifyUrl(provider, payload.verify_url);
      setProviderConnectStatusWithLink(
        openedFromStart
          ? "Verification tab opened."
          : "If no browser tab opened automatically, use this link:",
        payload.verify_url,
        payload.user_code
      );
    }} else if (payload.message) {{
      setProviderConnectStatus(String(payload.message));
    }}
    if (providerConnectPoll) clearTimeout(providerConnectPoll);
    var initialPollDelay = String(provider || "").toLowerCase() === "gemini" ? 250 : 1000;
    providerConnectPoll = setTimeout(function() {{ pollProviderConnect(provider, sessionId); }}, initialPollDelay);
  }} catch (err) {{
    setProviderConnectStatus("Could not start provider connect.");
  }}
}}
async function relayProviderCallback(provider) {{
  var sessionInput = document.querySelector('input[name="session_id"]');
  var sessionId = sessionInput ? String(sessionInput.value || "").trim() : "";
  if (!sessionId) {{
    setProviderConnectStatus("Missing session id.");
    return;
  }}
  var callbackInput = document.getElementById("gemini_callback_url");
  var callbackUrl = callbackInput ? String(callbackInput.value || "").trim() : "";
  if (!callbackUrl) {{
    setProviderConnectStatus("Paste the Gemini localhost callback URL first.");
    return;
  }}
  setProviderConnectStatus("Relaying callback URL to Gemini CLI...");
  try {{
    var response = await fetch("/auth/provider-connect/relay-callback", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        provider: provider,
        session_id: sessionId,
        callback_url: callbackUrl
      }})
    }});
    var payload = await response.json();
    if (!response.ok) {{
      setProviderConnectStatus(String(payload.message || "Could not relay callback URL."));
      return;
    }}
    if (payload.message) {{
      setProviderConnectStatus(String(payload.message));
    }}
    if (providerConnectPoll) clearTimeout(providerConnectPoll);
    providerConnectPoll = setTimeout(function() {{ pollProviderConnect(provider, sessionId); }}, 1000);
  }} catch (err) {{
    setProviderConnectStatus("Could not relay callback URL.");
  }}
}}
async function submitProviderAuthCode(provider) {{
  var sessionInput = document.querySelector('input[name="session_id"]');
  var sessionId = sessionInput ? String(sessionInput.value || "").trim() : "";
  if (!sessionId) {{
    setProviderConnectStatus("Missing session id.");
    return;
  }}
  var codeInput = document.getElementById("gemini_authorization_code");
  var authorizationCode = codeInput ? String(codeInput.value || "").trim() : "";
  if (!authorizationCode) {{
    setProviderConnectStatus("Paste the Gemini authorization code first.");
    return;
  }}
  setProviderConnectStatus("Submitting authorization code to Gemini CLI...");
  try {{
    var response = await fetch("/auth/provider-connect/submit-code", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        provider: provider,
        session_id: sessionId,
        authorization_code: authorizationCode
      }})
    }});
    var payload = await response.json();
    if (!response.ok) {{
      setProviderConnectStatus(String(payload.message || "Could not submit authorization code."));
      return;
    }}
    if (payload.message) {{
      setProviderConnectStatus(String(payload.message));
    }}
    if (providerConnectPoll) clearTimeout(providerConnectPoll);
    providerConnectPoll = setTimeout(function() {{ pollProviderConnect(provider, sessionId); }}, 1000);
  }} catch (err) {{
    setProviderConnectStatus("Could not submit authorization code.");
  }}
}}
</script>
"""


def _telegram_edit_message(*, chat_id: str, message_id: str, text: str) -> None:
    token = str(TELEGRAM_TOKEN or "").strip()
    if not token:
        return
    requests.post(
        f"https://api.telegram.org/bot{token}/editMessageText",
        json={
            "chat_id": chat_id,
            "message_id": int(message_id),
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=10,
    )


def _discord_edit_message(*, chat_id: str, message_id: str, text: str) -> None:
    token = str(DISCORD_TOKEN or "").strip()
    if not token:
        return
    requests.patch(
        f"https://discord.com/api/v10/channels/{quote(str(chat_id), safe='')}/messages/{quote(str(message_id), safe='')}",
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        },
        json={"content": text},
        timeout=10,
    )


def _notify_onboarding_message(
    *,
    session_id: str,
    text: str,
) -> None:
    resolved_session_id = _svc_resolve_login_session_id(session_id)
    if not resolved_session_id:
        return
    try:
        record = _svc_get_auth_session(str(resolved_session_id))
    except Exception as exc:
        logger.warning(
            "Failed to load auth session %s for onboarding message update: %s",
            resolved_session_id,
            exc,
        )
        return
    if not record:
        return

    platform = str(record.chat_platform or "").strip().lower()
    chat_id = str(record.chat_id or "").strip()
    message_id = str(record.onboarding_message_id or "").strip()
    if not platform or not chat_id or not message_id:
        return

    try:
        if platform == "telegram":
            _telegram_edit_message(chat_id=chat_id, message_id=message_id, text=text)
            return
        if platform == "discord":
            _discord_edit_message(chat_id=chat_id, message_id=message_id, text=text)
            return
    except Exception as exc:
        logger.warning(
            "Failed to update onboarding message for session %s (%s:%s/%s): %s",
            resolved_session_id,
            platform,
            chat_id,
            message_id,
            exc,
        )


@app.route("/auth/start", methods=["GET"])
def auth_start():
    if not NEXUS_AUTH_ENABLED:
        return _render_auth_message(
            "Auth Disabled",
            "Auth onboarding is disabled in this environment.",
            status_code=404,
        )
    session_value = str(request.args.get("session", "")).strip()
    if not session_value:
        return _render_auth_message("Invalid Request", "Missing <code>session</code> parameter.", status_code=400)
    resolved_session_id = _svc_resolve_login_session_id(session_value)
    if not resolved_session_id:
        return _render_auth_message("Invalid Request", "Invalid <code>session</code> parameter.", status_code=400)
    configured_providers = _configured_oauth_providers()
    if not configured_providers:
        return _render_auth_message(
            "Login Error",
            (
                "OAuth is not configured. Set both client ID and client secret for "
                "GitHub and/or GitLab."
            ),
            status_code=400,
        )
    provider = str(request.args.get("provider", "")).strip().lower()
    if not provider:
        provider = _default_oauth_provider(configured_providers)
    if provider not in {"github", "gitlab"}:
        return _render_auth_message(
            "Invalid Request",
            "Unsupported <code>provider</code>. Use <code>github</code> or <code>gitlab</code>.",
            status_code=400,
        )
    if provider not in configured_providers:
        return _render_auth_message(
            "Login Error",
            f"{provider.title()} OAuth is not fully configured in this environment.",
            status_code=400,
        )
    try:
        oauth_url, _state = _svc_start_oauth_flow(session_value, provider=provider)
    except Exception as exc:
        _notify_onboarding_message(
            session_id=resolved_session_id,
            text=f"❌ OAuth start failed for {provider.title()}: {exc}\nRun /login to retry.",
        )
        return _render_auth_message("Login Error", f"Failed to start OAuth: <code>{exc}</code>", status_code=400)
    return redirect(oauth_url, code=302)


@app.route("/auth/github/callback", methods=["GET"])
def auth_github_callback():
    if not NEXUS_AUTH_ENABLED:
        return _render_auth_message(
            "Auth Disabled",
            "Auth onboarding is disabled in this environment.",
            status_code=404,
        )
    code = str(request.args.get("code", "")).strip()
    state = str(request.args.get("state", "")).strip()
    oauth_error = str(request.args.get("error", "")).strip()
    if oauth_error:
        oauth_error_detail = str(
            request.args.get("error_description")
            or request.args.get("error_reason")
            or oauth_error
        ).strip()
        if state:
            try:
                session = _svc_get_auth_session_by_state(state)
                session_id = str(getattr(session, "session_id", "") or "").strip()
                if session_id:
                    _notify_onboarding_message(
                        session_id=session_id,
                        text=f"❌ GitHub OAuth failed: {oauth_error_detail}\nRun /login to retry.",
                    )
            except Exception:
                pass
        safe_detail = html.escape(oauth_error_detail, quote=True)
        return _render_auth_message("OAuth Error", f"GitHub returned: <code>{safe_detail}</code>", status_code=400)
    if not code or not state:
        return _render_auth_message(
            "OAuth Error",
            "Missing <code>code</code> or <code>state</code> in callback.",
            status_code=400,
        )
    try:
        result = _svc_complete_github_oauth(code=code, state=state)
    except Exception as exc:
        session_id = ""
        try:
            session = _svc_get_auth_session_by_state(state)
            session_id = str(getattr(session, "session_id", "") or "").strip()
        except Exception:
            session_id = ""
        if session_id:
            _notify_onboarding_message(
                session_id=session_id,
                text=f"❌ GitHub OAuth failed: {exc}\nRun /login to retry.",
            )
        return _render_auth_message("OAuth Error", f"{exc}", status_code=400)

    source_nexus_id = str(result.get("source_nexus_id") or "").strip()
    resolved_nexus_id = str(result.get("nexus_id") or "").strip()
    if source_nexus_id and resolved_nexus_id and source_nexus_id != resolved_nexus_id:
        try:
            get_user_manager().merge_users(resolved_nexus_id, source_nexus_id)
        except Exception as exc:
            logger.warning(
                "Failed to merge UNI users after GitHub OAuth (source=%s, target=%s): %s",
                source_nexus_id,
                resolved_nexus_id,
                exc,
            )

    session_id = str(result.get("session_id") or "").strip()
    session_ref = _svc_format_login_session_ref(session_id)
    grants_count = int(result.get("grants_count") or 0)
    github_login = str(result.get("github_login") or "").strip()
    setup_payload = _svc_get_session_and_setup_status(session_id)
    setup = setup_payload.get("setup") if isinstance(setup_payload, dict) else {}
    has_existing_keys = bool(
        isinstance(setup, dict)
        and (
            setup.get("codex_key_set")
            or setup.get("gemini_key_set")
            or setup.get("claude_key_set")
            or setup.get("codex_account_enabled")
            or setup.get("gemini_account_enabled")
            or setup.get("claude_account_enabled")
            or setup.get("copilot_account_enabled")
        )
    )
    api_keys_allowed = _allow_api_keys_in_web_setup()
    form_body = f"""
<p>GitHub login linked successfully as <strong>{github_login or "unknown"}</strong>.</p>
<p>Project grants resolved: <strong>{grants_count}</strong>.</p>
<p>Session reference: <code>{session_ref or session_id}</code>.</p>
    """ + _render_ai_key_form(
        session_id=session_ref or session_id,
        show_api_key_inputs=api_keys_allowed,
        copilot_checked=True,
        show_copilot_option=True,
        copilot_token_set=bool(isinstance(setup, dict) and setup.get("copilot_token_set")),
        codex_key_set=bool(isinstance(setup, dict) and setup.get("codex_key_set")),
        gemini_key_set=bool(isinstance(setup, dict) and setup.get("gemini_key_set")),
        claude_key_set=bool(isinstance(setup, dict) and setup.get("claude_key_set")),
        codex_account_enabled=bool(isinstance(setup, dict) and setup.get("codex_account_enabled")),
        gemini_account_enabled=bool(isinstance(setup, dict) and setup.get("gemini_account_enabled")),
        claude_account_enabled=bool(isinstance(setup, dict) and setup.get("claude_account_enabled")),
        copilot_account_enabled=bool(isinstance(setup, dict) and setup.get("copilot_account_enabled")),
        existing_keys_note=(
            "All fields are optional. Leave fields blank to keep previously saved values unchanged. "
            + (
                "Existing provider configuration is already saved for this account. "
                if has_existing_keys
                else ""
            )
            + (
                "Use Reset to clear a saved value. If all provider credentials are cleared, "
                "setup may show as not ready until one is added again. Keys/tokens are encrypted at "
                "rest and used only for your own task execution."
                if api_keys_allowed
                else (
                    "CLI auth mode is account-only, so API key/token fields are hidden on this page. "
                    "Enable provider account login toggles and save setup."
                )
            )
        ),
    )
    _notify_onboarding_message(
        session_id=session_id,
        text=(
            "✅ GitHub OAuth linked successfully.\n"
            "Continue in the browser to save provider setup (API keys or account login), then run /setup-status (Discord) or /setup_status (Telegram)."
        ),
    )
    response = make_response(_render_auth_message("Complete Setup", form_body, status_code=200))
    _set_web_session_cookie(response, session_id)
    return response


@app.route("/auth/gitlab/callback", methods=["GET"])
def auth_gitlab_callback():
    if not NEXUS_AUTH_ENABLED:
        return _render_auth_message(
            "Auth Disabled",
            "Auth onboarding is disabled in this environment.",
            status_code=404,
        )
    code = str(request.args.get("code", "")).strip()
    state = str(request.args.get("state", "")).strip()
    oauth_error = str(request.args.get("error", "")).strip()
    if oauth_error:
        oauth_error_detail = str(
            request.args.get("error_description")
            or request.args.get("error_reason")
            or oauth_error
        ).strip()
        if state:
            try:
                session = _svc_get_auth_session_by_state(state)
                session_id = str(getattr(session, "session_id", "") or "").strip()
                if session_id:
                    _notify_onboarding_message(
                        session_id=session_id,
                        text=f"❌ GitLab OAuth failed: {oauth_error_detail}\nRun /login to retry.",
                    )
            except Exception:
                pass
        safe_detail = html.escape(oauth_error_detail, quote=True)
        return _render_auth_message("OAuth Error", f"GitLab returned: <code>{safe_detail}</code>", status_code=400)
    if not code or not state:
        return _render_auth_message(
            "OAuth Error",
            "Missing <code>code</code> or <code>state</code> in callback.",
            status_code=400,
        )
    try:
        result = _svc_complete_gitlab_oauth(code=code, state=state)
    except Exception as exc:
        session_id = ""
        try:
            session = _svc_get_auth_session_by_state(state)
            session_id = str(getattr(session, "session_id", "") or "").strip()
        except Exception:
            session_id = ""
        if session_id:
            _notify_onboarding_message(
                session_id=session_id,
                text=f"❌ GitLab OAuth failed: {exc}\nRun /login to retry.",
            )
        return _render_auth_message("OAuth Error", f"{exc}", status_code=400)

    source_nexus_id = str(result.get("source_nexus_id") or "").strip()
    resolved_nexus_id = str(result.get("nexus_id") or "").strip()
    if source_nexus_id and resolved_nexus_id and source_nexus_id != resolved_nexus_id:
        try:
            get_user_manager().merge_users(resolved_nexus_id, source_nexus_id)
        except Exception as exc:
            logger.warning(
                "Failed to merge UNI users after GitLab OAuth (source=%s, target=%s): %s",
                source_nexus_id,
                resolved_nexus_id,
                exc,
            )

    session_id = str(result.get("session_id") or "").strip()
    session_ref = _svc_format_login_session_ref(session_id)
    grants_count = int(result.get("grants_count") or 0)
    gitlab_username = str(result.get("gitlab_username") or "").strip()
    setup_payload = _svc_get_session_and_setup_status(session_id)
    setup = setup_payload.get("setup") if isinstance(setup_payload, dict) else {}
    has_existing_keys = bool(
        isinstance(setup, dict)
        and (
            setup.get("codex_key_set")
            or setup.get("gemini_key_set")
            or setup.get("claude_key_set")
            or setup.get("codex_account_enabled")
            or setup.get("gemini_account_enabled")
            or setup.get("claude_account_enabled")
            or setup.get("copilot_account_enabled")
        )
    )
    api_keys_allowed = _allow_api_keys_in_web_setup()
    copilot_ready = bool(isinstance(setup, dict) and setup.get("copilot_ready"))
    form_body = f"""
<p>GitLab account linked successfully as <strong>{gitlab_username or "unknown"}</strong>.</p>
<p>Project grants resolved: <strong>{grants_count}</strong>.</p>
<p>Session reference: <code>{session_ref or session_id}</code>.</p>
    """ + _render_ai_key_form(
        session_id=session_ref or session_id,
        show_api_key_inputs=api_keys_allowed,
        copilot_checked=copilot_ready,
        show_copilot_option=False,
        copilot_token_set=bool(isinstance(setup, dict) and setup.get("copilot_token_set")),
        codex_key_set=bool(isinstance(setup, dict) and setup.get("codex_key_set")),
        gemini_key_set=bool(isinstance(setup, dict) and setup.get("gemini_key_set")),
        claude_key_set=bool(isinstance(setup, dict) and setup.get("claude_key_set")),
        codex_account_enabled=bool(isinstance(setup, dict) and setup.get("codex_account_enabled")),
        gemini_account_enabled=bool(isinstance(setup, dict) and setup.get("gemini_account_enabled")),
        claude_account_enabled=bool(isinstance(setup, dict) and setup.get("claude_account_enabled")),
        copilot_account_enabled=bool(isinstance(setup, dict) and setup.get("copilot_account_enabled")),
        existing_keys_note=(
            "All fields are optional. Leave fields blank to keep previously saved values unchanged. "
            + (
                "Existing provider configuration is already saved for this account. "
                if has_existing_keys
                else ""
            )
            + (
                "Use Reset to clear a saved value. If all provider credentials are cleared, "
                "setup may show as not ready until one is added again. Keys/tokens are encrypted at "
                "rest and used only for your own task execution."
                if api_keys_allowed
                else (
                    "CLI auth mode is account-only, so API key/token fields are hidden on this page. "
                    "Enable provider account login toggles and save setup."
                )
            )
        ),
    )
    _notify_onboarding_message(
        session_id=session_id,
        text=(
            "✅ GitLab OAuth linked successfully.\n"
            "Continue in the browser to save provider setup (API keys or account login), then run /setup-status (Discord) or /setup_status (Telegram)."
        ),
    )
    response = make_response(_render_auth_message("Complete Setup", form_body, status_code=200))
    _set_web_session_cookie(response, session_id)
    return response


@app.route("/auth/ai-keys", methods=["POST"])
def auth_ai_keys():
    if not NEXUS_AUTH_ENABLED:
        return _render_auth_message(
            "Auth Disabled",
            "Auth onboarding is disabled in this environment.",
            status_code=404,
        )
    payload = request.get_json(silent=True) if request.is_json else {}
    payload = payload if isinstance(payload, dict) else {}
    session_id = str(request.form.get("session_id") or payload.get("session_id") or "").strip()
    codex_api_key = str(request.form.get("codex_api_key") or payload.get("codex_api_key") or "").strip()
    gemini_api_key = str(
        request.form.get("gemini_api_key") or payload.get("gemini_api_key") or ""
    ).strip()
    claude_api_key = str(
        request.form.get("claude_api_key") or payload.get("claude_api_key") or ""
    ).strip()
    copilot_github_token = str(
        request.form.get("copilot_github_token") or payload.get("copilot_github_token") or ""
    ).strip()
    raw_use_copilot = request.form.get("use_copilot")
    if raw_use_copilot is None:
        raw_use_copilot = payload.get("use_copilot")
    use_copilot = False
    if isinstance(raw_use_copilot, bool):
        use_copilot = raw_use_copilot
    elif raw_use_copilot is not None:
        use_copilot = str(raw_use_copilot).strip().lower() in {"1", "true", "yes", "on"}
    raw_use_codex_account = request.form.get("use_codex_account")
    if raw_use_codex_account is None:
        raw_use_codex_account = payload.get("use_codex_account")
    use_codex_account = None
    if isinstance(raw_use_codex_account, bool):
        use_codex_account = raw_use_codex_account
    elif raw_use_codex_account is not None:
        use_codex_account = str(raw_use_codex_account).strip().lower() in {"1", "true", "yes", "on"}
    raw_use_gemini_account = request.form.get("use_gemini_account")
    if raw_use_gemini_account is None:
        raw_use_gemini_account = payload.get("use_gemini_account")
    use_gemini_account = None
    if isinstance(raw_use_gemini_account, bool):
        use_gemini_account = raw_use_gemini_account
    elif raw_use_gemini_account is not None:
        use_gemini_account = str(raw_use_gemini_account).strip().lower() in {"1", "true", "yes", "on"}
    raw_use_claude_account = request.form.get("use_claude_account")
    if raw_use_claude_account is None:
        raw_use_claude_account = payload.get("use_claude_account")
    use_claude_account = None
    if isinstance(raw_use_claude_account, bool):
        use_claude_account = raw_use_claude_account
    elif raw_use_claude_account is not None:
        use_claude_account = str(raw_use_claude_account).strip().lower() in {"1", "true", "yes", "on"}
    raw_use_copilot_account = request.form.get("use_copilot_account")
    if raw_use_copilot_account is None:
        raw_use_copilot_account = payload.get("use_copilot_account")
    use_copilot_account = None
    if isinstance(raw_use_copilot_account, bool):
        use_copilot_account = raw_use_copilot_account
    elif raw_use_copilot_account is not None:
        use_copilot_account = str(raw_use_copilot_account).strip().lower() in {"1", "true", "yes", "on"}
    codex_supplied = ("codex_api_key" in request.form) or (
        isinstance(payload, dict) and "codex_api_key" in payload
    )
    gemini_supplied = ("gemini_api_key" in request.form) or (
        isinstance(payload, dict) and "gemini_api_key" in payload
    )
    claude_supplied = ("claude_api_key" in request.form) or (
        isinstance(payload, dict) and "claude_api_key" in payload
    )
    copilot_token_supplied = ("copilot_github_token" in request.form) or (
        isinstance(payload, dict) and "copilot_github_token" in payload
    )
    api_keys_allowed = _allow_api_keys_in_web_setup()
    if (
        not api_keys_allowed
        and (codex_supplied or gemini_supplied or claude_supplied or copilot_token_supplied)
    ):
        return _render_auth_message(
            "Credential Error",
            (
                "API key/token submission is disabled because "
                "<code>NEXUS_CLI_AUTH_MODE=account</code>. Use provider account login toggles instead."
            ),
            status_code=400,
        )
    if not session_id:
        return _render_auth_message(
            "Invalid Request",
            "Field <code>session_id</code> is required.",
            status_code=400,
        )
    try:
        result = _svc_store_ai_provider_keys(
            session_id=session_id,
            codex_api_key=(codex_api_key if codex_supplied else None),
            gemini_api_key=(gemini_api_key if gemini_supplied else None),
            claude_api_key=(claude_api_key if claude_supplied else None),
            copilot_github_token=(copilot_github_token if copilot_token_supplied else None),
            allow_copilot=use_copilot,
            use_codex_account=use_codex_account,
            use_gemini_account=use_gemini_account,
            use_claude_account=use_claude_account,
            use_copilot_account=use_copilot_account,
        )
    except Exception as exc:
        _notify_onboarding_message(
            session_id=session_id,
            text=f"❌ Credential save failed: {exc}\nRetry from the web form or run /login again.",
        )
        return _render_auth_message("Credential Error", f"{exc}", status_code=400)

    ready = bool(result.get("ready"))
    grants = int(result.get("project_access_count") or 0)
    body = (
        "<p>✅ Setup completed successfully.</p>"
        if ready
        else "<p>⚠️ Credentials saved, but setup is not fully ready yet.</p>"
    )
    body += (
        f"<p>Project access count: <strong>{grants}</strong>.</p>"
        "<p>Go back to your chat app and run the matching command:"
        " Discord <code>/setup-status</code>, Telegram <code>/setup_status</code>.</p>"
    )
    _notify_onboarding_message(
        session_id=session_id,
        text=_svc_build_setup_completed_chat_message(session_id=session_id, ready=ready),
    )
    response = make_response(_render_auth_message("Setup Complete", body, status_code=200))
    _set_web_session_cookie(response, session_id)
    return response


@app.route("/auth/provider-connect/start", methods=["POST"])
def auth_provider_connect_start():
    if not NEXUS_AUTH_ENABLED:
        return jsonify({"status": "disabled", "message": "Auth onboarding is disabled."}), 404
    payload = request.get_json(silent=True) if request.is_json else {}
    payload = payload if isinstance(payload, dict) else {}
    session_id = str(payload.get("session_id") or request.form.get("session_id") or "").strip()
    provider = str(payload.get("provider") or request.form.get("provider") or "codex").strip().lower()
    if not session_id:
        return jsonify({"status": "error", "message": "session_id is required"}), 400
    try:
        result = _svc_start_provider_account_login(session_id=session_id, provider=provider)
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc), "provider": provider}), 400
    return jsonify({"status": "ok", **result}), 200


def _render_provider_connect_pending_page(*, provider: str, session_id: str) -> str:
    provider_key = str(provider or "codex").strip().lower()
    if provider_key not in {"codex", "gemini", "claude"}:
        provider_key = "codex"
    provider_label = "Codex" if provider_key == "codex" else ("Gemini" if provider_key == "gemini" else "Claude")
    safe_provider = html.escape(provider_key, quote=True)
    safe_provider_label = html.escape(provider_label, quote=False)
    safe_session_id = html.escape(str(session_id or ""), quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{safe_provider_label} Login</title>
  <style>
    body {{
      margin: 0;
      padding: 1.2rem;
      font: 16px/1.4 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      color: #0f172a;
      background: #f8fafc;
    }}
    .card {{
      max-width: 42rem;
      margin: 0 auto;
      background: #ffffff;
      border: 1px solid #cbd5e1;
      border-radius: 10px;
      padding: 1rem 1.1rem;
    }}
    .status {{
      margin-top: 0.6rem;
      color: #334155;
      white-space: pre-wrap;
    }}
    code {{
      background: #e2e8f0;
      border-radius: 5px;
      padding: 0.05rem 0.25rem;
    }}
  </style>
</head>
<body data-provider="{safe_provider}" data-session-id="{safe_session_id}">
  <div class="card">
    <h2 style="margin:0 0 0.4rem;">{safe_provider_label} Account Login</h2>
    <div id="provider_pending_status" class="status">
      Waiting for Nexus to generate the verification link...
    </div>
    <p style="margin:0.9rem 0 0; color:#475569;">
      Keep this tab open. It will redirect automatically when the provider link is ready.
    </p>
  </div>
  <script>
  (function() {{
    var root = document.body;
    var provider = String(root.getAttribute("data-provider") || "codex");
    var sessionId = String(root.getAttribute("data-session-id") || "");
    var statusEl = document.getElementById("provider_pending_status");
    var openerOrigin = window.location.origin;
    function setStatus(text) {{
      if (statusEl) statusEl.textContent = String(text || "");
    }}
    function notifyOpener(payload) {{
      try {{
        if (!window.opener || window.opener.closed) return;
        var body = payload && typeof payload === "object" ? payload : {{}};
        body.source = "nexus-provider-connect";
        body.provider = provider;
        body.session_id = sessionId;
        window.opener.postMessage(body, openerOrigin);
      }} catch (err) {{
        return;
      }}
    }}
    var stopStates = {{
      failed: true,
      connected: true,
      connected_but_not_saved: true,
      rate_limited: true,
      interactive_required: true,
      unsupported_provider: true,
      invalid_session: true
    }};
    async function poll() {{
      if (!sessionId) {{
        setStatus("Missing session id.");
        return;
      }}
      try {{
        var response = await fetch(
          "/auth/provider-connect/status?provider=" + encodeURIComponent(provider) + "&session_id=" + encodeURIComponent(sessionId),
          {{ credentials: "same-origin" }}
        );
        var payload = await response.json();
        var verifyUrl = String(payload.verify_url || "").trim();
        if (verifyUrl) {{
          notifyOpener({{
            state: String(payload.state || "pending"),
            verify_url: verifyUrl,
            message: String(payload.message || "")
          }});
          setStatus("Redirecting to provider verification...");
          window.location.replace(verifyUrl);
          return;
        }}
        if (payload.message) {{
          setStatus(String(payload.message));
        }}
        var state = String(payload.state || "");
        if (stopStates[state]) {{
          notifyOpener({{
            state: state,
            message: String(payload.message || "")
          }});
          if (state === "connected") {{
            setStatus("Connected. Closing this tab...");
            window.setTimeout(function() {{
              try {{ window.close(); }} catch (err) {{ /* ignore */ }}
            }}, 450);
          }}
          return;
        }}
      }} catch (err) {{
        setStatus("Waiting for provider instructions...");
      }}
      window.setTimeout(poll, 1200);
    }}
    poll();
  }})();
  </script>
</body>
</html>
"""


@app.route("/auth/provider-connect/pending", methods=["GET"])
def auth_provider_connect_pending():
    if not NEXUS_AUTH_ENABLED:
        return _render_auth_message("Auth Disabled", "Auth onboarding is disabled.", status_code=404)
    session_id = str(request.args.get("session_id", "")).strip()
    provider = str(request.args.get("provider", "codex")).strip().lower()
    if not session_id:
        return _render_auth_message(
            "Missing Session",
            "Missing <code>session_id</code> for provider account login.",
            status_code=400,
        )
    response = make_response(_render_provider_connect_pending_page(provider=provider, session_id=session_id))
    response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.route("/auth/provider-connect/status", methods=["GET"])
def auth_provider_connect_status():
    if not NEXUS_AUTH_ENABLED:
        return jsonify({"status": "disabled", "message": "Auth onboarding is disabled."}), 404
    session_id = str(request.args.get("session_id", "")).strip()
    provider = str(request.args.get("provider", "codex")).strip().lower()
    if not session_id:
        return jsonify({"status": "error", "message": "session_id query param is required"}), 400
    result = _svc_get_provider_account_login_status(session_id=session_id, provider=provider)
    return jsonify({"status": "ok", **result}), 200


@app.route("/auth/provider-connect/relay-callback", methods=["POST"])
def auth_provider_connect_relay_callback():
    if not NEXUS_AUTH_ENABLED:
        return jsonify({"status": "disabled", "message": "Auth onboarding is disabled."}), 404
    payload = request.get_json(silent=True) if request.is_json else {}
    payload = payload if isinstance(payload, dict) else {}
    session_id = str(payload.get("session_id") or request.form.get("session_id") or "").strip()
    provider = str(payload.get("provider") or request.form.get("provider") or "gemini").strip().lower()
    callback_url = str(payload.get("callback_url") or request.form.get("callback_url") or "").strip()
    if not session_id:
        return jsonify({"status": "error", "message": "session_id is required"}), 400
    if not callback_url:
        return jsonify({"status": "error", "message": "callback_url is required"}), 400
    try:
        result = _svc_relay_provider_account_login_callback(
            session_id=session_id,
            provider=provider,
            callback_url=callback_url,
        )
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc), "provider": provider}), 400
    return jsonify({"status": "ok", **result}), 200


@app.route("/auth/provider-connect/submit-code", methods=["POST"])
def auth_provider_connect_submit_code():
    if not NEXUS_AUTH_ENABLED:
        return jsonify({"status": "disabled", "message": "Auth onboarding is disabled."}), 404
    payload = request.get_json(silent=True) if request.is_json else {}
    payload = payload if isinstance(payload, dict) else {}
    session_id = str(payload.get("session_id") or request.form.get("session_id") or "").strip()
    provider = str(payload.get("provider") or request.form.get("provider") or "gemini").strip().lower()
    authorization_code = str(
        payload.get("authorization_code") or request.form.get("authorization_code") or ""
    ).strip()
    if not session_id:
        return jsonify({"status": "error", "message": "session_id is required"}), 400
    if not authorization_code:
        return jsonify({"status": "error", "message": "authorization_code is required"}), 400
    try:
        result = _svc_submit_provider_account_login_code(
            session_id=session_id,
            provider=provider,
            authorization_code=authorization_code,
        )
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc), "provider": provider}), 400
    return jsonify({"status": "ok", **result}), 200


@app.route("/auth/result", methods=["GET"])
def auth_result():
    if not NEXUS_AUTH_ENABLED:
        return jsonify({"enabled": False, "status": "disabled"}), 200
    session_id = str(request.args.get("session", "")).strip()
    if not session_id:
        return jsonify({"status": "error", "message": "session query parameter is required"}), 400
    payload = _svc_get_session_and_setup_status(session_id)
    return jsonify(payload), 200


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint."""
    _run_acl_sync_if_due()
    return jsonify({"status": "healthy", "service": "nexus-webhook", "version": "1.0.0"}), 200


@app.route("/api/v1", methods=["GET"])
def api_v1_index():
    """Minimal API discovery endpoint for runtime-safe client usage."""
    return (
        jsonify(
            {
                "status": "ok",
                "version": "v1",
                "service": "nexus-webhook",
                "endpoints": [
                    {
                        "method": "POST",
                        "path": "/api/v1/completion",
                        "description": "Submit step completion payload for orchestration processing.",
                    },
                    {
                        "method": "GET",
                        "path": "/api/v1/workflow-context/{issue_number}",
                        "description": (
                            "Read canonical workflow status and recent audit history "
                            "for an issue (requester-scoped authorization when auth is enabled)."
                        ),
                    },
                ],
            }
        ),
        200,
    )


@app.route("/api/v1/completion", methods=["POST"])
def api_v1_completion():
    """Persist-and-acknowledge completion endpoint (postgres backend).

    Agents POST their completion JSON here when ``NEXUS_STORAGE_BACKEND``
    is ``postgres``.  The payload is persisted to the ``nexus_completions``
    table and acknowledged with ``201 Created``.

    The orchestrator loop (``scan_and_process_completions``) handles
    auto-chaining separately — this endpoint only persists.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        logger.warning("API v1 completion rejected: invalid JSON payload")
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    issue_number = str(data.get("issue_number", "")).strip()
    agent_type = str(data.get("agent_type", "")).strip()
    step_id = str(data.get("step_id", "")).strip()
    try:
        step_num = int(data.get("step_num", 0) or 0)
    except (TypeError, ValueError):
        step_num = 0

    logger.info(
        "📥 API v1 completion attempt: issue=%s agent=%s step_id=%s step_num=%s remote=%s",
        issue_number or "<missing>",
        agent_type or "<missing>",
        step_id or "<missing>",
        step_num,
        request.remote_addr or "<unknown>",
    )

    if not issue_number or not agent_type or not step_id or step_num <= 0:
        logger.warning(
            "API v1 completion rejected: missing required fields "
            "(issue=%s agent=%s step_id=%s step_num=%s)",
            issue_number or "<missing>",
            agent_type or "<missing>",
            step_id or "<missing>",
            step_num,
        )
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "issue_number, agent_type, step_id, and step_num are required",
                }
            ),
            400,
        )

    def _normalize_ref(value: str) -> str:
        return str(value or "").strip().lstrip("@").lower()

    # Strict server-side gate: requester can only complete the currently running step.
    try:
        from nexus.core.orchestration.nexus_core_helpers import get_workflow_status

        status = asyncio.run(get_workflow_status(issue_number))
    except Exception as exc:
        logger.error("Failed to resolve workflow status for completion gate: %s", exc, exc_info=True)
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Could not validate active workflow step for completion",
                }
            ),
            500,
        )

    if not isinstance(status, dict):
        logger.warning(
            "API v1 completion rejected: no active workflow status for issue #%s",
            issue_number,
        )
        return (
            jsonify(
                {
                    "status": "error",
                    "message": (
                        f"No active workflow status found for issue #{issue_number}; "
                        "completion rejected."
                    ),
                }
            ),
            409,
        )

    expected_agent = _normalize_ref(
        str(status.get("current_agent_type") or status.get("current_agent") or "")
    )
    expected_step_id = _normalize_ref(str(status.get("current_step_id") or ""))
    try:
        expected_step_num = int(status.get("current_step_num") or status.get("current_step") or 0)
    except (TypeError, ValueError):
        expected_step_num = 0

    if (
        _normalize_ref(agent_type) != expected_agent
        or _normalize_ref(step_id) != expected_step_id
        or int(step_num) != expected_step_num
    ):
        logger.warning(
            "API v1 completion rejected as stale for issue #%s: expected %s/%s/%s, got %s/%s/%s",
            issue_number,
            expected_agent or "<none>",
            expected_step_id or "<none>",
            expected_step_num,
            _normalize_ref(agent_type) or "<none>",
            _normalize_ref(step_id) or "<none>",
            int(step_num),
        )
        return (
            jsonify(
                {
                    "status": "stale",
                    "message": (
                        "Completion does not match currently running workflow step; rejected."
                    ),
                    "expected": {
                        "agent_type": expected_agent,
                        "step_id": expected_step_id,
                        "step_num": expected_step_num,
                    },
                    "received": {
                        "agent_type": _normalize_ref(agent_type),
                        "step_id": _normalize_ref(step_id),
                        "step_num": int(step_num),
                    },
                }
            ),
            409,
        )

    workflow_id = str(status.get("workflow_id") or "").strip()
    if workflow_id:
        data["workflow_id"] = workflow_id

    logger.info(
        "📬 API v1 completion received: issue #%s, agent=%s",
        issue_number,
        agent_type,
    )

    try:
        from nexus.core.completion_store import CompletionStore

        store = _get_completion_store()
        dedup_key = store.save(issue_number, agent_type, data)
        return (
            jsonify(
                {
                    "status": "created",
                    "issue_number": issue_number,
                    "agent_type": agent_type,
                    "dedup_key": dedup_key,
                }
            ),
            201,
        )
    except Exception as exc:
        logger.error("Failed to persist completion: %s", exc, exc_info=True)
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/api/v1/workflow-context/<issue_number>", methods=["GET"])
def api_v1_workflow_context(issue_number: str):
    """Return canonical workflow context with requester-scoped authorization."""
    normalized_issue = str(issue_number or "").strip()
    if not normalized_issue.isdigit():
        return jsonify({"status": "error", "message": "issue_number must be numeric"}), 400

    try:
        audit_limit = int(str(request.args.get("audit_limit", "25")).strip())
    except (TypeError, ValueError):
        audit_limit = 25
    audit_limit = max(1, min(audit_limit, 200))

    try:
        context_payload = asyncio.run(get_workflow_context(normalized_issue, audit_limit=audit_limit))
    except Exception as exc:
        logger.error(
            "Failed to resolve workflow context for issue #%s: %s",
            normalized_issue,
            exc,
            exc_info=True,
        )
        return jsonify({"status": "error", "message": "Failed to resolve workflow context"}), 500

    if not isinstance(context_payload, dict):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"No active workflow status found for issue #{normalized_issue}",
                }
            ),
            404,
        )

    workflow_status = context_payload.get("workflow")
    if not isinstance(workflow_status, dict):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"No active workflow status found for issue #{normalized_issue}",
                }
            ),
            404,
        )

    metadata = workflow_status.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    project_name = str(request.args.get("project", "")).strip() or str(
        metadata.get("project_name") or metadata.get("project") or metadata.get("project_key") or ""
    ).strip()

    repo = str(request.args.get("repo", "")).strip() or str(
        metadata.get("repo")
        or metadata.get("repo_key")
        or metadata.get("git_repo")
        or metadata.get("repository")
        or ""
    ).strip()

    if not project_name and repo:
        project_name = str(resolve_project_name_for_repo(repo) or "").strip()
    if project_name and not repo:
        try:
            repo = str(get_repo(project_name) or "").strip()
        except Exception:
            repo = ""

    if NEXUS_AUTH_ENABLED:
        session_id, session_payload = _resolve_ready_web_session()
        if not session_id or not isinstance(session_payload, dict):
            return jsonify({"status": "error", "message": "Authentication required"}), 401

        requester_nexus_id = str(session_payload.get("nexus_id") or "").strip()
        allowed, reason = can_access_issue_context(
            nexus_id=requester_nexus_id,
            issue_number=normalized_issue,
            repo=repo,
            project_name=project_name or None,
        )
        if not allowed:
            logger.warning(
                "Workflow context denied: issue=%s requester=%s project=%s repo=%s reason=%s",
                normalized_issue,
                requester_nexus_id or "<missing>",
                project_name or "<unknown>",
                repo or "<unknown>",
                reason,
            )
            return jsonify({"status": "error", "message": "Forbidden"}), 403

    return (
        jsonify(
            {
                "status": "ok",
                "issue_number": normalized_issue,
                "project": project_name,
                "repo": repo,
                "context": context_payload,
            }
        ),
        200,
    )


def _get_completion_store():
    """Lazy-singleton for the CompletionStore."""
    if not hasattr(_get_completion_store, "_instance"):
        from nexus.core.completion_store import CompletionStore

        backend = NEXUS_STORAGE_BACKEND
        storage = None
        if backend == "postgres":
            from nexus.core.integrations.workflow_state_factory import get_storage_backend

            storage = get_storage_backend()

        _get_completion_store._instance = CompletionStore(
            backend=backend,
            storage=storage,
            base_dir=BASE_DIR,
        )
    return _get_completion_store._instance


@app.route("/webhook", methods=["POST"])
def webhook():
    """Main webhook endpoint for GitHub events."""
    _run_acl_sync_if_due()
    body, status = _process_webhook_request(
        payload_body=request.data,
        headers=dict(request.headers),
        payload_json=request.json,
        logger=logger,
        verify_signature=verify_signature,
        get_webhook_policy=_get_webhook_policy,
        handle_issue_opened=handle_issue_opened,
        handle_issue_comment=handle_issue_comment,
        handle_pull_request=handle_pull_request,
        handle_pull_request_review=handle_pull_request_review,
        emit_alert=emit_alert,
    )
    return jsonify(body), status


@app.route("/", methods=["GET"])
def index():
    """Root endpoint."""
    next_path = _safe_next_path(request.args.get("next"), default="/visualizer")
    allowed, mode, session_id = _resolve_visualizer_access()
    if allowed:
        response = make_response(redirect(next_path, code=302))
        if mode == "session" and session_id:
            _set_web_session_cookie(response, session_id)
        elif mode == "token":
            provided_token = _extract_visualizer_shared_token_from_request()
            if provided_token:
                _set_visualizer_shared_token_cookie(response, provided_token)
        return response

    if mode == "disabled":
        return _render_auth_message(
            "Visualizer Disabled",
            "The visualizer is disabled (<code>NEXUS_VISUALIZER_ENABLED=false</code>).",
            status_code=404,
        )

    if mode == "token-required":
        response = make_response(_render_visualizer_token_page(next_path=next_path))
        _clear_visualizer_shared_token_cookie(response)
        return response

    if mode == "unconfigured":
        return _render_auth_message(
            "Visualizer Locked",
            (
                "No access method is configured. "
                "Set <code>NEXUS_VISUALIZER_SHARED_TOKEN</code> or enable "
                "<code>NEXUS_AUTH_ENABLED=true</code>."
            ),
            status_code=503,
        )

    session_hint = str(request.args.get("session", "")).strip()
    ready_session_id, payload = _resolve_ready_web_session(session_hint or None)
    if ready_session_id and payload:
        response = make_response(redirect(next_path, code=302))
        _set_web_session_cookie(response, ready_session_id)
        return response

    info_message = ""
    if session_hint:
        info_message = (
            "<p><small>Session found but setup is not ready yet. Complete OAuth + key setup first.</small></p>"
        )
    response = make_response(
        _render_login_page(
            message_html=info_message,
            session_hint=session_hint,
        )
    )
    if session_hint:
        _clear_web_session_cookie(response)
    return response


@app.route("/visualizer/access", methods=["POST"])
def visualizer_access():
    """Exchange shared visualizer token for an HttpOnly cookie session."""
    if not _VISUALIZER_ENABLED:
        return _render_auth_message(
            "Visualizer Disabled",
            "The visualizer is disabled (<code>NEXUS_VISUALIZER_ENABLED=false</code>).",
            status_code=404,
        )
    if not _VISUALIZER_SHARED_TOKEN:
        return _render_auth_message(
            "Token Access Disabled",
            "Shared-token access is disabled because <code>NEXUS_VISUALIZER_SHARED_TOKEN</code> is not set.",
            status_code=404,
        )

    payload = request.get_json(silent=True) if request.is_json else {}
    payload = payload if isinstance(payload, dict) else {}
    raw_next = request.form.get("next") or payload.get("next")
    next_path = _safe_next_path(raw_next, default="/visualizer")
    token = str(request.form.get("token") or payload.get("token") or "").strip()
    if not _has_valid_visualizer_shared_token(token):
        response = make_response(
            _render_visualizer_token_page(
                message_html="<p><small>Invalid token. Try again.</small></p>",
                next_path=next_path,
            )
        )
        response.status_code = 401
        _clear_visualizer_shared_token_cookie(response)
        return response

    response = make_response(redirect(next_path, code=302))
    _set_visualizer_shared_token_cookie(response, token)
    return response


@app.route("/visualizer", methods=["GET"], strict_slashes=False)
def visualizer():
    """Serve the real-time workflow visualizer dashboard."""
    allowed, mode, session_id = _resolve_visualizer_access()
    if not allowed:
        if mode == "disabled":
            return _render_auth_message(
                "Visualizer Disabled",
                "The visualizer is disabled (<code>NEXUS_VISUALIZER_ENABLED=false</code>).",
                status_code=404,
            )
        if mode == "unconfigured":
            return _render_auth_message(
                "Visualizer Locked",
                (
                    "No access method is configured. "
                    "Set <code>NEXUS_VISUALIZER_SHARED_TOKEN</code> or enable "
                    "<code>NEXUS_AUTH_ENABLED=true</code>."
                ),
                status_code=503,
            )
        response = make_response(_visualizer_login_redirect("/visualizer"))
        if mode == "login-required":
            _clear_web_session_cookie(response)
        if mode == "token-required":
            _clear_visualizer_shared_token_cookie(response)
        return response

    if request.args.get("session"):
        response = make_response(redirect("/visualizer", code=302))
    else:
        try:
            response = make_response(send_from_directory(app.static_folder, "visualizer.html"))
        except NotFound:
            visualizer_path = os.path.join(app.static_folder or "", "visualizer.html")
            logger.exception("Visualizer asset not found at %s", visualizer_path)
            return _render_auth_message(
                "Visualizer Unavailable",
                (
                    "Visualizer assets could not be loaded. "
                    "Rebuild the image and ensure <code>src/static/visualizer.html</code> is present."
                ),
                status_code=503,
            )
    if mode == "session" and session_id:
        _set_web_session_cookie(response, session_id)
    elif mode == "token":
        provided_token = _extract_visualizer_shared_token_from_request()
        if provided_token:
            _set_visualizer_shared_token_cookie(response, provided_token)
    return response


@app.route("/visualizer/snapshot", methods=["GET"])
def visualizer_snapshot():
    """Return a snapshot payload for initial visualizer rendering."""
    allowed, mode, _session_id = _resolve_visualizer_access()
    if not allowed:
        if mode == "disabled":
            return jsonify({"status": "disabled", "message": "Visualizer is disabled."}), 404
        if mode == "unconfigured":
            return (
                jsonify(
                    {
                        "status": "locked",
                        "message": (
                            "No visualizer auth method configured. "
                            "Set NEXUS_VISUALIZER_SHARED_TOKEN or enable NEXUS_AUTH_ENABLED=true."
                        ),
                    }
                ),
                503,
            )
        return (
            jsonify(
                {
                    "status": "unauthorized",
                    "message": (
                        "Authentication required. Use /login or provide visualizer shared token "
                        f"via {_VISUALIZER_SHARED_TOKEN_HEADER}."
                    ),
                }
            ),
            401,
        )
    records = _collect_visualizer_snapshot()
    return (
        jsonify(
            {
                "count": len(records),
                "workflows": records,
            }
        ),
        200,
    )


def main():
    """Start the webhook server."""
    port = WEBHOOK_PORT
    logger.info(f"🚀 Starting webhook server on port {port}")

    # Initialize event handlers (including SocketIO bridge)
    try:
        from nexus.core.orchestration.nexus_core_helpers import setup_event_handlers

        setup_event_handlers()
        logger.info("✅ Event handlers initialized")
    except Exception as e:
        logger.warning(f"⚠️ Could not initialize event handlers: {e}")

    logger.info(f"📍 Webhook URL: http://localhost:{port}/webhook")
    logger.info(f"📊 Visualizer: http://localhost:{port}/visualizer")

    if not WEBHOOK_SECRET:
        logger.warning("⚠️ WEBHOOK_SECRET not configured - signature verification disabled!")

    # Flask-SocketIO uses Werkzeug in threading mode. Auto-enable the safety
    # escape hatch there to prevent hard startup failures on runtime fallback.
    allow_unsafe_werkzeug = _env_flag("NEXUS_ALLOW_UNSAFE_WERKZEUG", default=False)
    async_mode = str(getattr(socketio, "async_mode", _socketio_async_mode)).strip().lower()
    if async_mode == "threading" and not allow_unsafe_werkzeug:
        logger.warning(
            "Socket.IO async mode 'threading' detected; enabling "
            "allow_unsafe_werkzeug to avoid startup failure."
        )
        allow_unsafe_werkzeug = True
    logger.info("Socket.IO async mode: %s", async_mode)

    socketio.run(
        app,
        host="0.0.0.0",
        port=port,
        debug=False,
        allow_unsafe_werkzeug=allow_unsafe_werkzeug,
    )


if __name__ == "__main__":
    main()
