"""Tests for the shared automation git token helper (nexus.core.auth.git_token)."""

import os
from unittest.mock import patch

from nexus.core.auth.git_token import resolve_automation_git_token


class TestResolveAutomationGitTokenGitHub:
    def test_prefers_nexus_automation_github_token(self):
        env = {
            "NEXUS_AUTOMATION_GITHUB_TOKEN": "agh-token",
            "GITHUB_TOKEN": "gh-fallback",
        }
        with patch.dict(os.environ, env, clear=True):
            assert resolve_automation_git_token("github") == "agh-token"

    def test_falls_back_to_legacy_generic_token(self):
        env = {
            "NEXUS_AUTOMATION_GIT_TOKEN": "legacy-token",
            "GITHUB_TOKEN": "gh-fallback",
        }
        with patch.dict(os.environ, env, clear=True):
            assert resolve_automation_git_token("github") == "legacy-token"

    def test_falls_back_to_write_token(self):
        env = {"NEXUS_GITHUB_WRITE_TOKEN": "write-token", "GITHUB_TOKEN": "gh-fallback"}
        with patch.dict(os.environ, env, clear=True):
            assert resolve_automation_git_token("github") == "write-token"

    def test_falls_back_to_github_token(self):
        env = {"GITHUB_TOKEN": "gh-fallback"}
        with patch.dict(os.environ, env, clear=True):
            assert resolve_automation_git_token("github") == "gh-fallback"


class TestResolveAutomationGitTokenGitLab:
    def test_prefers_nexus_automation_gitlab_token(self):
        env = {
            "NEXUS_AUTOMATION_GITLAB_TOKEN": "agl-token",
            "GITLAB_TOKEN": "gl-fallback",
        }
        with patch.dict(os.environ, env, clear=True):
            assert resolve_automation_git_token("gitlab") == "agl-token"

    def test_falls_back_to_legacy_generic_token(self):
        env = {
            "NEXUS_AUTOMATION_GIT_TOKEN": "legacy-token",
            "GITLAB_TOKEN": "gl-fallback",
        }
        with patch.dict(os.environ, env, clear=True):
            assert resolve_automation_git_token("gitlab") == "legacy-token"

    def test_falls_back_to_gitlab_token(self):
        env = {"GITLAB_TOKEN": "gl-fallback"}
        with patch.dict(os.environ, env, clear=True):
            assert resolve_automation_git_token("gitlab") == "gl-fallback"


class TestResolveAutomationGitTokenPlatformIsolation:
    def test_github_platform_does_not_return_gitlab_only_token(self):
        env = {"NEXUS_AUTOMATION_GITLAB_TOKEN": "gl-only", "GITLAB_TOKEN": "gl-token"}
        with patch.dict(os.environ, env, clear=True):
            # GitHub-specific lookup should find nothing and return None
            assert resolve_automation_git_token("github") is None

    def test_gitlab_platform_does_not_return_github_only_token(self):
        env = {
            "NEXUS_AUTOMATION_GITHUB_TOKEN": "gh-only",
            "NEXUS_GITHUB_WRITE_TOKEN": "gh-write",
            "GITHUB_TOKEN": "gh-token",
            "GH_TOKEN": "gh-alt",
        }
        with patch.dict(os.environ, env, clear=True):
            # GitLab-specific lookup should find nothing and return None
            assert resolve_automation_git_token("gitlab") is None

    def test_unknown_platform_tries_all_keys(self):
        env = {"NEXUS_AUTOMATION_GITLAB_TOKEN": "gl-token"}
        with patch.dict(os.environ, env, clear=True):
            assert resolve_automation_git_token("unknown") == "gl-token"

    def test_none_platform_prefers_github_first(self):
        env = {
            "NEXUS_AUTOMATION_GITHUB_TOKEN": "gh-token",
            "NEXUS_AUTOMATION_GITLAB_TOKEN": "gl-token",
        }
        with patch.dict(os.environ, env, clear=True):
            assert resolve_automation_git_token(None) == "gh-token"

    def test_returns_none_when_no_env_vars_set(self):
        keys_to_clear = {
            k: ""
            for k in (
                "NEXUS_AUTOMATION_GITHUB_TOKEN",
                "NEXUS_AUTOMATION_GITLAB_TOKEN",
                "NEXUS_AUTOMATION_GIT_TOKEN",
                "NEXUS_GITHUB_WRITE_TOKEN",
                "GITHUB_TOKEN",
                "GH_TOKEN",
                "GITLAB_TOKEN",
                "GLAB_TOKEN",
            )
        }
        with patch.dict(os.environ, keys_to_clear, clear=False):
            # All override keys are empty strings, so should return None
            assert resolve_automation_git_token("github") is None
            assert resolve_automation_git_token("gitlab") is None
            assert resolve_automation_git_token(None) is None
