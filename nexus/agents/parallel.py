"""
nexus/agents/parallel.py — ParallelAgent: runs sub-agents concurrently.
"""
from __future__ import annotations

import asyncio
from typing import Literal

from .base import AgentContext, AgentOutput, BaseAgent
from .context import merge_outputs, slice_context


class ParallelAgent(BaseAgent):
    """
    Runs a list of sub-agents concurrently via asyncio.gather.
    Each sub-agent receives the same sliced context (task + prior outputs).
    Outputs are merged using the configured merge strategy.

    merge_strategy:
      - "concat"    : simple concatenation with separator (default, token-efficient)
      - "llm_merge" : reserved for future LLM-based output merging
    """

    def __init__(
        self,
        name: str,
        sub_agents: list[BaseAgent],
        description: str = "",
        merge_strategy: Literal["concat", "llm_merge"] = "concat",
        separator: str = "\n\n---\n\n",
    ) -> None:
        super().__init__(name=name, description=description)
        if not sub_agents:
            raise ValueError("ParallelAgent requires at least one sub-agent")
        self.sub_agents = sub_agents
        self.merge_strategy = merge_strategy
        self.separator = separator
        for agent in sub_agents:
            agent._parent = self

    async def run(self, context: AgentContext) -> AgentOutput:
        sliced = slice_context(context)

        # Run all sub-agents concurrently
        outputs: list[AgentOutput] = await asyncio.gather(
            *[agent.run(slice_context(sliced)) for agent in self.sub_agents]
        )

        if self.merge_strategy == "concat":
            merged = merge_outputs(list(outputs), separator=self.separator)
        else:
            # llm_merge: placeholder — falls back to concat until implemented
            merged = merge_outputs(list(outputs), separator=self.separator)

        merged.metadata["parallel_agent_count"] = len(self.sub_agents)
        merged.metadata["parallel_agent_names"] = [a.name for a in self.sub_agents]
        return merged
