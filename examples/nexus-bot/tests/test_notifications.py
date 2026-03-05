"""Tests for notifications module."""

from unittest.mock import MagicMock, patch

from nexus.core.integrations.notifications import (
    InlineKeyboard,
    notify_agent_completed,
    notify_agent_needs_input,
    notify_agent_timeout,
    notify_implementation_requested,
    notify_workflow_completed,
    notify_workflow_started,
    send_notification,
)


class TestInlineKeyboard:
    """Tests for InlineKeyboard builder."""

    def test_single_button(self):
        """Test creating keyboard with single button."""
        keyboard = InlineKeyboard().add_button("Test", "test_123")
        result = keyboard.build()

        assert result == {"inline_keyboard": [[{"text": "Test", "callback_data": "test_123"}]]}

    def test_multiple_buttons_same_row(self):
        """Test multiple buttons in same row."""
        keyboard = InlineKeyboard().add_button("Button 1", "btn1").add_button("Button 2", "btn2")
        result = keyboard.build()

        assert len(result["inline_keyboard"]) == 1
        assert len(result["inline_keyboard"][0]) == 2

    def test_multiple_rows(self):
        """Test multiple rows of buttons."""
        keyboard = (
            InlineKeyboard()
            .add_button("Button 1", "btn1")
            .add_button("Button 2", "btn2")
            .new_row()
            .add_button("Button 3", "btn3")
        )
        result = keyboard.build()

        assert len(result["inline_keyboard"]) == 2
        assert len(result["inline_keyboard"][0]) == 2
        assert len(result["inline_keyboard"][1]) == 1

    def test_url_button(self):
        """Test button with URL."""
        keyboard = InlineKeyboard().add_button("GitHub", "unused", url="https://github.com")
        result = keyboard.build()

        assert result["inline_keyboard"][0][0]["text"] == "GitHub"
        assert result["inline_keyboard"][0][0]["url"] == "https://github.com"
        assert "callback_data" not in result["inline_keyboard"][0][0]

    def test_mixed_buttons(self):
        """Test mixing callback and URL buttons."""
        keyboard = (
            InlineKeyboard()
            .add_button("Action", "do_action")
            .add_button("Link", "", url="https://example.com")
        )
        result = keyboard.build()

        assert "callback_data" in result["inline_keyboard"][0][0]
        assert "url" in result["inline_keyboard"][0][1]


