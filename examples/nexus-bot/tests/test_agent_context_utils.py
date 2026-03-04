from nexus.core.handlers.agent_context_utils import load_role_context


def test_load_role_context_index_mode_prefers_compact_index(tmp_path):
    project_root = tmp_path / "workspace"
    docs_dir = project_root / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "AGENTS.md").write_text(
        "# Agent Guide\nUse triage for routing and developer for implementation.",
        encoding="utf-8",
    )
    (docs_dir / "ROADMAP.md").write_text(
        "# Roadmap\nImprove onboarding and notification reliability.",
        encoding="utf-8",
    )

    agent_cfg = {
        "context_path": "docs",
        "context_files": ["AGENTS.md", "ROADMAP.md"],
        "context_mode": "index",
    }
    out = load_role_context(
        project_root=str(project_root),
        agent_cfg=agent_cfg,
        max_chars=400,
        query="routing onboarding",
        summary_max_chars=120,
    )

    assert "Context index (retrieval mode)" in out
    assert "docs/AGENTS.md" in out
    assert "docs/ROADMAP.md" in out


def test_load_role_context_full_mode_applies_budget(tmp_path):
    project_root = tmp_path / "workspace"
    docs_dir = project_root / "docs"
    docs_dir.mkdir(parents=True)
    very_long = "\n".join([f"line {idx} content" for idx in range(500)])
    (docs_dir / "LONG.md").write_text(very_long, encoding="utf-8")

    agent_cfg = {
        "context_path": "docs",
        "context_files": ["LONG.md"],
        "context_mode": "full",
    }
    out = load_role_context(
        project_root=str(project_root),
        agent_cfg=agent_cfg,
        max_chars=350,
        summary_max_chars=120,
    )

    assert len(out) <= 500
    assert "docs/LONG.md" in out


def test_load_role_context_defaults_to_index_mode(tmp_path):
    project_root = tmp_path / "workspace"
    docs_dir = project_root / "docs"
    docs_dir.mkdir(parents=True)
    (docs_dir / "AGENTS.md").write_text(
        "# Guide\nContext default should be retrieval-first.", encoding="utf-8"
    )

    agent_cfg = {
        "context_path": "docs",
        "context_files": ["AGENTS.md"],
    }
    out = load_role_context(
        project_root=str(project_root),
        agent_cfg=agent_cfg,
        max_chars=260,
        query="retrieval context",
        summary_max_chars=120,
    )

    assert "Context index (retrieval mode)" in out
    assert "docs/AGENTS.md" in out
