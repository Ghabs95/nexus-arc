"""
nexus/agents/sequential.py — SequentialAgent: runs sub-agents in order.
"""
from __future__ import annotations

from .base import AgentContext, AgentOutput, BaseAgent
from .context import slice_context


class SequentialAgent(BaseAgent):
    """
    Runs a list of sub-agents in order.
    Each sub-agent receives a sliced context containing the task and all
    prior outputs accumulated so far — never raw conversation history.
    """

    def __init__(self, name: str, sub_agents: list[BaseAgent], description: str = "") -> None:
        super().__init__(name=name, description=description)
        if not sub_agents:
            raise ValueError("SequentialAgent requires at least one sub-agent")
        self.sub_agents = sub_agents
        for agent in sub_agents:
            agent._parent = self

    async def run(self, context: AgentContext) -> AgentOutput:
        current_context = slice_context(context)
        last_output = AgentOutput(content="")

        for agent in self.sub_agents:
            sliced = slice_context(current_context)
            last_output = await agent.run(sliced)
            current_context = current_context.with_output(last_output)

        # Return the final agent's output; prior outputs available in context
        last_output.metadata["sequential_outputs"] = [
            o.content for o in current_context.prior_outputs
        ]
        return last_output
