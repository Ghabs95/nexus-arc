"""Pytest bootstrap for nexus-arc test collection."""

import os
import tempfile
from pathlib import Path


_TEST_RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix="nexus-arc-tests-"))
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
""".strip()
        + "\n",
        encoding="utf-8",
    )

os.environ.setdefault("PROJECT_CONFIG_PATH", str(_BOOTSTRAP_PROJECT_CONFIG))
os.environ.setdefault("BASE_DIR", "/tmp/test_nexus")
os.environ.setdefault("NEXUS_RUNTIME_DIR", str(_TEST_RUNTIME_ROOT))
os.environ.setdefault("NEXUS_STATE_DIR", str(_TEST_RUNTIME_ROOT / "state"))
os.environ.setdefault("LOGS_DIR", str(_TEST_RUNTIME_ROOT / "logs"))
