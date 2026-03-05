"""Unit tests for /visualize command — mermaid diagram builder and handler fallback."""

from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from nexus.core.mermaid_render_service import build_mermaid_diagram


# ---------------------------------------------------------------------------
# build_mermaid_diagram
# ---------------------------------------------------------------------------


def _make_step(name: str, status: str, agent_name: str = "") -> dict[str, Any]:
    return {"name": name, "status": status, "agent": {"name": agent_name} if agent_name else {}}


class TestBuildMermaidDiagram:
    def test_returns_flowchart_header(self):
        diagram = build_mermaid_diagram([], issue_num="42")
        assert diagram.startswith("flowchart TD")

    def test_issue_node_present(self):
        diagram = build_mermaid_diagram([], issue_num="99")
        assert 'I["Issue #99"]' in diagram

    def test_step_name_included_in_label(self):
        steps = [_make_step("my-step", "pending")]
        diagram = build_mermaid_diagram(steps, issue_num="1")
        assert "my-step" in diagram

    def test_step_count_label(self):
        steps = [_make_step("a", "complete"), _make_step("b", "pending")]
        diagram = build_mermaid_diagram(steps, issue_num="7")
        assert "1/2" in diagram
        assert "2/2" in diagram

    def test_status_icon_complete(self):
        steps = [_make_step("done", "complete")]
        assert "✅" in build_mermaid_diagram(steps, "1")

    def test_status_icon_running(self):
        steps = [_make_step("running", "running")]
        assert "▶️" in build_mermaid_diagram(steps, "1")

    def test_status_icon_failed(self):
        steps = [_make_step("bad", "failed")]
        assert "❌" in build_mermaid_diagram(steps, "1")

    def test_style_line_for_complete_step(self):
        steps = [_make_step("done", "complete")]
        diagram = build_mermaid_diagram(steps, "1")
        assert "style S1 fill:#3fb950" in diagram

    def test_agent_name_in_label(self):
        steps = [_make_step("task", "pending", agent_name="my-agent")]
        diagram = build_mermaid_diagram(steps, "1")
        assert "my-agent" in diagram

    def test_non_dict_steps_skipped(self):
        steps: list[Any] = ["not-a-dict", None, _make_step("real", "pending")]
        # Should not raise and should contain the real step
        diagram = build_mermaid_diagram(steps, "1")
        assert "real" in diagram

    def test_unknown_status_defaults_to_question_mark(self):
        steps = [_make_step("x", "weird-status")]
        assert "❓" in build_mermaid_diagram(steps, "1")


# ---------------------------------------------------------------------------
# visualize_handler — fallback-to-text vs photo path
# ---------------------------------------------------------------------------


def _make_ctx(user_id: str = "12345", args: list[str] | None = None):
    ctx = MagicMock()
    ctx.user_id = user_id
    ctx.channel = "telegram"
    ctx.args = args or []
    ctx.reply_text = AsyncMock(return_value="msg1")
    ctx.reply_image = AsyncMock()
    ctx.edit_message_text = AsyncMock()
    return ctx


def _make_deps(logger=None):
    from nexus.core.handlers.visualize_command_handlers import VisualizeHandlerDeps

    deps = VisualizeHandlerDeps(
        logger=logger or MagicMock(),
        allowed_user_ids=[],
        prompt_project_selection=AsyncMock(),
        ensure_project_issue=AsyncMock(return_value=("myproject", "42", [])),
    )
    return deps


_SAMPLE_STEPS = [
    {"name": "plan", "status": "complete", "agent": {"name": "planner"}},
    {"name": "impl", "status": "running", "agent": {"name": "coder"}},
]


@pytest.mark.asyncio
async def test_handler_prompts_project_selection_when_no_args():
    from nexus.core.handlers.visualize_command_handlers import visualize_handler

    ctx = _make_ctx(args=[])
    deps = _make_deps()
    await visualize_handler(ctx, deps)
    cast(AsyncMock, deps.prompt_project_selection).assert_awaited_once_with(ctx, "visualize")


@pytest.mark.asyncio
async def test_handler_sends_mermaid_text_with_steps(tmp_path, monkeypatch):
    import nexus.core.handlers.visualize_command_handlers as vch
    from nexus.core.handlers.visualize_command_handlers import visualize_handler

    ctx = _make_ctx(args=["myproject", "42"])
    deps = _make_deps()

    monkeypatch.setattr(vch.HostStateManager, "get_workflow_id_for_issue", lambda _: "wf-1")
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    workflow_file = workflows_dir / "wf-1.json"
    workflow_file.write_text(json.dumps({"steps": _SAMPLE_STEPS, "state": "running"}))
    monkeypatch.setattr(vch, "NEXUS_CORE_STORAGE_DIR", str(tmp_path))

    await visualize_handler(ctx, deps)

    ctx.edit_message_text.assert_awaited_once()
    call_kwargs = ctx.edit_message_text.call_args.kwargs
    assert "```mermaid" in call_kwargs.get("text", "")


@pytest.mark.asyncio
async def test_handler_reports_no_steps_when_workflow_missing(monkeypatch):
    import nexus.core.handlers.visualize_command_handlers as vch
    from nexus.core.handlers.visualize_command_handlers import visualize_handler

    ctx = _make_ctx(args=["myproject", "42"])
    deps = _make_deps()

    monkeypatch.setattr(vch.HostStateManager, "get_workflow_id_for_issue", lambda _: None)

    await visualize_handler(ctx, deps)

    ctx.edit_message_text.assert_awaited_once()
    call_kwargs = ctx.edit_message_text.call_args.kwargs
    assert "No workflow steps found" in call_kwargs.get("text", "")
