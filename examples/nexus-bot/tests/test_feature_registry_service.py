from __future__ import annotations

import pytest
import nexus.core.feature_registry_service as feature_registry_module
from nexus.core.feature_registry_service import FeatureRegistryService


def test_upsert_and_list_filesystem_registry(tmp_path):
    service = FeatureRegistryService(
        enabled=True,
        backend="filesystem",
        state_dir=str(tmp_path),
        max_items_per_project=10,
        dedup_similarity=0.86,
    )

    saved = service.upsert_feature(
        project_key="nexus",
        canonical_title="Two-factor authentication",
        aliases=["2FA"],
        source_issue="88",
        source_pr="https://example/pr/12",
        manual_override=True,
    )

    assert saved is not None
    assert saved["canonical_title"] == "Two-factor authentication"

    listed = service.list_features("nexus")
    assert len(listed) == 1
    assert listed[0]["source_issue"] == "88"
    assert listed[0]["manual_override"] is True


def test_filter_ideation_items_removes_exact_and_fuzzy_matches(tmp_path):
    service = FeatureRegistryService(
        enabled=True,
        backend="filesystem",
        state_dir=str(tmp_path),
        max_items_per_project=10,
        dedup_similarity=0.86,
    )
    service.upsert_feature(
        project_key="nexus",
        canonical_title="Improve onboarding funnel",
        aliases=["onboarding improvements"],
    )

    kept, removed = service.filter_ideation_items(
        project_key="nexus",
        items=[
            {"title": "Improve onboarding funnel", "summary": "duplicate"},
            {"title": "Improve onboarding funnels", "summary": "fuzzy duplicate"},
            {"title": "Add SOC2 export tooling", "summary": "new"},
        ],
        similarity_threshold=0.86,
    )

    assert [item["title"] for item in kept] == ["Add SOC2 export tooling"]
    assert len(removed) == 2


def test_forget_feature_by_id_or_title(tmp_path):
    service = FeatureRegistryService(
        enabled=True,
        backend="filesystem",
        state_dir=str(tmp_path),
    )
    service.upsert_feature(project_key="nexus", canonical_title="Slack alerts")
    feature = service.upsert_feature(project_key="nexus", canonical_title="Weekly reports")

    removed_by_title = service.forget_feature(project_key="nexus", feature_ref="Slack alerts")
    assert removed_by_title is not None

    removed_by_id = service.forget_feature(
        project_key="nexus", feature_ref=str(feature["feature_id"])
    )
    assert removed_by_id is not None
    assert service.list_features("nexus") == []


def test_completion_ingestion_is_conservative(tmp_path):
    service = FeatureRegistryService(
        enabled=True,
        backend="filesystem",
        state_dir=str(tmp_path),
    )

    no_insert = service.ingest_completion(
        project_key="nexus",
        issue_number="88",
        payload={"status": "in-progress", "summary": "draft"},
    )
    assert no_insert is None

    inserted = service.ingest_completion(
        project_key="nexus",
        issue_number="88",
        payload={
            "status": "complete",
            "summary": "Implemented",
            "agent_type": "developer",
            "next_agent": "reviewer",
            "key_findings": ["Feature: Automated runbooks"],
        },
    )
    assert inserted is not None
    assert inserted["canonical_title"] == "Automated runbooks"

    ambiguous = service.ingest_completion(
        project_key="nexus",
        issue_number="88",
        payload={
            "status": "complete",
            "summary": "Small cleanup release",
            "agent_type": "developer",
            "next_agent": "reviewer",
            "key_findings": ["Refined docs"],
        },
    )
    assert ambiguous is None


