"""
nexus/core/command_bridge/agents_handler.py

Handles POST /api/v1/agents/run — invokes nexus/agents/ composition primitives
from the bridge HTTP API (used by OpenClaw skill and other callers).

Request schema:
    {
        "task": "string (required) — the task to run",
        "agent_type": "sequential|parallel|loop|coordinator (default: sequential)",
        "agents": [
            {"name": "string", "description": "string", "response": "string (mock only)"}
        ],
        "router_url": "string (optional, default: http://127.0.0.1:7771)",
        "max_iterations": int (LoopAgent only, default 5),
        "stop_condition": "string (LoopAgent only, Python expression on output.content)"
    }

Response schema:
    {
        "ok": bool,
        "output": "string",
        "metadata": dict,
        "error": "string (if ok=False)"
    }
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def handle_agents_run(payload: dict[str, Any], config: dict | None = None) -> dict[str, Any]:
    """
    Entry point for POST /api/v1/agents/run.
    Builds the requested agent composition and runs it.
    """
    from nexus.agents.base import AgentContext, BaseAgent
    from nexus.agents.coordinator import Coordinator
    from nexus.agents.loop import LoopAgent
    from nexus.agents.parallel import ParallelAgent
    from nexus.agents.sequential import SequentialAgent

    task = (payload.get("task") or "").strip()
    if not task:
        return {"ok": False, "error": "task is required"}

    agent_type = (payload.get("agent_type") or "sequential").lower()
    agents_spec = payload.get("agents") or []
    router_url = payload.get("router_url") or "http://127.0.0.1:7771"
    max_iterations = int(payload.get("max_iterations") or 5)
    stop_expr = payload.get("stop_condition") or ""

    if not agents_spec:
        return {"ok": False, "error": "agents list is required"}

    # Build sub-agents from spec
    sub_agents: list[BaseAgent] = _build_sub_agents(agents_spec, config=config)

    try:
        agent: BaseAgent
        if agent_type == "sequential":
            agent = SequentialAgent(name="bridge_sequential", sub_agents=sub_agents)
        elif agent_type == "parallel":
            agent = ParallelAgent(name="bridge_parallel", sub_agents=sub_agents)
        elif agent_type == "loop":
            if len(sub_agents) != 1:
                return {"ok": False, "error": "loop agent_type requires exactly one agent"}
            stop_fn = _make_stop_condition(stop_expr)
            agent = LoopAgent(
                name="bridge_loop",
                sub_agent=sub_agents[0],
                stop_condition=stop_fn,
                max_iterations=max_iterations,
            )
        elif agent_type == "coordinator":
            ai_provider = _get_ai_provider(config)
            if ai_provider is None:
                return {"ok": False, "error": "coordinator requires an AI provider; none configured"}
            agent = Coordinator(
                name="bridge_coordinator",
                sub_agents=sub_agents,
                ai_provider=ai_provider,
                router_url=router_url,
            )
        else:
            return {"ok": False, "error": f"unknown agent_type: {agent_type}"}

        context = AgentContext(task=task)
        output = await agent.run(context)
        return {"ok": True, "output": output.content, "metadata": output.metadata}

    except Exception as exc:
        logger.exception("agents_run failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _build_sub_agents(specs: list[dict], config: dict | None = None):
    """
    Build sub-agents from spec list.
    If an AI provider is available, wraps each spec as an LLMSubAgent.
    Falls back to a MockSubAgent (returns description as output) for tests.
    """
    from nexus.agents.base import AgentContext, AgentOutput, BaseAgent

    ai_provider = _get_ai_provider(config)

    agents = []
    for spec in specs:
        name = spec.get("name") or "unnamed"
        description = spec.get("description") or ""

        if ai_provider is not None:
            from nexus.agents.coordinator import LLMSubAgent
            agents.append(LLMSubAgent(name=name, description=description, ai_provider=ai_provider))
        else:
            # MockSubAgent: returns spec-defined response (useful for testing/dry-run)
            fixed_response = spec.get("response") or f"[{name}]: {description}"

            class _MockAgent(BaseAgent):
                def __init__(self, _name, _desc, _resp):
                    super().__init__(name=_name, description=_desc)
                    self._resp = _resp

                async def run(self, context: AgentContext) -> AgentOutput:
                    return AgentOutput(content=self._resp, metadata={"mock": True, "agent": self.name})

            agents.append(_MockAgent(name, description, fixed_response))

    return agents


def _get_ai_provider(config: dict | None):
    """Try to resolve an AIProvider from config. Returns None if unavailable."""
    if not config:
        return None
    try:
        provider_factory = config.get("ai_provider_factory")
        if callable(provider_factory):
            return provider_factory()
    except Exception as exc:
        logger.debug("Could not resolve AI provider: %s", exc)
    return None


def _make_stop_condition(expr: str):
    """
    Build a stop_condition callable from a Python expression string.
    The expression is evaluated with `output` in scope (AgentOutput).
    Falls back to never-stop if expression is empty or invalid.
    """
    if not expr:
        return lambda output: False

    def _stop(output):
        try:
            return bool(eval(expr, {}, {"output": output, "content": output.content}))  # noqa: S307
        except Exception:
            return False

    return _stop
