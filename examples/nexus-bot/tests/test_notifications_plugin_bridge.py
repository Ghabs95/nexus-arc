"""Tests for plugin bridge behavior in notifications module."""


class _FakePlugin:
    def __init__(self):
        self.calls = 0

    def send_alert_sync(self, message, severity="info"):
        self.calls += 1
        return True


def test_emit_alert_uses_plugin(monkeypatch):
    import nexus.core.integrations.notifications as notifications

    fake = _FakePlugin()

    monkeypatch.setattr(notifications, "_get_notification_plugin", lambda: fake)
    monkeypatch.setattr(notifications, "send_notification", lambda *args, **kwargs: False)

    ok = notifications.emit_alert("hello")

    assert ok is True
    assert fake.calls == 1


def test_emit_alert_sync_path_uses_background_eventbus_bridge(monkeypatch):
    import nexus.core.integrations.notifications as notifications
    import nexus.core.orchestration.nexus_core_helpers as helpers

    captured: dict[str, object] = {}

    class _FakeBus:
        def subscriber_count(self, event_type: str) -> int:
            if event_type == "system.alert":
                return 1
            return 0

        async def emit(self, event) -> None:
            captured["emitted"] = event

    def _fake_emit_eventbus_sync(*, bus, event, timeout=10.0):
        captured["bus"] = bus
        captured["event"] = event

    monkeypatch.setattr(helpers, "get_event_bus", lambda: _FakeBus())
    monkeypatch.setattr(notifications, "_emit_eventbus_sync", _fake_emit_eventbus_sync)

    ok = notifications.emit_alert(
        "workflow warning on issue #110",
        severity="warning",
        source="unit-test",
        project_key="nexus",
    )

    assert ok is True
    assert "bus" in captured
    event = captured["event"]
    assert getattr(event, "event_type", "") == "system.alert"
    assert getattr(event, "severity", "") == "warning"
    assert getattr(event, "project_key", "") == "nexus"