@pytest.mark.skipif(
    not feature_registry_module._SA_AVAILABLE,
    reason="sqlalchemy is required for postgres parity tests",
)
def test_postgres_backend_matches_filesystem_core_dedup_behavior(tmp_path, monkeypatch):
    sqlite_path = tmp_path / "feature_registry_pg.db"
    real_create_engine = feature_registry_module.sa.create_engine

    def _sqlite_engine(_dsn: str, **_kwargs):
        return real_create_engine(f"sqlite:///{sqlite_path}")

    monkeypatch.setattr(feature_registry_module.sa, "create_engine", _sqlite_engine)

    fs_service = FeatureRegistryService(
        enabled=True,
        backend="filesystem",
        state_dir=str(tmp_path / "fs"),
        dedup_similarity=0.86,
    )
    pg_service = FeatureRegistryService(
        enabled=True,
        backend="postgres",
        state_dir=str(tmp_path / "pg-fallback"),
        postgres_dsn="postgresql://ignored-for-tests",
        dedup_similarity=0.86,
    )

    fs_saved = fs_service.upsert_feature(
        project_key="nexus",
        canonical_title="Weekly release summary",
        aliases=["release summary"],
        source_issue="88",
    )
    pg_saved = pg_service.upsert_feature(
        project_key="nexus",
        canonical_title="Weekly release summary",
        aliases=["release summary"],
        source_issue="88",
    )
    assert fs_saved is not None and pg_saved is not None
    assert fs_saved["canonical_title"] == pg_saved["canonical_title"]
    assert fs_saved["canonical_title_hash"] == pg_saved["canonical_title_hash"]

    candidate_items = [
        {"title": "Weekly release summary", "summary": "exact duplicate"},
        {"title": "Weekly release summaries", "summary": "fuzzy duplicate"},
        {"title": "Add on-call escalation digest", "summary": "new"},
    ]
    fs_kept, fs_removed = fs_service.filter_ideation_items(
        project_key="nexus",
        items=candidate_items,
    )
    pg_kept, pg_removed = pg_service.filter_ideation_items(
        project_key="nexus",
        items=candidate_items,
    )

    assert fs_kept == pg_kept
    assert len(fs_removed) == len(pg_removed) == 2
    assert [item["title"] for item in pg_kept] == ["Add on-call escalation digest"]


@pytest.mark.skipif(
    not feature_registry_module._SA_AVAILABLE,
    reason="sqlalchemy is required for postgres parity tests",
)
def test_postgres_backend_matches_filesystem_forget_and_manual_override(tmp_path, monkeypatch):
    sqlite_path = tmp_path / "feature_registry_pg_override.db"
    real_create_engine = feature_registry_module.sa.create_engine

    def _sqlite_engine(_dsn: str, **_kwargs):
        return real_create_engine(f"sqlite:///{sqlite_path}")

    monkeypatch.setattr(feature_registry_module.sa, "create_engine", _sqlite_engine)

    fs_service = FeatureRegistryService(
        enabled=True, backend="filesystem", state_dir=str(tmp_path / "fs")
    )
    pg_service = FeatureRegistryService(
        enabled=True,
        backend="postgres",
        state_dir=str(tmp_path / "pg-fallback"),
        postgres_dsn="postgresql://ignored-for-tests",
    )

    fs_manual = fs_service.upsert_feature(
        project_key="nexus",
        canonical_title="Incident timeline view",
        aliases=["timeline"],
        manual_override=True,
    )
    pg_manual = pg_service.upsert_feature(
        project_key="nexus",
        canonical_title="Incident timeline view",
        aliases=["timeline"],
        manual_override=True,
    )
    assert fs_manual is not None and pg_manual is not None

    fs_auto = fs_service.upsert_feature(
        project_key="nexus",
        canonical_title="Incident timeline view",
        aliases=["automatic alias"],
        manual_override=False,
    )
    pg_auto = pg_service.upsert_feature(
        project_key="nexus",
        canonical_title="Incident timeline view",
        aliases=["automatic alias"],
        manual_override=False,
    )
    assert fs_auto is not None and pg_auto is not None
    assert fs_auto["aliases"] == pg_auto["aliases"] == ["Incident timeline view", "timeline"]

    fs_removed = fs_service.forget_feature(project_key="nexus", feature_ref=fs_manual["feature_id"])
    pg_removed = pg_service.forget_feature(project_key="nexus", feature_ref=pg_manual["feature_id"])
    assert fs_removed is not None and pg_removed is not None
    assert fs_service.list_features("nexus") == []
    assert pg_service.list_features("nexus") == []
