# Installing nexus-arc

**nexus-arc** (Agentic Runtime Core) is designed to be installed as a foundational Python framework for building
autonomous agent systems and bots.

## Requirements

- Python 3.14.3 or higher

## Basic Installation

You can install the core framework directly using `pip`:

```bash
pip install nexus-arc
```

*(Note: For local development from source, use `pip install -e .`)*

## Optional Dependencies (Extras)

Nexus ARC relies heavily on optional dependencies to keep the base installation lightweight. Install only the components
you need for your use case:

```bash
# Add PostgreSQL storage backend support
pip install nexus-arc[postgres]

# Add Redis storage backend support
pip install nexus-arc[redis]

# Add Slack integration
pip install nexus-arc[slack]

# Add Telegram and Discord Bots capabilities (includes webhooks and interactive UI)
pip install "nexus-arc[nexus-telegram-bot,nexus-discord-bot]"

# Add AI/LLM providers (e.g. OpenAI)
pip install "nexus-arc[ai]"

# Add Whisper for Audio/Voice processing
pip install "nexus-arc[whisper]"

# Install multiple extras at once
pip install "nexus-arc[postgres,redis,nexus-telegram-bot,nexus-discord-bot,ai]"
```

## Using Nexus ARC in your project

Once installed, you can import and initialize the core components:

```python
from nexus.core.workflow_engine import WorkflowEngine
from nexus.core.events import EventBus

bus = EventBus()
engine = WorkflowEngine(bus)
```

For a comprehensive guide on building a bot using Nexus ARC, see the examples directory, such as
`examples/nexus-bot/INSTALL.md`.
