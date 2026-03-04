import pytest

from nexus.core.callbacks import callback_inline_service as svc


def test_parse_inline_action_requires_payload():
    assert svc.parse_inline_action("approve") is None


def test_parse_inline_action_parses_issue_and_project():
    assert svc.parse_inline_action("approve_#42|proj") == ("approve", "42", "proj")


class _Ctx:
    def __init__(self):
        self.messages = []

    async def edit_message_text(self, text):
        self.messages.append(text)


@pytest.mark.asyncio
async def test_handle_merge_queue_inline_action_no_matching_entries(monkeypatch):
    monkeypatch.setattr(svc.HostStateManager, "load_merge_queue", lambda: {})
    ctx = _Ctx()
    handled = await svc.handle_merge_queue_inline_action(
        ctx, action="mqapprove", issue_num="42", project_hint="proj"
    )
    assert handled is True
    assert "No merge-queue entries updated" in ctx.messages[-1]


@pytest.mark.asyncio
async def test_handle_merge_queue_inline_action_updates_and_confirms(monkeypatch):
    queue = {
        "https://example/pr/1": {
            "issue": "42",
            "project": "proj",
            "status": "pending_manual_review",
            "review_mode": "manual",
        }
    }
    monkeypatch.setattr(svc.HostStateManager, "load_merge_queue", lambda: queue)
    calls = []

    def _update(pr_url, **kwargs):
        calls.append((pr_url, kwargs))
        return {"ok": True}

    monkeypatch.setattr(svc.HostStateManager, "update_merge_candidate", _update)
    ctx = _Ctx()
    handled = await svc.handle_merge_queue_inline_action(
        ctx, action="mqapprove", issue_num="42", project_hint="proj"
    )
    assert handled is True
    assert calls[0][0] == "https://example/pr/1"
    assert calls[0][1]["status"] == "pending_auto_merge"
    assert "Merge approved" in ctx.messages[-1]
