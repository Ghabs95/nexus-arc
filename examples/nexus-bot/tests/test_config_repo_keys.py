"""Tests for provider-neutral repository keys in project config."""

import nexus.core.config as config
import nexus.core.config.repos as config_repos


def test_get_git_repos_auto_discovers_from_workspace(monkeypatch, tmp_path):
    workspace_root = tmp_path / "sampleco"
    backend_dir = workspace_root / "backend"
    mobile_dir = workspace_root / "mobile"
    backend_dir.mkdir(parents=True)
    mobile_dir.mkdir(parents=True)
    (backend_dir / ".git").mkdir()
    (mobile_dir / ".git").mkdir()

    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(
        config,
        "_get_project_config",
        lambda: {
            "sampleco": {
                "workspace": "sampleco",
                "git_platform": "gitlab",
            }
        },
    )

    class _Result:
        def __init__(self, returncode: int, stdout: str):
            self.returncode = returncode
            self.stdout = stdout

    def fake_run(cmd, capture_output, text, timeout, check):
        target = cmd[2]
        if target.endswith("/backend"):
            return _Result(0, "git@gitlab.com:sample-org/backend.git\n")
        if target.endswith("/mobile"):
            return _Result(0, "https://gitlab.com/sample-org/mobile-app.git\n")
        return _Result(1, "")

    monkeypatch.setattr(config_repos.subprocess, "run", fake_run)

    repos = config.get_repos("sampleco")

    assert sorted(repos) == ["sample-org/backend", "sample-org/mobile-app"]


def test_get_repos_supports_git_repo_and_git_repos(monkeypatch):
    monkeypatch.setattr(
        config,
        "_get_project_config",
        lambda: {
            "sampleco": {
                "workspace": "sampleco",
                "git_platform": "gitlab",
                "git_repo": "sample-org/backend",
                "git_repos": ["sample-org/backend", "sample-org/mobile-app"],
            }
        },
    )

    repos = config.get_repos("sampleco")

    assert repos == ["sample-org/backend", "sample-org/mobile-app"]


def test_get_workflow_profile_prefers_project_specific(monkeypatch):
    monkeypatch.setattr(
        config,
        "_get_project_config",
        lambda: {
            "workflow_definition_path": "shared/workflows/default_workflow.yaml",
            "sampleco": {
                "workspace": "sampleco",
                "workflow_definition_path": "sampleco/workflows/project_workflow.yaml",
            },
        },
    )

    assert config.get_workflow_profile("sampleco") == "sampleco/workflows/project_workflow.yaml"


def test_get_workflow_profile_falls_back_to_global(monkeypatch):
    monkeypatch.setattr(
        config,
        "_get_project_config",
        lambda: {
            "workflow_definition_path": "shared/workflows/default_workflow.yaml",
            "core": {"workspace": "core"},
        },
    )

    assert config.get_workflow_profile("core") == "shared/workflows/default_workflow.yaml"


def test_normalize_project_key_uses_config_aliases(monkeypatch):
    monkeypatch.setattr(
        config,
        "_get_project_config",
        lambda: {
            "projects": {
                "acme": {"code": "project_acme", "aliases": []},
                "corex": {"code": "project_core", "aliases": ["core app", "core-app"]},
            },
            "project_acme": {"workspace": "project_acme"},
            "project_core": {"workspace": "project_core"},
        },
    )

    assert config.normalize_project_key("acme") == "project_acme"
    assert config.normalize_project_key("corex") == "project_core"
    assert config.normalize_project_key("unknown") == "unknown"


def test_get_track_short_projects_derives_from_aliases(monkeypatch):
    monkeypatch.setattr(
        config,
        "_get_project_config",
        lambda: {
            "projects": {
                "aaa": {"code": "project_alpha", "aliases": []},
                "bbb": {"code": "project_beta", "aliases": []},
                "ccc": {"code": "project_gamma", "aliases": []},
                "corex": {"code": "project_core", "aliases": ["core app", "core-app"]},
            },
            "project_alpha": {"workspace": "project_alpha"},
            "project_beta": {"workspace": "project_beta"},
            "project_gamma": {"workspace": "project_gamma"},
            "project_core": {"workspace": "project_core"},
        },
    )

    assert config.get_track_short_projects() == ["aaa", "bbb", "ccc", "corex"]


def test_get_chat_agents_reads_mapping_shape(monkeypatch):
    monkeypatch.setattr(
        config,
        "_get_project_config",
        lambda: {
            "sampleco": {
                "workspace": "sampleco",
                "system_operations": {
                    "chat": {
                        "business": {"context_path": "sample-business-os", "label": "Business"},
                        "marketing": {"context_path": "sample-marketing-os", "label": "Marketing"},
                    }
                },
            }
        },
    )

    agents = config.get_chat_agents("sampleco")

    assert [item["agent_type"] for item in agents] == ["business", "marketing"]
    assert agents[0]["context_path"] == "sample-business-os"
    assert agents[0]["label"] == "Business"


def test_get_chat_agents_reads_list_shape(monkeypatch):
    monkeypatch.setattr(
        config,
        "_get_project_config",
        lambda: {
            "sampleco": {
                "workspace": "sampleco",
                "system_operations": {
                    "chat": [
                        {"business": {"context_path": "sample-business-os"}},
                        {"agent_type": "marketing", "context_path": "sample-marketing-os"},
                    ]
                },
            }
        },
    )

    agents = config.get_chat_agents("sampleco")

    assert [item["agent_type"] for item in agents] == ["business", "marketing"]
    assert agents[0]["context_path"] == "sample-business-os"
    assert agents[1]["context_path"] == "sample-marketing-os"
