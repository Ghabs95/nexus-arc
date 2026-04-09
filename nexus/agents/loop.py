"""
nexus/agents/loop.py — LoopAgent: runs a sub-agent in a loop until a condition is met.
"""
from __future__ import annotations

from typing import Callable

from .base import AgentContext, AgentOutput, BaseAgent
from .context import slice_context


class LoopAgent(BaseAgent):
    """
    Runs a single sub-agent repeatedly until stop_condition returns True
    or max_iterations is reached (hard limit — always respected).

    The sub-agent receives an updated context on each iteration:
    - task remains the same
    - prior_outputs accumulates all previous iteration outputs
    """

    def __init__(
        self,
        name: str,
        sub_agent: BaseAgent,
        stop_condition: Callable[[AgentOutput], bool],
        max_iterations: int = 5,
        description: str = "",
    ) -> None:
        super().__init__(name=name, description=description)
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        self.sub_agent = sub_agent
        self.stop_condition = stop_condition
        self.max_iterations = max_iterations
        sub_agent._parent = self

    async def run(self, context: AgentContext) -> AgentOutput:
        current_context = slice_context(context)
        last_output = AgentOutput(content="")
        iterations = 0

        for i in range(self.max_iterations):
            iterations = i + 1
            sliced = slice_context(current_context)
            last_output = await self.sub_agent.run(sliced)
            current_context = current_context.with_output(last_output)

            if self.stop_condition(last_output):
                break

        last_output.metadata["loop_iterations"] = iterations
        last_output.metadata["loop_completed"] = self.stop_condition(last_output)
        last_output.metadata["loop_hit_max"] = iterations == self.max_iterations
        return last_output
