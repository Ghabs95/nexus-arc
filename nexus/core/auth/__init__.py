"""Framework auth domain and manager interfaces."""

from nexus.core.auth.manager import (
    AuthManager,
    auth_manager,
    check_project_access,
    complete_github_oauth,
    complete_gitlab_oauth,
    create_login_session_for_user,
    get_session_and_setup_status,
    get_setup_status,
    has_project_access,
    register_onboarding_message,
    refresh_stale_access_grants,
    start_oauth_flow,
    store_ai_provider_keys,
)

__all__ = [
    "AuthManager",
    "auth_manager",
    "check_project_access",
    "complete_github_oauth",
    "complete_gitlab_oauth",
    "create_login_session_for_user",
    "get_session_and_setup_status",
    "get_setup_status",
    "has_project_access",
    "register_onboarding_message",
    "refresh_stale_access_grants",
    "start_oauth_flow",
    "store_ai_provider_keys",
]
