import json
import re
from typing import Any, Callable, Mapping

from nexus.core.prompt_budget import apply_prompt_budget


def run_analysis_with_provider(
    *,
    tool: Any,
    providers_map: Mapping[Any, Callable[..., dict[str, Any]]],
    text: str,
    task: str,
    kwargs: dict[str, Any],
    tool_unavailable_error: type[Exception],
) -> dict[str, Any]:
    """Dispatch an analysis task to the provider-specific implementation."""
    handler = providers_map.get(tool)
    if handler and callable(handler):
        return handler(text, task, **kwargs)
    raise tool_unavailable_error(f"{getattr(tool, 'value', tool)} does not support analysis tasks")


def run_analysis_attempts(
    *,
    tool_order: list[Any],
    text: str,
    task: str,
    kwargs: dict[str, Any],
    invoke_provider: Callable[[Any, str, str, dict[str, Any]], dict[str, Any]],
    rate_limited_error_type: type[Exception],
    record_rate_limit_with_context: Callable[[Any, Exception, int, str], None],
    get_default_analysis_result: Callable[..., dict[str, Any]],
    logger: Any,
) -> dict[str, Any]:
    """Execute provider attempts for an analysis task with fallback and defaults."""
    last_error: Exception | None = None
    for index, tool in enumerate(tool_order):
        try:
            result = invoke_provider(tool, text, task, kwargs)
            if result:
                logger.info(
                    "🧠 Analysis reply provider: task=%s provider=%s",
                    task,
                    getattr(tool, "value", tool),
                )
                if index > 0:
                    logger.info("✅ Fallback analysis succeeded with %s", tool.value)
                return result
        except rate_limited_error_type as exc:
            last_error = exc
            record_rate_limit_with_context(tool, exc, 1, f"analysis:{task}")
        except Exception as exc:
            last_error = exc
            logger.warning("⚠️  %s analysis failed: %s", tool.value, exc)

    if last_error:
        logger.error("❌ All analysis providers failed for %s: %s", task, last_error)

    logger.warning("⚠️  All tools failed for %s, returning default", task)
    return get_default_analysis_result(task, text=text, original_text=text, **kwargs)


def strip_cli_tool_output(text: str) -> str:
    """Remove Copilot/Gemini CLI tool-use artifacts from analysis output."""
    lines = text.splitlines()
    cleaned: list[str] = []
    skip_until_blank = False
    for line in lines:
        stripped = line.lstrip()
        # Legacy tool transcript blocks (Copilot/Gemini) and newer Codex transcript
        # entries share the same "header + indented detail lines + blank separator"
        # shape, so strip them uniformly.
        if stripped.startswith("●") or stripped.startswith("✗ ") or stripped.startswith("✓ "):
            skip_until_blank = True
            continue
        if skip_until_blank and stripped.startswith("$"):
            continue
        if skip_until_blank and stripped.startswith("└"):
            continue
        if skip_until_blank and (line.startswith("  ") or line.startswith("\t")):
            continue
        if not stripped:
            skip_until_blank = False
            if cleaned:
                cleaned.append(line)
            continue
        skip_until_blank = False
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def parse_analysis_result(output: str, task: str, *, logger: Any) -> dict[str, Any]:
    """Parse analysis result, preferring JSON and falling back to raw text."""
    cleaned_output = strip_cli_tool_output(output)

    candidates: list[str] = [cleaned_output.strip()]
    fenced_blocks = re.findall(
        r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned_output, flags=re.IGNORECASE
    )
    candidates.extend(block.strip() for block in fenced_blocks if block.strip())

    first_brace = cleaned_output.find("{")
    last_brace = cleaned_output.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidates.append(cleaned_output[first_brace : last_brace + 1].strip())

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    looks_like_json = "{" in cleaned_output and "}" in cleaned_output
    if looks_like_json or "```" in cleaned_output:
        logger.warning("Failed to parse %s result as JSON: %s", task, cleaned_output[:100])
        return {"text": cleaned_output, "parse_error": True}

    return {"text": cleaned_output}


