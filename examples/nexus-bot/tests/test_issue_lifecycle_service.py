import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from nexus.core import issue_lifecycle as svc


class _FakePlatform:
    def __init__(self):
        self.calls = []
        self.open_issues = []

    async def list_open_issues(self, limit=100, labels=None):
        self.calls.append(("list_open_issues", limit, labels))
        return list(self.open_issues)

    async def create_issue(self, title, body, labels=None):
        self.calls.append(("create_issue", title, body, labels))
        if labels is not None and any(lbl.startswith("workflow:") for lbl in labels):
            raise RuntimeError("label missing")
        return types.SimpleNamespace(number=12, url="https://github.com/acme/repo/issues/12")

    async def ensure_label(self, name, *, color, description=""):
        self.calls.append(("ensure_label", name, color, description))
        return True

    async def update_issue(self, issue_id, **kwargs):
        self.calls.append(("update_issue", issue_id, kwargs))
        return None


def test_create_issue_fallbacks_without_labels_and_reapplies_labels():
    platform = _FakePlatform()
    with patch.object(svc, "get_git_platform", return_value=platform):
        url = svc.create_issue(
            title="Title",
            body="Body",
            project="proj-a",
            workflow_label="workflow:full",
            task_type="feature",
            repo_key="acme/repo",
        )

    assert url.endswith("/12")
    create_calls = [c for c in platform.calls if c[0] == "create_issue"]
    assert len(create_calls) == 2
    assert create_calls[0][3] is not None
    assert create_calls[1][3] is None
    assert any(c[0] == "ensure_label" for c in platform.calls)
    assert any(c[0] == "update_issue" and c[1] == "12" for c in platform.calls)


def test_create_issue_reuses_recent_duplicate_issue():
    platform = _FakePlatform()
    platform.open_issues = [
        types.SimpleNamespace(
            number=77,
            title="Title",
            labels=["project:proj-a", "type:feature", "workflow:full"],
            created_at=datetime.now(tz=UTC) - timedelta(minutes=30),
            url="https://github.com/acme/repo/issues/77",
        )
    ]

    with patch.object(svc, "get_git_platform", return_value=platform):
        url = svc.create_issue(
            title="Title",
            body="Body",
            project="proj-a",
            workflow_label="workflow:full",
            task_type="feature",
            repo_key="acme/repo",
        )

    assert url.endswith("/77")
    assert not any(c[0] == "create_issue" for c in platform.calls)


def test_create_issue_reuses_recent_duplicate_issue_by_dedupe_key():
    platform = _FakePlatform()
    platform.open_issues = [
        types.SimpleNamespace(
            number=88,
            title="Completely different title",
            labels=["project:proj-a"],
            created_at=datetime.now(tz=UTC) - timedelta(minutes=10),
            body="blah\n<!-- nexus-inbox-source: task_123.md -->\n",
            url="https://github.com/acme/repo/issues/88",
        )
    ]

    with patch.object(svc, "get_git_platform", return_value=platform):
        url = svc.create_issue(
            title="New unique title",
            body="Body",
            project="proj-a",
            workflow_label="workflow:full",
            task_type="feature",
            repo_key="acme/repo",
            dedupe_key="task_123.md",
        )

    assert url.endswith("/88")
    assert not any(c[0] == "create_issue" for c in platform.calls)


def test_create_issue_appends_source_marker_to_body():
    platform = _FakePlatform()
    with patch.object(svc, "get_git_platform", return_value=platform):
        svc.create_issue(
            title="Title",
            body="Body",
            project="proj-a",
            workflow_label="workflow:full",
            task_type="feature",
            repo_key="acme/repo",
            dedupe_key="task_123.md",
        )

    create_calls = [c for c in platform.calls if c[0] == "create_issue"]
    assert create_calls
    assert "nexus-inbox-source: task_123.md" in create_calls[0][2]


def test_rename_task_file_and_sync_issue_body_updates_remote_body(tmp_path):
    task_file = tmp_path / "feature_123.md"
    task_file.write_text("x")

    platform = _FakePlatform()
    with patch.object(svc, "get_git_platform", return_value=platform):
        renamed = svc.rename_task_file_and_sync_issue_body(
            task_file_path=str(task_file),
            issue_url="https://github.com/acme/repo/issues/77",
            issue_body=f"Task file: `{task_file}`",
            project_name="proj-a",
            repo_key="acme/repo",
        )

    assert renamed.endswith("feature_77.md")
    assert Path(renamed).exists()
    update_calls = [c for c in platform.calls if c[0] == "update_issue"]
    assert update_calls
    assert update_calls[-1][1] == "77"
    assert "feature_77.md" in update_calls[-1][2]["body"]
