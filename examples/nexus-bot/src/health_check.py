"""Health check and monitoring endpoint for Nexus services.

Provides HTTP endpoints for monitoring system health, status, and metrics.
Designed to run alongside nexus-telegram, nexus-discord, and nexus-processor services.
"""

import logging
import os
import subprocess
from datetime import datetime

from flask import Flask, jsonify

from nexus.core.config.bootstrap import initialize_runtime

initialize_runtime(configure_logging=False)

from nexus.core.config import LOGS_DIR, NEXUS_RUNTIME_DIR
from nexus.core.integrations.audit_query_factory import get_audit_query
from nexus.core.rate_limiter import get_rate_limiter

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Create Flask app
app = Flask(__name__)


def check_service_status(service_name: str) -> dict:
    """
    Check if a systemd service is running.

    Args:
        service_name: Name of the systemd service

    Returns:
        Dict with status, uptime, and memory info
    """
    try:
        # Check if service is active
        result = subprocess.run(
            ["systemctl", "is-active", service_name], capture_output=True, text=True, timeout=5
        )
        is_active = result.stdout.strip() == "active"

        if not is_active:
            return {
                "running": False,
                "status": result.stdout.strip(),
                "error": "Service not active",
            }

        # Get service details
        status_result = subprocess.run(
            ["systemctl", "status", service_name, "--no-pager"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        # Parse uptime and memory from status output
        status_lines = status_result.stdout.split("\n")
        info: dict[str, bool | str | int] = {"running": True, "status": "active"}

        for line in status_lines:
            if "Active:" in line:
                # Extract uptime
                if "since" in line:
                    since_part = line.split("since")[1].strip()
                    info["since"] = since_part.split(";")[0].strip()
            elif "Memory:" in line:
                # Extract memory usage
                memory = line.split("Memory:")[1].strip().split()[0]
                info["memory"] = memory
            elif "Main PID:" in line:
                # Extract PID
                parts = line.split("Main PID:")[1].strip().split()
                if parts:
                    info["pid"] = int(parts[0])

        return info

    except subprocess.TimeoutExpired:
        return {"running": False, "error": "Check timeout"}
    except Exception as e:
        logger.error(f"Error checking {service_name}: {e}")
        return {"running": False, "error": str(e)}


def get_recent_audit_activity(hours: int = 1) -> dict:
    """
    Get activity from audit log in the last N hours.

    Args:
        hours: Number of hours to look back

    Returns:
        Dict with event counts
    """
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
        logger.error(f"Error reading audit log: {e}")
        return {"error": str(e)}


def get_disk_usage() -> dict:
    """Get disk usage for data and logs directories."""
    try:

        def get_dir_size(path):
            """Calculate directory size in bytes."""
            total = 0
            if os.path.exists(path):
                for entry in os.scandir(path):
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat().st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += get_dir_size(entry.path)
            return total

        data_size = get_dir_size(NEXUS_RUNTIME_DIR)
        logs_size = get_dir_size(LOGS_DIR)

        return {
            "runtime_dir": {
                "path": NEXUS_RUNTIME_DIR,
                "size_bytes": data_size,
                "size_mb": round(data_size / 1024 / 1024, 2),
            },
            "logs_dir": {
                "path": LOGS_DIR,
                "size_bytes": logs_size,
                "size_mb": round(logs_size / 1024 / 1024, 2),
            },
        }
    except Exception as e:
        logger.error(f"Error calculating disk usage: {e}")
        return {"error": str(e)}


@app.route("/health", methods=["GET"])
def health_check():
    """
    Basic health check endpoint.

    Returns 200 OK if service is running.
    """
    return (
        jsonify(
            {
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "service": "nexus-health-check",
            }
        ),
        200,
    )


@app.route("/status", methods=["GET"])
def full_status():
    """
    Comprehensive status check of all Nexus services.

    Returns detailed information about:
    - nexus-telegram service status
    - nexus-discord service status
    - nexus-processor service status
    - Recent audit activity
    - Disk usage
    - Rate limiter stats
    """
    # Check services
    telegram_status = check_service_status("nexus-telegram.service")
    discord_status = check_service_status("nexus-discord.service")
    processor_status = check_service_status("nexus-processor.service")

    # Get recent activity
    recent_activity = get_recent_audit_activity(hours=1)

    # Get disk usage
    disk_usage = get_disk_usage()

    # Get rate limiter stats
    rate_limiter = get_rate_limiter()
    rate_stats = rate_limiter.get_stats()

    # Overall health
    overall_healthy = (
        telegram_status.get("running", False)
        and discord_status.get("running", False)
        and processor_status.get("running", False)
    )

    return jsonify(
        {
            "overall_status": "healthy" if overall_healthy else "degraded",
            "timestamp": datetime.now().isoformat(),
            "services": {
                "nexus-telegram": telegram_status,
                "nexus-discord": discord_status,
                "nexus-processor": processor_status,
            },
            "recent_activity": recent_activity,
            "disk_usage": disk_usage,
            "rate_limiter": rate_stats,
        }
    ), (200 if overall_healthy else 503)


@app.route("/metrics", methods=["GET"])
def metrics():
    """
    Prometheus-style metrics endpoint.

    Returns metrics in a format suitable for scraping by monitoring tools.
    """
    telegram_status = check_service_status("nexus-telegram.service")
    discord_status = check_service_status("nexus-discord.service")
    processor_status = check_service_status("nexus-processor.service")
    recent_activity = get_recent_audit_activity(hours=1)
    rate_limiter = get_rate_limiter()
    rate_stats = rate_limiter.get_stats()

    # Format as simple key-value pairs
    metrics_lines = [
        "# HELP nexus_service_up Service is running (1 = up, 0 = down)",
        "# TYPE nexus_service_up gauge",
        f"nexus_service_up{{service=\"telegram\"}} {1 if telegram_status.get('running') else 0}",
        f"nexus_service_up{{service=\"discord\"}} {1 if discord_status.get('running') else 0}",
        f"nexus_service_up{{service=\"processor\"}} {1 if processor_status.get('running') else 0}",
        "",
        "# HELP nexus_audit_events_total Total audit events in last hour",
        "# TYPE nexus_audit_events_total counter",
        f"nexus_audit_events_total {recent_activity.get('total_events', 0)}",
        "",
        "# HELP nexus_rate_limiter_active_users Active users with rate limits",
        "# TYPE nexus_rate_limiter_active_users gauge",
        f"nexus_rate_limiter_active_users {rate_stats.get('active_users', 0)}",
        "",
        "# HELP nexus_rate_limiter_tracked_actions Tracked rate limit actions",
        "# TYPE nexus_rate_limiter_tracked_actions gauge",
        f"nexus_rate_limiter_tracked_actions {rate_stats.get('total_tracked_actions', 0)}",
    ]

    return "\n".join(metrics_lines), 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/ping", methods=["GET"])
def ping():
    """Simple ping endpoint for uptime monitoring."""
    return jsonify({"status": "pong", "timestamp": datetime.now().isoformat()}), 200


def main():
    """Run the health check server."""
    port = int(os.getenv("HEALTH_CHECK_PORT", 8080))
    host = os.getenv("HEALTH_CHECK_HOST", "127.0.0.1")

    logger.info(f"Starting health check server on {host}:{port}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
