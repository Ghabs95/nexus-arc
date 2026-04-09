"""Tests for CompositeStep — workflow integration of nexus/agents/ primitives."""
from __future__ import annotations

import asyncio

from nexus.agents.base import AgentContext, AgentOutput, BaseAgent
from nexus.agents.loop import LoopAgent
from nexus.agents.parallel import ParallelAgent
from nexus.agents.sequential import SequentialAgent
from nexus.core.workflow_engine.composite_step import CompositeStep


class FixedAgent(BaseAgent):
    def __init__(self, name: str, response: str):
        super().__init__(name=name, description=f"{name} agent")
        self.response = response

    async def run(self, context: AgentContext) -> AgentOutput:
        return AgentOutput(content=self.response, metadata={"agent": self.name})


def test_composite_step_runs_sequential():
    a = FixedAgent("A", "result_a")
    b = FixedAgent("B", "result_b")
    seq = SequentialAgent("pipeline", [a, b])
    cs = CompositeStep(seq)

    output = asyncio.run(cs.run(task="do work"))
    assert output.content == "result_b"
    assert "sequential_outputs" in output.metadata


def test_composite_step_runs_parallel():
    a = FixedAgent("X", "x_out")
    b = FixedAgent("Y", "y_out")
    par = ParallelAgent("par", [a, b])
    cs = CompositeStep(par)

    output = asyncio.run(cs.run(task="parallel work"))
    assert "x_out" in output.content
    assert "y_out" in output.content


def test_composite_step_runs_loop():
    counter = [0]

    class CountAgent(BaseAgent):
        async def run(self, context: AgentContext) -> AgentOutput:
            counter[0] += 1
            return AgentOutput(content=str(counter[0]))

    loop = LoopAgent(
        "loop", CountAgent("c"), stop_condition=lambda o: int(o.content) >= 2, max_iterations=5
    )
    cs = CompositeStep(loop)
    output = asyncio.run(cs.run(task="loop"))
    assert output.content == "2"
    assert output.metadata["loop_iterations"] == 2


def test_composite_step_passes_prior_outputs():
    received = []

    class RecordAgent(BaseAgent):
        async def run(self, context: AgentContext) -> AgentOutput:
            received.append(len(context.prior_outputs))
            return AgentOutput(content="ok")

    cs = CompositeStep(RecordAgent("r"))
    prior = [AgentOutput(content="prev1"), AgentOutput(content="prev2")]
    asyncio.run(cs.run(task="task", prior_outputs=prior))
    assert received[0] == 2


def test_composite_step_handles_agent_failure():
    """CompositeStep must not raise — returns error AgentOutput on failure."""

    class BrokenAgent(BaseAgent):
        async def run(self, context: AgentContext) -> AgentOutput:
            raise RuntimeError("agent exploded")

    cs = CompositeStep(BrokenAgent("broken"))
    output = asyncio.run(cs.run(task="risky"))
    assert "CompositeStep failed" in output.content
    assert "error" in output.metadata


def test_composite_step_from_metadata():
    agent = FixedAgent("agent", "result")
    cs = CompositeStep(agent)
    meta = {CompositeStep.METADATA_KEY: cs}
    retrieved = CompositeStep.from_metadata(meta)
    assert retrieved is cs


def test_composite_step_from_metadata_missing():
    result = CompositeStep.from_metadata({})
    assert result is None