class TestNotificationFunctions:
    """Tests for notification helper functions."""

    @patch("nexus.core.integrations.notifications._get_notification_plugin")
    @patch("nexus.core.integrations.notifications.TELEGRAM_TOKEN", "test_token")
    @patch("nexus.core.integrations.notifications.TELEGRAM_CHAT_ID", "12345")
    def test_send_notification_success(self, mock_get_plugin):
        """Test successful notification send."""
        plugin = MagicMock()
        plugin.send_message_sync.return_value = True
        mock_get_plugin.return_value = plugin

        result = send_notification("Test message")

        assert result is True
        plugin.send_message_sync.assert_called_once_with(
            message="Test message",
            parse_mode="Markdown",
            reply_markup=None,
        )

    @patch("nexus.core.integrations.notifications._get_notification_plugin")
    @patch("nexus.core.integrations.notifications.TELEGRAM_TOKEN", "test_token")
    @patch("nexus.core.integrations.notifications.TELEGRAM_CHAT_ID", "12345")
    def test_send_notification_with_keyboard(self, mock_get_plugin):
        """Test notification with inline keyboard."""
        plugin = MagicMock()
        plugin.send_message_sync.return_value = True
        mock_get_plugin.return_value = plugin

        keyboard = InlineKeyboard().add_button("Test", "test")
        result = send_notification("Message", keyboard=keyboard)

        assert result is True
        kwargs = plugin.send_message_sync.call_args.kwargs
        assert "reply_markup" in kwargs
        assert "inline_keyboard" in kwargs["reply_markup"]

    @patch("nexus.core.integrations.notifications.TELEGRAM_TOKEN", None)
    def test_send_notification_no_credentials(self):
        """Test notification when credentials not configured."""
        result = send_notification("Test")
        assert result is False

    @patch("nexus.core.integrations.notifications._get_notification_plugin")
    @patch("nexus.core.integrations.notifications.TELEGRAM_TOKEN", "test_token")
    @patch("nexus.core.integrations.notifications.TELEGRAM_CHAT_ID", "12345")
    def test_send_notification_plugin_error(self, mock_get_plugin):
        """Test notification when plugin returns failure."""
        plugin = MagicMock()
        plugin.send_message_sync.return_value = False
        mock_get_plugin.return_value = plugin

        result = send_notification("Test")
        assert result is False

    @patch("nexus.core.integrations.notifications.send_notification")
    def test_notify_agent_needs_input(self, mock_send):
        """Test agent needs input notification."""
        mock_send.return_value = True

        result = notify_agent_needs_input("123", "TestAgent", "Preview text")

        assert result is True
        mock_send.assert_called_once()
        message = mock_send.call_args[0][0]
        assert "Agent Needs Input" in message
        assert "#123" in message
        assert "TestAgent" in message
        assert "Preview text" in message

    @patch("nexus.core.integrations.notifications.send_notification")
    def test_notify_workflow_started(self, mock_send):
        """Test workflow started notification."""
        mock_send.return_value = True

        result = notify_workflow_started("456", "test-project", "full", "feature")

        assert result is True
        mock_send.assert_called_once()
        message = mock_send.call_args[0][0]
        assert "Workflow Started" in message
        assert "#456" in message
        assert "test-project" in message
        assert "full" in message
        assert "feature" in message

    @patch("nexus.core.integrations.notifications.send_notification")
    def test_notify_agent_completed(self, mock_send):
        """Test agent completed notification."""
        mock_send.return_value = True

        result = notify_agent_completed("789", "OldAgent", "NewAgent")

        assert result is True
        message = mock_send.call_args[0][0]
        assert "Agent Completed" in message
        assert "#789" in message
        assert "OldAgent" in message
        assert "NewAgent" in message

    @patch("nexus.core.integrations.notifications.send_notification")
    def test_notify_agent_completed_legacy_agent_name_kwarg(self, mock_send):
        """Legacy agent_name kwarg should map to completed agent."""
        mock_send.return_value = True

        result = notify_agent_completed(
            issue_number="790",
            agent_name="LegacyAgent",
            next_agent="NewAgent",
        )

        assert result is True
        message = mock_send.call_args[0][0]
        assert "#790" in message
        assert "LegacyAgent" in message
        assert "NewAgent" in message

    @patch("nexus.core.integrations.notifications.send_notification")
    def test_notify_agent_timeout_with_retry(self, mock_send):
        """Test agent timeout notification when retry will happen."""
        mock_send.return_value = True

        result = notify_agent_timeout("111", "TimeoutAgent", will_retry=True)

        assert result is True
        message = mock_send.call_args[0][0]
        assert "Timeout" in message
        assert "Retrying" in message
        assert "#111" in message
        assert "TimeoutAgent" in message

    @patch("nexus.core.integrations.notifications.send_notification")
    def test_notify_agent_timeout_no_retry(self, mock_send):
        """Test agent timeout notification when no retry."""
        mock_send.return_value = True

        result = notify_agent_timeout("222", "FailedAgent", will_retry=False)

        assert result is True
        message = mock_send.call_args[0][0]
        assert "Failed" in message
        assert "Max Retries" in message
        assert "#222" in message
        assert "FailedAgent" in message

    @patch("nexus.core.integrations.notifications.send_notification")
    def test_notify_agent_timeout_unresolved_agent(self, mock_send):
        """Unknown agent identity should not render Max Retries @unknown."""
        mock_send.return_value = True

        result = notify_agent_timeout("333", "unknown", will_retry=False)

        assert result is True
        message = mock_send.call_args[0][0]
        assert "Unresolved Agent" in message
        assert "Max Retries" not in message
        assert "Agent: n/a" in message

    @patch("nexus.core.integrations.notifications.send_notification")
    def test_notify_workflow_completed(self, mock_send):
        """Test workflow completed notification."""
        mock_send.return_value = True

        result = notify_workflow_completed("333", "my-project")

        assert result is True
        message = mock_send.call_args[0][0]
        assert "Workflow Completed" in message
        assert "#333" in message
        assert "my-project" in message

    @patch("nexus.core.integrations.notifications.send_notification")
    def test_notify_implementation_requested(self, mock_send):
        """Test implementation requested notification."""
        mock_send.return_value = True

        result = notify_implementation_requested("444", "developer@example.com")

        assert result is True
        message = mock_send.call_args[0][0]
        assert "Implementation Requested" in message
        assert "#444" in message
        assert "developer@example.com" in message
