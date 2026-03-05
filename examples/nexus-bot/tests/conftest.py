"""Pytest configuration and shared fixtures."""

import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add runtime src and repo root directories to Python path for imports.
src_path = Path(__file__).parent.parent / "src"
repo_root = Path(__file__).resolve().parents[3]
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

_REPO_ROOT = Path(__file__).parent.parent
_REPO_DATA_DIR = _REPO_ROOT / "data"
_REPO_LOGS_DIR = _REPO_ROOT / "logs"
_REPO_DATA_DIR_EXISTED = _REPO_DATA_DIR.exists()
_REPO_LOGS_DIR_EXISTED = _REPO_LOGS_DIR.exists()

_TEST_RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix="nexus-telegram-tests-"))
_TEST_DATA_DIR = _TEST_RUNTIME_ROOT / "data"
_TEST_LOGS_DIR = _TEST_RUNTIME_ROOT / "logs"


# Bootstrap mandatory config env vars early (during test collection/import).
_BOOTSTRAP_PROJECT_CONFIG = Path(__file__).parent / "_bootstrap_project_config.yaml"
if not _BOOTSTRAP_PROJECT_CONFIG.exists():
    _BOOTSTRAP_PROJECT_CONFIG.write_text(
        """
nexus:
    agents_dir: sample/nexus-agents
    workspace: sample/nexus
    git_repo: sample-org/nexus-repo
test-project:
    agents_dir: sample/test-agents
    workspace: sample/test
    git_repo: sample-org/test-repo
my-project:
    agents_dir: sample/my-agents
    workspace: sample/my
    git_repo: sample-org/my-repo
sample_core:
    agents_dir: sample/agents
    workspace: sample/core
    git_repo: sample-org/core-repo
sample_app:
    agents_dir: sample/app-agents
    workspace: sample/app
    git_repo: sample-org/app-repo
sample_ops:
    agents_dir: sample/ops-agents
    workspace: sample/ops
    git_repo: sample-org/ops-repo
""".strip() + "\n",
        encoding="utf-8",
    )

os.environ.setdefault("PROJECT_CONFIG_PATH", str(_BOOTSTRAP_PROJECT_CONFIG))
os.environ.setdefault("BASE_DIR", "/tmp/test_nexus")
os.environ.setdefault("DATA_DIR", str(_TEST_DATA_DIR))
os.environ.setdefault("LOGS_DIR", str(_TEST_LOGS_DIR))
os.environ.setdefault("NEXUS_RUNTIME_DIR", str(_TEST_RUNTIME_ROOT))


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch, tmp_path):
    """Auto-use fixture to set required environment variables for all tests."""
    monkeypatch.setenv("TELEGRAM_TOKEN", "test_token_123")
    monkeypatch.setenv("OPENAI_API_KEY", "test_openai_key")
    monkeypatch.setenv("GEMINI_API_KEY", "test_gemini_key")
    monkeypatch.setenv("ALLOWED_USER", "12345")
    monkeypatch.setenv("BASE_DIR", "/tmp/test_nexus")
    monkeypatch.setenv("DATA_DIR", str(_TEST_DATA_DIR))
    monkeypatch.setenv("LOGS_DIR", str(_TEST_LOGS_DIR))
    monkeypatch.setenv("NEXUS_RUNTIME_DIR", str(_TEST_RUNTIME_ROOT))

    # Create minimal project config for tests with multiple projects
    project_config_file = tmp_path / "project_config.yaml"
    project_config_file.write_text("""
    nexus:
        agents_dir: sample/nexus-agents
        workspace: sample/nexus
        git_repo: sample-org/nexus-repo
    test-project:
        agents_dir: sample/test-agents
        workspace: sample/test
        git_repo: sample-org/test-repo
    my-project:
        agents_dir: sample/my-agents
        workspace: sample/my
        git_repo: sample-org/my-repo
    sample_core:
        agents_dir: sample/agents
        workspace: sample/core
        git_repo: sample-org/core-repo
    sample_app:
        agents_dir: sample/app-agents
        workspace: sample/app
        git_repo: sample-org/app-repo
    sample_ops:
        agents_dir: sample/ops-agents
        workspace: sample/ops
        git_repo: sample-org/ops-repo
    """)

    monkeypatch.setenv("PROJECT_CONFIG_PATH", str(project_config_file))


@pytest.fixture(autouse=True)
def mock_audit_log():
    """Auto-use fixture to mock AuditStore.audit_log during tests."""
    with patch("nexus.core.audit_store.AuditStore.audit_log"):
        yield


@pytest.fixture
def temp_data_dir(tmp_path, monkeypatch):
    """Create a temporary data directory for tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    return data_dir


@pytest.fixture
def sample_audit_log(tmp_path):
    """Create a sample audit.log file for testing."""
    log_file = tmp_path / "audit.log"
    content = """2026-02-16T10:00:00 | Issue #1 | WORKFLOW_STARTED | Started full tier workflow
2026-02-16T10:01:00 | Issue #1 | AGENT_LAUNCHED | Launched @ProjectLead agent (PID: 12345)
2026-02-16T10:15:00 | Issue #1 | AGENT_LAUNCHED | Launched @Copilot agent (PID: 12346)
2026-02-16T10:30:00 | Issue #1 | WORKFLOW_COMPLETED | Workflow finished successfully
2026-02-16T11:00:00 | Issue #2 | WORKFLOW_STARTED | Started shortened tier workflow
2026-02-16T11:01:00 | Issue #2 | AGENT_LAUNCHED | Launched @ProjectLead agent (PID: 12347)
2026-02-16T11:16:00 | Issue #2 | AGENT_TIMEOUT_KILL | @ProjectLead timed out, killed process
2026-02-16T11:17:00 | Issue #2 | AGENT_RETRY | Retrying @ProjectLead (attempt 1/2)
2026-02-16T11:18:00 | Issue #2 | AGENT_LAUNCHED | Launched @ProjectLead agent (PID: 12348)
2026-02-16T11:35:00 | Issue #2 | WORKFLOW_COMPLETED | Workflow finished successfully
"""
    log_file.write_text(content)
    return log_file


@pytest.fixture
def sample_workflow_chain():
    """Sample workflow chain configuration for testing."""
    return {
        "full": [
            ("ProjectLead", "Vision & Scope"),
            ("Architect", "Technical Design"),
            ("Copilot", "Implementation"),
        ],
        "shortened": [("ProjectLead", "Triage"), ("Copilot", "Fix")],
        "fast-track": [("Copilot", "Quick Fix")],
    }


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_runtime_dirs():
    """Clean up temporary runtime directories and accidental repo dirs created by tests."""
    yield

    shutil.rmtree(_TEST_RUNTIME_ROOT, ignore_errors=True)

    if not _REPO_DATA_DIR_EXISTED and _REPO_DATA_DIR.exists():
        shutil.rmtree(_REPO_DATA_DIR, ignore_errors=True)
    if not _REPO_LOGS_DIR_EXISTED and _REPO_LOGS_DIR.exists():
        shutil.rmtree(_REPO_LOGS_DIR, ignore_errors=True)
