"""OpenAI AI provider implementation.

Requires the ``openai`` optional extra::

    pip install nexus-arc[openai]

Uses the ``openai`` Python SDK (v1+) with async support.
"""

import logging
import time

from nexus.adapters.ai.base import AIProvider, ExecutionContext
from nexus.core.models import AgentResult, RateLimitStatus

try:
    import openai as _openai_module

    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False

logger = logging.getLogger(__name__)

# Task types where GPT-class models excel
_OPENAI_PREFERRED_TASKS = {"reasoning", "analysis", "content_creation", "summarization"}

# Default model; can be overridden at construction time
_DEFAULT_MODEL = "gpt-4o"


def _require_openai() -> None:
    if not _OPENAI_AVAILABLE:
        raise ImportError(
            "openai package is required for OpenAIProvider. "
            "Install it with: pip install nexus-arc[openai]"
        )


class OpenAIProvider(AIProvider):
    """AI provider that uses the OpenAI Chat Completions API.

    Args:
        api_key: OpenAI API key.  Defaults to ``OPENAI_API_KEY`` env var.
        model: Chat model to use (default ``gpt-4o``).
        system_prompt: Optional system prompt prepended to every request.
        timeout: Default request timeout in seconds.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        system_prompt: str = "You are a helpful AI assistant.",
        timeout: int = 300,
    ):
        _require_openai()
        self._model = model
        self._system_prompt = system_prompt
        self._timeout = timeout
        # AsyncOpenAI lazily picks up OPENAI_API_KEY from env when api_key is None
        self._client = _openai_module.AsyncOpenAI(api_key=api_key)
        self._availability_cache: dict = {}

    @property
    def name(self) -> str:
        return "openai"

    async def check_availability(self) -> bool:
        """Return True if the OpenAI API is reachable and the key is valid."""
        import time as _time

        now = _time.time()
        cached = self._availability_cache.get("openai")
        if cached and now - cached["at"] < 300:
            return cached["available"]

        try:
            # Lightweight call: list up to 1 model
            await self._client.models.list()
            available = True
        except Exception as exc:
            logger.warning("OpenAI availability check failed: %s", exc)
            available = False

        self._availability_cache["openai"] = {"available": available, "at": now}
        return available

    async def get_rate_limit_status(self) -> RateLimitStatus:
        """Return current rate-limit information.

        The openai SDK does not expose headers directly; we return a best-effort
        result and log when errors surface rate-limiting.
        """
        return RateLimitStatus(
            provider=self.name,
            is_limited=False,  # updated to True on RateLimitError in execute_agent
        )

    def get_preference_score(self, task_type: str) -> float:
        """Return 0.9 for analysis/reasoning tasks, 0.7 otherwise."""
        return 0.9 if task_type in _OPENAI_PREFERRED_TASKS else 0.7

    async def execute_agent(self, context: ExecutionContext) -> AgentResult:
        """Send the prompt to the Chat Completions API and return the reply."""
        start = time.time()
        model = context.model_override or self._model
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": context.prompt},
        ]
        # Attach issue URL as additional context when provided
        if context.issue_url:
            messages[1]["content"] = f"{context.prompt}\n\nIssue: {context.issue_url}"

        try:
            create_kwargs: dict = {
                "model": model,
                "messages": messages,
                "timeout": context.timeout or self._timeout,
            }
            if context.max_tokens is not None:
                create_kwargs["max_tokens"] = context.max_tokens
            response = await self._client.chat.completions.create(**create_kwargs)
            elapsed = time.time() - start
            output = response.choices[0].message.content or ""
            return AgentResult(
                success=True,
                output=output,
                execution_time=elapsed,
                provider_used=self.name,
                metadata={
                    "model": response.model,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens,
                    },
                    "finish_reason": response.choices[0].finish_reason,
                },
            )

        except _openai_module.RateLimitError as exc:
            elapsed = time.time() - start
            logger.warning("OpenAI rate-limit hit: %s", exc)
            return AgentResult(
                success=False,
                output="",
                error=f"Rate limit: {exc}",
                execution_time=elapsed,
                provider_used=self.name,
            )

        except _openai_module.APITimeoutError as exc:
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
            logger.error("OpenAI execute_agent failed: %s", exc)
            return AgentResult(
                success=False,
                output="",
                error=str(exc),
                execution_time=elapsed,
                provider_used=self.name,
            )
