"""Unit tests for error_handling module.

Tests retry logic, error formatting, and configuration validation.
"""

import subprocess
from unittest.mock import patch

import pytest
from nexus.core.error_handling import (
    ConfigurationError,
    RetryExhaustedError,
    format_error_for_user,
    retry_with_backoff,
    run_command_with_retry,
    safe_file_read,
    safe_file_write,
    validate_required_env_vars,
)


class TestRetryWithBackoff:
    """Tests for retry_with_backoff decorator."""

    def test_successful_call_no_retry(self):
        """Test that successful call doesn't retry."""
        call_count = 0

        @retry_with_backoff(max_attempts=3)
        def success_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = success_func()
        assert result == "success"
        assert call_count == 1

    def test_retry_on_failure_then_success(self):
        """Test that function retries on failure then succeeds."""
        call_count = 0

        @retry_with_backoff(max_attempts=3, base_delay=0.01)
        def intermittent_func():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("Intermittent error")
            return "success"

        result = intermittent_func()
        assert result == "success"
        assert call_count == 2

    def test_exhausted_retries(self):
        """Test that RetryExhaustedError is raised after max attempts."""

        @retry_with_backoff(max_attempts=3, base_delay=0.01)
        def always_fails():
            raise ValueError("Always fails")

        with pytest.raises(RetryExhaustedError):
            always_fails()

    def test_specific_exceptions_only(self):
        """Test that only specific exceptions are retried."""

        @retry_with_backoff(max_attempts=3, exceptions=(ValueError,))
        def raises_different_error():
            raise TypeError("Not retryable")

        # Should not catch TypeError, should raise immediately
        with pytest.raises(TypeError):
            raises_different_error()


class TestRunCommandWithRetry:
    """Tests for run_command_with_retry function."""

    @patch("subprocess.run")
    def test_successful_command(self, mock_run):
        """Test successful command execution."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["echo", "test"], returncode=0, stdout="test\n", stderr=""
        )

        result = run_command_with_retry(["echo", "test"], max_attempts=3, timeout=10)
        assert result.returncode == 0
        assert result.stdout == "test\n"
        assert mock_run.call_count == 1

    @patch("subprocess.run")
    def test_command_retry_on_failure(self, mock_run):
        """Test command retries on failure."""
        # First two calls fail, third succeeds
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, ["test"]),
            subprocess.CalledProcessError(1, ["test"]),
            subprocess.CompletedProcess(args=["test"], returncode=0, stdout="success", stderr=""),
        ]

        result = run_command_with_retry(["test"], max_attempts=3, timeout=10)
        assert result.returncode == 0
        assert mock_run.call_count == 3


class TestValidation:
    """Tests for configuration validation functions."""

    def test_validate_required_env_vars_success(self, monkeypatch):
        """Test successful environment variable validation."""
        monkeypatch.setenv("TEST_VAR1", "value1")
        monkeypatch.setenv("TEST_VAR2", "value2")

        # Should not raise
        validate_required_env_vars(["TEST_VAR1", "TEST_VAR2"])

    def test_validate_required_env_vars_missing(self, monkeypatch):
        """Test validation failure when env vars missing."""
        monkeypatch.setenv("TEST_VAR1", "value1")

        with pytest.raises(ConfigurationError):
            validate_required_env_vars(["TEST_VAR1", "MISSING_VAR"])


class TestErrorFormatting:
    """Tests for format_error_for_user function."""

    def test_timeout_error_formatting(self):
        """Test user-friendly message for timeout errors."""
        error = subprocess.TimeoutExpired(cmd=["test"], timeout=30)
        msg = format_error_for_user(error, "test context")

        assert "⏱️" in msg
        assert "30" in msg
        assert "timed out" in msg.lower()

    def test_retry_exhausted_error_formatting(self):
        """Test user-friendly message for retry exhausted."""
        error = RetryExhaustedError("Failed after retries")
        msg = format_error_for_user(error, "test context")

        assert "🔄" in msg
        assert "retries" in msg.lower()

    def test_configuration_error_formatting(self):
        """Test user-friendly message for config errors."""
        error = ConfigurationError("Missing config")
        msg = format_error_for_user(error)

        assert "⚙️" in msg
        assert "Configuration error" in msg


class TestFileOperations:
    """Tests for safe file operations."""

    def test_safe_file_write_success(self, tmp_path):
        """Test successful file write."""
        test_file = tmp_path / "test.txt"
        content = "Test content"

        result = safe_file_write(str(test_file), content)
        assert result is True
        assert test_file.read_text() == content

    def test_safe_file_read_success(self, tmp_path):
        """Test successful file read."""
        test_file = tmp_path / "test.txt"
        content = "Test content"
        test_file.write_text(content)

        result = safe_file_read(str(test_file))
        assert result == content

    def test_safe_file_read_missing_file(self, tmp_path):
        """Test reading non-existent file returns default."""
        nonexistent = tmp_path / "nonexistent.txt"

        result = safe_file_read(str(nonexistent), default="default_value")
        assert result == "default_value"
