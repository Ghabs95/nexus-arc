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

Note on MockSubAgent: when no AI provider is available (all providers fail
availability check), agents fall back to MockSubAgent which returns the
spec-defined response field. Useful for dry-run / testing.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def handle_agents_run(payload: dict[str, Any], config: dict | None = None) -> dict[str, Any]:
    """
    Entry point for POST /api/v1/agents/run.
    Resolves the AI provider once and passes it into all sub-agent builders.
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

    # Resolve provider once — passed into both sub-agent builder and coordinator.
    # This avoids constructing multiple provider instances per request.
    ai_provider = await _get_ai_provider(config)

    sub_agents: list[BaseAgent] = _build_sub_agents(agents_spec, ai_provider=ai_provider)

    try:
        agent: BaseAgent
        if agent_type == "sequential":
            agent = SequentialAgent(name="bridge_sequential", sub_agents=sub_agents)
        elif agent_type == "parallel":
            agent = ParallelAgent(name="bridge_parallel", sub_agents=sub_agents)
        elif agent_type == "loop":
            if len(sub_agents) != 1:
                return {"ok": False, "error": "loop agent_type requires exactly one agent"}
            agent = LoopAgent(
                name="bridge_loop",
                sub_agent=sub_agents[0],
                stop_condition=_make_stop_condition(stop_expr),
                max_iterations=max_iterations,
            )
        elif agent_type == "coordinator":
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

        output = await agent.run(AgentContext(task=task))
        return {"ok": True, "output": output.content, "metadata": output.metadata}

    except Exception as exc:
        logger.exception("agents_run failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _build_sub_agents(specs: list[dict], *, ai_provider: Any) -> list:
    """
    Build sub-agents from spec list using a pre-resolved AI provider.
    Falls back to MockSubAgent when ai_provider is None (dry-run / testing).
    """
    from nexus.agents.base import AgentContext, AgentOutput, BaseAgent

    agents = []
    for spec in specs:
        name = spec.get("name") or "unnamed"
        description = spec.get("description") or ""

        if ai_provider is not None:
            from nexus.agents.coordinator import LLMSubAgent
            agents.append(LLMSubAgent(name=name, description=description, ai_provider=ai_provider))
        else:
            fixed_response = spec.get("response") or f"[{name}]: {description}"

            class _MockAgent(BaseAgent):
                def __init__(self, _name: str, _desc: str, _resp: str) -> None:
                    super().__init__(name=_name, description=_desc)
                    self._resp = _resp

                async def run(self, context: AgentContext) -> AgentOutput:
                    return AgentOutput(content=self._resp, metadata={"mock": True, "agent": self.name})

            agents.append(_MockAgent(name, description, fixed_response))

    return agents


async def _get_ai_provider(config: dict | None) -> Any:
    """Resolve and verify an AIProvider for agent execution.

    Priority:
    1. config["ai_provider_factory"] callable (explicit injection)
    2. Auto-discover from registered Nexus adapters (Claude > Copilot > Gemini)

    Constructors are cheap but don't check CLI/env availability — we call
    check_availability() to verify before returning. Returns None if no
    working provider is found.
    """
    # 1. Explicit factory from config
    if config:
        try:
            provider_factory = config.get("ai_provider_factory")
            if callable(provider_factory):
                provider = provider_factory()
                if provider is not None:
                    return provider
        except Exception as exc:
            logger.debug("ai_provider_factory failed: %s", exc)

    # 2. Auto-discover in preference order, checking availability
    candidates = []
    try:
        from nexus.adapters.ai.claude_provider import ClaudeProvider
        candidates.append(ClaudeProvider())
    except Exception as exc:
        logger.debug("ClaudeProvider init failed: %s", exc)

    try:
        from nexus.adapters.ai.copilot_provider import CopilotCLIProvider
        candidates.append(CopilotCLIProvider())
    except Exception as exc:
        logger.debug("CopilotCLIProvider init failed: %s", exc)

    try:
        from nexus.adapters.ai.gemini_provider import GeminiCLIProvider
        candidates.append(GeminiCLIProvider())
    except Exception as exc:
        logger.debug("GeminiCLIProvider init failed: %s", exc)

    for provider in candidates:
        try:
            if await provider.check_availability():
                return provider
        except Exception as exc:
            logger.debug("%s.check_availability() failed: %s", type(provider).__name__, exc)

    return None


def _make_stop_condition(expr: str):
    """
    Build a stop_condition callable from a Python expression string.
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
