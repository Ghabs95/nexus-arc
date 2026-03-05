"""Error handling utilities with retry logic and exponential backoff.

This module provides reusable error handling patterns for external service calls
like CLI tools, file I/O, and API requests.
"""

import logging
import subprocess
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)


class RetryExhaustedError(Exception):
    """Raised when all retry attempts are exhausted."""

    pass


class ConfigurationError(Exception):
    """Raised when configuration validation fails."""

    pass


def retry_with_backoff(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """Decorator that retries a function with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts (default: 3)
        base_delay: Initial delay in seconds (default: 1.0)
        max_delay: Maximum delay between retries (default: 30.0)
        exponential_base: Base for exponential backoff (default: 2.0)
        exceptions: Tuple of exceptions to catch and retry (default: all Exception)

    Example:
        @retry_with_backoff(max_attempts=5, base_delay=2.0)
        def call_service_api():
            return subprocess.run(["tool", "action"], check=True)
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            attempt = 1
            while attempt <= max_attempts:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    extra_context = ""
                    if isinstance(e, subprocess.CalledProcessError):
                        stderr = (e.stderr or "").strip()
                        stdout = (e.output or "").strip()
                        if stderr:
                            extra_context = f" stderr={stderr[:300]}"
                        elif stdout:
                            extra_context = f" stdout={stdout[:300]}"

                    if attempt == max_attempts:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}.{extra_context}",
                            exc_info=True,
                        )
                        raise RetryExhaustedError(
                            f"Failed after {max_attempts} attempts: {str(e)}"
                        ) from e

                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (exponential_base ** (attempt - 1)), max_delay)

                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}.{extra_context} "
                        f"Retrying in {delay:.1f}s..."
                    )

                    time.sleep(delay)
                    attempt += 1

            # This should never be reached, but just in case
            raise RetryExhaustedError(f"Unexpected retry exhaustion for {func.__name__}")

        return wrapper

    return decorator


def run_command_with_retry(
    cmd: list[str],
    max_attempts: int = 3,
    timeout: int = 30,
    check: bool = True,
    **subprocess_kwargs,
) -> subprocess.CompletedProcess:
    """Run a subprocess command with automatic retry and timeout.

    Args:
        cmd: Command to run as list of strings
        max_attempts: Maximum retry attempts
        timeout: Command timeout in seconds
        check: Whether to check return code
        **subprocess_kwargs: Additional arguments for subprocess.run

    Returns:
        CompletedProcess result

    Raises:
        RetryExhaustedError: If all attempts fail
        subprocess.TimeoutExpired: If command times out

    Example:
        result = run_command_with_retry(
            ["tool", "command", "args"],
            max_attempts=5,
            timeout=60
        )
    """

    @retry_with_backoff(
        max_attempts=max_attempts, exceptions=(subprocess.CalledProcessError, FileNotFoundError)
    )
    def _run():
        return subprocess.run(
            cmd, check=check, timeout=timeout, capture_output=True, text=True, **subprocess_kwargs
        )

    try:
        return _run()
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out after {timeout}s: {' '.join(cmd)}")
        raise
    except FileNotFoundError as e:
        logger.error(f"Command not found: {cmd[0]}. Please install it.")
        raise ConfigurationError(f"Required command '{cmd[0]}' not found") from e


def safe_file_write(filepath: str, content: str, encoding: str = "utf-8") -> bool:
    """Safely write to a file with error handling.

    Args:
        filepath: Path to file
        content: Content to write
        encoding: File encoding (default: utf-8)

    Returns:
        True if successful, False otherwise
    """
    try:
        with open(filepath, "w", encoding=encoding) as f:
            f.write(content)
        return True
    except OSError as e:
        logger.error(f"Failed to write to {filepath}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error writing to {filepath}: {e}", exc_info=True)
        return False


def safe_file_read(filepath: str, encoding: str = "utf-8", default: str = "") -> str:
    """Safely read from a file with error handling.

    Args:
        filepath: Path to file
        encoding: File encoding (default: utf-8)
        default: Default value if read fails (default: empty string)

    Returns:
        File contents or default value
    """
    try:
        with open(filepath, encoding=encoding) as f:
            return f.read()
    except FileNotFoundError:
        logger.debug(f"File not found: {filepath}, returning default")
        return default
    except OSError as e:
        logger.error(f"Failed to read {filepath}: {e}")
        return default
    except Exception as e:
        logger.error(f"Unexpected error reading {filepath}: {e}", exc_info=True)
        return default


def validate_required_env_vars(required_vars: list[str]) -> None:
    """Validate that required environment variables are set.

    Args:
        required_vars: List of required environment variable names

    Raises:
        ConfigurationError: If any required variable is missing
    """
    import os

    missing = [var for var in required_vars if not os.getenv(var)]

    if missing:
        error_msg = f"Missing required environment variables: {', '.join(missing)}"
        logger.error(error_msg)
        raise ConfigurationError(error_msg)

    logger.info(f"✅ All required environment variables present: {', '.join(required_vars)}")


def format_error_for_user(error: Exception, context: str = "") -> str:
    """Format error message for end-user display.

    Args:
        error: Exception object
        context: Additional context about what was being done

    Returns:
        User-friendly error message
    """
    error_type = type(error).__name__
    error_msg = str(error)

    # Common error patterns with user-friendly messages
    if isinstance(error, subprocess.TimeoutExpired):
        return f"⏱️ Operation timed out after {error.timeout}s. The service might be slow. Try again in a moment."

    elif isinstance(error, FileNotFoundError):
        tool = error_msg.split("'")[1] if "'" in error_msg else "unknown"
        return f"🔧 Required tool '{tool}' not found. Please contact administrator."

    elif isinstance(error, RetryExhaustedError):
        return f"🔄 Operation failed after multiple retries. {context}. Please try again later."

    elif isinstance(error, ConfigurationError):
        return f"⚙️ Configuration error: {error_msg}. Please contact administrator."

    elif "rate limit" in error_msg.lower():
        return "⏳ Rate limit reached for the platform. Please wait a few minutes and try again."

    elif "not found" in error_msg.lower() and "issue" in error_msg.lower():
        return "🔍 Issue not found. Please check the issue number and try again."

    else:
        # Generic error with some details but not too technical
        short_msg = error_msg[:100] + "..." if len(error_msg) > 100 else error_msg
        return f"❌ {error_type}: {short_msg}. {context}"
