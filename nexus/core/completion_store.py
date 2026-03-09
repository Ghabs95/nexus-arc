"""Backend-aware completion store.

Routes completion persistence and scanning to the configured storage backend
(``filesystem`` or ``postgres``).  Replaces direct use of
``scan_for_completions()`` and local ``completion_summary_*.json`` writes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from nexus.core.completion import (
    CompletionSummary,
    DetectedCompletion,
    budget_completion_payload,
    scan_for_completions,
)

if TYPE_CHECKING:
    from nexus.adapters.storage.base import StorageBackend

logger = logging.getLogger(__name__)


class CompletionStore:
    """Facade that routes to filesystem or postgres depending on config.

    Args:
        backend: ``"filesystem"`` or ``"postgres"``.
        storage: A :class:`StorageBackend` instance (required when *backend*
            is ``"postgres"``).
        base_dir: Root directory for filesystem scanning (used when
            *backend* is ``"filesystem"``).
        nexus_dir: Name of the ``.nexus`` directory (default ``".nexus"``).
    """

    def __init__(
        self,
        backend: str,
        storage: StorageBackend | None = None,
        base_dir: str = "",
        nexus_dir: str = ".nexus",
    ) -> None:
        self._backend = backend
        self._storage = storage
        self._base_dir = base_dir
        self._nexus_dir = nexus_dir

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def save(
        self,
        issue_number: str,
        agent_type: str,
        data: dict[str, Any],
    ) -> str:
        """Persist a completion summary.

        Returns:
            Dedup key string.
        """
        if self._backend == "postgres":
            if self._storage is None:
                raise RuntimeError("CompletionStore requires a StorageBackend for postgres mode")
            import asyncio

            return asyncio.run(
                self._storage.save_completion(
                    issue_number, agent_type, budget_completion_payload(data)
                )
            )

        # Filesystem: write JSON file (same as legacy)
        return self._save_to_filesystem(issue_number, agent_type, data)

    # ------------------------------------------------------------------
    # Read / scan path
    # ------------------------------------------------------------------

    def scan(self, issue_number: str | None = None) -> list[DetectedCompletion]:
        """Return detected completions, routing to the configured backend.

        When *issue_number* is provided, only completions for that issue are
        returned.
        """
        if self._backend == "postgres":
            return self._scan_postgres(issue_number)

        # Filesystem: delegate to existing scanner
        all_completions = scan_for_completions(self._base_dir, nexus_dir=self._nexus_dir)
        if issue_number:
            return [c for c in all_completions if c.issue_number == str(issue_number)]
        return all_completions

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _save_to_filesystem(
        self,
        issue_number: str,
        agent_type: str,
        data: dict[str, Any],
    ) -> str:
        """Write a completion_summary JSON file (legacy path)."""
        import json
        import os

        # Find or create the completions directory
        normalized = budget_completion_payload(data)
        completions_dir = os.path.join(
            self._base_dir,
            self._nexus_dir,
            "tasks",
            normalized.get("_project", ""),
            "completions",
        )
        os.makedirs(completions_dir, exist_ok=True)
        path = os.path.join(
            completions_dir,
            f"completion_summary_{issue_number}.json",
        )
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(normalized, fh, indent=2)

        dedup_key = f"{issue_number}:{agent_type}:{os.path.basename(path)}"
        logger.info("Saved filesystem completion: %s", path)
        return dedup_key

    def _scan_postgres(self, issue_number: str | None) -> list[DetectedCompletion]:
        """Query the database for completions and return DetectedCompletion objects."""
        if self._storage is None:
            return []

        import asyncio

        rows = asyncio.run(self._storage.list_completions(issue_number))
        results: list[DetectedCompletion] = []
        for row in rows:
            summary = CompletionSummary.from_dict(row)
            db_id = str(row.get("_db_id", "unknown"))
            version_raw = str(row.get("_updated_at") or row.get("_created_at") or db_id)
            version_token = "".join(ch if ch.isalnum() else "-" for ch in version_raw) or "v0"
            results.append(
                DetectedCompletion(
                    file_path=f"db://{db_id}_{version_token}",
                    issue_number=str(
                        row.get("issue_number")
                        or row.get("_issue_number")
                        or row.get("issue_num")
                        or ""
                    ),
                    summary=summary,
                )
            )
        return results
