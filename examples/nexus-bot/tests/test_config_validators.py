import pytest

from nexus.core.config.validators import validate_project_config


def test_validate_project_config_rejects_invalid_tool_pref_shape():
    payload = {
        "model_profiles": {"small": {"gemini": "gemini-2.0-flash"}},
        "ai_tool_preferences": {"triage": "gemini"},
        "nexus": {"workspace": "x", "agents_dir": "a", "git_platform": "github"},
    }
    with pytest.raises(ValueError):
        validate_project_config(payload)


def test_validate_project_config_rejects_unknown_profile_reference():
    payload = {
        "model_profiles": {"small": {"gemini": "gemini-2.0-flash"}},
        "ai_tool_preferences": {"writer": {"provider": "gemini", "profile": "large"}},
        "nexus": {"workspace": "x", "agents_dir": "a", "git_platform": "github"},
    }
    with pytest.raises(ValueError):
        validate_project_config(payload)


def test_validate_project_config_rejects_invalid_profile_priority_provider():
    payload = {
        "model_profiles": {"fast": {"gemini": "gemini-2.0-flash"}},
        "profile_provider_priority": {"fast": ["invalid-provider"]},
        "ai_tool_preferences": {"writer": {"provider": "gemini", "profile": "fast"}},
        "nexus": {"workspace": "x", "agents_dir": "a", "git_platform": "github"},
    }
    with pytest.raises(ValueError):
        validate_project_config(payload)


def test_validate_project_config_accepts_claude_provider_in_profiles_and_priority():
    payload = {
        "model_profiles": {"balanced": {"claude": "claude-sonnet-4"}},
        "profile_provider_priority": {"balanced": ["claude"]},
        "ai_tool_preferences": {"writer": {"provider": "claude", "profile": "balanced"}},
        "nexus": {"workspace": "x", "agents_dir": "a", "git_platform": "github"},
    }
    validate_project_config(payload)


def test_validate_project_config_accepts_access_control_users():
    payload = {
        "nexus": {
            "workspace": "x",
            "agents_dir": "a",
            "git_platform": "github",
            "access_control": {
                "github_users": ["octocat", "@alice-dev"],
                "gitlab_users": ["john.doe", "@jane_doe"],
            },
        }
    }
    validate_project_config(payload)


def test_validate_project_config_rejects_invalid_access_control_username():
    payload = {
        "nexus": {
            "workspace": "x",
            "agents_dir": "a",
            "git_platform": "github",
            "access_control": {"github_users": ["org/user"]},
        }
    }
    with pytest.raises(ValueError):
        validate_project_config(payload)


def test_validate_project_config_accepts_top_level_gitlab_group():
    payload = {
        "nexus": {
            "workspace": "x",
            "agents_dir": "a",
            "git_platform": "gitlab",
            "access_control": {"gitlab_groups": ["acme"]},
        }
    }
    validate_project_config(payload)
