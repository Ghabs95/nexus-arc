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
_CHAT_STATE_FILENAME = "chat_state.json"


def _redis_url() -> str:
    from nexus.core.config import REDIS_URL

    return str(REDIS_URL)


def _chat_state_path() -> str:
    from nexus.core.config import NEXUS_STATE_DIR

    return os.path.join(str(NEXUS_STATE_DIR), _CHAT_STATE_FILENAME)


def _chat_transcript_owner() -> str:
    from nexus.core.config import NEXUS_CHAT_TRANSCRIPT_OWNER, NEXUS_RUNTIME_MODE
    from nexus.core.config.runtime import normalize_chat_transcript_owner

    return normalize_chat_transcript_owner(NEXUS_CHAT_TRANSCRIPT_OWNER, NEXUS_RUNTIME_MODE)


def _persist_chat_transcript() -> bool:
    from nexus.core.config import NEXUS_CHAT_TRANSCRIPT_OWNER, NEXUS_RUNTIME_MODE
    from nexus.core.config.runtime import chat_transcript_persistence_enabled

    return chat_transcript_persistence_enabled(NEXUS_CHAT_TRANSCRIPT_OWNER, NEXUS_RUNTIME_MODE)


def _chat_metadata_backend() -> str:
    from nexus.core.config import NEXUS_CHAT_TRANSCRIPT_OWNER, NEXUS_RUNTIME_MODE
    from nexus.core.config.runtime import chat_metadata_backend

    return chat_metadata_backend(NEXUS_CHAT_TRANSCRIPT_OWNER, NEXUS_RUNTIME_MODE)


def _empty_chat_state() -> dict[str, Any]:
    return {"users": {}}


def _normalize_filesystem_chat_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return _empty_chat_state()
    users = state.get("users")
    if not isinstance(users, dict):
        users = {}
    return {"users": users}


def _load_chat_state() -> dict[str, Any]:
    path = _chat_state_path()
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        return _normalize_filesystem_chat_state(raw)
    except FileNotFoundError:
        return _empty_chat_state()
    except Exception as exc:
        logger.warning("Failed to load chat state from %s: %s", path, exc)
        return _empty_chat_state()


