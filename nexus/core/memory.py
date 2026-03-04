import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any

import redis

from nexus.core.prompt_budget import apply_prompt_budget

logger = logging.getLogger(__name__)

_CHAT_ENTRY_MAX_CHARS = int(os.getenv("AI_CHAT_ENTRY_MAX_CHARS", "800"))
_CHAT_HISTORY_MAX_CHARS = int(os.getenv("AI_CHAT_HISTORY_MAX_CHARS", "4000"))
_CONTEXT_SUMMARY_MAX_CHARS = int(os.getenv("AI_CONTEXT_SUMMARY_MAX_CHARS", "1200"))


def _redis_url() -> str:
    from config import REDIS_URL

    return str(REDIS_URL)


def _get_chat_agent_types(project_key: str) -> list[str]:
    from config import get_chat_agent_types

    return list(get_chat_agent_types(project_key) or [])


def _get_workflow_profile(project_key: str) -> str:
    from config import get_workflow_profile

    return str(get_workflow_profile(project_key) or "")


def _resolve_project_agent_types(project_key: str | None) -> list[str]:
    try:
        configured_types = _get_chat_agent_types(project_key or "nexus")
    except Exception:
        configured_types = []
    if not isinstance(configured_types, list):
        return []
    return [
        str(agent_type).strip().lower()
        for agent_type in configured_types
        if str(agent_type).strip()
    ]


def _resolve_primary_agent_type(project_key: str | None, allowed_agent_types: list[str]) -> str:
    candidates = [
        agent for agent in allowed_agent_types if isinstance(agent, str) and agent.strip()
    ]
    if not candidates:
        candidates = _resolve_project_agent_types(project_key)

    if not candidates:
        return "triage"
    return candidates[0]


def _resolve_workflow_profile(project_key: str | None) -> str:
    try:
        value = _get_workflow_profile(project_key or "nexus")
        normalized = str(value).strip()
        if normalized:
            return normalized
    except Exception:
        pass
    return "ghabs_org_workflow"


def _default_chat_metadata(project_key: str | None = None) -> dict[str, Any]:
    return {
        "project_key": project_key,
        "chat_mode": "strategy",
        "primary_agent_type": "triage",
        "allowed_agent_types": [],
        "workflow_profile": _resolve_workflow_profile(project_key),
        "delegation_enabled": True,
    }


def _normalize_chat_data(chat_data: dict[str, Any]) -> dict[str, Any]:
    merged = dict(chat_data or {})
    metadata = merged.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    inferred_project_key = metadata.get("project_key") if isinstance(metadata, dict) else None
    if isinstance(inferred_project_key, str):
        inferred_project_key = inferred_project_key.strip().lower() or None
    else:
        inferred_project_key = None

    defaults = _default_chat_metadata(inferred_project_key)
    normalized_metadata = {**defaults, **metadata}

    project_key = normalized_metadata.get("project_key")
    if isinstance(project_key, str):
        project_key = project_key.strip().lower() or None
    else:
        project_key = None
    normalized_metadata["project_key"] = project_key

    allowed_agent_types = normalized_metadata.get("allowed_agent_types")
    if not isinstance(allowed_agent_types, list):
        allowed_agent_types = []

    cleaned_allowed = [
        str(item).strip().lower()
        for item in allowed_agent_types
        if isinstance(item, str) and str(item).strip()
    ]
    if not cleaned_allowed:
        cleaned_allowed = _resolve_project_agent_types(project_key)
    normalized_metadata["allowed_agent_types"] = cleaned_allowed

    primary_agent_type = str(normalized_metadata.get("primary_agent_type") or "").strip().lower()
    if not primary_agent_type or (cleaned_allowed and primary_agent_type not in cleaned_allowed):
        primary_agent_type = _resolve_primary_agent_type(project_key, cleaned_allowed)
    normalized_metadata["primary_agent_type"] = primary_agent_type

    current_profile = str(normalized_metadata.get("workflow_profile") or "").strip()
    expected_profile = _resolve_workflow_profile(project_key)
    if not current_profile or (
        current_profile in {"ghabs_org_workflow", "default_workflow"} and expected_profile
    ):
        normalized_metadata["workflow_profile"] = expected_profile

    merged["metadata"] = normalized_metadata
    return merged


