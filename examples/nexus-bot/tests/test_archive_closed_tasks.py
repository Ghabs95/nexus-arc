"""Tests for automatic task archival on workflow finalization."""


def test_archive_closed_task_by_issue_url(tmp_path, monkeypatch):
    from nexus.core.task_archive import archive_closed_task_files

    workspace = tmp_path / "workspace"
    active_dir = workspace / ".nexus" / "tasks" / "nexus" / "active"
    closed_dir = workspace / ".nexus" / "tasks" / "nexus" / "closed"
    active_dir.mkdir(parents=True)

    task = active_dir / "feature-simple_123.md"
    task.write_text("""# Task\n
**Issue:** https://github.com/acme/repo/issues/41
""")

    project_config = {
        "nexus": {
            "workspace": "workspace",
            "git_repo": "acme/repo",
        }
    }
    archived = archive_closed_task_files(
        issue_num="41",
        project_name="nexus",
        project_config=project_config,
        base_dir=str(tmp_path),
        get_tasks_active_dir=lambda root, project: str(root + "/.nexus/tasks/" + project + "/active"),
        get_tasks_closed_dir=lambda root, project: str(root + "/.nexus/tasks/" + project + "/closed"),
        logger=__import__("logging").getLogger(__name__),
    )

    assert archived == 1
    assert not task.exists()
    assert (closed_dir / "feature-simple_123.md").exists()


def test_archive_closed_task_by_issue_filename(tmp_path, monkeypatch):
    from nexus.core.task_archive import archive_closed_task_files

    workspace = tmp_path / "workspace"
    active_dir = workspace / ".nexus" / "tasks" / "nexus" / "active"
    closed_dir = workspace / ".nexus" / "tasks" / "nexus" / "closed"
    active_dir.mkdir(parents=True)

    task = active_dir / "issue_41.md"
    task.write_text("# Webhook task")

    project_config = {
        "nexus": {
            "workspace": "workspace",
            "git_repo": "acme/repo",
        }
    }
    archived = archive_closed_task_files(
        issue_num="41",
        project_name="nexus",
        project_config=project_config,
        base_dir=str(tmp_path),
        get_tasks_active_dir=lambda root, project: str(root + "/.nexus/tasks/" + project + "/active"),
        get_tasks_closed_dir=lambda root, project: str(root + "/.nexus/tasks/" + project + "/closed"),
        logger=__import__("logging").getLogger(__name__),
    )

    assert archived == 1
    assert not task.exists()
    assert (closed_dir / "issue_41.md").exists()


def test_archive_closed_task_ignores_other_issues(tmp_path, monkeypatch):
    from nexus.core.task_archive import archive_closed_task_files

    workspace = tmp_path / "workspace"
    active_dir = workspace / ".nexus" / "tasks" / "nexus" / "active"
    closed_dir = workspace / ".nexus" / "tasks" / "nexus" / "closed"
    active_dir.mkdir(parents=True)

    other_task = active_dir / "feature-simple_999.md"
    other_task.write_text("""# Task\n
**Issue:** https://github.com/acme/repo/issues/999
""")

    project_config = {
        "nexus": {
            "workspace": "workspace",
            "git_repo": "acme/repo",
        }
    }
    archived = archive_closed_task_files(
        issue_num="41",
        project_name="nexus",
        project_config=project_config,
        base_dir=str(tmp_path),
        get_tasks_active_dir=lambda root, project: str(root + "/.nexus/tasks/" + project + "/active"),
        get_tasks_closed_dir=lambda root, project: str(root + "/.nexus/tasks/" + project + "/closed"),
        logger=__import__("logging").getLogger(__name__),
    )

    assert archived == 0
    assert other_task.exists()
    assert not closed_dir.exists()
