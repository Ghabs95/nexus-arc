import tempfile
from pathlib import Path

from nexus.translators.to_markdown import translate_agent_to_markdown


def test_translate_to_markdown(capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        agent_yaml = Path(tmpdir) / "agent.yaml"
        agent_yaml.write_text("""
apiVersion: "nexus-arc/v1"
kind: "Agent"
metadata:
  name: "Translating Agent"
  description: "Description"
spec:
  agent_type: "translator"
  purpose: "The purpose content"
""")

        md_output = translate_agent_to_markdown(str(agent_yaml))

        assert "# Translating Agent" in md_output
        assert "The purpose content" in md_output
        assert "**Agent Type:** `translator`" in md_output
