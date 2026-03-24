"""Anthropic Claude AI provider implementation.

Requires the ``anthropic`` optional extra::

    pip install nexus-arc[anthropic]

Uses the ``anthropic`` Python SDK (v0.25+) with async support.
"""

import logging
import time

from nexus.adapters.ai.base import AIProvider, ExecutionContext
from nexus.core.models import AgentResult, RateLimitStatus

try:
    import anthropic as _anthropic_module

    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

logger = logging.getLogger(__name__)

# Task types where Claude excels
_CLAUDE_PREFERRED_TASKS = {"reasoning", "analysis", "code_review", "content_creation", "summarization"}

# Default model; can be overridden at construction time
_DEFAULT_MODEL = "claude-sonnet-4-5"


def _require_anthropic() -> None:
    if not _ANTHROPIC_AVAILABLE:
        raise ImportError(
            "anthropic package is required for ClaudeProvider. "
            "Install it with: pip install nexus-arc[anthropic]"
        )


class ClaudeProvider(AIProvider):
    """AI provider that uses the Anthropic Claude API.

    Args:
        api_key: Anthropic API key. Defaults to ``ANTHROPIC_API_KEY`` env var.
        model: Model to use (default ``claude-sonnet-4-5``).
        system_prompt: Optional system prompt prepended to every request.
        timeout: Default request timeout in seconds.
        max_tokens: Default maximum output tokens (required by Anthropic API).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        system_prompt: str = "You are a helpful AI assistant.",
        timeout: int = 300,
        max_tokens: int = 4096,
    ):
        _require_anthropic()
        self._model = model
        self._system_prompt = system_prompt
        self._timeout = timeout
        self._max_tokens = max_tokens
        # AsyncAnthropic lazily picks up ANTHROPIC_API_KEY from env when api_key is None
        self._client = _anthropic_module.AsyncAnthropic(api_key=api_key)
        self._availability_cache: dict = {}

    @property
    def name(self) -> str:
        return "claude"

    async def check_availability(self) -> bool:
        """Return True if the Anthropic API is reachable and the key is valid."""
        now = time.time()
        cached = self._availability_cache.get("claude")
        if cached and now - cached["at"] < 300:
            return cached["available"]

        try:
            # Lightweight call: list models (or send a minimal message)
            await self._client.models.list()
            available = True
        except Exception as exc:
            logger.warning("Claude availability check failed: %s", exc)
            available = False

        self._availability_cache["claude"] = {"available": available, "at": now}
        return available

    async def get_rate_limit_status(self) -> RateLimitStatus:
        """Return current rate-limit information."""
        return RateLimitStatus(
            provider=self.name,
            is_limited=False,  # updated to True on RateLimitError in execute_agent
        )

    def get_preference_score(self, task_type: str) -> float:
        """Return 0.9 for reasoning/analysis tasks, 0.75 otherwise."""
        return 0.9 if task_type in _CLAUDE_PREFERRED_TASKS else 0.75

    async def execute_agent(self, context: ExecutionContext) -> AgentResult:
        """Send the prompt to the Anthropic Messages API and return the reply."""
        start = time.time()
        model = context.model_override or self._model
        max_tokens = context.max_tokens or self._max_tokens

        # Build the user message, appending issue URL as context when provided
        user_content = context.prompt
        if context.issue_url:
            user_content = f"{context.prompt}\n\nIssue: {context.issue_url}"

        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=self._system_prompt,
                messages=[{"role": "user", "content": user_content}],
                timeout=context.timeout or self._timeout,
            )
            elapsed = time.time() - start
            output = response.content[0].text if response.content else ""
            return AgentResult(
                success=True,
                output=output,
                execution_time=elapsed,
                provider_used=self.name,
                metadata={
                    "model": response.model,
                    "usage": {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                    },
                    "stop_reason": response.stop_reason,
                },
            )

        except _anthropic_module.RateLimitError as exc:
            elapsed = time.time() - start
            logger.warning("Claude rate-limit hit: %s", exc)
            return AgentResult(
                success=False,
                output="",
                error=f"Rate limit: {exc}",
                execution_time=elapsed,
                provider_used=self.name,
            )

        except _anthropic_module.APITimeoutError as exc:
            elapsed = time.time() - start
            return AgentResult(
                success=False,
                output="",
                error=f"Timeout: {exc}",
                execution_time=elapsed,
                provider_used=self.name,
            )

        except Exception as exc:
            elapsed = time.time() - start
            logger.error("Claude execute_agent failed: %s", exc)
            return AgentResult(
                success=False,
                output="",
                error=str(exc),
                execution_time=elapsed,
                provider_used=self.name,
            )
