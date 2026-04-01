from nexus.core.workflow_runtime.workflow_issue_resolution_service import (
    candidate_repos_for_issue_lookup,
    resolve_project_config_for_repo,
)


def test_candidate_repos_for_issue_lookup_includes_other_project_repos_after_requested_project():
    repos = candidate_repos_for_issue_lookup(
        project_key="nexus",
        project_config={
            "nexus": {
                "git_repo": "ghabs-org/nexus-arc",
                "git_repos": ["ghabs-org/nexus-arc", "ghabs-org/nexus"],
            },
            "projectA": {
                "git_repo": "acme/projectA-os",
                "git_repos": ["acme/projectA-be", "acme/projectA-app"],
            },
        },
        default_repo="ghabs-org/nexus-arc",
    )

    assert repos == [
        "ghabs-org/nexus-arc",
        "ghabs-org/nexus",
        "acme/projectA-be",
    ]


def test_resolve_project_config_for_repo_rebinds_to_matching_project():
    project_name, config = resolve_project_config_for_repo(
        repo="acme/projectA-be",
        requested_project_key="nexus",
        project_config={
            "nexus": {
                "agents_dir": "agents/nexus",
                "workspace": "nexus",
                "git_repo": "ghabs-org/nexus-arc",
            },
            "acme": {
                "agents_dir": "agents/acme",
                "workspace": "acme",
                "git_repo": "acme/projectA-os",
            },
        },
    )

    assert project_name == "acme"
    assert config == {
        "agents_dir": "agents/acme",
        "workspace": "acme",
        "git_repo": "acme/projectA-os",
    }
