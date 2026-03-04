"""Agent resolution helpers extracted from telegram_bot."""

from __future__ import annotations

import logging
import os

from nexus.core.handlers.agent_definition_utils import extract_agent_identity, load_agent_yaml

logger = logging.getLogger(__name__)


def resolve_agents_for_project(project_dir: str, nexus_dir_name: str) -> dict[str, str]:
    """Parse agents from either .agent.md files or Agent YAML definitions.

    Supports both:
    - Project-local generated agents: <project>/.nexus/agents/*.agent.md
    - Source agent YAMLs in configured agents_dir: *.yaml / *.yml with kind=Agent

    Returns a dictionary: {agent_display_name: source_filename}
    """
    agents_map: dict[str, str] = {}

    normalized_project_dir = os.path.abspath(project_dir)
    candidate_dirs = [
        os.path.join(normalized_project_dir, nexus_dir_name, "agents"),
        normalized_project_dir,
    ]

    seen_dirs = set()
    for agents_dir in candidate_dirs:
        if agents_dir in seen_dirs:
            continue
        seen_dirs.add(agents_dir)

        if not os.path.isdir(agents_dir):
            continue

        try:
            for filename in sorted(os.listdir(agents_dir)):
                filepath = os.path.join(agents_dir, filename)
                if not os.path.isfile(filepath):
                    continue

                if filename.endswith(".agent.md"):
                    try:
                        with open(filepath, encoding="utf-8") as file_handle:
                            lines = file_handle.readlines()
                        in_frontmatter = False
                        for line in lines:
                            if line.strip() == "---":
                                in_frontmatter = not in_frontmatter
                                continue
                            if in_frontmatter and line.strip().startswith("name:"):
                                agent_name = line.split("name:", 1)[1].strip()
                                if agent_name:
                                    agents_map[agent_name] = filename
                                break
                    except Exception as exc:
                        logger.warning(f"Failed to parse agent file {filename}: {exc}")
                    continue

                if filename.endswith(".yaml") or filename.endswith(".yml"):
                    try:
                        data = load_agent_yaml(filepath)
                        if not isinstance(data, dict):
                            continue
                        if str(data.get("kind", "")).lower() != "agent":
                            continue

                        agent_name, _agent_type = extract_agent_identity(filepath)
                        if not agent_name:
                            continue
                        agents_map[agent_name] = filename
                    except Exception as exc:
                        logger.warning(f"Failed to parse agent YAML {filename}: {exc}")
        except Exception as exc:
            logger.warning(f"Error listing agents in {agents_dir}: {exc}")

    return agents_map
