from nexus.core.task_context import load_task_context


def test_load_task_context_resolves_project_from_inbox_path(tmp_path):
    workspace = tmp_path / "repo"
    inbox = workspace / ".nexus" / "inbox" / "proj-a"
    inbox.mkdir(parents=True)
    task_file = inbox / "task.md"
    task_file.write_text("**Type:** bug\nbody")

    project_config = {"proj-a": {"workspace": str(workspace.relative_to(tmp_path))}}

    ctx = load_task_context(
        filepath=str(task_file),
        project_config=project_config,
        base_dir=str(tmp_path),
        get_nexus_dir_name=lambda: ".nexus",
        iter_project_configs=lambda cfg, _get_repos: [(k, v) for k, v in cfg.items()],
        get_repos=lambda _p: [],
    )

    assert ctx is not None
    assert ctx["task_type"] == "bug"
    assert ctx["project_name"] == "proj-a"
    assert ctx["project_root"] == str(workspace)
    assert "body" in ctx["content"]


def test_load_task_context_fallbacks_by_workspace_prefix(tmp_path):
    workspace = tmp_path / "repo"
    other_path = workspace / "nested" / "task.md"
    other_path.parent.mkdir(parents=True)
    other_path.write_text("No type line")

    project_config = {"proj-a": {"workspace": "repo"}}

    ctx = load_task_context(
        filepath=str(other_path),
        project_config=project_config,
        base_dir=str(tmp_path),
        get_nexus_dir_name=lambda: ".nexus",
        iter_project_configs=lambda cfg, _get_repos: [(k, v) for k, v in cfg.items()],
        get_repos=lambda _p: [],
    )

    assert ctx is not None
    assert ctx["task_type"] == "feature"
    assert ctx["project_name"] == "proj-a"
    assert ctx["project_root"] == str(workspace)


def test_load_task_context_returns_none_when_project_unresolved(tmp_path):
    task_file = tmp_path / "task.md"
    task_file.write_text("body")

    ctx = load_task_context(
        filepath=str(task_file),
        project_config={},
        base_dir=str(tmp_path),
        get_nexus_dir_name=lambda: ".nexus",
        iter_project_configs=lambda cfg, _get_repos: [],
        get_repos=lambda _p: [],
    )

    assert ctx is None
