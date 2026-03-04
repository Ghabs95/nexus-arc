"""Scheduled reports and daily digests for Nexus.

Sends automated reports via the EventBus (emit_alert) at configured times.
"""

import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from nexus.core.integrations.audit_query_factory import get_audit_query
from nexus.core.integrations.notifications import emit_alert
from nexus.core.state_manager import HostStateManager
from nexus.core.user_manager import get_user_manager

logger = logging.getLogger(__name__)


class ReportScheduler:
    """Manages scheduled reports.

    Uses :func:`get_audit_query` for data and :func:`emit_alert`
    for delivery (Telegram + Discord via EventBus).
    """

    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler()
        self.state_manager = HostStateManager()
        self.user_manager = get_user_manager()

    def start(self) -> None:
        """Start the scheduler."""
        # Daily digest at 9:00 AM
        daily_digest_hour = int(os.getenv("DAILY_DIGEST_HOUR", "9"))
        daily_digest_minute = int(os.getenv("DAILY_DIGEST_MINUTE", "0"))

        self.scheduler.add_job(
            self.send_daily_digest,
            trigger=CronTrigger(hour=daily_digest_hour, minute=daily_digest_minute),
            id="daily_digest",
            name="Daily Digest Report",
            replace_existing=True,
        )

        # Weekly summary on Monday at 9:00 AM
        weekly_summary_enabled = os.getenv("WEEKLY_SUMMARY_ENABLED", "false").lower() == "true"
        if weekly_summary_enabled:
            self.scheduler.add_job(
                self.send_weekly_summary,
                trigger=CronTrigger(day_of_week="mon", hour=9, minute=0),
                id="weekly_summary",
                name="Weekly Summary Report",
                replace_existing=True,
            )

        self.scheduler.start()
        logger.info(
            "Report scheduler started. Daily digest at %02d:%02d",
            daily_digest_hour,
            daily_digest_minute,
        )

    def stop(self) -> None:
        """Stop the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Report scheduler stopped")

    async def send_daily_digest(self) -> None:
        """Send daily digest report."""
        try:
            logger.info("Generating daily digest...")

            activity = self._get_recent_activity(hours=24)
            tracked_status = self._get_tracked_issues_status()
            user_stats = self.user_manager.get_all_users_stats()

            message = self._build_daily_digest_message(
                activity=activity, tracked_status=tracked_status, user_stats=user_stats
            )

            emit_alert(message, severity="info", source="report_scheduler")
            logger.info("Daily digest sent successfully")
        except Exception as e:
            logger.error("Error sending daily digest: %s", e)

    async def send_weekly_summary(self) -> None:
        """Send weekly summary report."""
        try:
            logger.info("Generating weekly summary...")

            activity = self._get_recent_activity(hours=24 * 7)
            tracked_status = self._get_tracked_issues_status()
            user_stats = self.user_manager.get_all_users_stats()

            message = self._build_weekly_summary_message(
                activity=activity, tracked_status=tracked_status, user_stats=user_stats
            )

            emit_alert(message, severity="info", source="report_scheduler")
            logger.info("Weekly summary sent successfully")
        except Exception as e:
            logger.error("Error sending weekly summary: %s", e)

    def _get_recent_activity(self, hours: int) -> dict:
        """Get recent activity from audit log."""
        try:
            query = get_audit_query()
            events = query.get_events(since_hours=hours)
            if not events:
                return {"total_events": 0, "event_types": {}, "time_window_hours": hours}

            event_counts: dict = {}
            for evt in events:
                et = evt.get("event_type", "UNKNOWN")
                event_counts[et] = event_counts.get(et, 0) + 1

            return {
                "total_events": len(events),
                "event_types": event_counts,
                "time_window_hours": hours,
            }
        except Exception as e:
            logger.error("Error reading audit log: %s", e)
            return {"error": str(e)}

    def _get_tracked_issues_status(self) -> dict:
        """Get status of all tracked issues."""
        try:
            tracked_issues = self.state_manager.load_tracked_issues()

            total_issues = len(tracked_issues)
            status_counts = {}

            for issue_key, issue_data in tracked_issues.items():
                payload = issue_data if isinstance(issue_data, dict) else {}
                status = str(payload.get("status", "")).strip().lower()
                if not status:
                    legacy_state = str(payload.get("last_seen_state", "")).strip().lower()
                    if legacy_state in {
                        "closed",
                        "resolved",
                        "done",
                        "completed",
                        "implemented",
                        "rejected",
                    }:
                        status = legacy_state
                    else:
                        status = "active"
                status_counts[status] = status_counts.get(status, 0) + 1

            return {"total_issues": total_issues, "status_counts": status_counts}
        except Exception as e:
            logger.error("Error getting tracked issues status: %s", e)
            return {"error": str(e)}

    def _build_daily_digest_message(
        self, activity: dict, tracked_status: dict, user_stats: dict
    ) -> str:
        """Build daily digest message."""
        now = datetime.now()

        message = "📊 Daily Digest\n"
        message += f"📅 {now.strftime('%A, %B %d, %Y')}\n\n"

        # Activity section
        message += "📈 Activity (Last 24 Hours)\n"
        if "error" in activity:
            message += f"⚠️ {activity['error']}\n"
        else:
            total = activity.get("total_events", 0)
            message += f"Total Events: {total}\n"

            if total > 0:
                event_types = activity.get("event_types", {})
                for event_type, count in sorted(
                    event_types.items(), key=lambda x: x[1], reverse=True
                )[:5]:
                    message += f"  • {event_type}: {count}\n"

        message += "\n"

        # Tracked issues section
        message += "🎯 Tracked Issues\n"
        if "error" in tracked_status:
            message += f"⚠️ {tracked_status['error']}\n"
        else:
            total_issues = tracked_status.get("total_issues", 0)
            message += f"Total: {total_issues}\n"

            status_counts = tracked_status.get("status_counts", {})
            for status, count in sorted(status_counts.items()):
                emoji = self._get_status_emoji(status)
                message += f"  {emoji} {status}: {count}\n"

        message += "\n"

        # User section
        message += "👥 Users\n"
        total_users = user_stats.get("total_users", 0)
        total_tracked = user_stats.get("total_tracked_issues", 0)
        message += f"Active Users: {total_users}\n"
        message += f"User-Tracked Issues: {total_tracked}\n"

        return message

    def _build_weekly_summary_message(
        self, activity: dict, tracked_status: dict, user_stats: dict
    ) -> str:
        """Build weekly summary message."""
        now = datetime.now()
        week_start = now - timedelta(days=7)

        message = "📊 Weekly Summary\n"
        message += f"📅 {week_start.strftime('%b %d')} - {now.strftime('%b %d, %Y')}\n\n"

        # Activity section
        message += "📈 Activity (Last 7 Days)\n"
        if "error" in activity:
            message += f"⚠️ {activity['error']}\n"
        else:
            total = activity.get("total_events", 0)
            message += f"Total Events: {total}\n"

            if total > 0:
                event_types = activity.get("event_types", {})
                message += "\nTop Events:\n"
                for event_type, count in sorted(
                    event_types.items(), key=lambda x: x[1], reverse=True
                )[:10]:
                    message += f"  • {event_type}: {count}\n"

        message += "\n"

        # Tracked issues section
        message += "🎯 Tracked Issues\n"
        if "error" in tracked_status:
            message += f"⚠️ {tracked_status['error']}\n"
        else:
            total_issues = tracked_status.get("total_issues", 0)
            message += f"Total: {total_issues}\n"

            status_counts = tracked_status.get("status_counts", {})
            for status, count in sorted(status_counts.items()):
                emoji = self._get_status_emoji(status)
                message += f"  {emoji} {status}: {count}\n"

        message += "\n"

        # User section
        message += "👥 User Engagement\n"
        total_users = user_stats.get("total_users", 0)
        total_tracked = user_stats.get("total_tracked_issues", 0)
        total_projects = user_stats.get("total_projects", 0)
        message += f"Active Users: {total_users}\n"
        message += f"Projects: {total_projects}\n"
        message += f"User-Tracked Issues: {total_tracked}\n"

        return message

    def _get_status_emoji(self, status: str) -> str:
        """Get emoji for status."""
        emoji_map = {
            "pending": "⏳",
            "processing": "🔄",
            "approved": "✅",
            "rejected": "❌",
            "implemented": "🎉",
            "error": "⚠️",
            "paused": "⏸️",
        }
        return emoji_map.get(status.lower(), "📌")
