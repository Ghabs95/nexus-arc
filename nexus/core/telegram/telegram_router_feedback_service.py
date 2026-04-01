from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

from nexus.core.config import NEXUS_STATE_DIR, TELEGRAM_TOKEN, ensure_state_dir
from nexus.core.interactive.context import Button

LOGGER = logging.getLogger(__name__)
TASK_LABELS = [
    "coding",
    "code_review",
    "reasoning",
    "summarization",
    "fast_utility",
    "long_context",
    "vision",
    "general_chat",
]
PENDING_KEY = "router_feedback_pending"
SUBMITTED_KEY = "router_feedback_submitted"
CALLBACK_PREFIX = "routefb:"
FALLBACK_STORE_PATH = os.path.join(NEXUS_STATE_DIR, "router_feedback_fallback.jsonl")
PENDING_STORE_PATH = os.path.join(NEXUS_STATE_DIR, "router_feedback_pending.json")

# Model-verdict options surfaced in step 2 of the "Wrong" flow
MODEL_VERDICT_LABELS: dict[str, str] = {
    "too_cheap": "🔼 Too cheap/fast",
    "ok": "✅ Model OK",
    "too_powerful": "🔽 Too powerful/slow",
}
MODEL_VERDICTS = list(MODEL_VERDICT_LABELS.keys())


def decision_token(decision_id: str | None) -> str:
    """Compact decision reference safe for Telegram callback_data (<=64 bytes)."""
    raw = str(decision_id or "").strip()
    if not raw:
        return ""
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def feedback_enabled(config: dict[str, Any] | None, *, surface: str) -> bool:
    cfg = config or {}
    if not cfg.get("enabled"):
        return False
    if surface == "telegram":
        return bool(cfg.get("telegram_enabled", True))
    if surface == "discord":
        return bool(cfg.get("discord_enabled", False))
    return False


