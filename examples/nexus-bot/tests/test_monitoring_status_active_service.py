import logging

import pytest

from nexus.core.monitoring.monitoring_status_active_service import handle_active, handle_status


class _Ctx:
    def __init__(self, args=None, user_id="1"):
        self.args = args or []
        self.user_id = user_id
        self.chat_id = 1
        self.calls = []

    async def reply_text(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return "msg"


def _deps():
    return type(
        "Deps",
        (),
        {
            "logger": logging.getLogger("test"),
            "allowed_user_ids": [],
            "normalize_project_key": lambda self, x: x,
            "iter_project_keys": lambda self: ["acme"],
            "get_project_label": lambda self, k: "Acme",
            "get_project_root": lambda self, k: None,
            "project_config": {},
            "types_map": {},
            "project_repo": lambda self, k: "owner/repo",
            "get_inbox_dir": lambda self, root, k: "/tmp/missing",
            "extract_issue_number_from_file": lambda self, p: None,
            "build_issue_url": lambda self, *a, **k: "u",
            "get_expected_running_agent_from_workflow": lambda self, n: None,
            "normalize_agent_reference": lambda self, s: s,
            "build_workflow_snapshot": lambda self, **k: {},
            "get_direct_issue_plugin": lambda self, repo: None,
            "find_task_file_by_issue": lambda self, issue: None,
            "read_latest_local_completion": lambda self, issue: None,
            "extract_structured_completion_signals": lambda self, events: [],
            "get_tasks_active_dir": lambda self, root, k: "/tmp/missing",
            "get_issue_details": lambda self, issue, repo=None: None,
            "get_tasks_closed_dir": lambda self, root, k: "/tmp/closed",
        },
    )()


@pytest.mark.asyncio
async def test_handle_status_prompts_when_no_args():
    ctx = _Ctx()
    deps = _deps()
    await handle_status(ctx, deps)
    assert "Please select a project" in ctx.calls[-1][0]
    buttons = ctx.calls[-1][1]["buttons"]
    assert any(getattr(btn, "label", "") == "❌ Close" for row in buttons for btn in row)


@pytest.mark.asyncio
async def test_handle_status_unknown_project():
    ctx = _Ctx(args=["missing"])
    deps = _deps()
    deps.normalize_project_key = lambda x: x
    deps.iter_project_keys = lambda: ["acme"]
    await handle_status(ctx, deps)
    assert "Unknown project" in ctx.calls[-1][0]


@pytest.mark.asyncio
async def test_handle_active_prompts_when_no_args():
    ctx = _Ctx()
    deps = _deps()
    await handle_active(ctx, deps)
    assert "Please select a project to view its active tasks" in ctx.calls[-1][0]
    buttons = ctx.calls[-1][1]["buttons"]
    assert any(getattr(btn, "label", "") == "❌ Close" for row in buttons for btn in row)
