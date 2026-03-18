"""Unit tests for multi-timeframe (MTF) data joining.

CRITICAL: These tests verify STRICT no-lookahead behavior using candle close times.
A regime bar is ONLY available when its close time is <= entry bar open time.
Regime Close Time = Regime Open Time + Regime Duration.
"""

from datetime import datetime

from src.features.reader import IndicatorReader


class TestJoinTimeframes:
    """Test _join_timeframes helper method with strict close-time lookahead."""

    def test_regime_not_available_before_close(self):
        """Regime bar at 08:00 (4h) closes at 12:00. NOT available at 09:00."""
        entry_data = [
            {"time": datetime(2024, 1, 1, 9, 0), "close": 100},
            {"time": datetime(2024, 1, 1, 10, 0), "close": 101},
            {"time": datetime(2024, 1, 1, 11, 0), "close": 102},
        ]
        regime_data = [
            {"time": datetime(2024, 1, 1, 8, 0), "ema_slope": 0.01},
        ]

        joined = IndicatorReader._join_timeframes(entry_data, regime_data, regime_timeframe="4h")

        for bar in joined:
            # 8:00 + 4h = 12:00. 12:00 > 9,10,11. So not available.
            assert "ema_slope_4h" not in bar

    def test_regime_available_exactly_at_close(self):
        """Regime bar at 08:00 (4h) closes at 12:00. Available at 12:00 entry."""
        entry_data = [
            {"time": datetime(2024, 1, 1, 12, 0), "close": 100},
        ]
        regime_data = [
            {"time": datetime(2024, 1, 1, 8, 0), "ema_slope": 0.01},
        ]

        joined = IndicatorReader._join_timeframes(entry_data, regime_data, regime_timeframe="4h")

        assert len(joined) == 1
        assert joined[0]["ema_slope_4h"] == 0.01

    def test_regime_updates_at_next_close(self):
        """Regime updates when the next bar closes."""
        entry_data = [
            {"time": datetime(2024, 1, 1, 12, 0), "close": 100},  # Sees 8:00 (Closed 12:00)
            {"time": datetime(2024, 1, 1, 15, 0), "close": 101},  # Still sees 8:00
            {"time": datetime(2024, 1, 1, 16, 0), "close": 102},  # Sees 12:00 (Closed 16:00)
        ]
        regime_data = [
            {"time": datetime(2024, 1, 1, 8, 0), "val": 1},
            {"time": datetime(2024, 1, 1, 12, 0), "val": 2},
        ]

        joined = IndicatorReader._join_timeframes(entry_data, regime_data, regime_timeframe="4h")

        assert joined[0]["val_4h"] == 1
        assert joined[1]["val_4h"] == 1
        assert joined[2]["val_4h"] == 2

    def test_daily_regime(self):
        """Test with 1d timeframe."""
        # 1d bar at 2024-01-01 00:00 closes at 2024-01-02 00:00.
        entry_data = [
            {"time": datetime(2024, 1, 1, 12, 0), "close": 100},  # Not yet closed
            {"time": datetime(2024, 1, 2, 0, 0), "close": 101},  # Closed!
        ]
        regime_data = [
            {"time": datetime(2024, 1, 1, 0, 0), "val": 99},
        ]

        joined = IndicatorReader._join_timeframes(entry_data, regime_data, regime_timeframe="1d")

        assert "val_1d" not in joined[0]
        assert joined[1]["val_1d"] == 99


class TestJoinTimeframesEdgeCases:
    """Test edge cases."""

    def test_missing_regime_time_key(self):
        """Should handle regime data with missing time key."""
        entry_data = [{"time": datetime(2024, 1, 1, 12, 0), "close": 100}]
        regime_data = [{"ema_slope": 0.01}]  # Missing time

        joined = IndicatorReader._join_timeframes(entry_data, regime_data, regime_timeframe="4h")

        assert "ema_slope_4h" not in joined[0]

    def test_empty_entry_returns_empty(self):
        """Empty entry data returns empty list."""
        result = IndicatorReader._join_timeframes([], [], regime_timeframe="4h")
        assert result == []

    def test_empty_regime_returns_entry_only(self):
        """Empty regime data returns entry data without regime indicators."""
        entry_data = [{"time": datetime(2024, 1, 1, 8, 0), "close": 100}]
        joined = IndicatorReader._join_timeframes(entry_data, [], regime_timeframe="4h")

        assert len(joined) == 1
        assert "ema_slope_4h" not in joined[0]

    def test_overlapping_keys_preserved(self):
        """Entry values should be preserved when regime has same keys."""
        entry_data = [{"time": datetime(2024, 1, 1, 12, 0), "close": 100, "ema_slope": 0.05}]
        regime_data = [{"time": datetime(2024, 1, 1, 8, 0), "ema_slope": 0.01}]

        joined = IndicatorReader._join_timeframes(entry_data, regime_data, regime_timeframe="4h")

        assert joined[0]["ema_slope"] == 0.05  # Entry value preserved
        assert joined[0]["ema_slope_4h"] == 0.01  # Regime value added
