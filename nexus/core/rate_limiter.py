"""Rate limiting and throttling for chat commands and Git API calls."""

import logging
import os
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field

from nexus.core.inbox.inbox_persistence_service import (
    load_json_state_file as _load_json_state_file,
)
from nexus.core.inbox.inbox_persistence_service import (
    save_json_state_file as _save_json_state_file,
)

logger = logging.getLogger(__name__)


@dataclass
class RateLimit:
    """Rate limit configuration for a specific action."""

    max_requests: int  # Maximum requests allowed
    window_seconds: int  # Time window in seconds
    name: str = ""  # Optional name for logging

    def __post_init__(self):
        if not self.name:
            self.name = f"{self.max_requests}/{self.window_seconds}s"


@dataclass
class UserQuota:
    """Track user's request history for rate limiting."""

    user_id: int
    timestamps: deque = field(default_factory=deque)  # Request timestamps

    def add_request(self, timestamp: float = None):
        """Record a new request."""
        if timestamp is None:
            timestamp = time.time()
        self.timestamps.append(timestamp)

    def cleanup_old(self, window_seconds: int):
        """Remove timestamps outside the sliding window."""
        cutoff = time.time() - window_seconds
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.popleft()

    def count_recent(self, window_seconds: int) -> int:
        """Count requests in the sliding window."""
        self.cleanup_old(window_seconds)
        return len(self.timestamps)


