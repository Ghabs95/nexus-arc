import textwrap

from nexus.core.execution import find_agent_definition


def _write_agent(path, *, name: str, agent_type: str):
    path.write_text(
        textwrap.dedent(
            f"""\
            apiVersion: \"nexus-arc/v1\"
            kind: \"Agent\"
            metadata:
              name: \"{name}\"
            spec:
              agent_type: \"{agent_type}\"
              timeout_seconds: 1200
            """
        )
    )


def test_find_agent_definition_prefers_spec_agent_type_over_filename(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    # Real-world shape: filename is persona, role is spec.agent_type.
    _write_agent(agents_dir / "project_lead.yaml", name="ProjectLead", agent_type="triage")

    path = find_agent_definition("triage", [str(agents_dir)])

    assert path is not None
    assert path.endswith("project_lead.yaml")


def test_find_agent_definition_keeps_legacy_filename_fallback(tmp_path):
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    # Legacy shape: filename matches role, but no matching agent_type.
    _write_agent(agents_dir / "triage-agent.yaml", name="Anything", agent_type="something_else")

    path = find_agent_definition("triage", [str(agents_dir)])

    assert path is not None
    assert path.endswith("triage-agent.yaml")
