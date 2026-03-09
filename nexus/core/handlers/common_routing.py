"""Shared message routing helpers used across bot frontends."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


def extract_json_dict(text: str) -> dict[str, Any]:
    """Extract a JSON object from raw or markdown-wrapped text."""
    try:
        direct = json.loads(text)
        if isinstance(direct, dict):
            return direct
    except Exception:
        pass

    if "{" in text and "}" in text:
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            candidate = json.loads(text[start:end])
            if isinstance(candidate, dict):
                return candidate
        except Exception:
            pass

    cleaned = text.replace("```json", "").replace("```", "").strip()
    try:
        candidate = json.loads(cleaned)
        if isinstance(candidate, dict):
            return candidate
    except Exception:
        pass
    return {}


def parse_intent_result(
    orchestrator: Any,
    text: str,
    extract_json_dict: Callable[[str], dict[str, Any]],
    requester_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run intent detection and normalize JSON-wrapped outputs."""
    analysis_kwargs: dict[str, Any] = {"text": text, "task": "detect_intent"}
    if requester_context is not None:
        analysis_kwargs["requester_context"] = requester_context
    intent_result = orchestrator.run_text_to_speech_analysis(**analysis_kwargs)
    if not isinstance(intent_result, dict):
        return {"intent": "task"}

    raw_text = intent_result.get("text")
    needs_reparse = intent_result.get("parse_error") or "intent" not in intent_result
    if isinstance(raw_text, str) and raw_text.strip() and needs_reparse:
        reparsed = extract_json_dict(raw_text)
        if reparsed:
            merged = dict(intent_result)
            merged.update(reparsed)
            intent_result = merged

    if "intent" not in intent_result:
        intent_result["intent"] = "task"
    if "feature_ideation" not in intent_result:
        intent_result["feature_ideation"] = False
    if "feature_ideation_confidence" not in intent_result:
        intent_result["feature_ideation_confidence"] = 0.0
    if "feature_ideation_reason" not in intent_result:
        intent_result["feature_ideation_reason"] = "not_provided"
    return intent_result


def run_conversation_turn(
    user_id: int,
    text: str,
    orchestrator: Any,
    get_chat_history: Callable[[int], str],
    append_message: Callable[[int, str, str], None],
    persona: str,
    project_name: str | None = None,
    requester_context: dict[str, Any] | None = None,
) -> str:
    """Execute one shared conversation turn and persist memory."""
    history = get_chat_history(user_id)
    append_message(user_id, "user", text)

    chat_result = orchestrator.run_text_to_speech_analysis(
        text=text,
        task="chat",
        history=history,
        persona=persona,
        project_name=project_name,
        requester_context=requester_context,
    )

    reply_text = "I'm offline right now, how can I help later?"
    if isinstance(chat_result, dict):
        candidate = chat_result.get("text")
        if isinstance(candidate, str) and candidate.strip():
            reply_text = candidate
        else:
            for key in ("response", "output", "content", "message"):
                alt = chat_result.get(key)
                if isinstance(alt, str) and alt.strip():
                    reply_text = alt
                    break
            else:
                # Last-resort extraction: first non-empty string field.
                for value in chat_result.values():
                    if isinstance(value, str) and value.strip():
                        reply_text = value
                        break

    append_message(user_id, "assistant", reply_text)
    return reply_text


async def route_task_with_context(
    user_id: int,
    text: str,
    orchestrator: Any,
    message_id: str,
    get_chat: Callable[[int], dict[str, Any]],
    process_inbox_task: Any,
    requester_context: dict[str, Any] | None = None,
    authorize_project=None,
    attachments: list[Any] | None = None,
) -> dict[str, Any]:
    """Route task through shared inbox logic using active chat project context."""
    active_chat = get_chat(user_id)
    metadata = active_chat.get("metadata") if isinstance(active_chat, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    project_hint = metadata.get("project_key")

    kwargs = {"project_hint": project_hint}
    if requester_context is not None:
        kwargs["requester_context"] = requester_context
    if authorize_project is not None:
        kwargs["authorize_project"] = authorize_project
    if attachments:
        kwargs["attachments"] = attachments

    try:
        return await process_inbox_task(
            text,
            orchestrator,
            message_id,
            **kwargs,
        )
    except TypeError:
        # Backward compatibility for older task processors.
        return await process_inbox_task(
            text,
            orchestrator,
            message_id,
            project_hint=project_hint,
        )
