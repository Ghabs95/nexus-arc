"""Auth readiness, project ACL sync, and per-user execution credential resolution."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Optional
from urllib.parse import quote_plus

import requests

from nexus.core.auth.credential_crypto import decrypt_secret, encrypt_secret
from nexus.core.auth.credential_store import (
    CredentialRecord,
    get_user_credentials,
    get_user_project_access,
    has_user_project_access,
    list_credentials_for_sync,
    replace_user_project_access,
    update_gitlab_oauth_tokens,
)
from nexus.core.config import (
    NEXUS_RUNTIME_DIR,
    PROJECT_CONFIG,
    get_project_platform,
    get_repo,
    normalize_project_key,
)

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def auth_enabled() -> bool:
    return _env_bool("NEXUS_AUTH_ENABLED", False)


def _cli_auth_mode() -> str:
    mode = str(os.getenv("NEXUS_CLI_AUTH_MODE", "account")).strip().lower()
    return mode if mode in {"account", "api-key", "auto"} else "account"


def _cli_account_mode_enabled() -> bool:
    return _cli_auth_mode() in {"account", "auto"}


def _ensure_private_runtime_dir(path: str) -> str | None:
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
    except Exception as exc:
        return f"Failed to prepare auth runtime directory '{path}': {exc}"
    try:
        os.chmod(path, 0o700)
    except Exception:
        pass

    getuid = getattr(os, "getuid", None)
    if callable(getuid):
        try:
            current_uid = int(getuid())
            owner_uid = int(os.stat(path).st_uid)
        except Exception as exc:
            return f"Failed to verify auth runtime directory owner for '{path}': {exc}"
        if owner_uid != current_uid:
            return (
                f"Refusing to use insecure auth runtime directory '{path}' owned by uid={owner_uid}; "
                f"expected uid={current_uid}."
            )

    return None


def _sync_interval_minutes() -> int:
    raw = os.getenv("NEXUS_ACCESS_SYNC_INTERVAL_MINUTES", "30")
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        parsed = 30
    return max(5, parsed)


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _credentials_last_sync_stale(record: CredentialRecord) -> bool:
    last_sync = record.last_access_sync_at
    if not isinstance(last_sync, datetime):
        return True
    if last_sync.tzinfo is None:
        last_sync = last_sync.replace(tzinfo=UTC)
    return (_sync_interval_minutes() > 0) and (
        _now_utc() - last_sync >= timedelta(minutes=_sync_interval_minutes())
    )


def _normalize_slug(value: str) -> str:
    candidate = str(value or "").strip().lower()
    if not candidate or "/" not in candidate:
        return ""
    left, right = candidate.split("/", 1)
    left = left.strip()
    right = right.strip()
    if not left or not right:
        return ""
    return f"{left}/{right}"


def _normalize_gitlab_group(value: str) -> str:
    candidate = str(value or "").strip().lower().strip("/")
    if not candidate:
        return ""
    if "/" not in candidate:
        return candidate
    parts = [part.strip() for part in candidate.split("/") if part.strip()]
    if not parts:
        return ""
    return "/".join(parts)


def _normalize_username(value: str) -> str:
    candidate = str(value or "").strip().lower()
    if candidate.startswith("@"):
        candidate = candidate[1:]
    return candidate


def _project_access_mapping(
    key_name: str,
    project_config: Mapping[str, Any] | None = None,
    *,
    normalizer: Callable[[str], str] = _normalize_slug,
) -> dict[str, set[str]]:
    config = project_config if isinstance(project_config, Mapping) else PROJECT_CONFIG
    mapping: dict[str, set[str]] = {}
    for key, payload in (config.items() if isinstance(config, Mapping) else []):
        if not isinstance(payload, Mapping) or not payload.get("workspace"):
            continue
        project_key = str(normalize_project_key(str(key)) or str(key)).strip().lower()
        access_control = payload.get("access_control")
        if not isinstance(access_control, Mapping):
            continue
        raw_items = access_control.get(key_name)
        if not isinstance(raw_items, list):
            continue
        slugs: set[str] = set()
        for item in raw_items:
            slug = normalizer(str(item))
            if slug:
                slugs.add(slug)
        if slugs:
            mapping[project_key] = slugs
    return mapping


def _project_user_mapping(
    key_name: str,
    project_config: Mapping[str, Any] | None = None,
) -> dict[str, set[str]]:
    config = project_config if isinstance(project_config, Mapping) else PROJECT_CONFIG
    mapping: dict[str, set[str]] = {}
    for key, payload in (config.items() if isinstance(config, Mapping) else []):
        if not isinstance(payload, Mapping) or not payload.get("workspace"):
            continue
        project_key = str(normalize_project_key(str(key)) or str(key)).strip().lower()
        access_control = payload.get("access_control")
        if not isinstance(access_control, Mapping):
            continue
        raw_items = access_control.get(key_name)
        if not isinstance(raw_items, list):
            continue
        users: set[str] = set()
        for item in raw_items:
            user = _normalize_username(str(item))
            if user:
                users.add(user)
        if users:
            mapping[project_key] = users
    return mapping


def _github_request(url: str, token: str, *, timeout: int = 10) -> requests.Response:
    common_headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    response = requests.get(
        url,
        headers={**common_headers, "Authorization": f"token {token}"},
        timeout=timeout,
    )
    if response.status_code in {401, 403}:
        response = requests.get(
            url,
            headers={**common_headers, "Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    return response


def _gitlab_base_url() -> str:
    return str(os.getenv("NEXUS_GITLAB_BASE_URL", os.getenv("GITLAB_BASE_URL", "https://gitlab.com"))).strip().rstrip("/")


def _auth_key_version(record: Optional[CredentialRecord] = None) -> int:
    if record and isinstance(record.key_version, int) and int(record.key_version) > 0:
        return int(record.key_version)
    raw = os.getenv("NEXUS_CREDENTIALS_KEY_VERSION", "1")
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        parsed = 1
    return max(1, parsed)


def _gitlab_refresh_supported() -> bool:
    client_id = str(os.getenv("NEXUS_GITLAB_CLIENT_ID", "")).strip()
    client_secret = str(os.getenv("NEXUS_GITLAB_CLIENT_SECRET", "")).strip()
    return bool(client_id and client_secret)


def _token_expiring_soon(expires_at: datetime | None, skew_seconds: int = 120) -> bool:
    if not isinstance(expires_at, datetime):
        return False
    value = expires_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value <= (_now_utc() + timedelta(seconds=max(0, int(skew_seconds))))


def _refresh_gitlab_token(record: CredentialRecord) -> str:
    if not record.gitlab_refresh_token_enc:
        raise RuntimeError("GitLab refresh token is not available.")
    if not _gitlab_refresh_supported():
        raise RuntimeError(
            "GitLab OAuth refresh is not configured. Missing NEXUS_GITLAB_CLIENT_ID/SECRET."
        )

    try:
        refresh_token = decrypt_secret(record.gitlab_refresh_token_enc)
    except Exception as exc:
        raise RuntimeError("Stored GitLab refresh token is invalid.") from exc

    response = requests.post(
        f"{_gitlab_base_url()}/oauth/token",
        data={
            "client_id": str(os.getenv("NEXUS_GITLAB_CLIENT_ID", "")).strip(),
            "client_secret": str(os.getenv("NEXUS_GITLAB_CLIENT_SECRET", "")).strip(),
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    if response.status_code != 200:
        raise RuntimeError(f"GitLab token refresh failed ({response.status_code}).")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("GitLab token refresh returned invalid payload.")

    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("GitLab token refresh returned no access token.")

    next_refresh_token_raw = str(payload.get("refresh_token") or "").strip()
    expires_in_raw = payload.get("expires_in")
    expires_at = None
    if isinstance(expires_in_raw, int) and expires_in_raw > 0:
        expires_at = _now_utc() + timedelta(seconds=expires_in_raw)

    key_version = _auth_key_version(record)
    encrypted_access_token = encrypt_secret(access_token, key_version=key_version)
    encrypted_refresh_token = (
        encrypt_secret(next_refresh_token_raw, key_version=key_version)
        if next_refresh_token_raw
        else None
    )

    update_gitlab_oauth_tokens(
        nexus_id=str(record.nexus_id),
        gitlab_token_enc=encrypted_access_token,
        gitlab_refresh_token_enc=encrypted_refresh_token,
        gitlab_token_expires_at=expires_at,
        key_version=key_version,
    )

    record.gitlab_token_enc = encrypted_access_token
    if encrypted_refresh_token is not None:
        record.gitlab_refresh_token_enc = encrypted_refresh_token
    record.gitlab_token_expires_at = expires_at
    record.key_version = key_version
    return access_token


def _resolve_gitlab_access_token(
    record: CredentialRecord,
    *,
    force_refresh: bool = False,
) -> tuple[str | None, str | None]:
    if not record.gitlab_token_enc:
        return None, "GitLab credentials are missing."
    try:
        current_token = decrypt_secret(record.gitlab_token_enc)
    except Exception:
        return None, "Stored GitLab credentials are invalid."

    if not force_refresh and not _token_expiring_soon(record.gitlab_token_expires_at):
        return current_token, None

    try:
        refreshed = _refresh_gitlab_token(record)
        return refreshed, None
    except Exception as exc:
        if force_refresh or _token_expiring_soon(record.gitlab_token_expires_at, skew_seconds=0):
            return None, str(exc)
        logger.warning(
            "GitLab token refresh failed for nexus_id=%s, continuing with current token: %s",
            record.nexus_id,
            exc,
        )
        return current_token, None


def _gitlab_request(path: str, token: str, *, timeout: int = 10) -> requests.Response:
    base_url = _gitlab_base_url()
    return requests.get(
        f"{base_url}{path}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
        timeout=timeout,
    )


def fetch_github_user_teams(token: str) -> set[str]:
    teams: set[str] = set()
    page = 1
    while page <= 10:
        response = _github_request(f"https://api.github.com/user/teams?per_page=100&page={page}", token)
        if response.status_code != 200:
            raise RuntimeError(f"GitHub teams API failed ({response.status_code}): {response.text}")
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            break
        for item in payload:
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug") or "").strip().lower()
            org = item.get("organization") if isinstance(item.get("organization"), dict) else {}
            org_login = str((org or {}).get("login") or "").strip().lower()
            normalized = _normalize_slug(f"{org_login}/{slug}")
            if normalized:
                teams.add(normalized)
        if len(payload) < 100:
            break
        page += 1
    return teams


def fetch_github_login(token: str) -> str:
    response = _github_request("https://api.github.com/user", token)
    if response.status_code != 200:
        raise RuntimeError(f"GitHub user API failed ({response.status_code}): {response.text}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub user API returned invalid payload")
    login = _normalize_username(str(payload.get("login") or ""))
    if not login:
        raise RuntimeError("GitHub user API returned empty login")
    return login


def fetch_gitlab_user_groups(token: str) -> set[str]:
    groups: set[str] = set()
    page = 1
    while page <= 10:
        response = _gitlab_request(f"/api/v4/groups?per_page=100&page={page}", token)
        if response.status_code != 200:
            raise RuntimeError(f"GitLab groups API failed ({response.status_code}): {response.text}")
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            break
        for item in payload:
            if not isinstance(item, dict):
                continue
            full_path = str(item.get("full_path") or "").strip().lower()
            normalized = _normalize_gitlab_group(full_path)
            if normalized:
                groups.add(normalized)
        if len(payload) < 100:
            break
        page += 1
    return groups


def fetch_gitlab_username(token: str) -> str:
    response = _gitlab_request("/api/v4/user", token)
    if response.status_code != 200:
        raise RuntimeError(f"GitLab user API failed ({response.status_code}): {response.text}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("GitLab user API returned invalid payload")
    username = _normalize_username(str(payload.get("username") or ""))
    if not username:
        raise RuntimeError("GitLab user API returned empty username")
    return username


def token_has_github_repo_access(token: str, repo_name: str) -> bool:
    repo = str(repo_name or "").strip()
    if not repo:
        return False
    response = _github_request(f"https://api.github.com/repos/{repo}", token)
    return response.status_code == 200


def token_has_gitlab_repo_access(token: str, repo_name: str) -> bool:
    repo = str(repo_name or "").strip()
    if not repo:
        return False
    encoded = quote_plus(repo)
    response = _gitlab_request(f"/api/v4/projects/{encoded}", token)
    if response.status_code in {401, 403}:
        raise PermissionError(f"GitLab token rejected with status {response.status_code}.")
    return response.status_code == 200


def compute_project_grants_for_teams(
    *,
    team_slugs: set[str],
    project_config: Mapping[str, Any] | None = None,
) -> list[tuple[str, str]]:
    mapping = _project_access_mapping("github_teams", project_config, normalizer=_normalize_slug)
    grants: list[tuple[str, str]] = []
    for project_key, required_teams in mapping.items():
        for team in required_teams:
            if team in team_slugs:
                grants.append((project_key, team))
    return grants


def compute_project_grants_for_github_acl(
    *,
    team_slugs: set[str] | None,
    github_login: str | None,
    project_config: Mapping[str, Any] | None = None,
) -> list[tuple[str, str]]:
    team_mapping = _project_access_mapping("github_teams", project_config, normalizer=_normalize_slug)
    user_mapping = _project_user_mapping("github_users", project_config)
    projects = set(team_mapping) | set(user_mapping)
    normalized_login = _normalize_username(str(github_login or ""))

    grants: list[tuple[str, str]] = []
    for project_key in sorted(projects):
        required_teams = team_mapping.get(project_key, set())
        required_users = user_mapping.get(project_key, set())

        team_ok = True
        source = "github_user"
        if required_teams:
            team_set = team_slugs or set()
            matched_teams = sorted(required_teams & team_set)
            team_ok = bool(matched_teams)
            if matched_teams:
                source = matched_teams[0]

        user_ok = True
        if required_users:
            user_ok = bool(normalized_login and normalized_login in required_users)
            if user_ok:
                source = normalized_login

        if team_ok and user_ok:
            grants.append((project_key, source))
    return grants


def compute_project_grants_for_gitlab_groups(
    *,
    group_paths: set[str],
    project_config: Mapping[str, Any] | None = None,
) -> list[tuple[str, str]]:
    mapping = _project_access_mapping(
        "gitlab_groups",
        project_config,
        normalizer=_normalize_gitlab_group,
    )
    grants: list[tuple[str, str]] = []
    for project_key, required_groups in mapping.items():
        for group in required_groups:
            group_normalized = str(group).strip().lower()
            if group_normalized in group_paths:
                grants.append((project_key, group_normalized))
    return grants


def compute_project_grants_for_gitlab_acl(
    *,
    group_paths: set[str] | None,
    gitlab_username: str | None,
    project_config: Mapping[str, Any] | None = None,
) -> list[tuple[str, str]]:
    group_mapping = _project_access_mapping(
        "gitlab_groups",
        project_config,
        normalizer=_normalize_gitlab_group,
    )
    user_mapping = _project_user_mapping("gitlab_users", project_config)
    projects = set(group_mapping) | set(user_mapping)
    normalized_username = _normalize_username(str(gitlab_username or ""))

    grants: list[tuple[str, str]] = []
    for project_key in sorted(projects):
        required_groups = group_mapping.get(project_key, set())
        required_users = user_mapping.get(project_key, set())

        group_ok = True
        source = "gitlab_user"
        if required_groups:
            group_set = group_paths or set()
            matched_groups = sorted(required_groups & group_set)
            group_ok = bool(matched_groups)
            if matched_groups:
                source = matched_groups[0]

        user_ok = True
        if required_users:
            user_ok = bool(normalized_username and normalized_username in required_users)
            if user_ok:
                source = normalized_username

        if group_ok and user_ok:
            grants.append((project_key, source))
    return grants


def sync_user_project_access(
    *,
    nexus_id: str,
    github_token: str,
    github_login: str | None = None,
    project_config: Mapping[str, Any] | None = None,
) -> int:
    team_mapping = _project_access_mapping("github_teams", project_config, normalizer=_normalize_slug)
    user_mapping = _project_user_mapping("github_users", project_config)

    team_slugs: set[str] | None = set()
    if team_mapping:
        team_slugs = fetch_github_user_teams(github_token)

    normalized_login = _normalize_username(str(github_login or ""))
    if user_mapping and not normalized_login:
        normalized_login = fetch_github_login(github_token)

    grants = compute_project_grants_for_github_acl(
        team_slugs=team_slugs,
        github_login=normalized_login,
        project_config=project_config,
    )
    return replace_user_project_access(
        nexus_id=str(nexus_id),
        grants=grants,
        granted_via="github_acl",
        replace_all=False,
    )


def sync_user_gitlab_project_access(
    *,
    nexus_id: str,
    gitlab_token: str,
    gitlab_username: str | None = None,
    project_config: Mapping[str, Any] | None = None,
) -> int:
    group_mapping = _project_access_mapping(
        "gitlab_groups",
        project_config,
        normalizer=_normalize_gitlab_group,
    )
    user_mapping = _project_user_mapping("gitlab_users", project_config)

    group_paths: set[str] | None = set()
    if group_mapping:
        group_paths = fetch_gitlab_user_groups(gitlab_token)

    normalized_username = _normalize_username(str(gitlab_username or ""))
    if user_mapping and not normalized_username:
        normalized_username = fetch_gitlab_username(gitlab_token)

    grants = compute_project_grants_for_gitlab_acl(
        group_paths=group_paths,
        gitlab_username=normalized_username,
        project_config=project_config,
    )
    return replace_user_project_access(
        nexus_id=str(nexus_id),
        grants=grants,
        granted_via="gitlab_acl",
        replace_all=False,
    )


def maybe_sync_user_project_access(nexus_id: str) -> bool:
    if not auth_enabled():
        return True
    record = get_user_credentials(str(nexus_id))
    if not record:
        return False
    if not _credentials_last_sync_stale(record):
        return True

    updated = False
    failed = False

    if record.github_token_enc:
        try:
            github_token = decrypt_secret(record.github_token_enc)
            sync_user_project_access(
                nexus_id=str(nexus_id),
                github_token=github_token,
                github_login=record.github_login,
            )
            updated = True
        except Exception as exc:
            failed = True
            logger.warning("Failed to refresh GitHub access for nexus_id=%s: %s", nexus_id, exc)

    if record.gitlab_token_enc:
        try:
            gitlab_token, gitlab_err = _resolve_gitlab_access_token(record)
            if not gitlab_token:
                raise RuntimeError(gitlab_err or "GitLab token resolution failed.")
            sync_user_gitlab_project_access(
                nexus_id=str(nexus_id),
                gitlab_token=gitlab_token,
                gitlab_username=record.gitlab_username,
            )
            updated = True
        except Exception as exc:
            failed = True
            logger.warning("Failed to refresh GitLab access for nexus_id=%s: %s", nexus_id, exc)

    return updated or not failed


def get_setup_status(nexus_id: str) -> dict[str, Any]:
    auth_mode = _cli_auth_mode()
    account_mode_enabled = _cli_account_mode_enabled()
    if not auth_enabled():
        return {
            "auth_enabled": False,
            "cli_auth_mode": auth_mode,
            "ready": True,
            "github_linked": False,
            "gitlab_linked": False,
            "git_provider_linked": False,
            "codex_key_set": False,
            "gemini_key_set": False,
            "claude_key_set": False,
            "codex_account_enabled": False,
            "gemini_account_enabled": False,
            "claude_account_enabled": False,
            "copilot_account_enabled": False,
            "account_provider_ready": False,
            "ai_provider_key_set": False,
            "copilot_ready": False,
            "ai_provider_ready": False,
            "org_verified": False,
            "project_access_count": 0,
            "projects": [],
        }

    maybe_sync_user_project_access(str(nexus_id))
    record = get_user_credentials(str(nexus_id))
    grants = get_user_project_access(str(nexus_id))

    github_linked = bool(record and record.github_token_enc and record.github_login)
    gitlab_linked = bool(record and record.gitlab_token_enc and record.gitlab_username)
    git_provider_linked = bool(github_linked or gitlab_linked)
    codex_key_set = bool(record and record.codex_api_key_enc)
    gemini_key_set = bool(record and record.gemini_api_key_enc)
    claude_key_set = bool(record and record.claude_api_key_enc)
    copilot_token_set = bool(record and record.copilot_github_token_enc)
    codex_account_enabled = bool(record and getattr(record, "codex_account_enabled", False))
    gemini_account_enabled = bool(record and getattr(record, "gemini_account_enabled", False))
    claude_account_enabled = bool(record and getattr(record, "claude_account_enabled", False))
    copilot_account_enabled = bool(record and getattr(record, "copilot_account_enabled", False))
    ai_provider_key_set = bool(codex_key_set or gemini_key_set or claude_key_set)
    # Copilot is available with linked GitHub OAuth or an explicit Copilot GitHub token.
    copilot_ready = bool(github_linked or copilot_token_set or (account_mode_enabled and copilot_account_enabled))
    account_provider_ready = bool(
        account_mode_enabled
        and (
            codex_account_enabled
            or gemini_account_enabled
            or claude_account_enabled
            or copilot_account_enabled
        )
    )
    ai_provider_ready = bool(ai_provider_key_set or copilot_ready or account_provider_ready)
    org_verified = bool(record and record.org_verified)
    projects = sorted({grant.project_key for grant in grants})
    project_access_count = len(projects)
    ready = bool(
        git_provider_linked and ai_provider_ready and org_verified and project_access_count > 0
    )

    return {
        "auth_enabled": True,
        "cli_auth_mode": auth_mode,
        "ready": ready,
        "github_linked": github_linked,
        "gitlab_linked": gitlab_linked,
        "git_provider_linked": git_provider_linked,
        "codex_key_set": codex_key_set,
        "gemini_key_set": gemini_key_set,
        "claude_key_set": claude_key_set,
        "codex_account_enabled": codex_account_enabled,
        "gemini_account_enabled": gemini_account_enabled,
        "claude_account_enabled": claude_account_enabled,
        "copilot_account_enabled": copilot_account_enabled,
        "account_provider_ready": account_provider_ready,
        "copilot_token_set": copilot_token_set,
        "ai_provider_key_set": ai_provider_key_set,
        "copilot_ready": copilot_ready,
        "ai_provider_ready": ai_provider_ready,
        "org_verified": org_verified,
        "github_login": (record.github_login if record else None),
        "gitlab_username": (record.gitlab_username if record else None),
        "project_access_count": project_access_count,
        "projects": projects,
    }


def has_setup_ready_user_for_project(project_key: str) -> bool:
    """Return True if at least one configured user can execute on a project."""
    if not auth_enabled():
        return True

    normalized = str(normalize_project_key(project_key) or project_key).strip().lower()
    if not normalized:
        return False

    try:
        credentials = list_credentials_for_sync(limit=1000)
    except Exception as exc:
        logger.warning("Could not enumerate credentials for polling gate: %s", exc)
        return False

    for record in credentials:
        try:
            if not has_user_project_access(str(record.nexus_id), normalized):
                continue

            account_mode_enabled = _cli_account_mode_enabled()
            github_linked = bool(record.github_token_enc and record.github_login)
            gitlab_linked = bool(record.gitlab_token_enc and record.gitlab_username)
            git_provider_linked = bool(github_linked or gitlab_linked)
            ai_provider_key_set = bool(
                record.codex_api_key_enc or record.gemini_api_key_enc or record.claude_api_key_enc
            )
            copilot_ready = bool(
                github_linked
                or record.copilot_github_token_enc
                or (account_mode_enabled and bool(getattr(record, "copilot_account_enabled", False)))
            )
            account_provider_ready = bool(
                account_mode_enabled
                and (
                    bool(getattr(record, "codex_account_enabled", False))
                    or bool(getattr(record, "gemini_account_enabled", False))
                    or bool(getattr(record, "claude_account_enabled", False))
                    or bool(getattr(record, "copilot_account_enabled", False))
                )
            )
            ai_provider_ready = bool(ai_provider_key_set or copilot_ready or account_provider_ready)
            if git_provider_linked and ai_provider_ready and bool(record.org_verified):
                return True
        except Exception:
            continue
    return False


def has_project_access(nexus_id: str, project_key: str, *, auto_sync: bool = True) -> bool:
    if not auth_enabled():
        return True
    normalized = str(normalize_project_key(project_key) or project_key).strip().lower()
    if auto_sync:
        maybe_sync_user_project_access(str(nexus_id))
    return has_user_project_access(str(nexus_id), normalized)


def check_project_access(nexus_id: str, project_key: str) -> tuple[bool, str]:
    normalized = str(normalize_project_key(project_key) or project_key).strip().lower()
    status = get_setup_status(str(nexus_id))
    if not status.get("ready"):
        return (
            False,
            "Your account setup is incomplete. Run `/login` then `/setup-status` (Discord) or `/setup_status` (Telegram).",
        )
    if has_project_access(str(nexus_id), normalized, auto_sync=False):
        return True, ""
    return (
        False,
        f"You are not authorized for project `{normalized}`. Ask an admin to add you to its GitHub team or GitLab group.",
    )


def check_repo_access(
    nexus_id: str,
    repo_name: str,
    project_key: str | None = None,
) -> tuple[bool, str]:
    if not auth_enabled():
        return True, ""

    record = get_user_credentials(str(nexus_id))
    if not record:
        return False, "Git provider credentials are missing. Run `/login` to reconnect your account."

    project = str(project_key or "").strip().lower()
    platform = ""
    if project:
        try:
            platform = str(get_project_platform(project) or "").strip().lower()
        except Exception:
            platform = ""

    def _check_github() -> tuple[bool, str]:
        if not record.github_token_enc:
            return False, "GitHub credentials are missing. Run `/login github`."
        try:
            token = decrypt_secret(record.github_token_enc)
        except Exception:
            return False, "Stored GitHub credentials are invalid. Run `/login github` again."
        try:
            allowed = token_has_github_repo_access(token, repo_name)
        except Exception as exc:
            return False, f"Could not verify GitHub repository access right now: {exc}"
        if not allowed:
            return (
                False,
                f"Your GitHub token has no access to `{repo_name}`. Ask for repo access and re-run `/login github`.",
            )
        return True, ""

    def _check_gitlab() -> tuple[bool, str]:
        if not record.gitlab_token_enc:
            return False, "GitLab credentials are missing. Run `/login gitlab`."
        token, token_err = _resolve_gitlab_access_token(record)
        if not token:
            return False, (
                token_err
                or "Stored GitLab credentials are invalid or expired. Run `/login gitlab` again."
            )
        try:
            allowed = token_has_gitlab_repo_access(token, repo_name)
        except PermissionError:
            retry_token, retry_err = _resolve_gitlab_access_token(record, force_refresh=True)
            if not retry_token:
                return False, retry_err or "GitLab credentials are invalid. Run `/login gitlab` again."
            try:
                allowed = token_has_gitlab_repo_access(retry_token, repo_name)
            except Exception as exc:
                return False, f"Could not verify GitLab project access right now: {exc}"
        except Exception as exc:
            return False, f"Could not verify GitLab project access right now: {exc}"
        if not allowed:
            return (
                False,
                f"Your GitLab token has no access to `{repo_name}`. Ask for repo access and re-run `/login gitlab`.",
            )
        return True, ""

    if platform == "gitlab":
        return _check_gitlab()
    if platform == "github":
        return _check_github()

    # Fallback for callers that do not pass project_key.
    gh_ok, gh_err = _check_github()
    if gh_ok:
        return True, ""
    gl_ok, gl_err = _check_gitlab()
    if gl_ok:
        return True, ""
    return False, gh_err or gl_err or "Repository access check failed."


def resolve_project_polling_git_token(project_key: str) -> tuple[str | None, str | None]:
    """Return a git token for polling from a user authorized on the given project.

    Returns:
    - (token, nexus_id) when a usable token is found
    - (None, None) when no eligible user token is available
    """
    if not auth_enabled():
        return None, None

    normalized = str(normalize_project_key(project_key) or project_key).strip().lower()
    if not normalized:
        return None, None

    try:
        platform = str(get_project_platform(normalized) or "").strip().lower()
    except Exception:
        platform = ""
    if platform not in {"github", "gitlab"}:
        return None, None

    try:
        repo_name = str(get_repo(normalized) or "").strip()
    except Exception:
        repo_name = ""

    try:
        credentials = list_credentials_for_sync(limit=1000)
    except Exception as exc:
        logger.warning("Could not enumerate credentials for project polling token: %s", exc)
        return None, None

    for record in credentials:
        try:
            nexus_id = str(record.nexus_id or "").strip()
            if not nexus_id:
                continue
            if not has_user_project_access(nexus_id, normalized):
                continue
            if not bool(record.org_verified):
                continue

            if platform == "gitlab":
                if not record.gitlab_token_enc:
                    continue
                token, token_err = _resolve_gitlab_access_token(record)
                if not token:
                    if token_err:
                        logger.debug(
                            "Skipping nexus_id=%s for project %s (gitlab token issue): %s",
                            nexus_id,
                            normalized,
                            token_err,
                        )
                    continue
                if repo_name:
                    try:
                        if not token_has_gitlab_repo_access(token, repo_name):
                            continue
                    except Exception:
                        continue
                return token, nexus_id

            if not record.github_token_enc:
                continue
            try:
                token = decrypt_secret(record.github_token_enc)
            except Exception:
                continue
            if repo_name:
                try:
                    if not token_has_github_repo_access(token, repo_name):
                        continue
                except Exception:
                    continue
            return token, nexus_id
        except Exception:
            continue

    return None, None


def build_execution_env(nexus_id: str) -> tuple[dict[str, str], str | None]:
    record = get_user_credentials(str(nexus_id))
    if not record:
        return {}, "No credential record found."

    env: dict[str, str] = {}
    github_token_present = False

    if record.github_token_enc:
        try:
            env["GITHUB_TOKEN"] = decrypt_secret(record.github_token_enc)
            github_token_present = True
        except Exception:
            return {}, "Stored GitHub token could not be decrypted."

    if record.gitlab_token_enc:
        gitlab_token, gitlab_err = _resolve_gitlab_access_token(record)
        if not gitlab_token:
            return {}, gitlab_err or "Stored GitLab token could not be decrypted."
        env["GITLAB_TOKEN"] = gitlab_token

    if record.copilot_github_token_enc:
        try:
            copilot_token = decrypt_secret(record.copilot_github_token_enc)
            env["COPILOT_GITHUB_TOKEN"] = copilot_token
            if "GITHUB_TOKEN" not in env and "GITLAB_TOKEN" not in env:
                env["GITHUB_TOKEN"] = copilot_token
        except Exception:
            return {}, "Stored Copilot Token could not be decrypted."

    if "GITLAB_TOKEN" not in env and "GITHUB_TOKEN" in env:
        env["GITLAB_TOKEN"] = env["GITHUB_TOKEN"]

    if "GITHUB_TOKEN" not in env and "GITLAB_TOKEN" in env:
        # Needed by toolchains that still read GITHUB_TOKEN for generic git auth.
        env["GITHUB_TOKEN"] = env["GITLAB_TOKEN"]

    if "GITHUB_TOKEN" not in env and "GITLAB_TOKEN" not in env:
        return {}, "Missing Git provider token."

    if record.codex_api_key_enc:
        try:
            env["OPENAI_API_KEY"] = decrypt_secret(record.codex_api_key_enc)
        except Exception:
            return {}, "Stored Codex API key could not be decrypted."

    if record.gemini_api_key_enc:
        try:
            env["GEMINI_API_KEY"] = decrypt_secret(record.gemini_api_key_enc)
        except Exception:
            return {}, "Stored Gemini API key could not be decrypted."

    if record.claude_api_key_enc:
        try:
            claude_key = decrypt_secret(record.claude_api_key_enc)
            env["ANTHROPIC_API_KEY"] = claude_key
            env["CLAUDE_API_KEY"] = claude_key
        except Exception:
            return {}, "Stored Claude API key could not be decrypted."

    account_mode_enabled = _cli_account_mode_enabled()
    account_auth_providers: list[str] = []
    if account_mode_enabled:
        if bool(getattr(record, "codex_account_enabled", False)):
            account_auth_providers.append("codex")
        if bool(getattr(record, "gemini_account_enabled", False)):
            account_auth_providers.append("gemini")
        if bool(getattr(record, "claude_account_enabled", False)):
            account_auth_providers.append("claude")
        if bool(getattr(record, "copilot_account_enabled", False)):
            account_auth_providers.append("copilot")
    if account_auth_providers:
        env["NEXUS_ACCOUNT_AUTH_PROVIDERS"] = ",".join(account_auth_providers)

    ai_key_present = (
        "OPENAI_API_KEY" in env
        or "GEMINI_API_KEY" in env
        or "ANTHROPIC_API_KEY" in env
        or "CLAUDE_API_KEY" in env
    )
    if not ai_key_present and not github_token_present and not account_auth_providers:
        return (
            {},
            "Missing AI provider credentials (Codex/OpenAI, Gemini, Claude, GitHub for Copilot, "
            "or account-based provider login via `/login`).",
        )

    auth_root = os.path.join(NEXUS_RUNTIME_DIR, "auth")
    user_home_root = os.path.join(auth_root, "home")
    user_home = os.path.join(user_home_root, str(nexus_id))
    codex_root = os.path.join(auth_root, "codex")
    gemini_root = os.path.join(auth_root, "gemini")
    claude_root = os.path.join(auth_root, "claude")
    codex_home = os.path.join(codex_root, str(nexus_id))
    gemini_home = os.path.join(gemini_root, str(nexus_id))
    claude_home = os.path.join(claude_root, str(nexus_id))
    for path in (
        auth_root,
        user_home_root,
        user_home,
        codex_root,
        gemini_root,
        claude_root,
        codex_home,
        gemini_home,
        claude_home,
    ):
        dir_err = _ensure_private_runtime_dir(path)
        if dir_err:
            return {}, dir_err
    env["HOME"] = user_home
    env["CODEX_HOME"] = codex_home
    env["GEMINI_HOME"] = gemini_home
    env["CLAUDE_HOME"] = claude_home
    return env, None


def refresh_stale_access_grants(limit: int = 200) -> dict[str, int]:
    """Refresh grants for users whose sync interval has elapsed."""
    if not auth_enabled():
        return {"processed": 0, "updated": 0, "failed": 0}

    processed = 0
    updated = 0
    failed = 0

    for record in list_credentials_for_sync(limit=limit):
        if not _credentials_last_sync_stale(record):
            continue
        if not record.github_token_enc and not record.gitlab_token_enc:
            continue

        processed += 1
        row_updated = False

        if record.github_token_enc:
            try:
                github_token = decrypt_secret(record.github_token_enc)
                sync_user_project_access(
                    nexus_id=record.nexus_id,
                    github_token=github_token,
                    github_login=record.github_login,
                )
                row_updated = True
            except Exception:
                pass

        if record.gitlab_token_enc:
            try:
                gitlab_token, gitlab_err = _resolve_gitlab_access_token(record)
                if not gitlab_token:
                    raise RuntimeError(gitlab_err or "GitLab token resolution failed.")
                sync_user_gitlab_project_access(
                    nexus_id=record.nexus_id,
                    gitlab_token=gitlab_token,
                    gitlab_username=record.gitlab_username,
                )
                row_updated = True
            except Exception:
                pass

        if row_updated:
            updated += 1
        else:
            failed += 1

    return {"processed": processed, "updated": updated, "failed": failed}


def resolve_project_repo(project_key: str) -> str:
    normalized = str(normalize_project_key(project_key) or project_key).strip().lower()
    return get_repo(normalized)
