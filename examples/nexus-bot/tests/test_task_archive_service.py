from unittest.mock import MagicMock

from nexus.core.task_archive import archive_closed_task_files


def test_archive_closed_task_files_matches_filename(tmp_path):
    base_dir = tmp_path
    project_root = base_dir / "workspace-a"
    active_dir = project_root / ".nexus" / "tasks" / "proj-a" / "active"
    closed_dir = project_root / ".nexus" / "tasks" / "proj-a" / "closed"
    active_dir.mkdir(parents=True)
    (active_dir / "issue_42.md").write_text("content")

    archived = archive_closed_task_files(
        issue_num="42",
        project_name="proj-a",
        project_config={"proj-a": {"workspace": "workspace-a"}},
        base_dir=str(base_dir),
        get_tasks_active_dir=lambda _root, _proj: str(active_dir),
        get_tasks_closed_dir=lambda _root, _proj: str(closed_dir),
        logger=MagicMock(),
    )

    assert archived == 1
    assert not (active_dir / "issue_42.md").exists()
    assert (closed_dir / "issue_42.md").exists()


def test_archive_closed_task_files_matches_issue_url_metadata(tmp_path):
    base_dir = tmp_path
    project_root = base_dir / "workspace-a"
    active_dir = project_root / ".nexus" / "tasks" / "proj-a" / "active"
    closed_dir = project_root / ".nexus" / "tasks" / "proj-a" / "closed"
    active_dir.mkdir(parents=True)
    (active_dir / "task_bug.md").write_text(
        "**Issue:** https://github.com/acme/repo/issues/77\n\nInvestigate regression"
    )

    archived = archive_closed_task_files(
        issue_num="77",
        project_name="proj-a",
        project_config={"proj-a": {"workspace": "workspace-a"}},
        base_dir=str(base_dir),
        get_tasks_active_dir=lambda _root, _proj: str(active_dir),
        get_tasks_closed_dir=lambda _root, _proj: str(closed_dir),
        logger=MagicMock(),
    )

    assert archived == 1
    assert (closed_dir / "task_bug.md").exists()
