"""Regression tests for completion metadata hydration in postgres storage."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import pytest

try:
    import sqlalchemy  # noqa: F401

    _SA = True
except ImportError:
    _SA = False

pytestmark = pytest.mark.skipif(not _SA, reason="sqlalchemy not installed")

from nexus.adapters.storage.postgres import PostgreSQLStorageBackend


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def order_by(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def query(self, *_args, **_kwargs):
        return _FakeQuery(self._rows)


def test_sync_list_completions_includes_issue_and_agent_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Legacy/stale payloads may omit canonical issue/agent fields.
    raw_payload = {
        "status": "complete",
        "summary": "Designer handoff",
        "next_agent": "developer",
    }
    rows = [
        type(
            "_Row",
            (),
            {
                "id": 7,
                "issue_number": "88",
                "agent_type": "designer",
                "status": "complete",
                "data": json.dumps(raw_payload),
                "dedup_key": "88:designer:complete",
                "created_at": datetime.now(tz=UTC),
            },
        )()
    ]

    backend = PostgreSQLStorageBackend.__new__(PostgreSQLStorageBackend)
    backend._engine = object()

    monkeypatch.setattr(
        "nexus.adapters.storage.postgres.Session",
        lambda _engine: _FakeSession(rows),
    )

    hydrated = backend._sync_list_completions(None)
    assert len(hydrated) == 1

    row = hydrated[0]
    assert row["issue_number"] == "88"
    assert row["_issue_number"] == "88"
    assert row["agent_type"] == "designer"
    assert row["_agent_type"] == "designer"
    assert row["status"] == "complete"


def test_sync_list_completions_applies_completion_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_payload = {
        "status": "complete",
        "summary": "verbose output " * 1000,
        "key_findings": [f"finding {idx}" for idx in range(20)],
        "next_agent": "developer",
    }
    rows = [
        type(
            "_Row",
            (),
            {
                "id": 8,
                "issue_number": "99",
                "agent_type": "reviewer",
                "status": "complete",
                "data": json.dumps(raw_payload),
                "dedup_key": "99:reviewer:complete",
                "created_at": datetime.now(tz=UTC),
            },
        )()
    ]

    backend = PostgreSQLStorageBackend.__new__(PostgreSQLStorageBackend)
    backend._engine = object()

    monkeypatch.setattr(
        "nexus.adapters.storage.postgres.Session",
        lambda _engine: _FakeSession(rows),
    )

    hydrated = backend._sync_list_completions(None)
    assert len(hydrated) == 1

    row = hydrated[0]
    assert len(row["summary"]) <= 900
    assert len(row["key_findings"]) <= 9


def test_sync_save_completion_upsert_refreshes_row_recency() -> None:
    backend = PostgreSQLStorageBackend(connection_string="sqlite:///:memory:")
    try:
        backend._sync_save_completion(
            "113",
            "developer",
            {"status": "complete", "summary": "initial implementation", "next_agent": "reviewer"},
        )
        time.sleep(0.01)
        backend._sync_save_completion(
            "113",
            "reviewer",
            {"status": "complete", "summary": "reviewed", "next_agent": "deployer"},
        )

        before = backend._sync_list_completions("113")
        assert before[0]["agent_type"] == "reviewer"

        time.sleep(0.01)
        backend._sync_save_completion(
            "113",
            "developer",
            {"status": "complete", "summary": "updated implementation", "next_agent": "reviewer"},
        )

        after = backend._sync_list_completions("113")
        assert after[0]["agent_type"] == "developer"
        assert after[0]["summary"] == "updated implementation"
        assert after[0]["_created_at"] != before[0]["_created_at"]
        assert after[0]["_updated_at"] == after[0]["_created_at"]
    finally:
        backend.close()
