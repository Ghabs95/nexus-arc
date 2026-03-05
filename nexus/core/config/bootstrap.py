"""Explicit runtime bootstrap for config environment and startup side effects."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

_bootstrapped = False


def bootstrap_environment(secret_file: str = ".env") -> bool:
    """Load environment from local secret file once.

    Returns True when a file was loaded, False otherwise.
    """
    global _bootstrapped
    if _bootstrapped:
        return bool(os.path.exists(secret_file))

    loaded = False
    if os.path.exists(secret_file):
        load_dotenv(secret_file)
        loaded = True
        logging.info(f"Loaded environment from {secret_file}")
    else:
        logging.info(f"No {secret_file} found, relying on shell environment")

    _bootstrapped = True
    return loaded


def initialize_runtime(*, secret_file: str = ".env", configure_logging: bool = True) -> None:
    """Initialize runtime explicitly for executable entrypoints."""
    bootstrap_environment(secret_file=secret_file)
    from nexus.core.config import configure_runtime_logging, initialize_runtime_directories

    if configure_logging:
        configure_runtime_logging()
    initialize_runtime_directories()
