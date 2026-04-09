"""
nexus/core/workflow_engine/composite_step.py — CompositeStep: wraps nexus/agents/ primitives
into a Nexus Workflow step, preserving Git artifact writing and DB persistence.
"""
from __future__ import annotations

import logging
from typing import Any

from nexus.agents.base import AgentContext, AgentOutput, BaseAgent

logger = logging.getLogger(__name__)


class CompositeStep:
    """
    Wraps a nexus/agents/ BaseAgent composition (Sequential/Parallel/Loop/Coordinator)
    as a callable that Nexus Workflow can invoke in place of a single AI provider call.

    The WorkflowEngine's on_step_transition still fires; Git artifact writing
    and DB state transitions (started → complete/failed) are handled by the caller
    as usual. CompositeStep only runs the agent tree and returns the merged output.

    Usage in a workflow step definition (optional, opt-in):
        step.metadata["composite_agent"] = CompositeStep(my_sequential_agent)

    When a step has a composite_agent in metadata, the workflow engine should call:
        output = await composite_step.run(task=prompt, prior_outputs=[...])
    and use the returned content as the step output instead of calling the AIProvider directly.
    """

    # Metadata key used to store a CompositeStep on a WorkflowStep
    METADATA_KEY = "composite_agent"

    def __init__(self, agent: BaseAgent) -> None:
        self.agent = agent

    async def run(
        self,
        task: str,
        prior_outputs: list[AgentOutput] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentOutput:
        """
        Execute the agent tree and return the final AgentOutput.
        prior_outputs: outputs from previous workflow steps (used as context)
        """
        context = AgentContext(
            task=task,
            prior_outputs=prior_outputs or [],
            metadata=metadata or {},
        )
        try:
            output = await self.agent.run(context)
            logger.info(
                "CompositeStep(%s) completed. output_length=%d",
                self.agent.name,
                len(output.content),
            )
            return output
        except Exception as exc:
            logger.error("CompositeStep(%s) failed: %s", self.agent.name, exc)
            return AgentOutput(
                content=f"[CompositeStep failed: {exc}]",
                metadata={"error": str(exc), "agent": self.agent.name},
            )

    @classmethod
    def from_metadata(cls, step_metadata: dict[str, Any]) -> CompositeStep | None:
        """Extract a CompositeStep from a WorkflowStep's metadata dict, if present."""
        return step_metadata.get(cls.METADATA_KEY)
