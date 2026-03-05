"""Tests for agent resolution handler."""

from nexus.core.handlers.agent_resolution_handler import resolve_agents_for_project


def test_resolve_agents_from_project_root_scans_nexus_and_root(tmp_path):
    project_root = tmp_path / "project"
    nexus_agents_dir = project_root / ".nexus" / "agents"
    nexus_agents_dir.mkdir(parents=True)

    # Generated agent markdown in .nexus/agents
    (nexus_agents_dir / "architect.agent.md").write_text(
        """---
name: Architect
---
# Agent
""",
        encoding="utf-8",
    )

    # Source YAML agent in configured root dir
    (project_root / "triage-agent.yaml").write_text(
        """apiVersion: \"nexus-arc/v1\"
kind: \"Agent\"
metadata:
  name: \"Triage\"
spec:
  agent_type: \"triage\"
""",
        encoding="utf-8",
    )

    agents_map = resolve_agents_for_project(str(project_root), ".nexus")

    assert agents_map["Architect"] == "architect.agent.md"
    assert agents_map["Triage"] == "triage-agent.yaml"


def test_resolve_agents_from_agents_dir_yaml_only(tmp_path):
    agents_dir = tmp_path / "examples" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "developer-agent.yaml").write_text(
        """apiVersion: \"nexus-arc/v1\"
kind: \"Agent\"
metadata:
  name: \"Developer\"
spec:
  agent_type: \"developer\"
""",
        encoding="utf-8",
    )

    (agents_dir / "README.md").write_text("not an agent", encoding="utf-8")

    agents_map = resolve_agents_for_project(str(agents_dir), ".nexus")

    assert agents_map == {"Developer": "developer-agent.yaml"}
