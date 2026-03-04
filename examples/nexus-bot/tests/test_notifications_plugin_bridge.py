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
