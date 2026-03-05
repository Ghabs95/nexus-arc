from __future__ import annotations

import json
import os
from typing import Any, Callable

from nexus.core.prompt_budget import apply_prompt_budget, prompt_prefix_fingerprint
from nexus.core.utils.log_utils import log_feature_ideation_success, truncate_for_log

_PROMPT_MAX_CHARS = int(os.getenv("AI_PROMPT_MAX_CHARS", "16000"))
_CONTEXT_SUMMARY_MAX_CHARS = int(os.getenv("AI_CONTEXT_SUMMARY_MAX_CHARS", "1200"))


def _extract_items_from_result(
    result: dict[str, Any],
    *,
    feature_count: int,
    normalize_generated_features: Callable[[Any, int], list[dict[str, Any]]],
    extract_json_payload: Callable[[str], Any],
) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []

    if isinstance(result.get("title"), str) and isinstance(result.get("summary"), str):
        single = normalize_generated_features([result], feature_count)
        if single:
            return single

    if isinstance(result.get("items"), list):
        direct = normalize_generated_features(result.get("items"), feature_count)
        if direct:
            return direct

    for list_key in ("features", "suggestions", "proposals"):
        if isinstance(result.get(list_key), list):
            direct = normalize_generated_features(result.get(list_key), feature_count)
            if direct:
                return direct

    for wrapped_key in ("response", "content", "message"):
        wrapped_value = result.get(wrapped_key)
        if not isinstance(wrapped_value, str) or not wrapped_value.strip():
            continue
        payload = extract_json_payload(wrapped_value)
        if isinstance(payload, list):
            direct = normalize_generated_features(payload, feature_count)
            if direct:
                return direct
        if isinstance(payload, dict):
            direct = normalize_generated_features(payload.get("items"), feature_count)
            if direct:
                return direct

    payload = extract_json_payload(str(result.get("text", "")))
    if isinstance(payload, list):
        return normalize_generated_features(payload, feature_count)
    if isinstance(payload, dict):
        return normalize_generated_features(payload.get("items"), feature_count)
    return []


def build_feature_suggestions(
    *,
    project_key: str,
    feature_count: int,
    text: str,
    routed_agent_type: str,
    persona: str,
    orchestrator: Any,
    logger: Any | None,
    normalize_generated_features: Callable[[Any, int], list[dict[str, Any]]],
    extract_json_payload: Callable[[str], Any],
) -> list[dict[str, Any]]:
    try:
        persona_budget = apply_prompt_budget(
            persona,
            max_chars=_PROMPT_MAX_CHARS,
            summary_max_chars=_CONTEXT_SUMMARY_MAX_CHARS,
        )
        text_budget = apply_prompt_budget(
            text,
            max_chars=min(_PROMPT_MAX_CHARS, 2500),
            summary_max_chars=min(_CONTEXT_SUMMARY_MAX_CHARS, 900),
        )
        if logger:
            logger.info(
                "Feature ideation prompt budget: persona=%s->%s summarized=%s truncated=%s "
                "text=%s->%s summarized=%s truncated=%s fp=%s",
                persona_budget["original_chars"],
                persona_budget["final_chars"],
                persona_budget["summarized"],
                persona_budget["truncated"],
                text_budget["original_chars"],
                text_budget["final_chars"],
                text_budget["summarized"],
                text_budget["truncated"],
                prompt_prefix_fingerprint(str(persona_budget["text"])),
            )
        result = orchestrator.run_text_to_speech_analysis(
            text=str(text_budget["text"]),
            task="chat",
            persona=str(persona_budget["text"]),
            project_name=project_key,
        )
        raw_text = result.get("text", "") if isinstance(result, dict) else ""
        if logger:
            logger.info("AI returned feature ideation text (length %d)", len(raw_text))

        generated = _extract_items_from_result(
            result or {},
            feature_count=feature_count,
            normalize_generated_features=normalize_generated_features,
            extract_json_payload=extract_json_payload,
        )
        if generated:
            provider = "primary"
            if isinstance(result, dict):
                provider = str(result.get("provider") or result.get("model") or "primary")
            log_feature_ideation_success(
                logger,
                provider=provider,
                primary_success=True,
                fallback_used=False,
                item_count=len(generated),
                project_key=project_key,
                agent_type=routed_agent_type,
            )
            return generated

        if logger:
            raw_text = ""
            if isinstance(result, dict):
                raw_text = str(result.get("text", ""))
            if not raw_text and isinstance(result, dict):
                logger.warning(
                    "Primary feature ideation structured response keys: %s",
                    sorted(result.keys()),
                )
                logger.warning(
                    "Primary feature ideation structured response payload (truncated): %s",
                    truncate_for_log(json.dumps(result, ensure_ascii=False)),
                )
            logger.warning(
                "Primary feature ideation raw response (truncated): %s",
                truncate_for_log(raw_text),
            )
            logger.warning(
                "Dynamic feature ideation returned non-JSON/empty output (primary path), retrying with fallback AI agent"
            )
    except Exception as exc:
        if logger:
            logger.warning("Dynamic feature ideation failed on primary path: %s", exc)

    try:
        run_fallback = getattr(orchestrator, "_run_fallback_analysis", None) or getattr(orchestrator, "_run_copilot_analysis", None)
        if callable(run_fallback):
            fallback_result = run_fallback(text, task="chat", persona=persona)
            generated = _extract_items_from_result(
                fallback_result or {},
                feature_count=feature_count,
                normalize_generated_features=normalize_generated_features,
                extract_json_payload=extract_json_payload,
            )
            if generated:
                log_feature_ideation_success(
                    logger,
                    provider="fallback",
                    primary_success=False,
                    fallback_used=True,
                    item_count=len(generated),
                    project_key=project_key,
                    agent_type=routed_agent_type,
                )
                return generated
            if logger:
                logger.warning(
                    "Dynamic feature ideation fallback retry returned non-JSON/empty output"
                )
    except Exception as exc:
        if logger:
            logger.warning("Dynamic feature ideation failed on fallback retry: %s", exc)

    return []
