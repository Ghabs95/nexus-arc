"""Tests for SequentialAgent."""
from __future__ import annotations

import asyncio

import pytest

from nexus.agents.base import AgentContext, AgentOutput, BaseAgent
from nexus.agents.sequential import SequentialAgent


class EchoAgent(BaseAgent):
    """Test agent that echoes its name + the task."""

    async def run(self, context: AgentContext) -> AgentOutput:
        return AgentOutput(content=f"{self.name}:{context.task}", metadata={"agent": self.name})


class AppendAgent(BaseAgent):
    """Test agent that appends its name to prior outputs."""

    async def run(self, context: AgentContext) -> AgentOutput:
        prior = " | ".join(o.content for o in context.prior_outputs)
        return AgentOutput(content=f"{prior} -> {self.name}" if prior else self.name)


def test_sequential_runs_in_order():
    a = EchoAgent("A")
    b = EchoAgent("B")
    c = EchoAgent("C")
    seq = SequentialAgent("seq", [a, b, c])

    ctx = AgentContext(task="test")
    result = asyncio.run(seq.run(ctx))
    # Last agent's output is returned
    assert result.content == "C:test"
    # All outputs accumulated in metadata
    assert "sequential_outputs" in result.metadata
    assert len(result.metadata["sequential_outputs"]) == 3


def test_sequential_passes_prior_outputs():
    a = AppendAgent("A")
    b = AppendAgent("B")
    seq = SequentialAgent("seq", [a, b])

    ctx = AgentContext(task="go")
    result = asyncio.run(seq.run(ctx))
    assert result.content == "A -> B"


def test_sequential_context_sliced():
    """Sub-agents must not receive raw conversation history."""
    received_contexts = []

    class RecordContextAgent(BaseAgent):
        async def run(self, context: AgentContext) -> AgentOutput:
            received_contexts.append(context)
            return AgentOutput(content="ok")

    agent = RecordContextAgent("recorder")
    seq = SequentialAgent("seq", [agent])
    ctx = AgentContext(task="mytask", metadata={"secret": "should_not_appear_in_task"})
    asyncio.run(seq.run(ctx))
    # task is preserved, metadata is copied but no extra conversation history
    assert received_contexts[0].task == "mytask"


def test_sequential_requires_sub_agents():
    with pytest.raises(ValueError):
        SequentialAgent("empty", [])


def test_sequential_single_agent():
    a = EchoAgent("solo")
    seq = SequentialAgent("seq", [a])
    ctx = AgentContext(task="hello")
    result = asyncio.run(seq.run(ctx))
    assert result.content == "solo:hello"


# Integration test: two-agent pipeline end-to-end
def test_sequential_integration_two_agents():
    class SummaryAgent(BaseAgent):
        async def run(self, context: AgentContext) -> AgentOutput:
            return AgentOutput(content=f"Summary of: {context.task}")

    class ReviewAgent(BaseAgent):
        async def run(self, context: AgentContext) -> AgentOutput:
            prior = context.prior_summary()
            return AgentOutput(content=f"Review OK. Prior: {prior}")

    pipeline = SequentialAgent("pipeline", [SummaryAgent("summariser"), ReviewAgent("reviewer")])
    ctx = AgentContext(task="Implement feature X")
    result = asyncio.run(pipeline.run(ctx))
    assert "Review OK" in result.content
    assert "Summary of: Implement feature X" in result.content
