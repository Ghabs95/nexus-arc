from nexus.core.workflow_runtime.workflow_issue_resolution_service import (
    candidate_repos_for_issue_lookup,
    resolve_project_config_for_repo,
)


def test_candidate_repos_for_issue_lookup_includes_other_project_repos_after_requested_project():
    repos = candidate_repos_for_issue_lookup(
        project_key="nexus",
        project_config={
            "nexus": {
                "git_repo": "Ghabs95/nexus-arc",
                "git_repos": ["Ghabs95/nexus-arc", "Ghabs95/nexus"],
            },
            "biome": {
                "git_repo": "mybiohackingdata/biome-literature",
                "git_repos": ["mybiohackingdata/biome-literature"],
            },
        },
        default_repo="Ghabs95/nexus-arc",
    )

    assert repos == [
        "Ghabs95/nexus-arc",
        "Ghabs95/nexus",
        "mybiohackingdata/biome-literature",
    ]


def test_resolve_project_config_for_repo_rebinds_to_matching_project():
    project_name, config = resolve_project_config_for_repo(
        repo="mybiohackingdata/biome-literature",
        requested_project_key="nexus",
        project_config={
            "nexus": {
                "agents_dir": "agents/nexus",
                "workspace": "nexus",
                "git_repo": "Ghabs95/nexus-arc",
            },
            "biome": {
                "agents_dir": "agents/biome",
                "workspace": "biome",
                "git_repo": "mybiohackingdata/biome-literature",
            },
        },
    )

    assert project_name == "biome"
    assert config == {
        "agents_dir": "agents/biome",
        "workspace": "biome",
        "git_repo": "mybiohackingdata/biome-literature",
    }
