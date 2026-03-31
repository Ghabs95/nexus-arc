import sys
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_chat_bridge_callback_emits_feedback(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", MagicMock())
    from nexus.core.command_bridge.router import CommandRouter

    router = CommandRouter()
    
    mock_client = MagicMock()
    mock_client.name = "telegram"
    
    class FakeMessage:
        message_id = "12345"
    class FakeRawEvent:
        message = FakeMessage()

    mock_raw_event = FakeRawEvent()
    mock_user_state = {}
    
    router.hands_free_deps = MagicMock()
    router.hands_free_deps.router_feedback_config = {"router_url": "http://test"}

    with patch("nexus.core.command_bridge.router.route_hands_free_text", new_callable=AsyncMock) as mock_route:
        mock_route.return_value = {"message": "Hello", "decision_id": "test_id"}
        
        with patch("nexus.core.command_bridge.router.maybe_send_feedback_prompt", new_callable=AsyncMock) as mock_feedback:
            cb = router._chat_bridge_callback()
            await cb(
                client=mock_client,
                user_id="user1",
                text="hello",
                args=["hello"],
                raw_event=mock_raw_event,
                attachments=None,
                user_state=mock_user_state
            )
            
            mock_route.assert_called_once()
            mock_feedback.assert_called_once()
            args, kwargs = mock_feedback.call_args
            assert kwargs["source_message_id"] == "12345"
            assert kwargs["result"] == {"message": "Hello", "decision_id": "test_id"}
            assert kwargs["feedback_config"] == {"router_url": "http://test"}
