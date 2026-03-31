import json

from nexus.core.telegram.telegram_router_feedback_service import (
    CALLBACK_PREFIX,
    build_feedback_prompt,
    build_feedback_payload,
    extract_feedback_meta,
    parse_feedback_text,
    submit_feedback,
)


def test_extract_feedback_meta_reads_result_payload():
    meta = extract_feedback_meta(
        {
            "routing_feedback": {
                "decision_id": "dec-1",
                "task_type": "reasoning",
                "selected_model": "gpt-5",
                "confidence": 0.91,
            }
        }
    )
    assert meta == {
        "decision_id": "dec-1",
        "feedback_mode": "router",
        "task_type": "reasoning",
        "selected_model": "gpt-5",
        "confidence": 0.91,
        "source_channel": "telegram",
        "metadata": {},
    }


def test_extract_feedback_meta_builds_fallback_for_handled_inbox_route():
    meta = extract_feedback_meta(
        {"success": True, "project": "nexus", "content": "Nexus: ship it"},
        source_message_id="123",
        source_user_id="42",
        source_chat_id="99",
    )
    assert meta is not None
    assert meta["feedback_mode"] == "fallback"
    assert meta["decision_id"].startswith("fallback-")
    assert meta["metadata"]["project"] == "nexus"


def test_build_feedback_prompt_contains_buttons():
    text, buttons = build_feedback_prompt(
        {"decision_id": "dec-1", "task_type": "coding", "selected_model": "gpt-5", "confidence": 0.82}
    )
    assert "coding" in text
    assert buttons[0][0].callback_data == f"{CALLBACK_PREFIX}ok:dec-1"
    assert buttons[0][1].callback_data == f"{CALLBACK_PREFIX}wrong:dec-1"


def test_submit_feedback_persists_fallback_record(tmp_path):
    payload = build_feedback_payload(
        meta={
            "decision_id": "fallback-1",
            "feedback_mode": "fallback",
            "source_channel": "telegram",
            "metadata": {"project": "nexus", "content": "Nexus: ship it"},
        },
        verdict="wrong",
        corrected_task="reasoning",
        source_message_id="123",
        source_user_id="42",
    )
    ok, detail = submit_feedback(
        router_url="http://router",
        payload=payload,
        fallback_store_path=str(tmp_path / "feedback.jsonl"),
    )
    assert ok is True
    stored = (tmp_path / "feedback.jsonl").read_text(encoding="utf-8").strip()
    record = json.loads(stored)
    assert detail.endswith("feedback.jsonl")
    assert record["decision_id"] == "fallback-1"
    assert record["corrected_task"] == "reasoning"


def test_parse_feedback_text_supports_shortcuts():
    assert parse_feedback_text("correct") == ("correct", None)
    assert parse_feedback_text("wrong -> reasoning") == ("wrong", "reasoning")
    assert parse_feedback_text("wrong coding") == ("wrong", "coding")
    assert parse_feedback_text("hello") is None
