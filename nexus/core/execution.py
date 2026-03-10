"""Agent execution and orchestration logic.

Handles resolving agent definitions, generating instructions, and
preparing execution parameters.
"""

import logging
import os
import re

from nexus.core.agents import find_agent_yaml, normalize_agent_key

logger = logging.getLogger(__name__)


def find_agent_definition(agent_name: str, search_dirs: list[str]) -> str | None:
    """Find the YAML definition for an agent.

    Resolution order:
    1. Preferred: match Agent YAML by ``spec.agent_type``.
    2. Fallback: legacy filename-based matching for backwards compatibility.
    """
    requested = agent_name.lstrip("@")

    # Preferred behavior: resolve by the declared YAML agent_type.
    resolved = find_agent_yaml(requested, search_dirs)
    if resolved:
        return resolved

    # Backward-compatible fallback: filename variants.
    normalized_name = normalize_agent_key(requested)
    legacy_candidates = {
        requested.lower(),
        normalized_name,
        normalized_name.replace("-", "_"),
        f"{requested.lower()}_agent",
        f"{requested.lower()}-agent",
        f"{normalized_name}_agent",
        f"{normalized_name}-agent",
    }
    extensions = (".yaml", ".yml")

    for directory in search_dirs:
        if not os.path.exists(directory):
            continue

        for candidate in legacy_candidates:
            for ext in extensions:
                path = os.path.join(directory, f"{candidate}{ext}")
                if os.path.exists(path):
                    return path

    return None


class ExecutionEngine:
    """Engine for preparing agent execution."""

    @staticmethod
    def build_default_prompt(
        agent_type: str, issue_url: str, tier_name: str, workflow_name: str, task_content: str
    ) -> str:
        """Build a standard execution prompt for an agent."""
        return (
            f"You are a {agent_type} agent.\n\n"
            f"Issue: {issue_url}\n"
            f"Tier: {tier_name}\n"
            f"Workflow: /{workflow_name}\n\n"
            f"Task details:\n{task_content}"
        )

    @staticmethod
    def sync_workspace_skill(
        workspace_dir: str, agent_name: str, instructions: str, force: bool = False
    ) -> str | None:
        """
        Sync agent instructions to the workspace skill directory.

        This enables Gemini-style workspace skills to be automatically
        updated from the agent definition.
        """
        if not workspace_dir or not instructions:
            return None

        # Standard skill path: .agent/skills/<agent_name>/SKILL.md
        normalized_name = re.sub(r"[^a-z0-9]+", "_", agent_name.lower()).strip("_")
        skill_dir = os.path.join(workspace_dir, ".agent", "skills", normalized_name)
        skill_path = os.path.join(skill_dir, "SKILL.md")

        try:
            if not force and os.path.exists(skill_path):
                # Optimization: check if content changed (optional)
                pass

            os.makedirs(skill_dir, exist_ok=True)
            with open(skill_path, "w", encoding="utf-8") as f:
                f.write(instructions)

            return skill_path
        except Exception as e:
            logger.error(f"Failed to sync workspace skill for {agent_name}: {e}")
            return None
