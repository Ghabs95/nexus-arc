"""Framework-facing auth manager facade.

This module centralizes auth/session/onboarding operations behind a stable
interface so channel adapters (webhook UI, Discord, Telegram) do not bind to
low-level service modules directly.
"""

from __future__ import annotations

from typing import Any

from nexus.core.auth import access_domain as _project_access
from nexus.core.auth import oauth_onboarding_domain as _auth_sessions


class AuthManager:
    """Shared auth orchestration facade for app channels."""

    def create_login_session_for_user(
        self,
        *,
        nexus_id: str,
        discord_user_id: str,
        discord_username: str | None,
        chat_platform: str | None = None,
        chat_id: str | None = None,
        onboarding_message_id: str | None = None,
    ) -> str:
        return _auth_sessions.create_login_session_for_user(
            nexus_id=nexus_id,
            discord_user_id=discord_user_id,
            discord_username=discord_username,
            chat_platform=chat_platform,
            chat_id=chat_id,
            onboarding_message_id=onboarding_message_id,
        )

    def register_onboarding_message(
        self,
        *,
        session_id: str,
        chat_platform: str,
        chat_id: str,
        message_id: str,
    ) -> None:
        _auth_sessions.register_onboarding_message(
            session_id=session_id,
            chat_platform=chat_platform,
            chat_id=chat_id,
            message_id=message_id,
        )

    def start_oauth_flow(self, session_id: str, provider: str = "github") -> tuple[str, str]:
        return _auth_sessions.start_oauth_flow(session_id=session_id, provider=provider)

    def complete_github_oauth(self, *, code: str, state: str) -> dict[str, Any]:
        return _auth_sessions.complete_github_oauth(code=code, state=state)

    def complete_gitlab_oauth(self, *, code: str, state: str) -> dict[str, Any]:
        return _auth_sessions.complete_gitlab_oauth(code=code, state=state)

    def store_ai_provider_keys(
        self,
        *,
        session_id: str,
        codex_api_key: str | None = None,
        gemini_api_key: str | None = None,
        claude_api_key: str | None = None,
        copilot_github_token: str | None = None,
        allow_copilot: bool = False,
    ) -> dict[str, Any]:
        return _auth_sessions.store_ai_provider_keys(
            session_id=session_id,
            codex_api_key=codex_api_key,
            gemini_api_key=gemini_api_key,
            claude_api_key=claude_api_key,
            copilot_github_token=copilot_github_token,
            allow_copilot=allow_copilot,
        )

    def get_session_and_setup_status(self, session_id: str) -> dict[str, Any]:
        return _auth_sessions.get_session_and_setup_status(session_id=session_id)

    def get_setup_status(self, nexus_id: str) -> dict[str, Any]:
        return _project_access.get_setup_status(nexus_id=nexus_id)

    def check_project_access(self, nexus_id: str, project_key: str) -> tuple[bool, str]:
        return _project_access.check_project_access(nexus_id=nexus_id, project_key=project_key)

    def has_project_access(self, nexus_id: str, project_key: str, *, auto_sync: bool = True) -> bool:
        return _project_access.has_project_access(
            nexus_id=nexus_id,
            project_key=project_key,
            auto_sync=auto_sync,
        )

    def refresh_stale_access_grants(self, limit: int = 200) -> dict[str, int]:
        return _project_access.refresh_stale_access_grants(limit=limit)


auth_manager = AuthManager()


def create_login_session_for_user(
    *,
    nexus_id: str,
    discord_user_id: str,
    discord_username: str | None,
    chat_platform: str | None = None,
    chat_id: str | None = None,
    onboarding_message_id: str | None = None,
) -> str:
    return auth_manager.create_login_session_for_user(
        nexus_id=nexus_id,
        discord_user_id=discord_user_id,
        discord_username=discord_username,
        chat_platform=chat_platform,
        chat_id=chat_id,
        onboarding_message_id=onboarding_message_id,
    )


def register_onboarding_message(
    *,
    session_id: str,
    chat_platform: str,
    chat_id: str,
    message_id: str,
) -> None:
    auth_manager.register_onboarding_message(
        session_id=session_id,
        chat_platform=chat_platform,
        chat_id=chat_id,
        message_id=message_id,
    )


def start_oauth_flow(session_id: str, provider: str = "github") -> tuple[str, str]:
    return auth_manager.start_oauth_flow(session_id=session_id, provider=provider)


def complete_github_oauth(*, code: str, state: str) -> dict[str, Any]:
    return auth_manager.complete_github_oauth(code=code, state=state)


def complete_gitlab_oauth(*, code: str, state: str) -> dict[str, Any]:
    return auth_manager.complete_gitlab_oauth(code=code, state=state)


def store_ai_provider_keys(
    *,
    session_id: str,
    codex_api_key: str | None = None,
    gemini_api_key: str | None = None,
    claude_api_key: str | None = None,
    copilot_github_token: str | None = None,
    allow_copilot: bool = False,
) -> dict[str, Any]:
    return auth_manager.store_ai_provider_keys(
        session_id=session_id,
        codex_api_key=codex_api_key,
        gemini_api_key=gemini_api_key,
        claude_api_key=claude_api_key,
        copilot_github_token=copilot_github_token,
        allow_copilot=allow_copilot,
    )


def get_session_and_setup_status(session_id: str) -> dict[str, Any]:
    return auth_manager.get_session_and_setup_status(session_id=session_id)


def get_setup_status(nexus_id: str) -> dict[str, Any]:
    return auth_manager.get_setup_status(nexus_id=nexus_id)


def check_project_access(nexus_id: str, project_key: str) -> tuple[bool, str]:
    return auth_manager.check_project_access(nexus_id=nexus_id, project_key=project_key)


def has_project_access(nexus_id: str, project_key: str, *, auto_sync: bool = True) -> bool:
    return auth_manager.has_project_access(
        nexus_id=nexus_id,
        project_key=project_key,
        auto_sync=auto_sync,
    )


def refresh_stale_access_grants(limit: int = 200) -> dict[str, int]:
    return auth_manager.refresh_stale_access_grants(limit=limit)