# Singleton redis client
_redis_client = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.from_url(_redis_url(), decode_responses=True)
            _redis_client.ping()
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis at {_redis_url()}: {e}")
            raise
    return _redis_client


def create_chat(user_id: int, title: str = None, metadata: dict[str, Any] | None = None) -> str:
    """Creates a new chat and sets it as active."""
    r = get_redis()
    chat_id = uuid.uuid4().hex
    if not title:
        title = f"Chat {datetime.now().strftime('%Y-%m-%d %H:%M')}"

    chat_data = _normalize_chat_data(
        {
            "id": chat_id,
            "title": title,
            "created_at": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
    )

    r.hset(f"user_chats:{user_id}", chat_id, json.dumps(chat_data))
    set_active_chat(user_id, chat_id)
    return chat_id


def get_active_chat(user_id: int) -> str:
    """Gets the active chat_id for a user. Creates a default one if none exists."""
    r = get_redis()
    active_chat_id = r.get(f"active_chat:{user_id}")

    # Verify the chat still exists
    if active_chat_id and r.hexists(f"user_chats:{user_id}", active_chat_id):
        return active_chat_id

    # If no active chat, see if they have *any* chats
    chats = r.hgetall(f"user_chats:{user_id}")
    if chats:
        # Pick the first one (or newest)
        first_chat_id = list(chats.keys())[0]
        set_active_chat(user_id, first_chat_id)
        return first_chat_id

    # No chats exist at all, create a new one
    return create_chat(user_id, "Main Chat")


def set_active_chat(user_id: int, chat_id: str) -> bool:
    """Sets the active chat for a user. Returns True if successful."""
    r = get_redis()
    if r.hexists(f"user_chats:{user_id}", chat_id):
        r.set(f"active_chat:{user_id}", chat_id)
        return True
    return False


def list_chats(user_id: int) -> list:
    """Lists all chats for a user, sorted by newest first."""
    r = get_redis()
    chats_raw = r.hgetall(f"user_chats:{user_id}")
    chats = []
    for chat_str in chats_raw.values():
        try:
            chat_data = json.loads(chat_str)
            chats.append(_normalize_chat_data(chat_data))
        except json.JSONDecodeError:
            continue

    # Sort by created_at descending
    chats.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return chats


def get_chat(user_id: int, chat_id: str | None = None) -> dict[str, Any]:
    """Return a single chat payload (active chat by default)."""
    r = get_redis()
    resolved_chat_id = chat_id or get_active_chat(user_id)
    raw_chat = r.hget(f"user_chats:{user_id}", resolved_chat_id)
    if not raw_chat:
        return {}
    try:
        parsed = json.loads(raw_chat)
        return _normalize_chat_data(parsed)
    except json.JSONDecodeError:
        return {}


def update_chat_metadata(user_id: int, chat_id: str, updates: dict[str, Any]) -> bool:
    """Update metadata fields for a chat and persist the change."""
    if not chat_id or not isinstance(updates, dict):
        return False

    r = get_redis()
    raw_chat = r.hget(f"user_chats:{user_id}", chat_id)
    if not raw_chat:
        return False

    try:
        chat_data = _normalize_chat_data(json.loads(raw_chat))
    except json.JSONDecodeError:
        return False

    metadata = dict(chat_data.get("metadata") or {})

    next_project_key = updates.get("project_key")
    if isinstance(next_project_key, str) and next_project_key.strip():
        normalized_project_key = next_project_key.strip().lower()
        project_agent_types = _resolve_project_agent_types(normalized_project_key)
        metadata["project_key"] = normalized_project_key
        metadata["allowed_agent_types"] = project_agent_types
        metadata["primary_agent_type"] = project_agent_types[0] if project_agent_types else "triage"
        metadata["workflow_profile"] = _resolve_workflow_profile(normalized_project_key)

    metadata.update(updates)

    if isinstance(metadata.get("project_key"), str) and str(metadata.get("project_key")).strip():
        normalized_project_key = str(metadata.get("project_key")).strip().lower()
        metadata["project_key"] = normalized_project_key
        if not str(metadata.get("workflow_profile") or "").strip():
            metadata["workflow_profile"] = _resolve_workflow_profile(normalized_project_key)

    chat_data["metadata"] = _normalize_chat_data({"metadata": metadata}).get("metadata")
    r.hset(f"user_chats:{user_id}", chat_id, json.dumps(chat_data))
    return True


def rename_chat(user_id: int, chat_id: str, new_title: str) -> bool:
    """Renames a chat. Returns True if successful."""
    if not chat_id or not new_title:
        return False

    r = get_redis()
    raw_chat = r.hget(f"user_chats:{user_id}", chat_id)
    if not raw_chat:
        return False

    try:
        chat_data = json.loads(raw_chat)
        chat_data["title"] = new_title
        r.hset(f"user_chats:{user_id}", chat_id, json.dumps(chat_data))
        return True
    except json.JSONDecodeError:
        return False


def delete_chat(user_id: int, chat_id: str) -> bool:
    """Deletes a chat and its history. Returns True if successful."""
    r = get_redis()
    if r.hexists(f"user_chats:{user_id}", chat_id):
        r.hdel(f"user_chats:{user_id}", chat_id)
        r.delete(f"chat_history:{chat_id}")

        # If the deleted chat was active, un-set it
        active = r.get(f"active_chat:{user_id}")
        if active == chat_id:
            r.delete(f"active_chat:{user_id}")
        return True
    return False


def get_chat_history(user_id: int, limit: int = 10, chat_id: str = None) -> str:
    """Retrieve the recent chat history for a given chat. Uses active chat if not provided."""
    try:
        r = get_redis()
        if not chat_id:
            chat_id = get_active_chat(user_id)

        key = f"chat_history:{chat_id}"
        messages = r.lrange(key, -limit, -1)
        if not messages:
            return ""

        history = []
        for msg in messages:
            try:
                data = json.loads(msg)
                role = data.get("role", "unknown")
                text = data.get("text", "")
                history.append(f"{role.capitalize()}: {text}")
            except json.JSONDecodeError:
                continue
        joined = "\n".join(history)
        budget = apply_prompt_budget(
            joined,
            max_chars=_CHAT_HISTORY_MAX_CHARS,
            summary_max_chars=_CONTEXT_SUMMARY_MAX_CHARS,
        )
        if budget["summarized"] or budget["truncated"]:
            logger.info(
                "Chat history budget applied: user=%s original=%s final=%s summarized=%s truncated=%s",
                user_id,
                budget["original_chars"],
                budget["final_chars"],
                budget["summarized"],
                budget["truncated"],
            )
        return str(budget["text"])
    except Exception as e:
        logger.error(f"Error retrieving chat history for {user_id}: {e}")
        return ""


def append_message(
    user_id: int, role: str, text: str, ttl_seconds: int = 604800, chat_id: str = None
):
    """Append a message to the chat history with a TTL (default 7 days)."""
    try:
        r = get_redis()
        if not chat_id:
            chat_id = get_active_chat(user_id)

        key = f"chat_history:{chat_id}"
        budget = apply_prompt_budget(
            str(text or ""),
            max_chars=_CHAT_ENTRY_MAX_CHARS,
            summary_max_chars=min(_CONTEXT_SUMMARY_MAX_CHARS, 700),
        )
        if budget["summarized"] or budget["truncated"]:
            logger.info(
                "Chat message budget applied: user=%s role=%s original=%s final=%s summarized=%s truncated=%s",
                user_id,
                role,
                budget["original_chars"],
                budget["final_chars"],
                budget["summarized"],
                budget["truncated"],
            )
        message = json.dumps({"role": role, "text": budget["text"]})

        pipe = r.pipeline()
        pipe.rpush(key, message)
        # Keep only the last 30 messages to avoid indefinitely growing lists
        pipe.ltrim(key, -30, -1)
        # Extend the TTL of the chat history every time a message is added
        pipe.expire(key, ttl_seconds)
        pipe.execute()
    except Exception as e:
        logger.error(f"Error appending chat message for {user_id}: {e}")
