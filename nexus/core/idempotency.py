"""Persistent idempotency ledger for workflow step completion events.

Enforces uniqueness per the composite key::

    (issue_id, step_num, agent_type, event_id)

where *event_id* is a caller-supplied identifier — typically a GitHub comment
ID or a hash of the completion file path/contents.  This prevents reprocessing
of stale or duplicate signals when ``complete_step_for_issue`` runs, before it
calls the underlying engine to complete the step.
"""

import hashlib
import json
import logging
import os
from dataclasses import dataclass

from nexus.core.inbox.inbox_persistence_service import (
    load_json_state_file as _load_json_state_file,
)
from nexus.core.inbox.inbox_persistence_service import (
    save_json_state_file as _save_json_state_file,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdempotencyKey:
    """Composite key identifying a single workflow step completion event."""

    issue_id: str
    step_num: int
    agent_type: str
    event_id: str  # GitHub comment ID or file-path/content hash

    def as_string(self) -> str:
        """Return a stable hex digest of the composite key."""
        raw = f"{self.issue_id}:{self.step_num}:{self.agent_type}:{self.event_id}"
        return hashlib.sha256(raw.encode()).hexdigest()


class IdempotencyLedger:
    """Backend-aware ledger for deduplicating workflow step completion events.

    Usage::

        ledger = IdempotencyLedger("/path/to/.nexus/idempotency_ledger.json")
        key = IdempotencyKey(issue_id="42", step_num=3,
                             agent_type="developer", event_id="comment-789")
        if ledger.is_duplicate(key):
            logger.info("Duplicate event — skipping")
            return
        # ... process event ...
        ledger.record(key)
    """

    def __init__(
        self,
        ledger_path: str,
        *,
        storage_backend: str | None = None,
        state_key: str | None = None,
    ) -> None:
        self._path = ledger_path
        self._storage_backend = storage_backend
        self._state_key = state_key or os.path.splitext(os.path.basename(self._path).lstrip("."))[0]
        self._seen: set[str] = set()
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_duplicate(self, key: IdempotencyKey) -> bool:
        """Return ``True`` if *key* has already been recorded."""
        return key.as_string() in self._seen

    def record(self, key: IdempotencyKey) -> None:
        """Mark *key* as processed and persist the ledger."""
        digest = key.as_string()
        if digest not in self._seen:
            self._seen.add(digest)
            self._save()

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _is_database_backend(self) -> bool:
        explicit = str(self._storage_backend or "").strip().lower()
        if explicit in {"database", "postgres", "postgresql"}:
            return True
        host_state_backend = str(os.getenv("NEXUS_HOST_STATE_BACKEND", "")).strip().lower()
        if host_state_backend in {"database", "postgres", "postgresql"}:
            return True
        storage_backend = str(os.getenv("NEXUS_STORAGE_BACKEND", "filesystem")).strip().lower()
        return storage_backend in {"database", "postgres", "postgresql"}

    def _load_legacy_file_seen(self) -> set[str]:
        if not os.path.exists(self._path):
            return set()
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return {str(item) for item in data if str(item).strip()}
            if isinstance(data, dict):
                seen_values = data.get("seen", [])
                if isinstance(seen_values, list):
                    return {str(item) for item in seen_values if str(item).strip()}
        except Exception as exc:
            logger.warning("IdempotencyLedger: failed to load %s: %s", self._path, exc)
        return set()

    def _load(self) -> None:
        if not self._is_database_backend():
            self._seen = self._load_legacy_file_seen()
            return

        payload = _load_json_state_file(
            path=self._path,
            logger=logger,
            warn_only=True,
            storage_backend=self._storage_backend,
            state_key=self._state_key,
            migrate_local_on_empty=False,
        )
        seen_values = payload.get("seen", []) if isinstance(payload, dict) else []
        if isinstance(seen_values, list):
            self._seen = {str(item) for item in seen_values if str(item).strip()}
            if self._seen:
                return

        legacy_seen = self._load_legacy_file_seen()
        if legacy_seen:
            self._seen = legacy_seen
            _save_json_state_file(
                path=self._path,
                data={"seen": sorted(self._seen)},
                logger=logger,
                warn_only=True,
                storage_backend=self._storage_backend,
                state_key=self._state_key,
            )
            logger.info(
                "Bootstrapped idempotency ledger host-state key '%s' from %s",
                self._state_key,
                self._path,
            )

    def _save(self) -> None:
        if self._is_database_backend():
            _save_json_state_file(
                path=self._path,
                data={"seen": sorted(self._seen)},
                logger=logger,
                warn_only=True,
                storage_backend=self._storage_backend,
                state_key=self._state_key,
            )
            return

        try:
            parent = os.path.dirname(self._path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp_path = f"{self._path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(sorted(self._seen), fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self._path)
        except Exception as exc:
            logger.warning("IdempotencyLedger: failed to save %s: %s", self._path, exc)
