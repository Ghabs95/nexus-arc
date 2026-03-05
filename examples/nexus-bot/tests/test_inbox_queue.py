from __future__ import annotations

import pytest

import nexus.core.integrations.inbox_queue as inbox_queue


def _insert_row(*, engine, project_key: str, workspace: str, filename: str, status: str, body: str):
    with inbox_queue.Session(engine) as session:
        row = inbox_queue._InboxTaskRow(
            project_key=project_key,
            workspace=workspace,
            filename=filename,
            markdown_content=body,
            status=status,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return int(row.id)


@pytest.fixture
def queue_engine(monkeypatch):
    if not inbox_queue._SA_AVAILABLE:
        pytest.skip("sqlalchemy not installed")

    sa = pytest.importorskip("sqlalchemy")
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    inbox_queue._InboxBase.metadata.create_all(engine)
    monkeypatch.setattr(inbox_queue, "_ENGINE", engine)
    yield engine
    engine.dispose()
    monkeypatch.setattr(inbox_queue, "_ENGINE", None)


def test_claim_pending_tasks_suppresses_duplicate_pending_rows(queue_engine):
    first_id = _insert_row(
        engine=queue_engine,
        project_key="nexus",
        workspace="nexus",
        filename="task_901.md",
        status="pending",
        body="first",
    )
    duplicate_id = _insert_row(
        engine=queue_engine,
        project_key="nexus",
        workspace="nexus",
        filename="task_901.md",
        status="pending",
        body="duplicate",
    )

    claimed = inbox_queue.claim_pending_tasks(limit=10, worker_id="worker-1")
    assert [task.id for task in claimed] == [first_id]

    with inbox_queue.Session(queue_engine) as session:
        first = session.get(inbox_queue._InboxTaskRow, first_id)
        duplicate = session.get(inbox_queue._InboxTaskRow, duplicate_id)

    assert first is not None and first.status == "processing"
    assert duplicate is not None and duplicate.status == "done"
    assert "Duplicate queue row suppressed" in str(duplicate.error or "")


def test_claim_pending_tasks_allows_retry_after_failed_prior_row(queue_engine):
    _insert_row(
        engine=queue_engine,
        project_key="nexus",
        workspace="nexus",
        filename="task_902.md",
        status="failed",
        body="failed earlier",
    )
    retry_id = _insert_row(
        engine=queue_engine,
        project_key="nexus",
        workspace="nexus",
        filename="task_902.md",
        status="pending",
        body="retry payload",
    )

    claimed = inbox_queue.claim_pending_tasks(limit=10, worker_id="worker-2")
    assert [task.id for task in claimed] == [retry_id]
