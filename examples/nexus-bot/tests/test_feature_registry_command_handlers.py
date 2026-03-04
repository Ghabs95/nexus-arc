import logging

import pytest
from nexus.core.handlers.feature_registry_command_handlers import (
    FeatureRegistryCommandDeps,
    feature_done_handler,
    feature_forget_handler,
    feature_list_handler,
)


class _Ctx:
    def __init__(self, *, args=None, user_id: int = 1):
        self.args = list(args or [])
        self.user_id = user_id
        self.replies = []

    async def reply_text(self, text, **_kwargs):
        self.replies.append(text)


class _RegistryStub:
    def __init__(self):
        self.enabled = True
        self.rows = []

    def is_enabled(self):
        return self.enabled

    def upsert_feature(self, **kwargs):
        title = kwargs["canonical_title"]
        feature_id = kwargs.get("feature_id") or f"feat_{len(self.rows) + 1}"
        row = {
            "feature_id": feature_id,
            "canonical_title": title,
            "source_issue": kwargs.get("source_issue", ""),
        }
        self.rows.append(row)
        return row

    def list_features(self, _project_key):
        return list(self.rows)

    def forget_feature(self, *, project_key, feature_ref):
        _ = project_key
        for idx, row in enumerate(self.rows):
            if row["feature_id"] == feature_ref or row["canonical_title"] == feature_ref:
                return self.rows.pop(idx)
        return None


def _deps(registry):
    return FeatureRegistryCommandDeps(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
        iter_project_keys=lambda: ["nexus"],
        normalize_project_key=lambda value: str(value or "").strip().lower() or None,
        get_project_label=lambda key: "Nexus" if key == "nexus" else key,
        feature_registry=registry,
    )


@pytest.mark.asyncio
async def test_feature_done_adds_record_and_echoes_feature_id():
    registry = _RegistryStub()
    ctx = _Ctx(args=["nexus", "Two-factor", "auth"])

    await feature_done_handler(ctx, _deps(registry))

    assert registry.rows == [
        {
            "feature_id": "feat_1",
            "canonical_title": "Two-factor auth",
            "source_issue": "manual",
        }
    ]
    assert "Added implemented feature" in ctx.replies[-1]
    assert "feat_1" in ctx.replies[-1]


@pytest.mark.asyncio
async def test_feature_list_is_deterministic_with_source_issue_suffix():
    registry = _RegistryStub()
    registry.rows = [
        {"feature_id": "feat_7", "canonical_title": "SLA alerts", "source_issue": "88"},
        {"feature_id": "feat_8", "canonical_title": "Runbook bot", "source_issue": ""},
    ]
    ctx = _Ctx(args=["nexus"])

    await feature_list_handler(ctx, _deps(registry))

    expected = (
        "📚 Implemented features for *Nexus*:\n"
        "- SLA alerts (`feat_7`) • issue `88`\n"
        "- Runbook bot (`feat_8`)"
    )
    assert ctx.replies[-1] == expected


@pytest.mark.asyncio
async def test_feature_forget_removes_by_title_then_reports_missing():
    registry = _RegistryStub()
    registry.rows = [
        {"feature_id": "feat_1", "canonical_title": "SLA alerts", "source_issue": "manual"}
    ]
    deps = _deps(registry)

    remove_ctx = _Ctx(args=["nexus", "SLA", "alerts"])
    await feature_forget_handler(remove_ctx, deps)
    assert "Removed implemented feature" in remove_ctx.replies[-1]
    assert registry.rows == []

    missing_ctx = _Ctx(args=["nexus", "SLA", "alerts"])
    await feature_forget_handler(missing_ctx, deps)
    assert "Feature not found" in missing_ctx.replies[-1]


@pytest.mark.asyncio
async def test_registry_disabled_short_circuits_commands():
    registry = _RegistryStub()
    registry.enabled = False
    deps = _deps(registry)

    done_ctx = _Ctx(args=["nexus", "Title"])
    list_ctx = _Ctx(args=["nexus"])
    forget_ctx = _Ctx(args=["nexus", "feat_1"])

    await feature_done_handler(done_ctx, deps)
    await feature_list_handler(list_ctx, deps)
    await feature_forget_handler(forget_ctx, deps)

    for ctx in (done_ctx, list_ctx, forget_ctx):
        assert "Feature registry is disabled" in ctx.replies[-1]


@pytest.mark.asyncio
async def test_usage_messages_when_project_or_args_invalid():
    registry = _RegistryStub()
    deps = _deps(registry)

    done_ctx = _Ctx(args=["missing", "Title"])
    list_ctx = _Ctx(args=[])
    forget_ctx = _Ctx(args=["nexus"])

    await feature_done_handler(done_ctx, deps)
    await feature_list_handler(list_ctx, deps)
    await feature_forget_handler(forget_ctx, deps)

    assert done_ctx.replies[-1] == "Usage: `/feature_done <project> <title>`"
    assert list_ctx.replies[-1] == "Usage: `/feature_list <project>`"
    assert forget_ctx.replies[-1] == "Usage: `/feature_forget <project> <feature_id|title>`"
