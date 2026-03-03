# ADR 001: Interactive Client Plugin Architecture

## Context

Currently, the `nexus-arc` framework supports one-way notification channels (e.g., sending alerts to Telegram via raw
HTTP requests). However, the complex, two-way interactive chat functionality (receiving messages, routing commands,
pressing inline buttons) is heavily hardcoded in a downstream implementation (`nexus/src/telegram_bot.py`).

To fulfill the vision of `nexus-arc` as a turnkey AI Agency framework, users should be able to leverage chat
interfaces (Telegram, Discord, Slack) out-of-the-box. This will allow users to invoke agentic skills directly from their
phones/clients (e.g., `/direct TechLead use @awesome-skill`).

## Decision

We will introduce a new plugin interface: `InteractiveClientPlugin`.

This interface abstracts the concepts of:

1. **Event Listening** (Polling or Webhooks)
2. **Command Routing** (Mapping `/commands` to framework callbacks)
3. **Message Routing** (Forwarding plain text to AI Orchestrators)
4. **Interactive Responses** (Sending text with contextual buttons/keyboards)

### The Interface Contract

Any interactive client (Telegram, Discord, Slack, CLI) must implement:

```python
class InteractiveClientPlugin(Plugin):
    @abstractmethod
    async def start(self) -> None:
        """Begin listening for events from the provider."""
        pass
        
    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shutdown the listener."""
        pass
        
    @abstractmethod
    def register_command(self, command: str, callback: Callable) -> None:
        """Bind a slash command to a framework action."""
        pass
        
    @abstractmethod
    def register_message_handler(self, callback: Callable) -> None:
        """Bind general text messages to a framework orchestrator."""
        pass
        
    @abstractmethod
    async def send_interactive(self, user_id: str, message: str, actions: List[Dict]) -> str:
        """Send a message with interactive buttons/actions."""
        pass
```

## Consequences

### Positive

- **Provider Agnostic**: The core framework will not care if a message came from Discord or Telegram. It simply receives
  a normalized `Message` object.
- **Plug-and-Play**: Users can spin up an agentic chat interface with zero boilerplate.
- **Skill Usage**: This drastically lowers the friction of using AI skills on-the-go.

### Negative

- **Dependency Weight**: Integrating official SDKs (like `python-telegram-bot` or `discord.py`) into the framework might
  bloat `nexus-arc`. We will mitigate this by using optional dependency extras (e.g.,
  `pip install nexus-arc[telegram]`).
- **Loss of Hyper-Specificity**: Moving the bot to the core means the framework cannot hardcode project-specific logic.
  Callbacks must be injected dynamically via configuration.
