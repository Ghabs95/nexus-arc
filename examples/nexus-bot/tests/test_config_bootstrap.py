"""Tests for explicit config bootstrap lifecycle helpers."""

from unittest.mock import MagicMock

import nexus.core.config as config
import nexus.core.config.bootstrap as bootstrap


def test_bootstrap_environment_loads_env_once(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("EXAMPLE_KEY=value\n", encoding="utf-8")

    load_mock = MagicMock()
    monkeypatch.setattr(bootstrap, "load_dotenv", load_mock)
    monkeypatch.setattr(bootstrap, "_bootstrapped", False)

    assert bootstrap.bootstrap_environment(str(env_file)) is True
    assert bootstrap.bootstrap_environment(str(env_file)) is True
    load_mock.assert_called_once_with(str(env_file))


def test_bootstrap_environment_returns_false_when_file_missing(tmp_path, monkeypatch):
    missing_file = tmp_path / ".env-missing"
    load_mock = MagicMock()
    monkeypatch.setattr(bootstrap, "load_dotenv", load_mock)
    monkeypatch.setattr(bootstrap, "_bootstrapped", False)

    assert bootstrap.bootstrap_environment(str(missing_file)) is False
    load_mock.assert_not_called()


def test_initialize_runtime_skips_logging_when_disabled(monkeypatch):
    called = {"log": 0, "dirs": 0, "bootstrap": 0}

    def _bootstrap(secret_file: str = ".env") -> bool:
        called["bootstrap"] += 1
        return True

    monkeypatch.setattr(bootstrap, "bootstrap_environment", _bootstrap)
    monkeypatch.setattr(config, "configure_runtime_logging", lambda **_kwargs: called.__setitem__("log", called["log"] + 1))
    monkeypatch.setattr(
        config,
        "initialize_runtime_directories",
        lambda: called.__setitem__("dirs", called["dirs"] + 1),
    )

    bootstrap.initialize_runtime(configure_logging=False)
    assert called == {"log": 0, "dirs": 1, "bootstrap": 1}


def test_initialize_runtime_runs_logging_when_enabled(monkeypatch):
    called = {"log": 0, "dirs": 0, "bootstrap": 0}

    def _bootstrap(secret_file: str = ".env") -> bool:
        called["bootstrap"] += 1
        return True

    monkeypatch.setattr(bootstrap, "bootstrap_environment", _bootstrap)
    monkeypatch.setattr(config, "configure_runtime_logging", lambda **_kwargs: called.__setitem__("log", called["log"] + 1))
    monkeypatch.setattr(
        config,
        "initialize_runtime_directories",
        lambda: called.__setitem__("dirs", called["dirs"] + 1),
    )

    bootstrap.initialize_runtime(configure_logging=True)
    assert called == {"log": 1, "dirs": 1, "bootstrap": 1}
