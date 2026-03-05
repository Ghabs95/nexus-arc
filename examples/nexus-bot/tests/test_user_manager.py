"""Tests for user_manager module."""

import json
from pathlib import Path

from nexus.core import user_manager as user_manager_module
from nexus.core.user_manager import UserManager


class TestUserManager:
    """Tests for UserManager class."""

    def test_initialization(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        assert manager.data_file == data_file
        assert manager.users == {}
        assert manager.identity_map == {}

    def test_create_new_user(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        user = manager.get_or_create_user(telegram_id=12345, username="testuser", first_name="Test")

        assert user.telegram_id == 12345
        assert user.username == "testuser"
        assert user.first_name == "Test"
        assert user.projects == {}
        assert user.nexus_id in manager.users
        assert manager.resolve_nexus_id("telegram", "12345") == user.nexus_id

    def test_get_existing_user_updates_last_seen(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        user1 = manager.get_or_create_user(12345, "user1", "User")
        first_seen = user1.last_seen

        user2 = manager.get_or_create_user(12345, "user1_updated")

        assert user1 is user2
        assert user2.username == "user1_updated"
        assert user2.last_seen > first_seen

    def test_track_issue_by_legacy_telegram_api(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        manager.track_issue(
            telegram_id=12345, project="proj_a", issue_number="123", username="testuser"
        )

        nexus_id = manager.resolve_nexus_id("telegram", "12345")
        assert nexus_id is not None
        user = manager.users[nexus_id]
        assert "proj_a" in user.projects
        assert "123" in user.projects["proj_a"].tracked_issues

    def test_track_issue_by_nexus_id(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        user = manager.get_or_create_user_by_identity("discord", "444", "discuser", "Disc")
        manager.track_issue_by_nexus_id(user.nexus_id, "proj_a", "123")

        assert "123" in manager.users[user.nexus_id].projects["proj_a"].tracked_issues

    def test_link_identity_and_cross_platform_lookup(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        user = manager.get_or_create_user_by_identity("telegram", "12345", "tguser", "TG")
        manager.link_identity(user.nexus_id, "discord", "98765")

        assert manager.resolve_nexus_id("discord", "98765") == user.nexus_id
        tracked = manager.get_user_tracked_issues_by_nexus_id(user.nexus_id)
        assert tracked == {}

    def test_link_identity_rejects_existing_mapping_to_other_user(self, tmp_path):
        import pytest

        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        user_a = manager.get_or_create_user_by_identity("telegram", "12345", "tguser", "TG")
        user_b = manager.get_or_create_user_by_identity("telegram", "54321", "tguser2", "TG2")
        manager.link_identity(user_a.nexus_id, "discord", "98765")

        with pytest.raises(ValueError, match="already linked"):
            manager.link_identity(user_b.nexus_id, "discord", "98765")

        assert manager.resolve_nexus_id("discord", "98765") == user_a.nexus_id

    def test_untrack_issue(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        manager.track_issue(12345, "proj_a", "123")
        success = manager.untrack_issue(12345, "proj_a", "123")

        assert success is True
        nexus_id = manager.resolve_nexus_id("telegram", "12345")
        assert nexus_id is not None
        assert "123" not in manager.users[nexus_id].projects["proj_a"].tracked_issues

    def test_get_user_tracked_issues(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        manager.track_issue(12345, "proj_a", "123")
        manager.track_issue(12345, "proj_a", "456")
        manager.track_issue(12345, "proj_b", "789")

        tracked = manager.get_user_tracked_issues(12345)

        assert tracked["proj_a"] == ["123", "456"]
        assert tracked["proj_b"] == ["789"]

    def test_get_issue_trackers_legacy_returns_telegram_ids(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        manager.track_issue(111, "proj_a", "123")
        manager.track_issue(222, "proj_a", "123")

        trackers = manager.get_issue_trackers("proj_a", "123")
        assert set(trackers) == {111, 222}

    def test_get_issue_tracker_nexus_ids(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        u1 = manager.get_or_create_user_by_identity("discord", "111")
        u2 = manager.get_or_create_user_by_identity("discord", "222")
        manager.track_issue_by_nexus_id(u1.nexus_id, "proj_a", "123")
        manager.track_issue_by_nexus_id(u2.nexus_id, "proj_a", "123")

        trackers = manager.get_issue_tracker_nexus_ids("proj_a", "123")
        assert set(trackers) == {u1.nexus_id, u2.nexus_id}

    def test_get_user_stats(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        manager.track_issue(12345, "proj_a", "123", username="testuser", first_name="Test")
        manager.track_issue(12345, "proj_a", "456")
        manager.track_issue(12345, "proj_b", "789")

        stats = manager.get_user_stats(12345)

        assert stats["exists"] is True
        assert stats["username"] == "testuser"
        assert stats["first_name"] == "Test"
        assert len(stats["projects"]) == 2
        assert stats["total_tracked_issues"] == 3

    def test_get_nonexistent_user_stats(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        stats = manager.get_user_stats(99999)
        assert stats["exists"] is False

    def test_get_all_users_stats(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        manager.track_issue(111, "proj_a", "123")
        manager.track_issue(222, "proj_b", "456")
        manager.track_issue(333, "proj_c", "789")
        manager.track_issue(333, "proj_a", "999")

        stats = manager.get_all_users_stats()

        assert stats["total_users"] == 3
        assert stats["total_projects"] == 3
        assert set(stats["projects"]) == {"proj_a", "proj_b", "proj_c"}
        assert stats["total_tracked_issues"] == 4

    def test_save_and_load_users_new_format(self, tmp_path):
        data_file = tmp_path / "users.json"

        manager1 = UserManager(data_file)
        manager1.track_issue(12345, "proj_a", "123", username="user1")
        manager1.track_issue(67890, "proj_b", "456", username="user2")

        manager2 = UserManager(data_file)

        assert len(manager2.users) == 2
        n1 = manager2.resolve_nexus_id("telegram", "12345")
        n2 = manager2.resolve_nexus_id("telegram", "67890")
        assert n1 is not None and n2 is not None
        assert manager2.users[n1].username == "user1"
        assert "123" in manager2.users[n1].projects["proj_a"].tracked_issues

    def test_load_legacy_telegram_format_migrates(self, tmp_path):
        data_file = tmp_path / "users.json"
        legacy_payload = {
            "12345": {
                "telegram_id": 12345,
                "username": "legacy",
                "first_name": "User",
                "projects": {
                    "proj_a": {
                        "project_name": "proj_a",
                        "tracked_issues": ["1"],
                        "last_activity": "2024-01-01T00:00:00",
                    }
                },
                "created_at": "2024-01-01T00:00:00",
                "last_seen": "2024-01-01T00:00:00",
            }
        }
        data_file.write_text(json.dumps(legacy_payload), encoding="utf-8")

        manager = UserManager(data_file)
        nexus_id = manager.resolve_nexus_id("telegram", "12345")
        assert nexus_id is not None
        assert manager.users[nexus_id].username == "legacy"
        assert manager.users[nexus_id].projects["proj_a"].tracked_issues == ["1"]

    def test_merge_users_combines_identities_and_projects(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        tg_user = manager.get_or_create_user_by_identity("telegram", "123", "tg", "TG")
        dc_user = manager.get_or_create_user_by_identity("discord", "999", "dc", "DC")
        manager.track_issue_by_nexus_id(tg_user.nexus_id, "proj_a", "1")
        manager.track_issue_by_nexus_id(dc_user.nexus_id, "proj_a", "2")

        merged_id = manager.merge_users(tg_user.nexus_id, dc_user.nexus_id)

        assert merged_id == tg_user.nexus_id
        assert manager.resolve_nexus_id("telegram", "123") == merged_id
        assert manager.resolve_nexus_id("discord", "999") == merged_id
        tracked = manager.get_user_tracked_issues_by_nexus_id(merged_id, "proj_a")
        assert tracked["proj_a"] == ["1", "2"]

    def test_resolve_nexus_id_auto_reloads_after_external_file_change(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager_a = UserManager(data_file)
        manager_b = UserManager(data_file)

        user_a = manager_a.get_or_create_user_by_identity("telegram", "123")
        user_b = manager_a.get_or_create_user_by_identity("discord", "999")

        manager_b.merge_users(user_a.nexus_id, user_b.nexus_id)

        assert manager_a.resolve_nexus_id("discord", "999") == user_a.nexus_id

    def test_multi_user_isolation(self, tmp_path):
        data_file = tmp_path / "users.json"
        manager = UserManager(data_file)

        manager.track_issue(111, "proj_a", "123")
        manager.track_issue(222, "proj_a", "456")

        user1_issues = manager.get_user_tracked_issues(111)
        user2_issues = manager.get_user_tracked_issues(222)

        assert user1_issues["proj_a"] == ["123"]
        assert user2_issues["proj_a"] == ["456"]


def test_resolve_state_dir_prefers_nexus_state_dir(monkeypatch):
    monkeypatch.setenv("NEXUS_STATE_DIR", "/tmp/nexus-state")
    monkeypatch.setenv("DATA_DIR", "/tmp/data-dir")
    monkeypatch.setenv("NEXUS_RUNTIME_DIR", "/tmp/runtime-dir")
    assert user_manager_module._resolve_state_dir() == Path("/tmp/nexus-state")


def test_resolve_state_dir_falls_back_to_data_dir(monkeypatch):
    monkeypatch.delenv("NEXUS_STATE_DIR", raising=False)
    monkeypatch.setenv("DATA_DIR", "/tmp/data-dir")
    monkeypatch.setenv("NEXUS_RUNTIME_DIR", "/tmp/runtime-dir")
    assert user_manager_module._resolve_state_dir() == Path("/tmp/data-dir")


def test_resolve_state_dir_falls_back_to_runtime_dir(monkeypatch):
    monkeypatch.delenv("NEXUS_STATE_DIR", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setenv("NEXUS_RUNTIME_DIR", "/tmp/runtime-dir")
    assert user_manager_module._resolve_state_dir() == Path("/tmp/runtime-dir/state")


def test_resolve_storage_backend_prefers_forced_backend(monkeypatch):
    monkeypatch.setenv("NEXUS_USER_MANAGER_BACKEND", "postgres")
    monkeypatch.setenv("NEXUS_STORAGE_BACKEND", "filesystem")
    backend = user_manager_module._resolve_storage_backend(data_file=user_manager_module.USER_DATA_FILE)
    assert backend == "postgres"


def test_resolve_storage_backend_uses_filesystem_for_custom_data_file(monkeypatch, tmp_path):
    monkeypatch.delenv("NEXUS_USER_MANAGER_BACKEND", raising=False)
    monkeypatch.setenv("NEXUS_STORAGE_BACKEND", "postgres")
    backend = user_manager_module._resolve_storage_backend(data_file=tmp_path / "users.json")
    assert backend == "filesystem"


def test_resolve_storage_backend_uses_postgres_for_default_data_file(monkeypatch):
    monkeypatch.delenv("NEXUS_USER_MANAGER_BACKEND", raising=False)
    monkeypatch.setenv("NEXUS_STORAGE_BACKEND", "postgres")
    backend = user_manager_module._resolve_storage_backend(data_file=user_manager_module.USER_DATA_FILE)
    assert backend == "postgres"