def _synthetic_decision_id(
    *,
    result: dict[str, Any],
    source_message_id: str | None,
    source_user_id: str | None,
    source_chat_id: str | None,
) -> str:
    seed = {
        "project": result.get("project"),
        "content": result.get("content"),
        "message_id": str(source_message_id or ""),
        "user_id": str(source_user_id or ""),
        "chat_id": str(source_chat_id or ""),
    }
    digest = hashlib.sha1(json.dumps(seed, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"fallback-{digest}"


def _is_handled_task_result(result: dict[str, Any]) -> bool:
    return bool(result.get("success")) and bool(result.get("project")) and bool(result.get("content"))


def extract_feedback_meta(
    result: dict[str, Any] | None,
    *,
    source_message_id: str | None = None,
    source_user_id: str | None = None,
    source_chat_id: str | None = None,
    source_channel: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None

    feedback_meta = result.get("routing_feedback")
    if isinstance(feedback_meta, dict):
        decision_id = str(feedback_meta.get("decision_id") or "").strip()
        if not decision_id:
            return None
        return {
            "decision_id": decision_id,
            "feedback_mode": "router",
            "task_type": str(feedback_meta.get("task_type") or feedback_meta.get("predicted_task") or "").strip(),
            "selected_model": str(feedback_meta.get("selected_model") or feedback_meta.get("model") or "").strip(),
            "confidence": feedback_meta.get("confidence"),
            "source_channel": str(feedback_meta.get("source_channel") or source_channel or "telegram").strip() or "telegram",
            "metadata": feedback_meta.get("metadata") if isinstance(feedback_meta.get("metadata"), dict) else {},
        }

    if not _is_handled_task_result(result):
        return None

    return {
        "decision_id": _synthetic_decision_id(
            result=result,
            source_message_id=source_message_id,
            source_user_id=source_user_id,
            source_chat_id=source_chat_id,
        ),
        "feedback_mode": "fallback",
        "task_type": str(result.get("task_type") or result.get("type") or "inbox_classification").strip() or "inbox_classification",
        "selected_model": str(result.get("selected_model") or result.get("model") or "inbox_route").strip() or "inbox_route",
        "confidence": result.get("confidence"),
        "source_channel": str(source_channel or "telegram").strip() or "telegram",
        "metadata": {
            "project": result.get("project"),
            "content": result.get("content"),
            "success": result.get("success"),
        },
    }


def build_feedback_prompt(meta: dict[str, Any]) -> tuple[str, list[list[Button]]]:
    confidence = meta.get("confidence")
    confidence_text = "?"
    try:
        if confidence is not None:
            confidence_text = f"{float(confidence):.2f}"
    except (TypeError, ValueError):
        pass
    task = str(meta.get("task_type") or "unknown")
    model = str(meta.get("selected_model") or "unknown")
    text = f"🧭 {task} · {model} · {confidence_text}\nFeedback?"
    token = decision_token(str(meta.get("decision_id") or ""))
    buttons = [
        [
            Button("✅ Correct", callback_data=f"{CALLBACK_PREFIX}ok:{token}"),
            Button("❌ Wrong", callback_data=f"{CALLBACK_PREFIX}wrong:{token}"),
        ],
    ]
    return text, buttons


def build_wrong_task_prompt(meta: dict[str, Any]) -> tuple[str, list[list[Button]]]:
    """Step 1 of 'Wrong' flow: ask which task was correct."""
    token = decision_token(str(meta.get("decision_id") or ""))
    text = "❌ Step 1/2 — Which task was it?"
    buttons: list[list[Button]] = []
    row: list[Button] = []
    for label in TASK_LABELS:
        row.append(Button(label, callback_data=f"{CALLBACK_PREFIX}wrong_task:{token}:{label}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([Button("⏭ Skip", callback_data=f"{CALLBACK_PREFIX}wrong_task:{token}:skip")])
    return text, buttons


def build_wrong_model_prompt(meta: dict[str, Any], corrected_task: str | None) -> tuple[str, list[list[Button]]]:
    """Step 2 of 'Wrong' flow: ask about model quality (too cheap/ok/too powerful)."""
    token = decision_token(str(meta.get("decision_id") or ""))
    task_slot = corrected_task or "skip"
    text = "❌ Step 2/2 — Was the model right?"
    buttons: list[list[Button]] = [[
        Button(label_text, callback_data=f"{CALLBACK_PREFIX}wrong_model:{token}:{task_slot}:{verdict_key}")
        for verdict_key, label_text in MODEL_VERDICT_LABELS.items()
    ]]
    buttons.append([Button("⏭ Skip", callback_data=f"{CALLBACK_PREFIX}wrong_model:{token}:{task_slot}:skip")])
    return text, buttons


async def maybe_send_feedback_prompt(*, ctx: Any, user_state: dict[str, Any], feedback_config: dict[str, Any] | None, result: dict[str, Any] | None, source_message_id: str | None = None) -> None:
    if not feedback_enabled(feedback_config, surface="telegram"):
        return
    source_user_id = str(getattr(ctx, "user_id", "") or "") or None
    raw_event = getattr(ctx, "raw_event", None)
    source_chat_id = str(getattr(getattr(raw_event, "chat", None), "id", "") or "") or None
    source_channel = str(getattr(getattr(ctx, "client", None), "name", "telegram") or "telegram")
    meta = extract_feedback_meta(
        result,
        source_message_id=source_message_id,
        source_user_id=source_user_id,
        source_chat_id=source_chat_id,
        source_channel=source_channel,
    )
    if not meta:
        return
    meta["source_message_id"] = str(source_message_id or "") or None
    meta["source_user_id"] = source_user_id
    meta["source_chat_id"] = source_chat_id
    meta["source_channel"] = source_channel
    user_state[PENDING_KEY] = meta
    text, buttons = build_feedback_prompt(meta)
    try:
        sent_id = await ctx.reply_text(text, buttons=buttons, parse_mode=None)
        meta["feedback_message_id"] = str(sent_id or "") or None
        user_state[PENDING_KEY] = meta
    except Exception:
        LOGGER.debug("Router feedback card send failed", exc_info=True)


def parse_feedback_text(text: str) -> tuple[str, str | None] | None:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"correct", "✅ correct", "right"}:
        return ("correct", None)
    if normalized in {"wrong", "❌ wrong"}:
        return ("wrong", None)
    for sep in ("->", "=>"):
        if sep in normalized:
            left, right = [part.strip() for part in normalized.split(sep, 1)]
            if left == "wrong" and right in TASK_LABELS:
                return ("wrong", right)
    if normalized.startswith("wrong "):
        candidate = normalized.split(None, 1)[1].strip()
        if candidate in TASK_LABELS:
            return ("wrong", candidate)
    return None


def build_feedback_payload(*, meta: dict[str, Any], verdict: str, corrected_task: str | None, source_message_id: str | None, source_user_id: str | None, model_verdict: str | None = None) -> dict[str, Any]:
    metadata = dict(meta.get("metadata") or {}) if isinstance(meta.get("metadata"), dict) else {}
    metadata.update(
        {
            "feedback_mode": meta.get("feedback_mode") or "router",
            "source_chat_id": str(meta.get("source_chat_id") or "") or None,
            "feedback_message_id": str(meta.get("feedback_message_id") or "") or None,
            "task_type": str(meta.get("task_type") or "") or None,
            "selected_model": str(meta.get("selected_model") or "") or None,
            "confidence": meta.get("confidence"),
            "submitted_at": int(time.time()),
        }
    )
    return {
        "decision_id": meta.get("decision_id"),
        "verdict": verdict,
        "corrected_task": corrected_task,
        "model_verdict": model_verdict or None,
        "source_surface": "telegram",
        "source_channel": meta.get("source_channel") or "telegram",
        "source_message_id": str(source_message_id or meta.get("source_message_id") or "") or None,
        "source_user_id": str(source_user_id or meta.get("source_user_id") or "") or None,
        "metadata": metadata,
    }


def _append_fallback_feedback(payload: dict[str, Any], *, store_path: str = FALLBACK_STORE_PATH) -> tuple[bool, str]:
    try:
        ensure_state_dir()
        os.makedirs(os.path.dirname(store_path), exist_ok=True)
        with open(store_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
        return (True, store_path)
    except Exception as exc:
        return (False, str(exc))


def submit_feedback(*, router_url: str, payload: dict[str, Any], timeout_seconds: float = 3.0, fallback_store_path: str = FALLBACK_STORE_PATH) -> tuple[bool, str]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    feedback_mode = str(metadata.get("feedback_mode") or "router").strip().lower()
    if feedback_mode == "fallback":
        return _append_fallback_feedback(payload, store_path=fallback_store_path)
    if not router_url:
        return (False, "router feedback disabled")
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{router_url.rstrip('/')}/feedback",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
        return (True, raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return (False, detail or str(exc))
    except Exception as exc:
        return (False, str(exc))


def _load_pending_store(*, store_path: str = PENDING_STORE_PATH) -> dict[str, Any]:
    try:
        with open(store_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        LOGGER.debug("Failed to load router feedback pending store", exc_info=True)
        return {}


def _save_pending_store(payload: dict[str, Any], *, store_path: str = PENDING_STORE_PATH) -> None:
    ensure_state_dir()
    os.makedirs(os.path.dirname(store_path), exist_ok=True)
    tmp_path = f"{store_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
    os.replace(tmp_path, store_path)


def store_external_pending_feedback(*, user_id: str, meta: dict[str, Any], store_path: str = PENDING_STORE_PATH) -> None:
    key = str(user_id or "").strip()
    if not key:
        return
    payload = _load_pending_store(store_path=store_path)
    payload[key] = meta
    try:
        _save_pending_store(payload, store_path=store_path)
    except Exception:
        LOGGER.warning("Failed to persist external router feedback pending store at %s", store_path, exc_info=True)


def load_external_pending_feedback(*, user_id: str, store_path: str = PENDING_STORE_PATH) -> dict[str, Any] | None:
    key = str(user_id or "").strip()
    if not key:
        return None
    payload = _load_pending_store(store_path=store_path)
    data = payload.get(key)
    return data if isinstance(data, dict) else None


def clear_external_pending_feedback(*, user_id: str, decision_id: str | None = None, store_path: str = PENDING_STORE_PATH) -> None:
    key = str(user_id or "").strip()
    if not key:
        return
    payload = _load_pending_store(store_path=store_path)
    current = payload.get(key)
    if not isinstance(current, dict):
        return
    if decision_id and str(current.get("decision_id") or "") != str(decision_id):
        return
    payload.pop(key, None)
    try:
        _save_pending_store(payload, store_path=store_path)
    except Exception:
        LOGGER.warning(
            "Failed to persist external router feedback pending store at %s",
            store_path,
            exc_info=True,
        )


def send_feedback_prompt_to_telegram_user(*, chat_id: str, text: str, buttons: list[list[Button]], token: str | None = None, timeout_seconds: float = 4.0) -> tuple[bool, str]:
    api_token = str(token or TELEGRAM_TOKEN or "").strip()
    if not api_token:
        return (False, "missing_telegram_token")
    inline_keyboard = [
        [
            {"text": str(button.label), "callback_data": str(button.callback_data)}
            for button in row
            if getattr(button, "label", None) and getattr(button, "callback_data", None)
        ]
        for row in buttons
    ]
    inline_keyboard = [row for row in inline_keyboard if row]
    payload = {
        "chat_id": str(chat_id),
        "text": str(text or ""),
        "disable_web_page_preview": True,
        "reply_markup": {"inline_keyboard": inline_keyboard},
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{api_token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
        data = json.loads(raw)
        result = data.get("result") if isinstance(data, dict) else None
        message_id = ""
        if isinstance(result, dict):
            message_id = str(result.get("message_id") or "")
        return (True, message_id)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        return (False, detail or str(exc))
    except Exception as exc:
        return (False, str(exc))


async def maybe_send_feedback_prompt_external(
    *,
    telegram_user_id: str,
    feedback_config: dict[str, Any] | None,
    result: dict[str, Any] | None,
    source_message_id: str | None = None,
    source_channel: str = "openclaw",
) -> bool:
    if not feedback_enabled(feedback_config, surface="telegram"):
        return False
    meta = extract_feedback_meta(
        result,
        source_message_id=source_message_id,
        source_user_id=telegram_user_id,
        source_chat_id=telegram_user_id,
        source_channel=source_channel,
    )
    if not meta:
        return False

    meta["source_message_id"] = str(source_message_id or "") or None
    meta["source_user_id"] = str(telegram_user_id or "") or None
    meta["source_chat_id"] = str(telegram_user_id or "") or None
    meta["source_channel"] = source_channel

    text, buttons = build_feedback_prompt(meta)
    ok, detail = send_feedback_prompt_to_telegram_user(chat_id=telegram_user_id, text=text, buttons=buttons)
    if not ok:
        LOGGER.warning("Router feedback card dispatch failed for telegram_user=%s detail=%s", telegram_user_id, detail)
        return False

    meta["feedback_message_id"] = str(detail or "") or None
    store_external_pending_feedback(user_id=telegram_user_id, meta=meta)
    return True


def remember_feedback_submission(user_state: dict[str, Any], *, decision_id: str, user_id: str | None) -> None:
    submitted = user_state.setdefault(SUBMITTED_KEY, {})
    key = f"{decision_id}:{user_id or ''}"
    submitted[key] = True


def has_feedback_submission(user_state: dict[str, Any], *, decision_id: str, user_id: str | None) -> bool:
    submitted = user_state.get(SUBMITTED_KEY)
    if not isinstance(submitted, dict):
        return False
    return bool(submitted.get(f"{decision_id}:{user_id or ''}"))
