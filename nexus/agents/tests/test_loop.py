"""Tests for LoopAgent."""
from __future__ import annotations

import asyncio
import pytest

from nexus.agents.base import AgentContext, AgentOutput, BaseAgent
from nexus.agents.loop import LoopAgent


class CounterAgent(BaseAgent):
    """Agent that increments a counter and returns it."""
    def __init__(self):
        super().__init__("counter")
        self.call_count = 0

    async def run(self, context: AgentContext) -> AgentOutput:
        self.call_count += 1
        return AgentOutput(content=str(self.call_count), metadata={"count": self.call_count})


class ToggleAgent(BaseAgent):
    """Agent that alternates outputs."""
    def __init__(self):
        super().__init__("toggle")
        self._flip = False

    async def run(self, context: AgentContext) -> AgentOutput:
        self._flip = not self._flip
        return AgentOutput(content="done" if self._flip else "not_done")


def test_loop_stops_on_condition():
    counter = CounterAgent()
    loop = LoopAgent("loop", counter, stop_condition=lambda o: int(o.content) >= 3, max_iterations=10)
    ctx = AgentContext(task="count")
    result = asyncio.run(loop.run(ctx))
    assert result.metadata["loop_iterations"] == 3
    assert result.metadata["loop_completed"] is True
    assert result.metadata["loop_hit_max"] is False


def test_loop_respects_max_iterations():
    counter = CounterAgent()
    loop = LoopAgent("loop", counter, stop_condition=lambda o: False, max_iterations=4)
    ctx = AgentContext(task="forever")
    result = asyncio.run(loop.run(ctx))
    assert result.metadata["loop_iterations"] == 4
    assert result.metadata["loop_hit_max"] is True
    assert counter.call_count == 4


def test_loop_single_iteration():
    counter = CounterAgent()
    loop = LoopAgent("loop", counter, stop_condition=lambda o: True, max_iterations=5)
    ctx = AgentContext(task="once")
    result = asyncio.run(loop.run(ctx))
    assert result.metadata["loop_iterations"] == 1
    assert counter.call_count == 1


def test_loop_passes_prior_outputs():
    prior_outputs_received = []

    class RecordPriorAgent(BaseAgent):
        async def run(self, context: AgentContext) -> AgentOutput:
            prior_outputs_received.append(len(context.prior_outputs))
            return AgentOutput(content=f"iter{len(context.prior_outputs)}")

    agent = RecordPriorAgent("recorder")
    call_count = [0]

    def stop_after_3(output):
        call_count[0] += 1
        return call_count[0] >= 3

    loop = LoopAgent("loop", agent, stop_condition=stop_after_3, max_iterations=10)
    ctx = AgentContext(task="test")
    asyncio.run(loop.run(ctx))
    # First iter has 0 prior outputs, second has 1, third has 2
    assert prior_outputs_received == [0, 1, 2]


def test_loop_invalid_max_iterations():
    agent = CounterAgent()
    with pytest.raises(ValueError):
        LoopAgent("loop", agent, stop_condition=lambda o: True, max_iterations=0)
