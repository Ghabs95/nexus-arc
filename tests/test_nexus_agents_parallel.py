"""Tests for ParallelAgent."""
from __future__ import annotations

import asyncio
import time

import pytest

from nexus.agents.base import AgentContext, AgentOutput, BaseAgent
from nexus.agents.parallel import ParallelAgent


class SlowAgent(BaseAgent):
    def __init__(self, name: str, delay: float, response: str):
        super().__init__(name)
        self.delay = delay
        self.response = response

    async def run(self, context: AgentContext) -> AgentOutput:
        await asyncio.sleep(self.delay)
        return AgentOutput(content=self.response, metadata={"agent": self.name})


class EchoAgent(BaseAgent):
    async def run(self, context: AgentContext) -> AgentOutput:
        return AgentOutput(content=f"{self.name}:{context.task}")


def test_parallel_runs_all_agents():
    a = EchoAgent("A")
    b = EchoAgent("B")
    c = EchoAgent("C")
    par = ParallelAgent("par", [a, b, c])
    ctx = AgentContext(task="test")
    result = asyncio.run(par.run(ctx))
    assert "A:test" in result.content
    assert "B:test" in result.content
    assert "C:test" in result.content


def test_parallel_runs_concurrently():
    """Three 0.05s agents should complete in ~0.05s, not ~0.15s."""
    agents = [SlowAgent(f"s{i}", 0.05, f"result{i}") for i in range(3)]
    par = ParallelAgent("par", agents)
    ctx = AgentContext(task="concurrent")
    start = time.monotonic()
    result = asyncio.run(par.run(ctx))
    elapsed = time.monotonic() - start
    assert elapsed < 0.2, f"Expected concurrent execution, took {elapsed:.2f}s"
    assert result.metadata["parallel_agent_count"] == 3


def test_parallel_metadata():
    a = EchoAgent("alpha")
    b = EchoAgent("beta")
    par = ParallelAgent("par", [a, b])
    ctx = AgentContext(task="x")
    result = asyncio.run(par.run(ctx))
    assert result.metadata["parallel_agent_count"] == 2
    assert "alpha" in result.metadata["parallel_agent_names"]
    assert "beta" in result.metadata["parallel_agent_names"]


def test_parallel_requires_sub_agents():
    with pytest.raises(ValueError, match="at least one sub-agent"):
        ParallelAgent("empty", [])


def test_parallel_custom_separator():
    a = EchoAgent("A")
    b = EchoAgent("B")
    par = ParallelAgent("par", [a, b], separator=" | ")
    ctx = AgentContext(task="sep")
    result = asyncio.run(par.run(ctx))
    assert " | " in result.content


def test_parallel_all_receive_same_context():
    received = []

    class RecordAgent(BaseAgent):
        async def run(self, context: AgentContext) -> AgentOutput:
            received.append(context.task)
            return AgentOutput(content="ok")

    agents = [RecordAgent(f"r{i}") for i in range(3)]
    par = ParallelAgent("par", agents)
    ctx = AgentContext(task="shared_task")
    asyncio.run(par.run(ctx))
    assert all(t == "shared_task" for t in received)
