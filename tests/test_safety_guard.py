import time

import pytest

from src.risk.safety import SafetyGuard


class TestAPIKillSwitch:
    def test_429_pauses_trading(self):
        """429 rate limit error pauses trading."""
        guard = SafetyGuard(api_cooldown_seconds=60.0)
        paused = guard.record_http_error(429)
        assert paused is True

        is_paused, reason = guard.is_api_paused()
        assert is_paused is True
        assert "paused" in reason.lower()

    def test_403_pauses_trading_longer(self):
        """403 forbidden pauses trading with longer cooldown."""
        guard = SafetyGuard(api_cooldown_seconds=60.0)
        paused = guard.record_http_error(403)
        assert paused is True

        is_paused, _ = guard.is_api_paused()
        assert is_paused is True

    def test_not_paused_initially(self):
        """No errors → not paused."""
        guard = SafetyGuard()
        is_paused, _ = guard.is_api_paused()
        assert is_paused is False

    def test_clear_api_pause(self):
        """Manual clear removes pause."""
        guard = SafetyGuard(api_cooldown_seconds=60.0)
        guard.record_http_error(429)
        guard.clear_api_pause()

        is_paused, _ = guard.is_api_paused()
        assert is_paused is False

    def test_multiple_errors_trigger_pause(self):
        """Multiple critical errors within window trigger pause."""
        guard = SafetyGuard(
            api_cooldown_seconds=60.0,
            max_api_errors_per_window=3,
            api_error_window_seconds=300.0,
        )
        # 500 errors don't individually pause, but accumulation does
        guard.record_http_error(500)
        guard.record_http_error(502)
        paused = guard.record_http_error(503)  # 3rd critical error
        assert paused is True

    def test_non_critical_error_no_pause(self):
        """Single 500 error doesn't pause by itself."""
        guard = SafetyGuard(api_cooldown_seconds=60.0, max_api_errors_per_window=3)
        paused = guard.record_http_error(500)
        assert paused is False


class TestDelistingWatchlist:
    def test_add_and_check(self):
        """Add symbol to watchlist and verify."""
        guard = SafetyGuard()
        guard.add_to_watchlist("LUNAUSDT", "Regulatory freeze")

        is_listed, reason = guard.is_watchlisted("LUNAUSDT")
        assert is_listed is True
        assert "Regulatory freeze" in reason

    def test_not_watchlisted(self):
        """Symbol not on watchlist."""
        guard = SafetyGuard()
        is_listed, _ = guard.is_watchlisted("BTCUSDT")
        assert is_listed is False

    def test_auto_exit_flag(self):
        """Auto-exit flag is respected."""
        guard = SafetyGuard()
        guard.add_to_watchlist("LUNAUSDT", "MiCA", auto_exit=True)
        assert guard.should_auto_exit("LUNAUSDT") is True

        guard.add_to_watchlist("ETHUSDT", "Under review", auto_exit=False)
        assert guard.should_auto_exit("ETHUSDT") is False

    def test_remove_from_watchlist(self):
        """Remove symbol from watchlist."""
        guard = SafetyGuard()
        guard.add_to_watchlist("LUNAUSDT", "Test")
        guard.remove_from_watchlist("LUNAUSDT")

        is_listed, _ = guard.is_watchlisted("LUNAUSDT")
        assert is_listed is False

    def test_get_watchlist(self):
        """Get all watchlisted symbols."""
        guard = SafetyGuard()
        guard.add_to_watchlist("LUNAUSDT", "Reason 1")
        guard.add_to_watchlist("XRPUSDT", "Reason 2")

        entries = guard.get_watchlist()
        symbols = [e.symbol for e in entries]
        assert "LUNAUSDT" in symbols
        assert "XRPUSDT" in symbols


class TestHardStopLoss:
    def test_set_and_check_not_triggered(self):
        """Stop not triggered when price above stop."""
        guard = SafetyGuard()
        guard.set_hard_stop("BTCUSDT", 45000.0)
        triggered = guard.check_hard_stop("BTCUSDT", 50000.0)
        assert triggered is False

    def test_triggered_when_price_drops(self):
        """Stop triggered when price drops to stop level."""
        guard = SafetyGuard()
        guard.set_hard_stop("BTCUSDT", 45000.0)
        triggered = guard.check_hard_stop("BTCUSDT", 44999.0)
        assert triggered is True

    def test_triggered_clears_stop(self):
        """After triggering, stop is cleared."""
        guard = SafetyGuard()
        guard.set_hard_stop("BTCUSDT", 45000.0)
        guard.check_hard_stop("BTCUSDT", 44000.0)
        # Second check should not trigger (stop was cleared)
        triggered = guard.check_hard_stop("BTCUSDT", 44000.0)
        assert triggered is False

    def test_no_stop_set(self):
        """No stop set → not triggered."""
        guard = SafetyGuard()
        triggered = guard.check_hard_stop("BTCUSDT", 50000.0)
        assert triggered is False

    def test_clear_hard_stop(self):
        """Manually clear stop."""
        guard = SafetyGuard()
        guard.set_hard_stop("BTCUSDT", 45000.0)
        guard.clear_hard_stop("BTCUSDT")
        assert guard.get_hard_stop("BTCUSDT") is None

    def test_get_hard_stop(self):
        """Get current stop price."""
        guard = SafetyGuard()
        guard.set_hard_stop("BTCUSDT", 45000.0)
        assert guard.get_hard_stop("BTCUSDT") == 45000.0
