"""
Example usage of the built-in TelegramInteractivePlugin in nexus-arc.

Set TELEGRAM_TOKEN to test locally:
    TELEGRAM_TOKEN=123:abc python examples/interactive_bot.py
"""

import asyncio
import logging
import os
from typing import Any

from nexus.adapters.registry import AdapterRegistry

logging.basicConfig(level=logging.INFO)


# Dummy handler
async def handle_hello(user_id: str, text: str, context: Any, raw_event: Any) -> None:
    logging.info(f"Received /hello from {user_id} with text: {text}")


async def main():
    registry = AdapterRegistry()
    bot_token = os.environ.get("TELEGRAM_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")

    config = {
        "interactive_clients": [
            {
                "type": "telegram-interactive-http",
                "config": {
                    "bot_token": bot_token,
                },
            }
        ]
    }

    # Load from registry
    adapters = registry.from_config(config)

    if not adapters.interactive_clients:
        logging.error("No interactive clients loaded!")
        return

    plugin = adapters.interactive_clients[0]
    logging.info(f"Loaded plugin: {plugin.name}")

    plugin.register_command("hello", handle_hello)

    try:
        logging.info("Calling start() to test Application initialization and token format...")
        await plugin.start()
        logging.info("Started successfully! In a real scenario, this would now be polling.")
    except Exception as e:
        # A fake token will raise telegram.error.InvalidToken, which confirms it works!
        logging.info(f"Start failed with expected error due to fake token: {e}")
    finally:
        await plugin.stop()


if __name__ == "__main__":
    asyncio.run(main())