def build_analysis_prompt(text: str, task: str, **kwargs) -> str:
    """Build provider-agnostic analysis prompts for supported analysis tasks."""
    prompt_max_chars = int(kwargs.get("prompt_max_chars", 16000) or 16000)
    summary_max_chars = int(kwargs.get("summary_max_chars", 1200) or 1200)
    text_budget = apply_prompt_budget(
        text,
        max_chars=min(prompt_max_chars, 6000),
        summary_max_chars=summary_max_chars,
    )
    text_payload = text_budget["text"]

    if task == "classify":
        projects = kwargs.get("projects", [])
        types = kwargs.get("types", [])
        return f"""Classify this task.
1. Map to project (one of: {", ".join(projects)}). Use key format.
2. Classify type (one of: {", ".join(types)}).
3. Generate concise task name (3-6 words, kebab-case).
4. Return JSON: {{"project": "key", "type": "type_key", "task_name": "name"}}

Return ONLY valid JSON.

Input:
{text_payload}"""

    if task == "route":
        return f"""Route this task to the best agent.
1. Identify primary work type (coding, design, testing, ops, content).
2. Suggest best agent.
3. Rate confidence 0-100.
4. Return JSON: {{"agent": "name", "type": "work_type", "confidence": 85}}

Return ONLY valid JSON.

Input:
{text_payload}"""

    if task == "generate_name":
        project = kwargs.get("project_name", "")
        return f"""Generate a concise task name (3-6 words, kebab-case).
Project: {project}

Return ONLY the name, no quotes.

Input:
{text_payload}"""

    if task == "refine_description":
        return f"""Rewrite this task description to be clear, concise, and structured.
Preserve all concrete requirements, constraints, and details. Do not invent facts.

Return in plain text (no Markdown headers), using short paragraphs and bullet points if helpful.

Input:
{text_payload.strip()}
"""

    if task == "detect_intent":
        return f"""Classify this user input for routing.
Return ONLY valid JSON with this shape:
{{
  "intent": "task" | "conversation",
  "intent_confidence": 0.0-1.0,
  "feature_ideation": true | false,
  "feature_ideation_confidence": 0.0-1.0,
  "feature_ideation_reason": "short reason"
}}

Rules:
- Set "intent" to "task" for concrete implementation requests (feature, bug, chore) that should go to developer inbox.
- Set "intent" to "conversation" for advisory questions, brainstorming, analysis, or discussion.
- Set "feature_ideation" to true only when the user explicitly asks to generate multiple new feature ideas/proposals/roadmap suggestions.
- Set "feature_ideation" to false for single concrete change/integration feasibility questions (e.g., "can we integrate X?", "should we add Y?").
- Set "feature_ideation" to false for implementation/status/history questions (including "what was already implemented").
- If uncertain between ideation and a concrete single-change request, prefer "feature_ideation": false.
- Support multilingual input; do not assume English-only phrasing.

Return ONLY valid JSON.

Input:
{text_payload}"""

    if task == "detect_feature_ideation":
        return f"""Decide whether the user is asking for feature ideation/brainstorming.

Return ONLY valid JSON:
{{"feature_ideation": true|false, "confidence": 0.0-1.0, "reason": "short reason"}}

Set feature_ideation=true only if the user is asking for new feature proposals/ideas/roadmap items.
Set feature_ideation=false for status/history/reporting questions (e.g., asking what was already implemented).
Support multilingual user text; do not assume English-only phrasing.

Input:
{text_payload}"""

    if task == "chat":
        history = kwargs.get("history", "")
        history_budget = apply_prompt_budget(
            str(history or ""),
            max_chars=3000,
            summary_max_chars=min(summary_max_chars, 1000),
        )
        persona = kwargs.get("persona", "You are a helpful AI assistant.")
        return f"""{persona}

Recent Conversation History:
{history_budget["text"]}

User Input:
{text_payload.strip()}"""

    return text_payload
