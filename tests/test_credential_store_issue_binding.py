from __future__ import annotations

from types import SimpleNamespace


def test_bind_issue_requester_rejects_cross_user_rebind(monkeypatch):
    from nexus.core.auth import credential_store as store

    if not getattr(store, "_SA_AVAILABLE", False):
        return

    row = SimpleNamespace(
        repo_key="acme/repo",
        issue_number=7,
        issue_url="https://github.com/acme/repo/issues/7",
        project_key="nexus",
        requester_nexus_id="nexus-owner-1",
        updated_at=None,
    )

    class _Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return row

    class _Session:
        def __init__(self):
            self.committed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def query(self, *_args, **_kwargs):
            return _Query()

        def add(self, *_args, **_kwargs):
            raise AssertionError("add() should not be called for existing rows")

        def commit(self):
            self.committed = True

    session = _Session()
    monkeypatch.setattr(store, "_get_engine", lambda: object())
    monkeypatch.setattr(store, "Session", lambda _engine: session)

    store.bind_issue_requester(
        repo_key="acme/repo",
        issue_number=7,
        issue_url="https://github.com/acme/repo/issues/7",
        project_key="nexus",
        requester_nexus_id="nexus-owner-2",
    )

    assert row.requester_nexus_id == "nexus-owner-1"
    assert session.committed is False


def test_bind_issue_requester_allows_same_user_refresh(monkeypatch):
    from nexus.core.auth import credential_store as store

    if not getattr(store, "_SA_AVAILABLE", False):
        return

    row = SimpleNamespace(
        repo_key="acme/repo",
        issue_number=7,
        issue_url="https://github.com/acme/repo/issues/7",
        project_key="nexus",
        requester_nexus_id="nexus-owner-1",
        updated_at=None,
    )

    class _Query:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return row

    class _Session:
        def __init__(self):
            self.committed = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def query(self, *_args, **_kwargs):
            return _Query()

        def add(self, *_args, **_kwargs):
            raise AssertionError("add() should not be called for existing rows")

        def commit(self):
            self.committed = True

    session = _Session()
    monkeypatch.setattr(store, "_get_engine", lambda: object())
    monkeypatch.setattr(store, "Session", lambda _engine: session)

    store.bind_issue_requester(
        repo_key="acme/repo",
        issue_number=7,
        issue_url="https://github.com/acme/repo/issues/7?updated=1",
        project_key="prj-app",
        requester_nexus_id="nexus-owner-1",
    )

    assert row.issue_url == "https://github.com/acme/repo/issues/7?updated=1"
    assert row.project_key == "prj-app"
    assert row.requester_nexus_id == "nexus-owner-1"
    assert session.committed is True
