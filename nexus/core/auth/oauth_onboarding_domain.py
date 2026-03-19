"""OAuth session orchestration and AI provider key onboarding helpers."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlencode, urlparse

import requests

from nexus.core.auth.access_domain import (
    get_setup_status,
    sync_user_gitlab_project_access,
    sync_user_project_access,
)
from nexus.core.auth.credential_crypto import encrypt_secret
from nexus.core.auth.credential_store import (
    cleanup_expired_auth_sessions,
    create_auth_session,
    find_user_credentials_by_github_identity,
    find_user_credentials_by_gitlab_identity,
    get_auth_session,
    get_auth_session_by_state,
    get_latest_auth_session_for_nexus,
    get_user_credentials,
    update_auth_session,
    upsert_ai_provider_keys,
    upsert_github_credentials,
    upsert_gitlab_credentials,
)

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _current_uid() -> int | None:
    getuid = cast(Callable[[], int] | None, getattr(os, "getuid", None))
    if not callable(getuid):
        return None
    return int(getuid())


def _required_env(name: str) -> str:
    value = str(os.getenv(name, "")).strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _allowed_github_orgs() -> set[str]:
    raw = os.getenv("NEXUS_AUTH_ALLOWED_GITHUB_ORGS", "")
    return {value.strip().lower() for value in str(raw).split(",") if str(value).strip()}


def _allowed_gitlab_groups() -> set[str]:
    raw = os.getenv("NEXUS_AUTH_ALLOWED_GITLAB_GROUPS", "")
    return {value.strip().lower() for value in str(raw).split(",") if str(value).strip()}


def _session_ttl_seconds() -> int:
    raw = os.getenv("NEXUS_AUTH_SESSION_TTL_SECONDS", "900")
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        parsed = 900
    return max(120, parsed)


def _key_version() -> int:
    raw = os.getenv("NEXUS_CREDENTIALS_KEY_VERSION", "1")
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        parsed = 1
    return max(1, parsed)


def _normalize_provider(provider: str | None) -> str:
    value = str(provider or "github").strip().lower()
    if value not in {"github", "gitlab"}:
        raise ValueError("Unsupported auth provider. Use 'github' or 'gitlab'.")
    return value


def _gitlab_base_url() -> str:
    return str(
        os.getenv("NEXUS_GITLAB_BASE_URL", os.getenv("GITLAB_BASE_URL", "https://gitlab.com"))
    ).strip().rstrip("/")


def _oauth_callback_url(provider: str) -> str:
    auth_provider = _normalize_provider(provider)
    if auth_provider == "github":
        override = str(os.getenv("NEXUS_GITHUB_CALLBACK_URL", "")).strip()
        if override:
            return override
    else:
        override = str(os.getenv("NEXUS_GITLAB_CALLBACK_URL", "")).strip()
        if override:
            return override
    base_url = _required_env("NEXUS_PUBLIC_BASE_URL").rstrip("/")
    return f"{base_url}/auth/{auth_provider}/callback"


_SESSION_REF_PREFIX = "lsr_"
_DEVICE_AUTH_JOBS: dict[str, dict[str, Any]] = {}
_DEVICE_AUTH_LOCK = threading.Lock()
_SUPPORTED_PROVIDER_ACCOUNT_CONNECTORS = ("codex", "gemini", "claude")
_PROVIDER_ACCOUNT_CONNECT_LABELS = {
    "codex": "Codex",
    "gemini": "Gemini",
    "claude": "Claude",
}
_PROVIDER_ACCOUNT_CONNECT_TOGGLE_FIELDS = {
    "codex": "use_codex_account",
    "gemini": "use_gemini_account",
    "claude": "use_claude_account",
}
_PROVIDER_ACCOUNT_CONNECT_HOME_ENV = {
    "codex": "CODEX_HOME",
    "gemini": "GEMINI_HOME",
    "claude": "CLAUDE_HOME",
}
_PROVIDER_ACCOUNT_CONNECT_CLI_ENV = {
    "codex": "CODEX_CLI_PATH",
    "gemini": "GEMINI_CLI_PATH",
    "claude": "CLAUDE_CLI_PATH",
}
_PROVIDER_ACCOUNT_CONNECT_ARGS_ENV = {
    "codex": "NEXUS_CODEX_ACCOUNT_CONNECT_ARGS",
    "gemini": "NEXUS_GEMINI_ACCOUNT_CONNECT_ARGS",
    "claude": "NEXUS_CLAUDE_ACCOUNT_CONNECT_ARGS",
}
_PROVIDER_ACCOUNT_CONNECT_DEFAULT_ARGS = {
    "codex": "login --device-auth",
    "gemini": "--debug",
    "claude": "auth login",
}
_PROVIDER_ACCOUNT_CONNECT_FAILURE_STATES = {
    "failed",
    "rate_limited",
    "interactive_required",
    "connected_but_not_saved",
    "unsupported_provider",
    "invalid_session",
    "idle",
    "oauth_required",
}


def _device_job_key(*, session_id: str, provider: str) -> str:
    return f"{str(session_id).strip()}::{str(provider).strip().lower()}"


def _normalize_provider_account_connector(provider: str | None) -> str:
    value = str(provider or "").strip().lower()
    if value not in _SUPPORTED_PROVIDER_ACCOUNT_CONNECTORS:
        supported = ", ".join(_SUPPORTED_PROVIDER_ACCOUNT_CONNECTORS)
        raise ValueError(
            f"Unsupported provider account-connect target: {value or '<empty>'}. "
            f"Supported: {supported}."
        )
    return value


def _provider_account_label(provider: str) -> str:
    return str(_PROVIDER_ACCOUNT_CONNECT_LABELS.get(provider) or provider.title())


def _provider_account_toggle_field(provider: str) -> str:
    return str(_PROVIDER_ACCOUNT_CONNECT_TOGGLE_FIELDS.get(provider) or "")


def _provider_user_runtime_home(*, nexus_id: str) -> str:
    runtime_root = str(os.getenv("NEXUS_RUNTIME_DIR", "/var/lib/nexus")).strip() or "/var/lib/nexus"
    return os.path.join(runtime_root, "auth", "home", str(nexus_id).strip())


def _provider_account_login_command(provider: str) -> list[str]:
    cli_env_name = str(_PROVIDER_ACCOUNT_CONNECT_CLI_ENV.get(provider) or "").strip()
    args_env_name = str(_PROVIDER_ACCOUNT_CONNECT_ARGS_ENV.get(provider) or "").strip()
    default_args = str(_PROVIDER_ACCOUNT_CONNECT_DEFAULT_ARGS.get(provider) or "").strip()
    cli_path = str(os.getenv(cli_env_name, provider)).strip() or provider
    args_raw = str(os.getenv(args_env_name, default_args)).strip() or default_args
    try:
        args = shlex.split(args_raw) if args_raw else []
    except ValueError as exc:
        raise ValueError(f"Invalid {args_env_name}: {exc}") from exc
    return [cli_path, *args]


def _read_json_file(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_file(path: str, payload: dict[str, Any]) -> None:
    parent = os.path.dirname(path)
    if parent:
        _ensure_private_dir(parent)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp_path, path)
    _ensure_private_file(path)


def _set_nested_value(payload: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor: dict[str, Any] = payload
    for key in path[:-1]:
        next_value = cursor.get(key)
        if not isinstance(next_value, dict):
            next_value = {}
            cursor[key] = next_value
        cursor = next_value
    cursor[path[-1]] = value


def _get_nested_str(payload: dict[str, Any], path: tuple[str, ...]) -> str:
    cursor: Any = payload
    for key in path:
        if not isinstance(cursor, dict):
            return ""
        cursor = cursor.get(key)
    return str(cursor or "").strip()


def _prepare_provider_runtime_state(*, provider: str, user_home: str) -> None:
    provider_name = str(provider or "").strip().lower()
    if provider_name != "gemini":
        return
    selected_auth = str(os.getenv("NEXUS_GEMINI_SELECTED_AUTH_TYPE", "oauth-personal")).strip()
    if not selected_auth:
        return
    raw_folder_trust = str(os.getenv("NEXUS_GEMINI_FOLDER_TRUST_ENABLED", "false")).strip().lower()
    folder_trust_enabled = raw_folder_trust in {"1", "true", "yes", "on"}
    settings_path = os.path.join(user_home, ".gemini", "settings.json")
    settings = _read_json_file(settings_path)
    current_security_auth = _get_nested_str(settings, ("security", "auth", "selectedType"))
    current_legacy_auth = str(settings.get("selectedAuthType") or "").strip()
    security = settings.get("security")
    security_map = security if isinstance(security, dict) else {}
    folder_trust_map = security_map.get("folderTrust")
    current_folder_trust = (
        bool(folder_trust_map.get("enabled"))
        if isinstance(folder_trust_map, dict)
        else None
    )
    if (
        current_security_auth == selected_auth
        and current_legacy_auth == selected_auth
        and current_folder_trust is folder_trust_enabled
    ):
        return
    _set_nested_value(settings, ("security", "auth", "selectedType"), selected_auth)
    _set_nested_value(settings, ("security", "folderTrust", "enabled"), folder_trust_enabled)
    # Keep legacy key for older Gemini CLI builds that still read selectedAuthType.
    settings["selectedAuthType"] = selected_auth
    _write_json_file(settings_path, settings)
    logger.info(
        "[provider-connect] prepared gemini settings path=%s selectedType=%s folderTrustEnabled=%s",
        settings_path,
        selected_auth,
        folder_trust_enabled,
    )


def _provider_runtime_home(*, provider: str, nexus_id: str) -> str:
    runtime_root = str(os.getenv("NEXUS_RUNTIME_DIR", "/var/lib/nexus")).strip() or "/var/lib/nexus"
    return os.path.join(runtime_root, "auth", str(provider).strip().lower(), str(nexus_id).strip())


def _ensure_private_dir(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except Exception:
        pass
    current_uid = _current_uid()
    if current_uid is not None:
        try:
            owner_uid = int(os.stat(path).st_uid)
        except Exception as exc:
            raise RuntimeError(f"Unable to verify private directory owner for {path}: {exc}") from exc
        if owner_uid != current_uid:
            raise PermissionError(
                f"Refusing to use insecure auth directory '{path}' owned by uid={owner_uid}; "
                f"expected uid={current_uid}."
            )


def _ensure_private_file(path: str) -> None:
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    current_uid = _current_uid()
    if current_uid is not None:
        try:
            owner_uid = int(os.stat(path).st_uid)
        except Exception as exc:
            raise RuntimeError(f"Unable to verify private file owner for {path}: {exc}") from exc
        if owner_uid != current_uid:
            raise PermissionError(
                f"Refusing to use insecure auth file '{path}' owned by uid={owner_uid}; "
                f"expected uid={current_uid}."
            )


def _strip_terminal_control_sequences(raw_text: str) -> str:
    text = str(raw_text or "")
    # Remove ANSI escape/control sequences emitted by CLIs so extracted text stays parseable.
    text = re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1B]8;;.*?(?:\x1B\\|\x07)", "", text)
    text = re.sub(r"[\x00-\x08\x0B-\x1F\x7F]", "", text)
    return text


def _parse_device_auth_url_and_code(raw_text: str) -> tuple[str, str]:
    text = _strip_terminal_control_sequences(raw_text)
    url_match = re.search(r"https?://[^]\s<>\")']+", text, flags=re.IGNORECASE)
    code_match = re.search(r"\b[A-Z0-9]{4}(?:-[A-Z0-9]{3,})+\b", text, flags=re.IGNORECASE)
    url = str(url_match.group(0) if url_match else "").strip().rstrip(".,;:")
    code = str(code_match.group(0) if code_match else "").strip().upper()
    if code:
        return url, code

    inline_patterns = (
        r"(?:verification|device|user|authorization)\s+code\s*(?:is|:)?\s*([A-Z0-9]{6,16})\b",
        r"\bcode\s*(?:is|:)\s*([A-Z0-9]{6,16})\b",
        r"\benter\s+([A-Z0-9]{6,16})\s*(?:at|in|on)\b",
    )
    for pattern in inline_patterns:
        inline_code = re.search(pattern, text, flags=re.IGNORECASE)
        if not inline_code:
            continue
        raw_candidate = str(inline_code.group(1) or "").strip()
        if not raw_candidate:
            continue
        # Avoid false positives like "authorization" being parsed as a code.
        has_digit = any(char.isdigit() for char in raw_candidate)
        has_hyphen = "-" in raw_candidate
        looks_all_upper = raw_candidate == raw_candidate.upper()
        if not (has_digit or has_hyphen or looks_all_upper):
            continue
        return url, raw_candidate.upper()
    return url, ""


def _parse_local_callback_url(raw_text: str) -> str:
    text = _strip_terminal_control_sequences(raw_text)
    callback_match = re.search(
        r"https?://(?:localhost|127\.0\.0\.1|\[::1]|::1)(?::\d+)?/oauth2[^]\s<>\")']+",
        text,
        flags=re.IGNORECASE,
    )
    if not callback_match:
        return ""
    return str(callback_match.group(0) or "").strip().rstrip(".,;:")


def _requires_manual_auth_code(*, provider: str, log_text: str) -> bool:
    provider_name = str(provider or "").strip().lower()
    if provider_name != "gemini":
        return False
    normalized = _strip_terminal_control_sequences(log_text).lower()
    return (
        "enter the authorization code" in normalized
        or "authorization code:" in normalized
    )


def _gemini_has_logged_in_identity(log_text: str) -> bool:
    normalized = _strip_terminal_control_sequences(log_text).lower()
    return "logged in with google:" in normalized


def _log_tail(path: str, *, max_chars: int = 3000) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            content = fh.read()
    except Exception:
        return ""
    if len(content) <= max_chars:
        return content
    return content[-max_chars:]


def _last_log_line(log_text: str, default: str) -> str:
    lines = [str(line).strip() for line in str(log_text or "").splitlines() if str(line).strip()]
    return lines[-1] if lines else default


def _truncate_for_log(value: str, *, limit: int = 400) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _format_log_excerpt(log_text: str, *, max_chars: int = 1200) -> str:
    text = str(log_text or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        text = text[-max_chars:]
    return _truncate_for_log(text, limit=max_chars)


def _classify_provider_account_login_failure(*, provider: str, log_text: str) -> tuple[str, str]:
    provider_name = str(provider or "").strip().lower()
    provider_label = _provider_account_label(provider_name)
    raw = str(log_text or "").strip()
    normalized = raw.lower()
    last_line = _truncate_for_log(_last_log_line(raw, ""), limit=220)

    if (
        "429 too many requests" in normalized
        or "status 429" in normalized
        or ("too many requests" in normalized and "device code" in normalized)
    ):
        return (
            "rate_limited",
            (
                f"{provider_label} login is temporarily rate-limited by the provider "
                "(HTTP 429). Wait 1-2 minutes, then retry."
                + (f" Last output: {last_line}" if last_line else "")
            ),
        )

    if provider_name == "gemini" and (
        "please set an auth method" in normalized
        or ".gemini/settings.json" in normalized
        or "gemini_api_key" in normalized
        or "google_genai_use_vertexai" in normalized
        or "google_genai_use_gca" in normalized
    ):
        return (
            "interactive_required",
            (
                "Gemini CLI did not start account login for this workspace runtime. "
                "Retry Connect Gemini Account and complete browser confirmation."
                + (f" Last output: {last_line}" if last_line else "")
            ),
        )

    if provider_name == "gemini" and "no input provided via stdin" in normalized:
        return (
            "failed",
            (
                "Gemini login did not start in interactive mode. "
                "Retry Connect Gemini Account."
                + (f" Last output: {last_line}" if last_line else "")
            ),
        )

    if provider_name == "claude" and (
        "not logged in" in normalized
        or "please run /login" in normalized
    ):
        return (
            "interactive_required",
            (
                "Claude CLI is not authenticated for this workspace runtime yet. "
                "Retry Connect Claude Account and complete browser confirmation."
                + (f" Last output: {last_line}" if last_line else "")
            ),
        )

    return "failed", _last_log_line(raw, "Device-auth process failed")


def _validate_local_callback_url(callback_url: str) -> str:
    candidate = str(callback_url or "").strip()
    if not candidate:
        raise ValueError("callback_url is required")
    parsed = urlparse(candidate)
    scheme = str(parsed.scheme or "").strip().lower()
    host = str(parsed.hostname or "").strip().lower()
    if scheme not in {"http", "https"}:
        raise ValueError("callback_url must use http or https")
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("callback_url host must be localhost/127.0.0.1/::1")
    if parsed.port is None:
        raise ValueError("callback_url must include localhost port")
    if not str(parsed.path or "").startswith("/oauth2"):
        raise ValueError("callback_url path must start with /oauth2")
    if not str(parsed.query or "").strip():
        raise ValueError("callback_url must include query parameters")
    return candidate


def _normalize_provider_auth_code(raw_code: str) -> str:
    code = str(raw_code or "").strip()
    if not code:
        raise ValueError("authorization_code is required")
    if len(code) > 2048:
        raise ValueError("authorization_code is too long")
    return code


def _gemini_should_use_pty_wrapper() -> bool:
    raw_value = str(os.getenv("NEXUS_GEMINI_ACCOUNT_CONNECT_USE_PTY", "true")).strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


def _wrap_command_with_script_tty(command: list[str]) -> list[str]:
    if not command:
        return command
    script_path = shutil.which("script")
    if not script_path:
        return command
    return [str(script_path), "-q", "-e", "-c", shlex.join(command), "/dev/null"]


def format_login_session_ref(session_id: str) -> str:
    normalized = str(session_id or "").strip()
    if not normalized:
        return ""
    encoded = base64.urlsafe_b64encode(normalized.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{_SESSION_REF_PREFIX}{encoded}"


def resolve_login_session_id(session_ref_or_id: str) -> str:
    candidate = str(session_ref_or_id or "").strip()
    if not candidate:
        return ""
    if candidate.startswith(_SESSION_REF_PREFIX):
        encoded = candidate[len(_SESSION_REF_PREFIX) :]
        if not encoded:
            return ""
        padding = "=" * ((4 - (len(encoded) % 4)) % 4)
        try:
            decoded = base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8")
        except Exception:
            return ""
        return str(decoded or "").strip()
    return candidate


def setup_status_command_for_platform(chat_platform: str | None) -> str:
    platform = str(chat_platform or "").strip().lower()
    if platform == "discord":
        return "/setup-status"
    if platform == "telegram":
        return "/setup_status"
    return "/setup-status (Discord) or /setup_status (Telegram)"


def build_setup_completed_chat_message(*, session_id: str, ready: bool) -> str:
    command_hint = setup_status_command_for_platform(None)
    resolved_session_id = resolve_login_session_id(session_id)
    if resolved_session_id:
        record = get_auth_session(resolved_session_id)
        command_hint = setup_status_command_for_platform(getattr(record, "chat_platform", None))
    prefix = "✅ Setup completed." if ready else "⚠️ Setup updated."
    return f"{prefix}\nRun {command_hint} to check setup status."


def create_login_session_for_user(
    *,
    nexus_id: str,
    discord_user_id: str,
    discord_username: str | None,
    chat_platform: str | None = None,
    chat_id: str | None = None,
    onboarding_message_id: str | None = None,
) -> str:
    cleanup_expired_auth_sessions()
    return create_auth_session(
        nexus_id=str(nexus_id),
        discord_user_id=str(discord_user_id),
        discord_username=discord_username,
        chat_platform=chat_platform,
        chat_id=chat_id,
        onboarding_message_id=onboarding_message_id,
        ttl_seconds=_session_ttl_seconds(),
    )


def register_onboarding_message(
    *,
    session_id: str,
    chat_platform: str,
    chat_id: str,
    message_id: str,
) -> None:
    update_auth_session(
        session_id=str(session_id),
        chat_platform=str(chat_platform or "").strip().lower(),
        chat_id=str(chat_id or "").strip(),
        onboarding_message_id=str(message_id or "").strip(),
    )


def start_oauth_flow(session_id: str, provider: str = "github") -> tuple[str, str]:
    """Return (auth_url, state) and persist one-time state hash for provider."""
    auth_provider = _normalize_provider(provider)
    state = secrets.token_urlsafe(32)

    resolved_session_id = resolve_login_session_id(session_id)
    record = get_auth_session(str(resolved_session_id))
    if not record:
        raise ValueError("Invalid session")
    if record.expires_at < _now_utc():
        update_auth_session(session_id=str(resolved_session_id), status="expired")
        raise ValueError("Session expired")

    from nexus.core.auth.credential_store import hash_oauth_state

    update_auth_session(
        session_id=str(resolved_session_id),
        oauth_provider=auth_provider,
        oauth_state_hash=hash_oauth_state(state),
        status="pending",
        last_error="",
    )

    if auth_provider == "github":
        client_id = _required_env("NEXUS_GITHUB_CLIENT_ID")
        _required_env("NEXUS_GITHUB_CLIENT_SECRET")
        callback_url = _oauth_callback_url("github")
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": callback_url,
                "state": state,
                "scope": "read:user read:org repo",
            }
        )
        return f"https://github.com/login/oauth/authorize?{query}", state

    client_id = _required_env("NEXUS_GITLAB_CLIENT_ID")
    _required_env("NEXUS_GITLAB_CLIENT_SECRET")
    callback_url = _oauth_callback_url("gitlab")
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": callback_url,
            "response_type": "code",
            "state": state,
            "scope": "read_user api",
        }
    )
    return f"{_gitlab_base_url()}/oauth/authorize?{query}", state


def _github_exchange_code_for_token(code: str) -> dict[str, Any]:
    client_id = _required_env("NEXUS_GITHUB_CLIENT_ID")
    client_secret = _required_env("NEXUS_GITHUB_CLIENT_SECRET")
    callback_url = _oauth_callback_url("github")
    response = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": str(code or "").strip(),
            "redirect_uri": callback_url,
        },
        timeout=15,
    )
    if response.status_code != 200:
        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                msg = str(payload.get("error_description") or payload.get("error") or "").strip()
                if msg:
                    detail = f": {msg}"
        except Exception:
            text = str(response.text or "").strip().replace("\n", " ")
            if text:
                detail = f": {text[:200]}"
        raise RuntimeError(f"OAuth exchange failed ({response.status_code}){detail}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("OAuth exchange returned invalid payload")
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError(f"OAuth access token missing ({payload.get('error') or 'unknown error'})")
    return payload


def _gitlab_exchange_code_for_token(code: str) -> dict[str, Any]:
    client_id = _required_env("NEXUS_GITLAB_CLIENT_ID")
    client_secret = _required_env("NEXUS_GITLAB_CLIENT_SECRET")
    callback_url = _oauth_callback_url("gitlab")
    response = requests.post(
        f"{_gitlab_base_url()}/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": str(code or "").strip(),
            "grant_type": "authorization_code",
            "redirect_uri": callback_url,
        },
        timeout=15,
    )
    if response.status_code != 200:
        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                msg = str(payload.get("error_description") or payload.get("error") or "").strip()
                if msg:
                    detail = f": {msg}"
        except Exception:
            text = str(response.text or "").strip().replace("\n", " ")
            if text:
                detail = f": {text[:200]}"
        raise RuntimeError(f"OAuth exchange failed ({response.status_code}){detail}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("OAuth exchange returned invalid payload")
    token = str(payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError(f"OAuth access token missing ({payload.get('error') or 'unknown error'})")
    return payload


def _github_get(path: str, token: str) -> requests.Response:
    url = f"https://api.github.com{path}"
    common_headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = requests.get(
        url,
        headers={**common_headers, "Authorization": f"token {token}"},
        timeout=15,
    )
    if response.status_code in {401, 403}:
        response = requests.get(
            url,
            headers={**common_headers, "Authorization": f"Bearer {token}"},
            timeout=15,
        )
    return response


def _gitlab_get(path: str, token: str) -> requests.Response:
    return requests.get(
        f"{_gitlab_base_url()}{path}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
        timeout=15,
    )


def _fetch_github_profile(token: str) -> dict[str, Any]:
    response = _github_get("/user", token)
    if response.status_code != 200:
        raise RuntimeError(f"GitHub /user failed ({response.status_code})")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub /user returned invalid payload")
    return payload


def _fetch_github_org_logins(token: str) -> set[str]:
    response = _github_get("/user/orgs?per_page=100", token)
    if response.status_code != 200:
        raise RuntimeError(f"GitHub /user/orgs failed ({response.status_code})")
    payload = response.json()
    if not isinstance(payload, list):
        return set()
    logins: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        login = str(item.get("login") or "").strip().lower()
        if login:
            logins.add(login)
    return logins


def _fetch_gitlab_profile(token: str) -> dict[str, Any]:
    response = _gitlab_get("/api/v4/user", token)
    if response.status_code != 200:
        raise RuntimeError(f"GitLab /user failed ({response.status_code})")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("GitLab /user returned invalid payload")
    return payload


def _fetch_gitlab_group_paths(token: str) -> set[str]:
    groups: set[str] = set()
    page = 1
    while page <= 10:
        response = _gitlab_get(f"/api/v4/groups?per_page=100&page={page}", token)
        if response.status_code != 200:
            raise RuntimeError(f"GitLab /groups failed ({response.status_code})")
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            break
        for item in payload:
            if not isinstance(item, dict):
                continue
            full_path = str(item.get("full_path") or "").strip().lower()
            if full_path:
                groups.add(full_path)
        if len(payload) < 100:
            break
        page += 1
    return groups


def _assert_valid_callback_session(state: str, provider: str) -> Any:
    session_record = get_auth_session_by_state(str(state))
    if not session_record:
        raise ValueError("Invalid or expired OAuth state")
    if session_record.expires_at < _now_utc():
        update_auth_session(session_id=session_record.session_id, status="expired")
        raise ValueError("Session expired")
    if session_record.status != "pending":
        raise ValueError("OAuth callback already used or session is no longer valid")
    expected_provider = _normalize_provider(provider)
    recorded_provider = str(session_record.oauth_provider or expected_provider).strip().lower()
    if recorded_provider != expected_provider:
        raise ValueError("OAuth provider mismatch for this session")
    return session_record


def complete_github_oauth(*, code: str, state: str) -> dict[str, Any]:
    """Complete GitHub callback and persist credentials + grants."""
    session_record = _assert_valid_callback_session(state, "github")

    oauth_payload = _github_exchange_code_for_token(code)
    access_token = str(oauth_payload.get("access_token") or "").strip()
    refresh_token = str(oauth_payload.get("refresh_token") or "").strip()
    expires_in = oauth_payload.get("expires_in")
    expires_at = None
    if isinstance(expires_in, int) and expires_in > 0:
        expires_at = _now_utc() + timedelta(seconds=expires_in)

    profile = _fetch_github_profile(access_token)
    github_user_id = int(profile.get("id") or 0)
    github_login = str(profile.get("login") or "").strip()
    if github_user_id <= 0 or not github_login:
        raise RuntimeError("GitHub profile missing id/login")

    orgs = _fetch_github_org_logins(access_token)
    allowed_orgs = _allowed_github_orgs()
    if allowed_orgs and not (orgs & allowed_orgs):
        update_auth_session(
            session_id=session_record.session_id,
            status="pending",
            last_error="User is not part of an allowed GitHub organization",
        )
        raise PermissionError("Your GitHub account is not in the allowed organizations")

    source_nexus_id = str(session_record.nexus_id)
    target_nexus_id = source_nexus_id
    existing = find_user_credentials_by_github_identity(
        github_user_id=github_user_id,
        github_login=github_login,
    )
    if existing and str(existing.nexus_id) != source_nexus_id:
        target_nexus_id = str(existing.nexus_id)

    encrypted_token = encrypt_secret(access_token, key_version=_key_version())
    encrypted_refresh = encrypt_secret(refresh_token, key_version=_key_version()) if refresh_token else None
    upsert_github_credentials(
        nexus_id=target_nexus_id,
        github_user_id=github_user_id,
        github_login=github_login,
        github_token_enc=encrypted_token,
        github_refresh_token_enc=encrypted_refresh,
        github_token_expires_at=expires_at,
        org_verified=True,
        org_verified_at=_now_utc(),
        key_version=_key_version(),
    )

    grants_count = sync_user_project_access(
        nexus_id=target_nexus_id,
        github_token=access_token,
        github_login=github_login,
    )
    update_auth_session(
        session_id=session_record.session_id,
        nexus_id=target_nexus_id,
        oauth_provider="github",
        status="oauth_done",
        last_error="",
    )
    return {
        "session_id": session_record.session_id,
        "nexus_id": target_nexus_id,
        "source_nexus_id": source_nexus_id,
        "provider": "github",
        "github_login": github_login,
        "orgs": sorted(orgs),
        "grants_count": grants_count,
    }


def complete_gitlab_oauth(*, code: str, state: str) -> dict[str, Any]:
    """Complete GitLab callback and persist credentials + grants."""
    session_record = _assert_valid_callback_session(state, "gitlab")

    oauth_payload = _gitlab_exchange_code_for_token(code)
    access_token = str(oauth_payload.get("access_token") or "").strip()
    refresh_token = str(oauth_payload.get("refresh_token") or "").strip()
    expires_in = oauth_payload.get("expires_in")
    expires_at = None
    if isinstance(expires_in, int) and expires_in > 0:
        expires_at = _now_utc() + timedelta(seconds=expires_in)

    profile = _fetch_gitlab_profile(access_token)
    gitlab_user_id = int(profile.get("id") or 0)
    gitlab_username = str(profile.get("username") or "").strip()
    if gitlab_user_id <= 0 or not gitlab_username:
        raise RuntimeError("GitLab profile missing id/username")

    groups = _fetch_gitlab_group_paths(access_token)
    allowed_groups = _allowed_gitlab_groups()
    if allowed_groups and not (groups & allowed_groups):
        update_auth_session(
            session_id=session_record.session_id,
            status="pending",
            last_error="User is not part of an allowed GitLab group",
        )
        raise PermissionError("Your GitLab account is not in the allowed groups")

    source_nexus_id = str(session_record.nexus_id)
    target_nexus_id = source_nexus_id
    existing = find_user_credentials_by_gitlab_identity(
        gitlab_user_id=gitlab_user_id,
        gitlab_username=gitlab_username,
    )
    if existing and str(existing.nexus_id) != source_nexus_id:
        target_nexus_id = str(existing.nexus_id)

    encrypted_token = encrypt_secret(access_token, key_version=_key_version())
    encrypted_refresh = encrypt_secret(refresh_token, key_version=_key_version()) if refresh_token else None
    upsert_gitlab_credentials(
        nexus_id=target_nexus_id,
        gitlab_user_id=gitlab_user_id,
        gitlab_username=gitlab_username,
        gitlab_token_enc=encrypted_token,
        gitlab_refresh_token_enc=encrypted_refresh,
        gitlab_token_expires_at=expires_at,
        org_verified=True,
        org_verified_at=_now_utc(),
        key_version=_key_version(),
    )

    grants_count = sync_user_gitlab_project_access(
        nexus_id=target_nexus_id,
        gitlab_token=access_token,
        gitlab_username=gitlab_username,
    )
    update_auth_session(
        session_id=session_record.session_id,
        nexus_id=target_nexus_id,
        oauth_provider="gitlab",
        status="oauth_done",
        last_error="",
    )
    return {
        "session_id": session_record.session_id,
        "nexus_id": target_nexus_id,
        "source_nexus_id": source_nexus_id,
        "provider": "gitlab",
        "gitlab_username": gitlab_username,
        "groups": sorted(groups),
        "grants_count": grants_count,
    }


def _validate_codex_api_key_with_provider(api_key: str) -> tuple[bool, str]:
    should_validate = str(os.getenv("NEXUS_AUTH_VALIDATE_CODEX_KEY", "false")).strip().lower()
    if should_validate not in {"1", "true", "yes", "on"}:
        return True, ""
    response = requests.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10,
    )
    if response.status_code == 200:
        return True, ""
    return False, f"Codex/OpenAI key validation failed ({response.status_code})"


def _validate_codex_api_key_with_codex_cli_login(api_key: str) -> tuple[bool, str]:
    codex_cli_path = str(os.getenv("CODEX_CLI_PATH", "codex")).strip() or "codex"
    try:
        help_result = subprocess.run(
            [codex_cli_path, "login", "--help"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except FileNotFoundError:
        return False, "Codex CLI is not installed or not in PATH for auth validation."
    except Exception as exc:
        return False, f"Codex CLI login capability check failed: {exc}"

    output = f"{help_result.stdout}\n{help_result.stderr}".lower()
    if "--with-api-key" not in output:
        return (
            False,
            "Codex CLI does not support '--with-api-key'. Upgrade Codex CLI to continue.",
        )

    try:
        with tempfile.TemporaryDirectory(prefix="nexus-codex-auth-") as tmp_codex_home:
            login_env = {**os.environ, "CODEX_HOME": tmp_codex_home}
            login_result = subprocess.run(
                [codex_cli_path, "login", "--with-api-key"],
                env=login_env,
                input=f"{api_key}\n",
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
    except subprocess.TimeoutExpired:
        return False, "Codex CLI login validation timed out."
    except Exception as exc:
        return False, f"Codex CLI login validation failed: {exc}"

    if login_result.returncode == 0:
        return True, ""
    stderr_tail = (login_result.stderr or login_result.stdout or "").strip().splitlines()
    reason = stderr_tail[-1] if stderr_tail else "unknown error"
    return False, f"Codex CLI login validation failed: {reason}"


def _validate_gemini_api_key_with_provider(api_key: str) -> tuple[bool, str]:
    should_validate = str(os.getenv("NEXUS_AUTH_VALIDATE_GEMINI_KEY", "false")).strip().lower()
    if should_validate not in {"1", "true", "yes", "on"}:
        return True, ""
    response = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": api_key},
        timeout=10,
    )
    if response.status_code == 200:
        return True, ""
    return False, f"Gemini key validation failed ({response.status_code})"


def _validate_claude_api_key_with_provider(api_key: str) -> tuple[bool, str]:
    should_validate = str(os.getenv("NEXUS_AUTH_VALIDATE_CLAUDE_KEY", "false")).strip().lower()
    if should_validate not in {"1", "true", "yes", "on"}:
        return True, ""
    response = requests.get(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout=10,
    )
    if response.status_code == 200:
        return True, ""
    return False, f"Claude key validation failed ({response.status_code})"


def _cli_account_auth_mode_enabled() -> bool:
    mode = str(os.getenv("NEXUS_CLI_AUTH_MODE", "account")).strip().lower()
    return mode in {"account", "auto"}


def store_ai_provider_keys(
    *,
    session_id: str,
    codex_api_key: str | None = None,
    gemini_api_key: str | None = None,
    claude_api_key: str | None = None,
    copilot_github_token: str | None = None,
    allow_copilot: bool = False,
    use_codex_account: bool | None = None,
    use_gemini_account: bool | None = None,
    use_claude_account: bool | None = None,
    use_copilot_account: bool | None = None,
) -> dict[str, Any]:
    resolved_session_id = resolve_login_session_id(session_id)
    session_record = get_auth_session(str(resolved_session_id))
    if not session_record:
        raise ValueError("Invalid session")
    if session_record.expires_at < _now_utc():
        update_auth_session(session_id=str(resolved_session_id), status="expired")
        raise ValueError("Session expired")
    if session_record.status not in {"oauth_done", "completed"}:
        raise ValueError("OAuth step is not complete yet")

    codex_provided = codex_api_key is not None
    gemini_provided = gemini_api_key is not None
    claude_provided = claude_api_key is not None
    copilot_provided = copilot_github_token is not None
    codex_candidate = str(codex_api_key or "").strip()
    gemini_candidate = str(gemini_api_key or "").strip()
    claude_candidate = str(claude_api_key or "").strip()
    copilot_candidate = str(copilot_github_token or "").strip()
    record = get_user_credentials(session_record.nexus_id)
    existing_ai_key_set = bool(
        record
        and (
            record.codex_api_key_enc
            or record.gemini_api_key_enc
            or record.claude_api_key_enc
        )
    )
    has_github_for_copilot = bool(record and record.github_token_enc and record.github_login)
    has_stored_copilot_token = bool(record and record.copilot_github_token_enc)
    codex_account_enabled = bool(use_codex_account) if use_codex_account is not None else bool(
        record and getattr(record, "codex_account_enabled", False)
    )
    gemini_account_enabled = bool(use_gemini_account) if use_gemini_account is not None else bool(
        record and getattr(record, "gemini_account_enabled", False)
    )
    claude_account_enabled = bool(use_claude_account) if use_claude_account is not None else bool(
        record and getattr(record, "claude_account_enabled", False)
    )
    copilot_account_enabled = bool(use_copilot_account) if use_copilot_account is not None else bool(
        record and getattr(record, "copilot_account_enabled", False)
    )
    account_selected = bool(
        codex_account_enabled or gemini_account_enabled or claude_account_enabled or copilot_account_enabled
    )
    if account_selected and not _cli_account_auth_mode_enabled():
        raise ValueError(
            "CLI account login is selected, but NEXUS_CLI_AUTH_MODE is not account/auto. "
            "Set NEXUS_CLI_AUTH_MODE=account (recommended) or auto."
        )

    if copilot_account_enabled and not has_github_for_copilot:
        raise ValueError(
            "Copilot account mode requires linked GitHub OAuth. Run `/login github` first."
        )

    if not codex_candidate and not gemini_candidate and not claude_candidate and allow_copilot:
        copilot_available = bool(
            has_github_for_copilot
            or has_stored_copilot_token
            or (copilot_provided and copilot_candidate)
            or copilot_account_enabled
        )
        if not copilot_available:
            raise ValueError(
                "Copilot requires a linked GitHub account or Copilot Token. "
                "Run `/login github`, provide Copilot Token, or disable Copilot."
            )

    codex_encrypted: str | None = None
    gemini_encrypted: str | None = None
    claude_encrypted: str | None = None
    copilot_encrypted: str | None = None

    if codex_provided and not codex_candidate:
        codex_encrypted = ""
    elif codex_candidate:
        if len(codex_candidate) < 16:
            raise ValueError("Codex API key is too short")
        valid, error_message = _validate_codex_api_key_with_codex_cli_login(codex_candidate)
        if not valid:
            raise ValueError(error_message or "Invalid Codex API key")
        valid, error_message = _validate_codex_api_key_with_provider(codex_candidate)
        if not valid:
            raise ValueError(error_message or "Invalid Codex API key")
        codex_encrypted = encrypt_secret(codex_candidate, key_version=_key_version())

    if gemini_provided and not gemini_candidate:
        gemini_encrypted = ""
    elif gemini_candidate:
        if len(gemini_candidate) < 16:
            raise ValueError("Gemini API key is too short")
        valid, error_message = _validate_gemini_api_key_with_provider(gemini_candidate)
        if not valid:
            raise ValueError(error_message or "Invalid Gemini API key")
        gemini_encrypted = encrypt_secret(gemini_candidate, key_version=_key_version())

    if claude_provided and not claude_candidate:
        claude_encrypted = ""
    elif claude_candidate:
        if len(claude_candidate) < 16:
            raise ValueError("Claude API key is too short")
        valid, error_message = _validate_claude_api_key_with_provider(claude_candidate)
        if not valid:
            raise ValueError(error_message or "Invalid Claude API key")
        claude_encrypted = encrypt_secret(claude_candidate, key_version=_key_version())

    if copilot_provided and not copilot_candidate:
        copilot_encrypted = ""
    elif copilot_candidate:
        if len(copilot_candidate) < 16:
            raise ValueError("Copilot Token is too short")
        copilot_encrypted = encrypt_secret(copilot_candidate, key_version=_key_version())

    upsert_ai_provider_keys(
        nexus_id=session_record.nexus_id,
        codex_api_key_enc=codex_encrypted,
        gemini_api_key_enc=gemini_encrypted,
        claude_api_key_enc=claude_encrypted,
        copilot_github_token_enc=copilot_encrypted,
        codex_account_enabled=codex_account_enabled,
        gemini_account_enabled=gemini_account_enabled,
        claude_account_enabled=claude_account_enabled,
        copilot_account_enabled=copilot_account_enabled,
        key_version=_key_version(),
    )

    update_auth_session(
        session_id=str(resolved_session_id),
        status="completed",
        last_error="",
        used_at=_now_utc(),
    )

    status = get_setup_status(session_record.nexus_id)
    return {
        "session_id": session_record.session_id,
        "session_ref": format_login_session_ref(session_record.session_id),
        "nexus_id": session_record.nexus_id,
        "ready": bool(status.get("ready")),
        "project_access_count": int(status.get("project_access_count") or 0),
    }


def start_provider_account_login(*, session_id: str, provider: str) -> dict[str, Any]:
    resolved_session_id = resolve_login_session_id(session_id)
    session_record = get_auth_session(str(resolved_session_id))
    if not session_record:
        raise ValueError("Invalid session")
    if session_record.expires_at < _now_utc():
        update_auth_session(session_id=str(resolved_session_id), status="expired")
        raise ValueError("Session expired")
    if session_record.status not in {"oauth_done", "completed"}:
        raise ValueError("OAuth step is not complete yet")

    provider_name = _normalize_provider_account_connector(provider)
    provider_label = _provider_account_label(provider_name)
    login_cmd = _provider_account_login_command(provider_name)
    if provider_name == "gemini" and _gemini_should_use_pty_wrapper():
        wrapped = _wrap_command_with_script_tty(login_cmd)
        if wrapped != login_cmd:
            login_cmd = wrapped
        else:
            logger.warning(
                "[provider-connect] gemini pty wrapper unavailable; continuing without pseudo-tty"
            )
    provider_home_env = str(_PROVIDER_ACCOUNT_CONNECT_HOME_ENV.get(provider_name) or "").strip()
    provider_home = _provider_runtime_home(provider=provider_name, nexus_id=session_record.nexus_id)
    user_home = _provider_user_runtime_home(nexus_id=session_record.nexus_id)

    _ensure_private_dir(user_home)
    _ensure_private_dir(provider_home)
    _ensure_private_dir(os.path.join(provider_home, "log"))
    _ensure_private_dir(os.path.join(provider_home, "memories"))
    _prepare_provider_runtime_state(provider=provider_name, user_home=user_home)
    log_dir = os.path.join(provider_home, "device-auth")
    _ensure_private_dir(log_dir)
    timestamp = _now_utc().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"{provider_name}_{timestamp}.log")

    job_key = _device_job_key(session_id=str(session_record.session_id), provider=provider_name)
    with _DEVICE_AUTH_LOCK:
        existing = _DEVICE_AUTH_JOBS.get(job_key)
        if isinstance(existing, dict):
            existing_proc = existing.get("process")
            if getattr(existing_proc, "poll", None) and existing_proc.poll() is None:
                log_text = _log_tail(str(existing.get("log_path") or ""))
                verify_url, user_code = _parse_device_auth_url_and_code(log_text)
                callback_url_hint = _parse_local_callback_url(log_text)
                requires_code = _requires_manual_auth_code(provider=provider_name, log_text=log_text)
                last_line = _truncate_for_log(_last_log_line(log_text, ""), limit=220)
                if last_line:
                    logger.info(
                        "[provider-connect] still pending provider=%s session=%s output=%s",
                        provider_name,
                        session_record.session_id,
                        last_line,
                    )
                return {
                    "started": False,
                    "session_id": session_record.session_id,
                    "session_ref": format_login_session_ref(session_record.session_id),
                    "provider": provider_name,
                    "state": "pending",
                    "verify_url": verify_url,
                    "user_code": user_code,
                    "callback_url_hint": callback_url_hint,
                    "requires_code": requires_code,
                    "message": f"{provider_label} account login is already running.",
                }
            existing_handle = existing.get("log_file")
            if existing_handle is not None:
                try:
                    existing_handle.close()
                except Exception:
                    pass
            _DEVICE_AUTH_JOBS.pop(job_key, None)

        try:
            log_file = open(log_path, "a", encoding="utf-8")
        except Exception as exc:
            raise RuntimeError(f"Unable to create device-auth log file: {exc}") from exc
        _ensure_private_file(log_path)

        env = {**os.environ, "HOME": user_home}
        if provider_home_env:
            env[provider_home_env] = provider_home
        if provider_name == "gemini":
            # Force browser URL output in server-side environments where opening a local browser is impossible.
            env.setdefault("NO_BROWSER", "true")
            # Gemini CLI variants resolve config from GEMINI_CLI_HOME while older builds use GEMINI_HOME/HOME.
            env.setdefault("GEMINI_CLI_HOME", user_home)
        process_stdin: int | Any = subprocess.DEVNULL
        if provider_name == "gemini":
            # Gemini asks for an authorization code on stdin after browser confirmation.
            process_stdin = subprocess.PIPE
        try:
            process = subprocess.Popen(
                login_cmd,
                cwd=provider_home,
                env=env,
                stdin=process_stdin,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except Exception as exc:
            try:
                log_file.close()
            except Exception:
                pass
            raise RuntimeError(f"Failed to start {provider_label} account login: {exc}") from exc

        _DEVICE_AUTH_JOBS[job_key] = {
            "provider": provider_name,
            "session_id": session_record.session_id,
            "nexus_id": session_record.nexus_id,
            "process": process,
            "log_path": log_path,
            "log_file": log_file,
            "started_at": _now_utc().isoformat(),
            "provider_home": provider_home,
            "user_home": user_home,
            "command": login_cmd,
            "last_status_line_logged": "",
        }

    logger.info(
        "[provider-connect] started provider=%s session=%s cmd=%s home=%s log=%s",
        provider_name,
        session_record.session_id,
        " ".join(login_cmd),
        provider_home,
        log_path,
    )
    return {
        "started": True,
        "session_id": session_record.session_id,
        "session_ref": format_login_session_ref(session_record.session_id),
        "provider": provider_name,
        "state": "starting",
        "verify_url": "",
        "user_code": "",
        "message": f"{provider_label} account connection started. Poll status for device instructions.",
    }


def get_provider_account_login_status(*, session_id: str, provider: str) -> dict[str, Any]:
    resolved_session_id = resolve_login_session_id(session_id)
    session_record = get_auth_session(str(resolved_session_id))
    if not session_record:
        return {"exists": False, "state": "invalid_session", "message": "Invalid session"}

    provider_name = str(provider or "").strip().lower()
    if provider_name not in _SUPPORTED_PROVIDER_ACCOUNT_CONNECTORS:
        supported = ", ".join(_SUPPORTED_PROVIDER_ACCOUNT_CONNECTORS)
        return {
            "exists": False,
            "session_id": session_record.session_id,
            "session_ref": format_login_session_ref(session_record.session_id),
            "provider": provider_name,
            "state": "unsupported_provider",
            "message": f"Unsupported provider account-connect target. Supported: {supported}",
        }
    provider_label = _provider_account_label(provider_name)
    provider_toggle_key = f"{provider_name}_account_enabled"
    store_toggle_field = _provider_account_toggle_field(provider_name)
    if not store_toggle_field:
        return {
            "exists": False,
            "session_id": session_record.session_id,
            "session_ref": format_login_session_ref(session_record.session_id),
            "provider": provider_name,
            "state": "unsupported_provider",
            "message": f"Unsupported provider account-connect target: {provider_name}",
        }

    setup = get_setup_status(session_record.nexus_id)
    connected_flag = bool(setup.get(provider_toggle_key))
    job_key = _device_job_key(session_id=session_record.session_id, provider=provider_name)
    force_connect_via_existing_login = False

    with _DEVICE_AUTH_LOCK:
        job = _DEVICE_AUTH_JOBS.get(job_key)
        if not isinstance(job, dict):
            return {
                "exists": False,
                "session_id": session_record.session_id,
                "session_ref": format_login_session_ref(session_record.session_id),
                "provider": provider_name,
                "state": "idle",
                "connected": connected_flag,
                "verify_url": "",
                "user_code": "",
                "message": "No active account-connect job.",
            }

        process = job.get("process")
        log_path = str(job.get("log_path") or "")
        log_text = _log_tail(log_path)
        verify_url, user_code = _parse_device_auth_url_and_code(log_text)
        callback_url_hint = _parse_local_callback_url(log_text)
        requires_code = _requires_manual_auth_code(provider=provider_name, log_text=log_text)
        exit_code = process.poll() if getattr(process, "poll", None) else None

        if exit_code is None:
            if provider_name == "gemini" and not verify_url and _gemini_has_logged_in_identity(log_text):
                force_connect_via_existing_login = True
                if getattr(process, "terminate", None):
                    try:
                        process.terminate()
                    except Exception:
                        pass
                log_file = job.get("log_file")
                if log_file is not None:
                    try:
                        log_file.close()
                    except Exception:
                        pass
                _DEVICE_AUTH_JOBS.pop(job_key, None)

            if not force_connect_via_existing_login:
                status_line = _truncate_for_log(_last_log_line(log_text, ""), limit=220)
                previous_logged_line = str(job.get("last_status_line_logged") or "").strip()
                if status_line and status_line != previous_logged_line:
                    logger.info(
                        "[provider-connect] pending provider=%s session=%s output=%s",
                        provider_name,
                        session_record.session_id,
                        status_line,
                    )
                    job["last_status_line_logged"] = status_line
                return {
                    "exists": True,
                    "session_id": session_record.session_id,
                    "session_ref": format_login_session_ref(session_record.session_id),
                    "provider": provider_name,
                    "state": "pending",
                    "connected": connected_flag,
                    "verify_url": verify_url,
                    "user_code": user_code,
                    "callback_url_hint": callback_url_hint,
                    "requires_code": requires_code,
                    "message": (
                        "Gemini login is waiting for authorization code submission."
                        if requires_code
                        else f"{provider_label} login is waiting for browser confirmation."
                    ),
                }

        if not force_connect_via_existing_login:
            log_file = job.get("log_file")
            if log_file is not None:
                try:
                    log_file.close()
                except Exception:
                    pass
            _DEVICE_AUTH_JOBS.pop(job_key, None)

    if force_connect_via_existing_login:
        try:
            store_ai_provider_keys(**{"session_id": session_record.session_id, store_toggle_field: True})
        except Exception as exc:
            return {
                "exists": True,
                "session_id": session_record.session_id,
                "session_ref": format_login_session_ref(session_record.session_id),
                "provider": provider_name,
                "state": "connected_but_not_saved",
                "connected": connected_flag,
                "verify_url": verify_url,
                "user_code": user_code,
                "callback_url_hint": callback_url_hint,
                "message": f"Login detected but saving setup failed: {exc}",
            }

        refreshed = get_setup_status(session_record.nexus_id)
        logger.info(
            "[provider-connect] connected provider=%s session=%s (existing login detected)",
            provider_name,
            session_record.session_id,
        )
        return {
            "exists": True,
            "session_id": session_record.session_id,
            "session_ref": format_login_session_ref(session_record.session_id),
            "provider": provider_name,
            "state": "connected",
            "connected": bool(refreshed.get(provider_toggle_key)),
            "verify_url": verify_url,
            "user_code": user_code,
            "callback_url_hint": callback_url_hint,
            "message": f"{provider_label} account is already authenticated and has been connected.",
        }

    if int(exit_code) == 0:
        try:
            store_ai_provider_keys(**{"session_id": session_record.session_id, store_toggle_field: True})
        except Exception as exc:
            return {
                "exists": True,
                "session_id": session_record.session_id,
                "session_ref": format_login_session_ref(session_record.session_id),
                "provider": provider_name,
                "state": "connected_but_not_saved",
                "connected": connected_flag,
                "verify_url": verify_url,
                "user_code": user_code,
                "message": f"Login succeeded but saving setup failed: {exc}",
            }

        refreshed = get_setup_status(session_record.nexus_id)
        logger.info(
            "[provider-connect] connected provider=%s session=%s",
            provider_name,
            session_record.session_id,
        )
        return {
            "exists": True,
            "session_id": session_record.session_id,
            "session_ref": format_login_session_ref(session_record.session_id),
            "provider": provider_name,
            "state": "connected",
                "connected": bool(refreshed.get(provider_toggle_key)),
                "verify_url": verify_url,
                "user_code": user_code,
                "callback_url_hint": callback_url_hint,
                "message": f"{provider_label} account connected successfully.",
            }

    failure_state, failure_message = _classify_provider_account_login_failure(
        provider=provider_name,
        log_text=log_text,
    )
    logger.error(
        "[provider-connect] failed provider=%s session=%s state=%s message=%s output_tail=%s",
        provider_name,
        session_record.session_id,
        failure_state,
        _truncate_for_log(failure_message, limit=260),
        _format_log_excerpt(log_text, max_chars=900),
    )
    return {
        "exists": True,
        "session_id": session_record.session_id,
        "session_ref": format_login_session_ref(session_record.session_id),
        "provider": provider_name,
        "state": failure_state,
        "connected": connected_flag,
        "verify_url": verify_url,
        "user_code": user_code,
        "callback_url_hint": callback_url_hint,
        "message": failure_message,
    }


def relay_provider_account_login_callback(
    *,
    session_id: str,
    provider: str,
    callback_url: str,
) -> dict[str, Any]:
    resolved_session_id = resolve_login_session_id(session_id)
    session_record = get_auth_session(str(resolved_session_id))
    if not session_record:
        return {"relayed": False, "state": "invalid_session", "message": "Invalid session"}

    provider_name = _normalize_provider_account_connector(provider)
    if provider_name != "gemini":
        return {
            "relayed": False,
            "session_id": session_record.session_id,
            "session_ref": format_login_session_ref(session_record.session_id),
            "provider": provider_name,
            "state": "unsupported_provider",
            "message": "Callback relay is currently supported only for Gemini.",
        }

    try:
        safe_callback_url = _validate_local_callback_url(callback_url)
    except ValueError as exc:
        return {
            "relayed": False,
            "session_id": session_record.session_id,
            "session_ref": format_login_session_ref(session_record.session_id),
            "provider": provider_name,
            "state": "invalid_callback_url",
            "message": str(exc),
        }

    job_key = _device_job_key(session_id=str(session_record.session_id), provider=provider_name)
    with _DEVICE_AUTH_LOCK:
        job = _DEVICE_AUTH_JOBS.get(job_key)
        process = job.get("process") if isinstance(job, dict) else None
        is_running = bool(getattr(process, "poll", None) and process.poll() is None)
    if not is_running:
        idle_status = get_provider_account_login_status(
            session_id=session_record.session_id,
            provider=provider_name,
        )
        idle_status["relayed"] = False
        if str(idle_status.get("state") or "").strip().lower() == "idle":
            idle_status["state"] = "no_active_job"
            idle_status["message"] = "No active Gemini login job. Click Connect Gemini Account first."
        return idle_status

    timeout_raw = str(os.getenv("NEXUS_PROVIDER_CALLBACK_RELAY_TIMEOUT_SECONDS", "12")).strip()
    try:
        timeout_seconds = max(2, min(30, int(timeout_raw)))
    except ValueError:
        timeout_seconds = 12

    try:
        relay_response = requests.get(
            safe_callback_url,
            timeout=timeout_seconds,
            allow_redirects=False,
        )
        logger.info(
            "[provider-connect] relay-callback provider=%s session=%s callback=%s http_status=%s",
            provider_name,
            session_record.session_id,
            safe_callback_url,
            relay_response.status_code,
        )
    except Exception as exc:
        logger.error(
            "[provider-connect] relay-callback failed provider=%s session=%s callback=%s error=%s",
            provider_name,
            session_record.session_id,
            safe_callback_url,
            exc,
        )
        pending_status = get_provider_account_login_status(
            session_id=session_record.session_id,
            provider=provider_name,
        )
        pending_status["relayed"] = False
        pending_status["state"] = "callback_relay_failed"
        pending_status["message"] = f"Failed to relay callback URL to Gemini CLI: {exc}"
        return pending_status

    status = get_provider_account_login_status(session_id=session_record.session_id, provider=provider_name)
    status["relayed"] = True
    status["relay_http_status"] = int(relay_response.status_code)
    if str(status.get("state") or "").strip().lower() in {"starting", "pending"}:
        status["message"] = (
            "Gemini callback URL relayed. Waiting for Gemini login confirmation..."
        )
    return status


def submit_provider_account_login_code(
    *,
    session_id: str,
    provider: str,
    authorization_code: str,
) -> dict[str, Any]:
    resolved_session_id = resolve_login_session_id(session_id)
    session_record = get_auth_session(str(resolved_session_id))
    if not session_record:
        return {"submitted": False, "state": "invalid_session", "message": "Invalid session"}

    provider_name = _normalize_provider_account_connector(provider)
    if provider_name != "gemini":
        return {
            "submitted": False,
            "session_id": session_record.session_id,
            "session_ref": format_login_session_ref(session_record.session_id),
            "provider": provider_name,
            "state": "unsupported_provider",
            "message": "Authorization-code submission is currently supported only for Gemini.",
        }

    code_value = _normalize_provider_auth_code(authorization_code)

    job_key = _device_job_key(session_id=str(session_record.session_id), provider=provider_name)
    process: Any = None
    with _DEVICE_AUTH_LOCK:
        job = _DEVICE_AUTH_JOBS.get(job_key)
        if isinstance(job, dict):
            process = job.get("process")
    if not job or not isinstance(job, dict):
        idle_status = get_provider_account_login_status(
            session_id=session_record.session_id,
            provider=provider_name,
        )
        idle_status["submitted"] = False
        if str(idle_status.get("state") or "").strip().lower() == "idle":
            idle_status["state"] = "no_active_job"
            idle_status["message"] = "No active Gemini login job. Click Connect Gemini Account first."
        return idle_status

    if not (getattr(process, "poll", None) and process.poll() is None):
        completed_status = get_provider_account_login_status(
            session_id=session_record.session_id,
            provider=provider_name,
        )
        completed_status["submitted"] = False
        if str(completed_status.get("state") or "").strip().lower() == "idle":
            completed_status["state"] = "no_active_job"
            completed_status["message"] = "Gemini login process is no longer active."
        return completed_status

    stdin_handle = getattr(process, "stdin", None)
    if stdin_handle is None:
        return {
            "submitted": False,
            "session_id": session_record.session_id,
            "session_ref": format_login_session_ref(session_record.session_id),
            "provider": provider_name,
            "state": "stdin_unavailable",
            "message": "Gemini login stdin is unavailable; restart Connect Gemini Account.",
        }

    try:
        stdin_handle.write(f"{code_value}\n")
        stdin_handle.flush()
    except Exception as exc:
        logger.error(
            "[provider-connect] submit-code failed provider=%s session=%s error=%s",
            provider_name,
            session_record.session_id,
            exc,
        )
        return {
            "submitted": False,
            "session_id": session_record.session_id,
            "session_ref": format_login_session_ref(session_record.session_id),
            "provider": provider_name,
            "state": "submit_failed",
            "message": f"Failed to submit authorization code to Gemini CLI: {exc}",
        }

    logger.info(
        "[provider-connect] submit-code provider=%s session=%s",
        provider_name,
        session_record.session_id,
    )
    status = get_provider_account_login_status(
        session_id=session_record.session_id,
        provider=provider_name,
    )
    status["submitted"] = True
    if str(status.get("state") or "").strip().lower() in {"starting", "pending"}:
        status["message"] = "Authorization code submitted to Gemini CLI. Waiting for confirmation..."
    return status


def get_session_and_setup_status(session_id: str) -> dict[str, Any]:
    resolved_session_id = resolve_login_session_id(session_id)
    session_record = get_auth_session(str(resolved_session_id))
    if not session_record:
        return {"exists": False}
    setup = get_setup_status(session_record.nexus_id)
    return {
        "exists": True,
        "session_id": session_record.session_id,
        "session_ref": format_login_session_ref(session_record.session_id),
        "status": session_record.status,
        "provider": session_record.oauth_provider,
        "expires_at": session_record.expires_at.isoformat() if session_record.expires_at else None,
        "last_error": session_record.last_error,
        "nexus_id": session_record.nexus_id,
        "setup": setup,
    }


def get_latest_login_session_status(nexus_id: str) -> dict[str, Any]:
    record = get_latest_auth_session_for_nexus(str(nexus_id))
    if not record:
        return {"exists": False}
    return {
        "exists": True,
        "session_id": record.session_id,
        "session_ref": format_login_session_ref(record.session_id),
        "status": record.status,
        "provider": record.oauth_provider,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "last_error": record.last_error,
        "nexus_id": record.nexus_id,
    }


def start_provider_account_login_for_nexus(*, nexus_id: str, provider: str) -> dict[str, Any]:
    record = get_latest_auth_session_for_nexus(str(nexus_id))
    if not record:
        return {
            "started": False,
            "exists": False,
            "state": "oauth_required",
            "message": "No existing OAuth session found. Run /login github or /login gitlab first.",
        }

    session_ref = format_login_session_ref(record.session_id)
    if record.expires_at and record.expires_at < _now_utc():
        return {
            "started": False,
            "exists": True,
            "session_id": record.session_id,
            "session_ref": session_ref,
            "state": "oauth_required",
            "message": "Latest OAuth session expired. Run /login github or /login gitlab again.",
        }

    status = str(record.status or "").strip().lower()
    if status not in {"oauth_done", "completed"}:
        return {
            "started": False,
            "exists": True,
            "session_id": record.session_id,
            "session_ref": session_ref,
            "state": "oauth_required",
            "message": "OAuth setup is incomplete. Finish /login github or /login gitlab first.",
        }

    start_result = start_provider_account_login(session_id=record.session_id, provider=provider)
    login_status = get_provider_account_login_status(session_id=record.session_id, provider=provider)
    merged = dict(start_result)
    for key in ("exists", "state", "verify_url", "user_code", "connected", "message"):
        if key in login_status:
            merged[key] = login_status[key]
    final_state = str(merged.get("state") or "").strip().lower()
    if final_state in _PROVIDER_ACCOUNT_CONNECT_FAILURE_STATES:
        merged["started"] = False
    if "session_id" not in merged:
        merged["session_id"] = record.session_id
    if "session_ref" not in merged:
        merged["session_ref"] = session_ref
    return merged
