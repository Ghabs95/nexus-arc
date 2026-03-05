"""User management and per-project tracking.

Manages user access and tracks which issues each user is monitoring per project.
Allows users to track different issues across multiple projects (nxs, etc.).
"""

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

def _resolve_state_dir() -> Path:
    explicit_state = str(os.getenv("NEXUS_STATE_DIR", "")).strip()
    if explicit_state:
        return Path(explicit_state)
    legacy_data = str(os.getenv("DATA_DIR", "")).strip()
    if legacy_data:
        return Path(legacy_data)
    runtime_dir = str(os.getenv("NEXUS_RUNTIME_DIR", "")).strip()
    if runtime_dir:
        return Path(runtime_dir) / "state"
    return Path(".nexus/state")


def _resolve_storage_backend(*, data_file: Path) -> str:
    forced_backend = str(os.getenv("NEXUS_USER_MANAGER_BACKEND", "")).strip().lower()
    if forced_backend in {"filesystem", "postgres", "postgresql"}:
        return "postgres" if forced_backend in {"postgres", "postgresql"} else "filesystem"

    if data_file != USER_DATA_FILE:
        return "filesystem"

    host_state = str(os.getenv("NEXUS_HOST_STATE_BACKEND", "")).strip().lower()
    if host_state in {"postgres", "postgresql"}:
        return "postgres"

    storage_backend = str(os.getenv("NEXUS_STORAGE_BACKEND", "filesystem")).strip().lower()
    if storage_backend in {"postgres", "postgresql"}:
        return "postgres"
    return "filesystem"


# User tracking file
USER_DATA_FILE = _resolve_state_dir() / "user_tracking.json"


@dataclass
class UserProject:
    """Represents a user's tracking for a specific project."""

    project_name: str  # nxs, etc.
    tracked_issues: list[str]  # List of issue numbers as strings
    last_activity: str  # ISO format timestamp


@dataclass
class User:
    """Represents a Nexus user."""

    nexus_id: str
    identities: dict[str, str]  # platform -> platform_user_id
    username: str | None
    first_name: str | None
    projects: dict[str, UserProject]  # project_name -> UserProject
    created_at: str  # ISO format timestamp
    last_seen: str  # ISO format timestamp

    @property
    def telegram_id(self) -> int | None:
        """Compatibility accessor for legacy Telegram-specific callers/tests."""
        value = self.identities.get("telegram")
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None