def _save_chat_state(state: dict[str, Any]) -> None:
    path = _chat_state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    normalized = _normalize_filesystem_chat_state(state)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(normalized, handle, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def _get_user_chat_state(
    state: dict[str, Any],
    user_id: int,
    *,
    create: bool = False,
) -> dict[str, Any]:
    users = state.setdefault("users", {})
    if not isinstance(users, dict):
        users = {}
        state["users"] = users

    user_key = str(int(user_id))
    candidate = users.get(user_key)
    chats = candidate.get("chats") if isinstance(candidate, dict) else None
    if not isinstance(chats, dict):
        chats = {}

    normalized = {
        "active_chat_id": (
            str(candidate.get("active_chat_id") or "").strip()
            if isinstance(candidate, dict)
            else ""
        ),
        "chats": chats,
    }
    if create or user_key in users:
        users[user_key] = normalized
    return normalized


def _fs_create_chat(user_id: int, title: str | None, metadata: dict[str, Any] | None) -> str:
    state = _load_chat_state()
    user_state = _get_user_chat_state(state, user_id, create=True)
    chat_id = uuid.uuid4().hex
    resolved_title = title or f"Chat {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    chat_data = _normalize_chat_data(
        {
            "id": chat_id,
            "title": resolved_title,
            "created_at": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
    )
    user_state["chats"][chat_id] = chat_data
    user_state["active_chat_id"] = chat_id
    _save_chat_state(state)
    return chat_id


def _sorted_fs_chats(user_state: dict[str, Any]) -> list[dict[str, Any]]:
    chats = []
    for chat_data in (user_state.get("chats") or {}).values():
        if isinstance(chat_data, dict):
            chats.append(_normalize_chat_data(chat_data))
    chats.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return chats


def _fs_get_active_chat(user_id: int) -> str:
    state = _load_chat_state()
    user_state = _get_user_chat_state(state, user_id, create=True)
    chats = user_state.get("chats") or {}
    active_chat_id = str(user_state.get("active_chat_id") or "").strip()
    if active_chat_id and active_chat_id in chats:
        return active_chat_id
    sorted_chats = _sorted_fs_chats(user_state)
    if sorted_chats:
        first_chat_id = str(sorted_chats[0].get("id") or "").strip()
        if first_chat_id:
            user_state["active_chat_id"] = first_chat_id
            _save_chat_state(state)
            return first_chat_id
    return _fs_create_chat(user_id, "Main Chat", None)


def _fs_get_chat(user_id: int, chat_id: str | None = None) -> dict[str, Any]:
    state = _load_chat_state()
    user_state = _get_user_chat_state(state, user_id)
    resolved_chat_id = str(chat_id or _fs_get_active_chat(user_id) or "").strip()
    candidate = (user_state.get("chats") or {}).get(resolved_chat_id)
    if not isinstance(candidate, dict):
        return {}
    return _normalize_chat_data(candidate)


def _get_chat_agent_types(project_key: str) -> list[str]:
    from nexus.core.config import get_chat_agent_types

    return list(get_chat_agent_types(project_key) or [])


def _get_workflow_profile(project_key: str) -> str:
    from nexus.core.config import get_workflow_profile

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
_ephemeral_chat_history: dict[str, list[dict[str, str]]] = {}


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
    if _chat_metadata_backend() != "redis":
        return _fs_create_chat(user_id, title, metadata)

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
    if _chat_metadata_backend() != "redis":
        return _fs_get_active_chat(user_id)

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
    if _chat_metadata_backend() != "redis":
        state = _load_chat_state()
        user_state = _get_user_chat_state(state, user_id, create=True)
        if str(chat_id or "").strip() in (user_state.get("chats") or {}):
            user_state["active_chat_id"] = str(chat_id).strip()
            _save_chat_state(state)
            return True
        return False

    r = get_redis()
    if r.hexists(f"user_chats:{user_id}", chat_id):
        r.set(f"active_chat:{user_id}", chat_id)
        return True
    return False


def list_chats(user_id: int) -> list:
    """Lists all chats for a user, sorted by newest first."""
    if _chat_metadata_backend() != "redis":
        state = _load_chat_state()
        user_state = _get_user_chat_state(state, user_id)
        return _sorted_fs_chats(user_state)

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
    if _chat_metadata_backend() != "redis":
        return _fs_get_chat(user_id, chat_id)

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

    if _chat_metadata_backend() != "redis":
        state = _load_chat_state()
        user_state = _get_user_chat_state(state, user_id, create=True)
        raw_chat = (user_state.get("chats") or {}).get(chat_id)
        if not isinstance(raw_chat, dict):
            return False
        chat_data = _normalize_chat_data(raw_chat)
    else:
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
    if _chat_metadata_backend() != "redis":
        user_state["chats"][chat_id] = chat_data
        _save_chat_state(state)
    else:
        r.hset(f"user_chats:{user_id}", chat_id, json.dumps(chat_data))
    return True


def rename_chat(user_id: int, chat_id: str, new_title: str) -> bool:
    """Renames a chat. Returns True if successful."""
    if not chat_id or not new_title:
        return False

    if _chat_metadata_backend() != "redis":
        state = _load_chat_state()
        user_state = _get_user_chat_state(state, user_id, create=True)
        raw_chat = (user_state.get("chats") or {}).get(chat_id)
        if not isinstance(raw_chat, dict):
            return False
        raw_chat["title"] = new_title
        user_state["chats"][chat_id] = _normalize_chat_data(raw_chat)
        _save_chat_state(state)
        return True

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
    _ephemeral_chat_history.pop(str(chat_id or "").strip(), None)

    if _chat_metadata_backend() != "redis":
        state = _load_chat_state()
        user_state = _get_user_chat_state(state, user_id, create=True)
        chats = user_state.get("chats") or {}
        if chat_id in chats:
            chats.pop(chat_id, None)
            if user_state.get("active_chat_id") == chat_id:
                user_state["active_chat_id"] = ""
            _save_chat_state(state)
            return True
        return False

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
        if not chat_id:
            chat_id = get_active_chat(user_id)

        if not _persist_chat_transcript():
            messages = list(_ephemeral_chat_history.get(str(chat_id or "").strip(), []))[-limit:]
        else:
            r = get_redis()
            key = f"chat_history:{chat_id}"
            messages = r.lrange(key, -limit, -1)
        if not messages:
            return ""

        history = []
        for msg in messages:
            try:
                data = msg if isinstance(msg, dict) else json.loads(msg)
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
        if not chat_id:
            chat_id = get_active_chat(user_id)

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
        chat_key = str(chat_id or "").strip()
        payload = {"role": role, "text": str(budget["text"])}
        if not _persist_chat_transcript():
            history = _ephemeral_chat_history.setdefault(chat_key, [])
            history.append(payload)
            if len(history) > 30:
                del history[:-30]
            if _chat_transcript_owner() != "nexus":
                logger.debug(
                    "Skipped durable transcript persistence for chat=%s owner=%s",
                    chat_key,
                    _chat_transcript_owner(),
                )
            return

        r = get_redis()
        key = f"chat_history:{chat_key}"
        message = json.dumps(payload)
        pipe = r.pipeline()
        pipe.rpush(key, message)
        pipe.ltrim(key, -30, -1)
        pipe.expire(key, ttl_seconds)
        pipe.execute()
    except Exception as e:
        logger.error(f"Error appending chat message for {user_id}: {e}")
