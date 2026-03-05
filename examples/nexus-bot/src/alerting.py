"""Alerting system for Nexus - monitors for errors and stuck workflows.

Sends alerts for critical issues via the EventBus (emit_alert):
- High error rates
- Stuck workflows (>1 hour without progress)
- Repeated agent failures
- System degradation
"""

import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from nexus.core.integrations.audit_query_factory import get_audit_query
from nexus.core.integrations.notifications import emit_alert
from nexus.core.state_manager import HostStateManager

logger = logging.getLogger(__name__)


class AlertingSystem:
    """Monitors system health and sends alerts for critical issues.

    Uses :func:`get_audit_query` for data and :func:`emit_alert`
    for notification delivery (via EventBus).
    """

    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler()
        self.state_manager = HostStateManager()

        # Thresholds (configurable via environment)
        self.error_rate_threshold = int(
            os.getenv("ALERT_ERROR_RATE_THRESHOLD", "10")
        )  # errors per hour
        self.stuck_workflow_hours = int(
            os.getenv("ALERT_STUCK_WORKFLOW_HOURS", "2")
        )  # hours without progress
        self.agent_failure_threshold = int(
            os.getenv("ALERT_AGENT_FAILURE_THRESHOLD", "3")
        )  # failures in 1 hour

        # Alert cooldown (prevent spam)
        self.alert_cooldown_minutes = int(os.getenv("ALERT_COOLDOWN_MINUTES", "30"))
        self.last_alerts: dict[str, datetime] = {}

    def start(self) -> None:
        """Start the alerting scheduler."""
        check_interval_minutes = int(os.getenv("ALERT_CHECK_INTERVAL_MINUTES", "15"))

        self.scheduler.add_job(
            self.check_for_alerts,
            trigger=IntervalTrigger(minutes=check_interval_minutes),
            id="alert_check",
            name="Alert System Check",
            replace_existing=True,
        )

        self.scheduler.start()
        logger.info("Alerting system started. Checking every %s minutes", check_interval_minutes)

    def stop(self) -> None:
        """Stop the alerting scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Alerting system stopped")

    async def check_for_alerts(self) -> None:
        """Main alert checking loop - runs periodically."""
        try:
            logger.debug("Running alert checks...")
            await self._check_error_rates()
            await self._check_stuck_workflows()
            await self._check_agent_failures()
        except Exception as e:
            logger.error("Error in alert check: %s", e)

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    async def _check_error_rates(self) -> None:
        """Check for high error rates."""
        try:
            query = get_audit_query()
            error_events = {"AGENT_FAILED", "AGENT_TIMEOUT_KILL", "ERROR", "WORKFLOW_ERROR"}
            error_count = query.count_events(error_events, since_hours=1)

            if error_count >= self.error_rate_threshold:
                alert_key = "high_error_rate"
                if self._should_send_alert(alert_key):
                    message = (
                        f"🚨 High Error Rate Detected\n\n"
                        f"Detected {error_count} errors in the last hour.\n"
                        f"Threshold: {self.error_rate_threshold} errors/hour\n\n"
                        f"Check /logs and /audit for details."
                    )
                    emit_alert(message, severity="error", source="alerting")
                    self.last_alerts[alert_key] = datetime.now()
                    logger.info("Alert sent: %s", alert_key)
        except Exception as e:
            logger.error("Error checking error rates: %s", e)

    async def _check_stuck_workflows(self) -> None:
        """Check for workflows stuck without progress."""
        try:
            stuck_workflows = self._find_stuck_workflows()

            if stuck_workflows:
                alert_key = "stuck_workflows"
                if self._should_send_alert(alert_key):
                    message = f"⏰ Stuck Workflows Detected\n\nFound {len(stuck_workflows)} stuck workflow(s):\n\n"

                    for workflow in stuck_workflows[:5]:
                        issue = workflow["issue_number"]
                        status = workflow["status"]
                        hours_stuck = workflow["hours_stuck"]
                        message += f"• Issue #{issue} - {status} ({hours_stuck:.1f}h)\n"

                    if len(stuck_workflows) > 5:
                        message += f"\n... and {len(stuck_workflows) - 5} more"

                    message += "\n\nUse /continue <issue#> to check status\n"
                    message += "Use /kill <issue#> to stop stuck agents"

                    emit_alert(message, severity="warning", source="alerting")
                    self.last_alerts[alert_key] = datetime.now()
                    logger.info("Alert sent: %s", alert_key)
        except Exception as e:
            logger.error("Error checking stuck workflows: %s", e)

    async def _check_agent_failures(self) -> None:
        """Check for repeated agent failures."""
        try:
            query = get_audit_query()
            failure_events = {"AGENT_FAILED", "AGENT_TIMEOUT_KILL"}
            failures = query.count_events(failure_events, since_hours=1)

            if failures >= self.agent_failure_threshold:
                alert_key = "agent_failures"
                if self._should_send_alert(alert_key):
                    message = (
                        f"⚠️ Repeated Agent Failures\n\n"
                        f"Detected {failures} agent failures in the last hour.\n"
                        f"Threshold: {self.agent_failure_threshold} failures/hour\n\n"
                        f"Check /audit and /logs for details."
                    )
                    emit_alert(message, severity="warning", source="alerting")
                    self.last_alerts[alert_key] = datetime.now()
                    logger.info("Alert sent: %s", alert_key)
        except Exception as e:
            logger.error("Error checking agent failures: %s", e)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_stuck_workflows(self) -> list[dict]:
        """Find workflows that haven't made progress recently."""
        try:
            stuck = []
            cutoff_time = datetime.now() - timedelta(hours=self.stuck_workflow_hours)

            tracked_issues = self.state_manager.load_tracked_issues()

            for issue_key, issue_data in tracked_issues.items():
                status = issue_data.get("status", "unknown")

                if status in ["implemented", "rejected", "stopped"]:
                    continue

                last_update_str = issue_data.get("updated_at")
                if last_update_str:
                    try:
                        last_update = datetime.fromisoformat(last_update_str)

                        if last_update < cutoff_time:
                            hours_stuck = (datetime.now() - last_update).total_seconds() / 3600

                            stuck.append(
                                {
                                    "issue_number": issue_key,
                                    "status": status,
                                    "last_update": last_update_str,
                                    "hours_stuck": hours_stuck,
                                }
                            )
                    except Exception:
                        continue

            return stuck
        except Exception as e:
            logger.error("Error finding stuck workflows: %s", e)
            return []

    def _should_send_alert(self, alert_key: str) -> bool:
        """Check if enough time has passed since last alert of this type."""
        if alert_key not in self.last_alerts:
            return True

        last_alert_time = self.last_alerts[alert_key]
        cooldown_duration = timedelta(minutes=self.alert_cooldown_minutes)

        return datetime.now() - last_alert_time >= cooldown_duration

    async def send_custom_alert(self, message: str, title: str = "🔔 Alert") -> None:
        """Send a custom alert via EventBus."""
        try:
            full_message = f"{title}\n\n{message}"
            emit_alert(full_message, severity="info", source="alerting")
            logger.info("Custom alert sent: %s", title)
        except Exception as e:
            logger.error("Error sending custom alert: %s", e)


# Global singleton
_alerting_system: AlertingSystem | None = None


def get_alerting_system() -> AlertingSystem | None:
    """Get the global AlertingSystem instance."""
    return _alerting_system


def init_alerting_system() -> AlertingSystem:
    """Initialize the global alerting system.

    Returns:
        AlertingSystem instance
    """
    global _alerting_system
    _alerting_system = AlertingSystem()
    return _alerting_system