class UserManager:
    """Manages users and their per-project issue tracking."""

    def __init__(self, data_file: Path = USER_DATA_FILE):
        """
        Initialize user manager.

        Args:
            data_file: Path to user data JSON file
        """
        self.data_file = data_file
        self._storage_backend = _resolve_storage_backend(data_file=data_file)
        self.users: dict[str, User] = {}
        self.identity_map: dict[str, str] = {}
        self._last_loaded_mtime_ns: int | None = None
        self._last_loaded_db_updated_at: str | None = None
        self.load_users()

    @staticmethod
    def _identity_key(platform: str, platform_user_id: str) -> str:
        return f"{platform.lower().strip()}:{str(platform_user_id).strip()}"

    def _index_identity(self, nexus_id: str, platform: str, platform_user_id: str) -> None:
        platform_key = platform.lower().strip()
        platform_value = str(platform_user_id).strip()
        if not platform_key or not platform_value:
            return
        self.users[nexus_id].identities[platform_key] = platform_value
        self.identity_map[self._identity_key(platform_key, platform_value)] = nexus_id

    def _refresh_mtime_snapshot(self) -> None:
        if self._storage_backend == "postgres":
            try:
                from nexus.core.auth.credential_store import get_user_tracking_state

                _payload, updated_at = get_user_tracking_state()
                self._last_loaded_db_updated_at = (
                    updated_at.isoformat() if isinstance(updated_at, datetime) else None
                )
            except Exception:
                self._last_loaded_db_updated_at = None
            return
        try:
            self._last_loaded_mtime_ns = self.data_file.stat().st_mtime_ns
        except FileNotFoundError:
            self._last_loaded_mtime_ns = None

    def _maybe_reload_from_disk(self) -> None:
        if self._storage_backend == "postgres":
            try:
                from nexus.core.auth.credential_store import get_user_tracking_state

                _payload, updated_at = get_user_tracking_state()
                current_token = updated_at.isoformat() if isinstance(updated_at, datetime) else None
            except Exception:
                return
            if self._last_loaded_db_updated_at is None:
                self._last_loaded_db_updated_at = current_token
                return
            if current_token != self._last_loaded_db_updated_at:
                self.load_users()
            return
        try:
            current_mtime_ns = self.data_file.stat().st_mtime_ns
        except FileNotFoundError:
            return
        if self._last_loaded_mtime_ns is None:
            self._last_loaded_mtime_ns = current_mtime_ns
            return
        if current_mtime_ns != self._last_loaded_mtime_ns:
            self.load_users()

    def _hydrate_from_payload(self, data: Any) -> None:
        self.users = {}
        self.identity_map = {}
        users_blob = data.get("users") if isinstance(data, dict) else None
        if isinstance(users_blob, dict):
            for nexus_id, user_data in users_blob.items():
                projects = {}
                for proj_name, proj_data in user_data.get("projects", {}).items():
                    projects[proj_name] = UserProject(
                        project_name=proj_data["project_name"],
                        tracked_issues=proj_data["tracked_issues"],
                        last_activity=proj_data["last_activity"],
                    )
                user = User(
                    nexus_id=nexus_id,
                    identities={
                        str(k): str(v)
                        for k, v in (user_data.get("identities") or {}).items()
                        if k and v is not None
                    },
                    username=user_data.get("username"),
                    first_name=user_data.get("first_name"),
                    projects=projects,
                    created_at=user_data["created_at"],
                    last_seen=user_data["last_seen"],
                )
                self.users[nexus_id] = user
                for platform, platform_user_id in user.identities.items():
                    self.identity_map[self._identity_key(platform, platform_user_id)] = nexus_id

            # Reconcile optional persisted map with in-memory derived values.
            persisted_map = data.get("identity_map") if isinstance(data, dict) else None
            persisted_map = persisted_map or {}
            if isinstance(persisted_map, dict):
                for identity_key, nexus_id in persisted_map.items():
                    if nexus_id in self.users:
                        self.identity_map[str(identity_key)] = str(nexus_id)
            return

        if not isinstance(data, dict):
            return
        # Legacy format migration (top-level keyed by telegram_id).
        for user_id_str, user_data in data.items():
            telegram_id = str(user_data.get("telegram_id", user_id_str)).strip()
            projects = {}
            for proj_name, proj_data in user_data.get("projects", {}).items():
                projects[proj_name] = UserProject(
                    project_name=proj_data["project_name"],
                    tracked_issues=proj_data["tracked_issues"],
                    last_activity=proj_data["last_activity"],
                )
            nexus_id = str(uuid4())
            self.users[nexus_id] = User(
                nexus_id=nexus_id,
                identities={"telegram": telegram_id},
                username=user_data.get("username"),
                first_name=user_data.get("first_name"),
                projects=projects,
                created_at=user_data["created_at"],
                last_seen=user_data["last_seen"],
            )
            self.identity_map[self._identity_key("telegram", telegram_id)] = nexus_id

    def _serialize_payload(self) -> dict[str, Any]:
        users_blob: dict[str, dict] = {}
        for nexus_id, user in self.users.items():
            projects_dict = {}
            for proj_name, proj in user.projects.items():
                projects_dict[proj_name] = asdict(proj)

            users_blob[nexus_id] = {
                "nexus_id": user.nexus_id,
                "identities": user.identities,
                "username": user.username,
                "first_name": user.first_name,
                "projects": projects_dict,
                "created_at": user.created_at,
                "last_seen": user.last_seen,
            }
        return {"users": users_blob, "identity_map": self.identity_map}

    def load_users(self) -> None:
        """Load user data from configured backend."""
        try:
            if self._storage_backend == "postgres":
                from nexus.core.auth.credential_store import get_user_tracking_state
                from nexus.core.auth.credential_store import upsert_user_tracking_state

                payload, updated_at = get_user_tracking_state()
                if payload is None:
                    if self.data_file.exists():
                        with open(self.data_file) as f:
                            payload = json.load(f)
                        self._hydrate_from_payload(payload)
                        upsert_user_tracking_state(self._serialize_payload())
                        logger.info(
                            "Bootstrapped UNI tracking state to Postgres from %s (%s users)",
                            self.data_file,
                            len(self.users),
                        )
                    else:
                        self.users = {}
                        self.identity_map = {}
                        logger.info("No existing Postgres UNI tracking state, starting fresh")
                else:
                    self._hydrate_from_payload(payload)
                    logger.info("Loaded %s users from Postgres UNI tracking state", len(self.users))
                self._last_loaded_db_updated_at = (
                    updated_at.isoformat() if isinstance(updated_at, datetime) else None
                )
                return

            if self.data_file.exists():
                with open(self.data_file) as f:
                    data = json.load(f)
                self._hydrate_from_payload(data)
                logger.info(f"Loaded {len(self.users)} users from {self.data_file}")
            else:
                self.users = {}
                self.identity_map = {}
                logger.info("No existing user data file, starting fresh")
            self._refresh_mtime_snapshot()

        except Exception as e:
            logger.error(f"Error loading user data: {e}")
            self.users = {}
            self.identity_map = {}
            self._refresh_mtime_snapshot()

    def save_users(self) -> None:
        """Save user data to configured backend."""
        try:
            data = self._serialize_payload()

            if self._storage_backend == "postgres":
                from nexus.core.auth.credential_store import upsert_user_tracking_state

                updated_at = upsert_user_tracking_state(data)
                self._last_loaded_db_updated_at = (
                    updated_at.isoformat() if isinstance(updated_at, datetime) else None
                )
                logger.debug("Saved %s users to Postgres UNI tracking state", len(self.users))
                return

            # Ensure directory exists
            self.data_file.parent.mkdir(parents=True, exist_ok=True)

            # Write to file
            with open(self.data_file, "w") as f:
                json.dump(data, f, indent=2)
            self._refresh_mtime_snapshot()

            logger.debug(f"Saved {len(self.users)} users to {self.data_file}")

        except Exception as e:
            logger.error(f"Error saving user data: {e}")

    def resolve_nexus_id(self, platform: str, platform_user_id: str | int) -> str | None:
        """Resolve canonical nexus_id for a platform identity."""
        self._maybe_reload_from_disk()
        key = self._identity_key(platform, str(platform_user_id))
        resolved = self.identity_map.get(key)
        if resolved is not None:
            return resolved
        # Fallback for filesystems with coarse mtime granularity or very fast writes.
        self.load_users()
        return self.identity_map.get(key)

    def get_user_by_nexus_id(self, nexus_id: str) -> User | None:
        """Return user by canonical nexus_id."""
        self._maybe_reload_from_disk()
        return self.users.get(str(nexus_id))

    def link_identity(self, nexus_id: str, platform: str, platform_user_id: str) -> None:
        """Link an additional platform identity to an existing nexus user."""
        self._maybe_reload_from_disk()
        nexus_key = str(nexus_id)
        if nexus_key not in self.users:
            raise KeyError(f"Unknown nexus_id: {nexus_id}")
        identity_key = self._identity_key(platform, str(platform_user_id))
        existing_nexus_id = self.identity_map.get(identity_key)
        if existing_nexus_id and existing_nexus_id != nexus_key:
            raise ValueError(
                f"Identity '{identity_key}' is already linked to nexus_id={existing_nexus_id}"
            )
        self._index_identity(nexus_key, platform, str(platform_user_id))
        self.users[nexus_key].last_seen = datetime.now().isoformat()
        self.save_users()

    def merge_users(self, target_nexus_id: str, source_nexus_id: str) -> str:
        """Merge source UNI user into target UNI user and return target nexus_id."""
        self._maybe_reload_from_disk()
        target_key = str(target_nexus_id).strip()
        source_key = str(source_nexus_id).strip()
        if not target_key:
            raise ValueError("target_nexus_id is required")
        if not source_key or source_key == target_key:
            return target_key

        target_user = self.users.get(target_key)
        source_user = self.users.get(source_key)

        if target_user is None and source_user is None:
            self.load_users()
            target_user = self.users.get(target_key)
            source_user = self.users.get(source_key)
            if target_user is None and source_user is None:
                raise KeyError(f"Unknown nexus users: target={target_key}, source={source_key}")
        if target_user is None and source_user is not None:
            source_user.nexus_id = target_key
            self.users[target_key] = source_user
            del self.users[source_key]
            target_user = source_user
        if source_user is None:
            return target_key

        assert target_user is not None
        for platform, platform_user_id in (source_user.identities or {}).items():
            key = self._identity_key(platform, str(platform_user_id))
            owner = self.identity_map.get(key)
            if owner and owner not in {source_key, target_key}:
                logger.warning(
                    "Skipping conflicting identity merge for %s: already owned by %s",
                    key,
                    owner,
                )
                continue
            target_user.identities[str(platform)] = str(platform_user_id)
            self.identity_map[key] = target_key

        if not target_user.username and source_user.username:
            target_user.username = source_user.username
        if not target_user.first_name and source_user.first_name:
            target_user.first_name = source_user.first_name

        for project_name, source_project in (source_user.projects or {}).items():
            existing = target_user.projects.get(project_name)
            if existing is None:
                target_user.projects[project_name] = source_project
                continue
            existing.tracked_issues = list(
                dict.fromkeys((existing.tracked_issues or []) + (source_project.tracked_issues or []))
            )
            existing.last_activity = max(str(existing.last_activity), str(source_project.last_activity))

        target_user.last_seen = max(str(target_user.last_seen), str(source_user.last_seen))
        for identity_key, owner in list(self.identity_map.items()):
            if owner == source_key:
                self.identity_map[identity_key] = target_key

        if source_key in self.users and source_key != target_key:
            del self.users[source_key]

        self.save_users()
        logger.info("Merged UNI users: source=%s -> target=%s", source_key, target_key)
        return target_key

    def get_or_create_user_by_identity(
        self,
        platform: str,
        platform_user_id: str | int,
        username: str | None = None,
        first_name: str | None = None,
    ) -> User:
        """Get existing user by platform identity or create a new UNI user."""
        self._maybe_reload_from_disk()
        platform_key = str(platform).lower().strip()
        identity_value = str(platform_user_id).strip()
        now = datetime.now().isoformat()
        identity_key = self._identity_key(platform_key, identity_value)
        nexus_id = self.identity_map.get(identity_key)

        if nexus_id and nexus_id in self.users:
            user = self.users[nexus_id]
            user.last_seen = now
            if username:
                user.username = username
            if first_name:
                user.first_name = first_name
            self._index_identity(user.nexus_id, platform_key, identity_value)
            self.save_users()
            return user

        user = User(
            nexus_id=str(uuid4()),
            identities={platform_key: identity_value},
            username=username,
            first_name=first_name,
            projects={},
            created_at=now,
            last_seen=now,
        )
        self.users[user.nexus_id] = user
        self.identity_map[identity_key] = user.nexus_id
        self.save_users()
        logger.info(
            "Created new UNI user: nexus_id=%s via %s identity %s",
            user.nexus_id,
            platform_key,
            identity_value,
        )
        return user

    def get_or_create_user(
        self, telegram_id: int, username: str | None = None, first_name: str | None = None
    ) -> User:
        """
        Get existing user or create new one (legacy Telegram compatibility).

        Args:
            telegram_id: Telegram user ID
            username: Telegram username
            first_name: User's first name

        Returns:
            User object
        """
        return self.get_or_create_user_by_identity(
            "telegram", str(telegram_id), username, first_name
        )

    def track_issue_by_nexus_id(self, nexus_id: str, project: str, issue_number: str) -> None:
        """Track an issue for a canonical UNI user."""
        user = self.get_user_by_nexus_id(nexus_id)
        if not user:
            raise KeyError(f"Unknown nexus_id: {nexus_id}")

        if project not in user.projects:
            user.projects[project] = UserProject(
                project_name=project,
                tracked_issues=[],
                last_activity=datetime.now().isoformat(),
            )

        project_data = user.projects[project]
        if issue_number not in project_data.tracked_issues:
            project_data.tracked_issues.append(issue_number)
            project_data.last_activity = datetime.now().isoformat()
            user.last_seen = datetime.now().isoformat()
            logger.info("User %s now tracking %s#%s", user.nexus_id, project, issue_number)
            self.save_users()

    def track_issue(
        self,
        telegram_id: int,
        project: str,
        issue_number: str,
        username: str | None = None,
        first_name: str | None = None,
    ) -> None:
        """
        Track an issue for a user in a specific project.

        Args:
            telegram_id: Telegram user ID
            project: Project name (nxs, etc.)
            issue_number: Git issue number
            username: Telegram username
            first_name: User's first name
        """
        user = self.get_or_create_user(telegram_id, username, first_name)
        self.track_issue_by_nexus_id(user.nexus_id, project, issue_number)

    def untrack_issue_by_nexus_id(self, nexus_id: str, project: str, issue_number: str) -> bool:
        """Stop tracking an issue for a canonical UNI user."""
        user = self.get_user_by_nexus_id(nexus_id)
        if not user or project not in user.projects:
            return False

        project_data = user.projects[project]
        if issue_number in project_data.tracked_issues:
            project_data.tracked_issues.remove(issue_number)
            project_data.last_activity = datetime.now().isoformat()
            user.last_seen = datetime.now().isoformat()
            logger.info("User %s stopped tracking %s#%s", user.nexus_id, project, issue_number)
            self.save_users()
            return True

        return False

    def untrack_issue(self, telegram_id: int, project: str, issue_number: str) -> bool:
        """
        Stop tracking an issue for a user in a specific project.

        Args:
            telegram_id: Telegram user ID
            project: Project name
            issue_number: Git issue number

        Returns:
            True if issue was untracked, False if it wasn't being tracked
        """
        nexus_id = self.resolve_nexus_id("telegram", telegram_id)
        if not nexus_id:
            return False
        return self.untrack_issue_by_nexus_id(nexus_id, project, issue_number)

    def get_user_tracked_issues_by_nexus_id(
        self, nexus_id: str, project: str | None = None
    ) -> dict[str, list[str]]:
        """Get all tracked issues for a canonical UNI user."""
        user = self.get_user_by_nexus_id(nexus_id)
        if not user:
            return {}

        if project:
            if project in user.projects:
                return {project: user.projects[project].tracked_issues}
            return {}

        result = {}
        for proj_name, proj_data in user.projects.items():
            if proj_data.tracked_issues:
                result[proj_name] = proj_data.tracked_issues
        return result

    def get_user_tracked_issues(
        self, telegram_id: int, project: str | None = None
    ) -> dict[str, list[str]]:
        """
        Get all issues tracked by a user.

        Args:
            telegram_id: Telegram user ID
            project: Optional project filter

        Returns:
            Dict mapping project names to lists of issue numbers
        """
        nexus_id = self.resolve_nexus_id("telegram", telegram_id)
        if not nexus_id:
            return {}
        return self.get_user_tracked_issues_by_nexus_id(nexus_id, project)

    def get_issue_tracker_nexus_ids(self, project: str, issue_number: str) -> list[str]:
        """
        Get all users tracking a specific issue.

        Args:
            project: Project name
            issue_number: Git issue number

        Returns:
            List of canonical nexus IDs
        """
        trackers: list[str] = []

        for nexus_id, user in self.users.items():
            if project in user.projects:
                if issue_number in user.projects[project].tracked_issues:
                    trackers.append(nexus_id)

        return trackers

    def get_issue_trackers(self, project: str, issue_number: str) -> list[int]:
        """Compatibility helper returning Telegram IDs for issue trackers."""
        telegram_ids: list[int] = []
        for nexus_id in self.get_issue_tracker_nexus_ids(project, issue_number):
            telegram_id = self.users[nexus_id].telegram_id
            if telegram_id is not None:
                telegram_ids.append(telegram_id)
        return telegram_ids

    def get_user_stats_by_nexus_id(self, nexus_id: str) -> dict:
        """Get statistics for a canonical UNI user."""
        user = self.get_user_by_nexus_id(nexus_id)
        if not user:
            return {"exists": False}

        total_issues = sum(len(proj.tracked_issues) for proj in user.projects.values())
        return {
            "exists": True,
            "nexus_id": user.nexus_id,
            "identities": user.identities,
            "username": user.username,
            "first_name": user.first_name,
            "projects": list(user.projects.keys()),
            "total_tracked_issues": total_issues,
            "created_at": user.created_at,
            "last_seen": user.last_seen,
        }

    def get_user_stats(self, telegram_id: int) -> dict:
        """
        Get statistics for a user.

        Args:
            telegram_id: Telegram user ID

        Returns:
            Dict with user statistics
        """
        nexus_id = self.resolve_nexus_id("telegram", telegram_id)
        if not nexus_id:
            return {"exists": False}
        stats = self.get_user_stats_by_nexus_id(nexus_id)
        stats.pop("identities", None)
        stats.pop("nexus_id", None)
        return stats

    def get_all_users_stats(self) -> dict:
        """
        Get statistics for all users.

        Returns:
            Dict with overall statistics
        """
        total_users = len(self.users)
        total_projects = set()
        total_tracked = 0

        for user in self.users.values():
            total_projects.update(user.projects.keys())
            total_tracked += sum(len(proj.tracked_issues) for proj in user.projects.values())

        return {
            "total_users": total_users,
            "total_projects": len(total_projects),
            "projects": sorted(total_projects),
            "total_tracked_issues": total_tracked,
        }


# Global singleton
_user_manager: UserManager | None = None


def get_user_manager() -> UserManager:
    """Get the global UserManager instance."""
    global _user_manager
    if _user_manager is None:
        _user_manager = UserManager()
    return _user_manager
