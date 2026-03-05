"""Unit tests for analytics module.

Tests the Loki-backed ``get_stats_report()`` function.
"""

from unittest.mock import MagicMock, patch


class TestGetStatsReport:
    """Tests for the public ``get_stats_report()`` function."""

    def test_delegates_to_loki_adapter(self):
        mock_adapter = MagicMock()
        mock_adapter.format_stats_report.return_value = "📊 **Report**"

        with patch("nexus.core.analytics.reporting.LokiAnalyticsAdapter", return_value=mock_adapter):
            from nexus.core.analytics.reporting import get_stats_report

            result = get_stats_report(lookback_days=7)

        mock_adapter.format_stats_report.assert_called_once_with(lookback_days=7)
        assert "📊" in result

    def test_returns_fallback_on_loki_error(self):
        with patch("nexus.core.analytics.reporting.LokiAnalyticsAdapter", side_effect=ConnectionError("no loki")):
            from nexus.core.analytics.reporting import get_stats_report

            result = get_stats_report(lookback_days=1)

        assert "⚠️" in result
        assert "Unable" in result

    def test_default_lookback_days(self):
        mock_adapter = MagicMock()
        mock_adapter.format_stats_report.return_value = "report"

        with patch("nexus.core.analytics.reporting.LokiAnalyticsAdapter", return_value=mock_adapter):
            from nexus.core.analytics.reporting import get_stats_report

            get_stats_report()

        mock_adapter.format_stats_report.assert_called_once_with(lookback_days=30)
