"""Tests for nexus.core.agents — agent YAML discovery and resolution."""

import os
import textwrap

import pytest

from nexus.core.agents import find_agent_yaml, load_agent_definition, normalize_agent_key

# ---------------------------------------------------------------------------
# normalize_agent_key
# ---------------------------------------------------------------------------


class TestNormalizeAgentKey:
    def test_camel_case(self):
        assert normalize_agent_key("ProductDesigner") == "product-designer"

    def test_underscore(self):
        assert normalize_agent_key("qa_guard") == "qa-guard"

    def test_simple_name(self):
        assert normalize_agent_key("Atlas") == "atlas"

    def test_multiple_caps(self):
        assert normalize_agent_key("QAGuard") == "qaguard"

    def test_spaces(self):
        assert normalize_agent_key("Ops Commander") == "ops-commander"

    def test_mixed_separators(self):
        assert normalize_agent_key("Ops_Commander Lead") == "ops-commander-lead"

    def test_already_kebab(self):
        assert normalize_agent_key("ops-commander") == "ops-commander"

    def test_empty_string(self):
        assert normalize_agent_key("") == ""


# ---------------------------------------------------------------------------
# Fixtures — create temp agent YAML files
# ---------------------------------------------------------------------------

AGENT_YAML_TEMPLATE = textwrap.dedent("""\
    apiVersion: "nexus-arc/v1"
    kind: "Agent"
    metadata:
      name: "{name}"
    spec:
      agent_type: "{agent_type}"
      timeout_seconds: 1800
""")


@pytest.fixture()
def agents_dir(tmp_path):
    """Create a temp directory with a few agent YAML files."""
    d = tmp_path / "agents"
    d.mkdir()

    # triage agent
    (d / "triage-agent.yaml").write_text(
        AGENT_YAML_TEMPLATE.format(name="triage", agent_type="triage")
    )
    # Atlas agent (CamelCase agent_type)
    (d / "atlas-agent.yaml").write_text(
        AGENT_YAML_TEMPLATE.format(name="atlas", agent_type="Atlas")
    )
    # A non-agent YAML (should be skipped)
    (d / "workflow.yaml").write_text(textwrap.dedent("""\
            apiVersion: "nexus-arc/v1"
            kind: "Workflow"
            metadata:
              name: "dev-workflow"
        """))
    # An invalid YAML file (should be skipped gracefully)
    (d / "broken.yaml").write_text("{{ invalid yaml content")

    return str(d)


@pytest.fixture()
def shared_dir(tmp_path):
    """Create a secondary (shared) agents directory."""
    d = tmp_path / "shared"
    d.mkdir()

    (d / "architect-agent.yaml").write_text(
        AGENT_YAML_TEMPLATE.format(name="architect", agent_type="Architect")
    )
    return str(d)


# ---------------------------------------------------------------------------
# find_agent_yaml
# ---------------------------------------------------------------------------


class TestFindAgentYaml:
    def test_match_exact(self, agents_dir):
        path = find_agent_yaml("triage", [agents_dir])
        assert path.endswith("triage-agent.yaml")

    def test_match_camel_case(self, agents_dir):
        path = find_agent_yaml("Atlas", [agents_dir])
        assert path.endswith("atlas-agent.yaml")

    def test_match_normalised_input(self, agents_dir):
        """Input is kebab-case, agent_type in YAML is CamelCase."""
        path = find_agent_yaml("atlas", [agents_dir])
        assert path.endswith("atlas-agent.yaml")

    def test_not_found(self, agents_dir):
        assert find_agent_yaml("nonexistent", [agents_dir]) == ""

    def test_skips_non_agent_kind(self, agents_dir):
        """Workflow YAML files should be ignored."""
        assert find_agent_yaml("dev-workflow", [agents_dir]) == ""

    def test_skips_broken_yaml(self, agents_dir):
        """Invalid YAML should be silently skipped."""
        result = find_agent_yaml("triage", [agents_dir])
        assert result  # should still find triage

    def test_multiple_search_dirs(self, agents_dir, shared_dir):
        """Finds agent in second directory when not in first."""
        path = find_agent_yaml("Architect", [agents_dir, shared_dir])
        assert path.endswith("architect-agent.yaml")

    def test_first_dir_takes_priority(self, agents_dir, shared_dir):
        """If both dirs have a matching agent, first one wins."""
        # Add Atlas to shared as well
        shared_atlas = os.path.join(shared_dir, "atlas-agent.yaml")
        with open(shared_atlas, "w") as f:
            f.write(AGENT_YAML_TEMPLATE.format(name="atlas-shared", agent_type="Atlas"))

        path = find_agent_yaml("Atlas", [agents_dir, shared_dir])
        # Should come from agents_dir (first)
        assert agents_dir in path

    def test_nonexistent_dir_skipped(self, agents_dir):
        """Non-existent directories in search path are silently skipped."""
        path = find_agent_yaml("triage", ["/nonexistent/dir", agents_dir])
        assert path.endswith("triage-agent.yaml")

    def test_empty_search_dirs(self):
        assert find_agent_yaml("triage", []) == ""

    def test_returns_absolute_path(self, agents_dir):
        path = find_agent_yaml("triage", [agents_dir])
        assert os.path.isabs(path)

    def test_subdirectory_recursive(self, tmp_path):
        """Agent YAML in a nested subdirectory is found."""
        nested = tmp_path / "deep" / "nested"
        nested.mkdir(parents=True)
        (nested / "dev-agent.yml").write_text(
            AGENT_YAML_TEMPLATE.format(name="developer", agent_type="Developer")
        )
        path = find_agent_yaml("Developer", [str(tmp_path)])
        assert path.endswith("dev-agent.yml")


# ---------------------------------------------------------------------------
# load_agent_definition
# ---------------------------------------------------------------------------


class TestLoadAgentDefinition:
    def test_load_existing(self, agents_dir):
        data = load_agent_definition("triage", [agents_dir])
        assert data is not None
        assert data["kind"] == "Agent"
        assert data["spec"]["agent_type"] == "triage"

    def test_load_not_found(self, agents_dir):
        assert load_agent_definition("nonexistent", [agents_dir]) is None

    def test_load_from_shared(self, agents_dir, shared_dir):
        data = load_agent_definition("Architect", [agents_dir, shared_dir])
        assert data is not None
        assert data["spec"]["agent_type"] == "Architect"