class RateLimiter:
    """
    Sliding window rate limiter for chat commands and Git API calls.

    Features:
    - Per-user rate limiting
    - Per-command rate limiting
    - Global rate limiting
    - Persistent state across restarts
    - Automatic cleanup of old data
    """

    # Default rate limits (can be overridden)
    DEFAULT_LIMITS = {
        # Per-user limits
        "user_global": RateLimit(30, 60, "30 requests/minute per user"),
        "user_commands": RateLimit(10, 60, "10 commands/minute per user"),
        # Per-command limits (stricter for heavy operations)
        "logs": RateLimit(5, 60, "5 log views/minute"),
        "stats": RateLimit(3, 60, "3 stats requests/minute"),
        "direct": RateLimit(3, 300, "3 direct agent calls/5min"),
        "reprocess": RateLimit(2, 300, "2 reprocesses/5min"),
        "implement": RateLimit(5, 300, "5 implementations/5min"),
        # Git API limits
        "git_api": RateLimit(100, 3600, "100 API calls/hour"),
        "git_issue_create": RateLimit(10, 3600, "10 issue creates/hour"),
    }
    _REDIS_USERS_KEY = "nexus:rate_limits:users"
    _REDIS_USER_ACTIONS_KEY = "nexus:rate_limits:user_actions"
    _REDIS_GLOBAL_KEY = "nexus:rate_limits:global"

    def __init__(
        self,
        state_file: str | None = None,
        *,
        state_backend: str | None = None,
        state_key: str = "rate_limits",
        redis_url: str | None = None,
        redis_client=None,
    ):
        """
        Initialize rate limiter.

        Args:
            state_file: Optional path to persist rate limit state
        """
        self.state_file = state_file
        self.state_backend = self._normalize_state_backend(state_backend)
        self.state_key = state_key
        self.redis_url = str(redis_url or "").strip()
        self._redis = redis_client
        if self.state_backend == "redis":
            if self._redis is None:
                self._redis = self._build_redis_client()
            if self._redis is None:
                logger.warning(
                    "Redis rate limiter backend unavailable; falling back to database/filesystem backend"
                )
                self.state_backend = "database"
        self.user_quotas: dict[int, dict[str, UserQuota]] = defaultdict(
            lambda: defaultdict(lambda: UserQuota(user_id=0))
        )
        self.global_quota = UserQuota(user_id=0)  # For global limits

        # Load persisted state if available
        if state_file:
            self.load_state()

    @staticmethod
    def _normalize_state_backend(value: str | None) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"redis"}:
            return "redis"
        if normalized in {"database", "postgres", "postgresql"}:
            return "database"
        if normalized in {"filesystem", "file"}:
            return "filesystem"
        # Default to shared backend when available.
        return "redis"

    def _build_redis_client(self):
        try:
            import redis
        except Exception:
            logger.warning("Redis package not installed; rate limiter will use database/filesystem")
            return None

        try:
            url = self.redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
            client = redis.from_url(url, decode_responses=True)
            client.ping()
            return client
        except Exception as exc:
            logger.warning("Failed to connect rate limiter to Redis (%s): %s", self.redis_url, exc)
            return None

    @staticmethod
    def _redis_quota_key(user_id: int, action: str) -> str:
        return f"nexus:rate_limits:user:{int(user_id)}:{str(action).strip().lower()}"

    @staticmethod
    def _redis_now_ts() -> float:
        return time.time()

    def _redis_cleanup_quota(self, *, user_id: int, action: str, window_seconds: int) -> None:
        if self._redis is None:
            return
        cutoff = self._redis_now_ts() - max(1, int(window_seconds))
        key = self._redis_quota_key(user_id, action)
        self._redis.zremrangebyscore(key, "-inf", cutoff)

    def _check_limit_redis(
        self, user_id: int, action: str, limit: RateLimit
    ) -> tuple[bool, str | None]:
        if self._redis is None:
            return (True, None)

        key = self._redis_quota_key(user_id, action)
        self._redis_cleanup_quota(user_id=user_id, action=action, window_seconds=limit.window_seconds)
        recent_count = int(self._redis.zcard(key) or 0)
        if recent_count >= limit.max_requests:
            oldest = self._redis.zrange(key, 0, 0, withscores=True)
            if oldest:
                oldest_ts = float(oldest[0][1] or 0.0)
                wait_time = max(1, int(limit.window_seconds - (self._redis_now_ts() - oldest_ts)))
            else:
                wait_time = max(1, int(limit.window_seconds))
            return (
                False,
                f"⏱ Rate limit exceeded for {action}. Limit: {limit.name}. Try again in {wait_time}s.",
            )
        return (True, None)

    def _record_request_redis(self, user_id: int, action: str) -> None:
        if self._redis is None:
            return
        now = self._redis_now_ts()
        member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
        key = self._redis_quota_key(user_id, action)
        self._redis.zadd(key, {member: now})
        self._redis.sadd(self._REDIS_USERS_KEY, str(user_id))
        self._redis.sadd(self._REDIS_USER_ACTIONS_KEY, f"{user_id}:{action}")

        limit = self.DEFAULT_LIMITS.get(action)
        if limit:
            self._redis.expire(key, max(limit.window_seconds * 2, 120))

        if action in ["git_api", "git_issue_create"]:
            self._redis.zadd(self._REDIS_GLOBAL_KEY, {member: now})
            self._redis.zremrangebyscore(self._REDIS_GLOBAL_KEY, "-inf", now - 3600)
            self._redis.expire(self._REDIS_GLOBAL_KEY, 7200)

    def check_limit(
        self, user_id: int, action: str, limit: RateLimit | None = None
    ) -> tuple[bool, str | None]:
        """
        Check if a request is allowed under rate limits.

        Args:
            user_id: Chat user ID
            action: Action being performed (e.g., "logs", "stats", "git_api")
            limit: Optional custom rate limit (uses default if not provided)

        Returns:
            Tuple of (allowed: bool, error_message: Optional[str])
        """
        if limit is None:
            limit = self.DEFAULT_LIMITS.get(action)
            if limit is None:
                # No limit configured, allow by default
                return (True, None)

        if self.state_backend == "redis":
            return self._check_limit_redis(user_id, action, limit)

        # Get or create user quota for this action
        quota = self.user_quotas[user_id][action]
        quota.user_id = user_id

        # Count recent requests in the sliding window
        recent_count = quota.count_recent(limit.window_seconds)

        if recent_count >= limit.max_requests:
            # Rate limit exceeded
            wait_time = int(limit.window_seconds - (time.time() - quota.timestamps[0]))
            error_msg = (
                f"⏱ Rate limit exceeded for {action}. "
                f"Limit: {limit.name}. "
                f"Try again in {wait_time}s."
            )
            logger.warning(
                f"Rate limit exceeded: user={user_id}, action={action}, "
                f"count={recent_count}/{limit.max_requests}"
            )
            return (False, error_msg)

        # Request is allowed
        return (True, None)

    def record_request(self, user_id: int, action: str):
        """
        Record a successful request for rate limiting tracking.

        Call this AFTER check_limit() returns True and the request succeeds.
        """
        if self.state_backend == "redis":
            self._record_request_redis(user_id, action)
            return

        quota = self.user_quotas[user_id][action]
        quota.user_id = user_id
        quota.add_request()

        # Also record in global quota if applicable
        if action in ["git_api", "git_issue_create"]:
            self.global_quota.add_request()

        # Periodic cleanup (every 100 requests)
        if len(quota.timestamps) % 100 == 0:
            self.cleanup_old_data()

    def check_and_record(
        self, user_id: int, action: str, limit: RateLimit | None = None
    ) -> tuple[bool, str | None]:
        """
        Convenience method to check limit and record if allowed.

        Returns:
            Tuple of (allowed: bool, error_message: Optional[str])
        """
        allowed, error = self.check_limit(user_id, action, limit)
        if allowed:
            self.record_request(user_id, action)
        return (allowed, error)

    def get_remaining(self, user_id: int, action: str) -> int:
        """
        Get remaining requests for a user+action in the current window.

        Returns:
            Number of requests remaining (or -1 if no limit configured)
        """
        limit = self.DEFAULT_LIMITS.get(action)
        if limit is None:
            return -1

        if self.state_backend == "redis":
            if self._redis is None:
                return limit.max_requests
            self._redis_cleanup_quota(
                user_id=user_id,
                action=action,
                window_seconds=limit.window_seconds,
            )
            key = self._redis_quota_key(user_id, action)
            recent_count = int(self._redis.zcard(key) or 0)
            return max(0, limit.max_requests - recent_count)

        quota = self.user_quotas[user_id][action]
        recent_count = quota.count_recent(limit.window_seconds)
        return max(0, limit.max_requests - recent_count)

    def reset_user(self, user_id: int, action: str | None = None):
        """
        Reset rate limits for a user.

        Args:
            user_id: User to reset
            action: Optional specific action to reset (resets all if None)
        """
        if self.state_backend == "redis":
            if self._redis is None:
                return
            if action:
                self._redis.delete(self._redis_quota_key(user_id, action))
                self._redis.srem(self._REDIS_USER_ACTIONS_KEY, f"{user_id}:{action}")
                return
            for tracked_action in self.DEFAULT_LIMITS.keys():
                self._redis.delete(self._redis_quota_key(user_id, tracked_action))
                self._redis.srem(self._REDIS_USER_ACTIONS_KEY, f"{user_id}:{tracked_action}")
            self._redis.srem(self._REDIS_USERS_KEY, str(user_id))
            return

        if action:
            if user_id in self.user_quotas and action in self.user_quotas[user_id]:
                self.user_quotas[user_id][action] = UserQuota(user_id=user_id)
                logger.info(f"Reset rate limit: user={user_id}, action={action}")
        else:
            self.user_quotas[user_id] = defaultdict(lambda: UserQuota(user_id=user_id))
            logger.info(f"Reset all rate limits for user={user_id}")

    def cleanup_old_data(self):
        """Remove old timestamps from all quotas to free memory."""
        if self.state_backend == "redis":
            return

        # Clean up user quotas
        for user_id in list(self.user_quotas.keys()):
            for action, quota in list(self.user_quotas[user_id].items()):
                limit = self.DEFAULT_LIMITS.get(action)
                if limit:
                    quota.cleanup_old(limit.window_seconds)
                    # Remove empty quotas
                    if not quota.timestamps:
                        del self.user_quotas[user_id][action]

            # Remove users with no active quotas
            if not self.user_quotas[user_id]:
                del self.user_quotas[user_id]

        # Clean up global quota
        self.global_quota.cleanup_old(3600)  # Keep 1 hour of data

    def get_stats(self) -> dict:
        """Get rate limiter statistics."""
        if self.state_backend == "redis":
            if self._redis is None:
                return {
                    "active_users": 0,
                    "total_tracked_actions": 0,
                    "global_requests_last_hour": 0,
                    "configured_limits": len(self.DEFAULT_LIMITS),
                    "backend": "redis-unavailable",
                }
            now = self._redis_now_ts()
            self._redis.zremrangebyscore(self._REDIS_GLOBAL_KEY, "-inf", now - 3600)
            return {
                "active_users": int(self._redis.scard(self._REDIS_USERS_KEY) or 0),
                "total_tracked_actions": int(self._redis.scard(self._REDIS_USER_ACTIONS_KEY) or 0),
                "global_requests_last_hour": int(self._redis.zcard(self._REDIS_GLOBAL_KEY) or 0),
                "configured_limits": len(self.DEFAULT_LIMITS),
                "backend": "redis",
            }
        return {
            "active_users": len(self.user_quotas),
            "total_tracked_actions": sum(len(actions) for actions in self.user_quotas.values()),
            "global_requests_last_hour": len(self.global_quota.timestamps),
            "configured_limits": len(self.DEFAULT_LIMITS),
            "backend": self.state_backend,
        }

    def save_state(self):
        """Save rate limiter state to disk for persistence."""
        if self.state_backend == "redis":
            return
        if not self.state_file:
            return

        # Convert to JSON-serializable format
        state = {
            "timestamp": time.time(),
            "user_quotas": {
                str(user_id): {action: list(quota.timestamps) for action, quota in actions.items()}
                for user_id, actions in self.user_quotas.items()
            },
            "global_quota": list(self.global_quota.timestamps),
        }

        try:
            _save_json_state_file(
                path=self.state_file,
                data=state,
                logger=logger,
                warn_only=False,
                storage_backend=self.state_backend if self.state_backend != "filesystem" else "filesystem",
                state_key=self.state_key,
            )
            logger.debug(f"Saved rate limiter state to {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to save rate limiter state: {e}")

    def load_state(self):
        """Load rate limiter state from disk."""
        if self.state_backend == "redis":
            return
        if not self.state_file:
            return

        try:
            state = _load_json_state_file(
                path=self.state_file,
                logger=logger,
                warn_only=False,
                storage_backend=self.state_backend if self.state_backend != "filesystem" else "filesystem",
                state_key=self.state_key,
                migrate_local_on_empty=True,
            )
            if not isinstance(state, dict) or not state:
                return

            # Restore user quotas
            for user_id_str, actions in state.get("user_quotas", {}).items():
                user_id = int(user_id_str)
                for action, timestamps in actions.items():
                    quota = UserQuota(user_id=user_id)
                    quota.timestamps = deque(timestamps)
                    self.user_quotas[user_id][action] = quota

            # Restore global quota
            self.global_quota.timestamps = deque(state.get("global_quota", []))

            # Cleanup old data after loading
            self.cleanup_old_data()

            logger.info(f"Loaded rate limiter state from {self.state_file}")
        except Exception as e:
            logger.error(f"Failed to load rate limiter state: {e}")


_rate_limiter: RateLimiter | None = None


def reset_rate_limiter() -> None:
    """Reset process-level shared rate limiter singleton (mainly for tests)."""
    global _rate_limiter
    _rate_limiter = None


def get_rate_limiter(
    state_file: str | None = None,
    *,
    settings=None,
) -> RateLimiter:
    """Return a shared rate limiter configured from core settings."""
    global _rate_limiter
    if _rate_limiter is None:
        state_backend = None
        redis_url = None
        if state_file is None:
            if settings is None:
                from nexus.core.config import get_runtime_settings

                settings = get_runtime_settings()

            state_file = os.path.join(settings.nexus_state_dir, "rate_limits.json")
            redis_url = settings.redis_url
            state_backend = settings.nexus_rate_limit_backend
            if str(state_backend).strip().lower() in {"database", "postgres", "postgresql"}:
                storage_backend = str(settings.nexus_storage_backend).strip().lower()
                state_backend = "database" if storage_backend != "filesystem" else "filesystem"
        _rate_limiter = RateLimiter(
            state_file,
            state_backend=state_backend,
            state_key="rate_limits",
            redis_url=redis_url,
        )
    return _rate_limiter
