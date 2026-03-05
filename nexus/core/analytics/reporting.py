"""Analytics module for generating workflow statistics.

Delegates logic to nexus-arc LokiAnalyticsAdapter and Grafana.
"""

import logging
import os

from nexus.adapters.analytics.loki import LokiAnalyticsAdapter

logger = logging.getLogger(__name__)

LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100")


def get_stats_report(lookback_days: int = 30) -> str:
    """Generate a statistics report using Loki observability backend.

    Args:
        lookback_days: Number of days to include in analysis

    Returns:
        Formatted statistics report
    """
    try:
        adapter = LokiAnalyticsAdapter(loki_url=LOKI_URL)
        return adapter.format_stats_report(lookback_days=lookback_days)
    except Exception as e:
        logger.error(f"Failed to generate stats from Loki: {e}")
        return "⚠️ Unable to connect to Loki to retrieve analytics."
