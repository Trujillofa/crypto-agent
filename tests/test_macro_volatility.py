import time

import pytest

from src.strategy.macro_volatility import (
    MacroEvent,
    MacroEventFeed,
    MacroVolatilityStrategy,
)
from src.strategy.signals import SignalType


def _make_strategy(**overrides) -> MacroVolatilityStrategy:
    config = {
        "min_surprise_pct": 0.3,
        "strong_surprise_pct": 1.0,
        "min_atr_pct": 0.003,
        "require_momentum_confirmation": False,
    }
    config.update(overrides)
    return MacroVolatilityStrategy(config)


def _indicators(
    close: float = 50000.0,
    atr_pct: float = 0.01,
    rsi: float = 50.0,
) -> dict[str, float]:
    return {
        "close_price": close,
        "atr_pct": atr_pct,
        "rsi_14": rsi,
    }


class TestMacroVolatilityStrategy:
    @pytest.mark.asyncio
    async def test_hold_without_event_feed(self):
        """No event feed configured → HOLD."""
        strategy = _make_strategy()
        signal = await strategy.evaluate("BTCUSDT", _indicators())
        assert signal.type == SignalType.HOLD

    @pytest.mark.asyncio
    async def test_hold_without_active_event(self):
        """Event feed configured but no active event → HOLD."""
        strategy = _make_strategy()
        feed = MacroEventFeed()
        strategy.set_event_feed(feed)
        signal = await strategy.evaluate("BTCUSDT", _indicators())
        assert signal.type == SignalType.HOLD

    @pytest.mark.asyncio
    async def test_buy_on_positive_surprise(self):
        """Positive macro surprise above threshold → BUY."""
        strategy = _make_strategy()
        feed = MacroEventFeed()
        feed.push_event(
            MacroEvent(
                name="CPI",
                timestamp=time.time(),
                surprise_pct=0.5,
                direction="above",
            )
        )
        strategy.set_event_feed(feed)
        signal = await strategy.evaluate("BTCUSDT", _indicators())
        assert signal.type == SignalType.BUY
        assert signal.confidence >= 0.55
        assert "CPI" in signal.reason

    @pytest.mark.asyncio
    async def test_sell_on_negative_surprise(self):
        """Negative macro surprise → SELL."""
        strategy = _make_strategy()
        feed = MacroEventFeed()
        feed.push_event(
            MacroEvent(
                name="NFP",
                timestamp=time.time(),
                surprise_pct=-0.8,
                direction="below",
            )
        )
        strategy.set_event_feed(feed)
        signal = await strategy.evaluate("BTCUSDT", _indicators())
        assert signal.type == SignalType.SELL
        assert signal.confidence >= 0.55

    @pytest.mark.asyncio
    async def test_hold_on_small_surprise(self):
        """Surprise below minimum threshold → HOLD."""
        strategy = _make_strategy()
        feed = MacroEventFeed()
        feed.push_event(
            MacroEvent(
                name="CPI",
                timestamp=time.time(),
                surprise_pct=0.1,  # Below 0.3 threshold
                direction="above",
            )
        )
        strategy.set_event_feed(feed)
        signal = await strategy.evaluate("BTCUSDT", _indicators())
        assert signal.type == SignalType.HOLD

    @pytest.mark.asyncio
    async def test_hold_when_atr_too_low(self):
        """Volatility too low for macro trade → HOLD."""
        strategy = _make_strategy()
        feed = MacroEventFeed()
        feed.push_event(
            MacroEvent(
                name="CPI",
                timestamp=time.time(),
                surprise_pct=0.5,
                direction="above",
            )
        )
        strategy.set_event_feed(feed)
        signal = await strategy.evaluate("BTCUSDT", _indicators(atr_pct=0.001))
        assert signal.type == SignalType.HOLD

    @pytest.mark.asyncio
    async def test_no_duplicate_trades_same_event(self):
        """Same event should only be traded once."""
        strategy = _make_strategy()
        feed = MacroEventFeed()
        event = MacroEvent(
            name="CPI",
            timestamp=time.time(),
            surprise_pct=0.5,
            direction="above",
        )
        feed.push_event(event)
        strategy.set_event_feed(feed)

        # First trade
        signal1 = await strategy.evaluate("BTCUSDT", _indicators())
        assert signal1.type == SignalType.BUY

        # Second evaluation of same event → HOLD
        signal2 = await strategy.evaluate("BTCUSDT", _indicators())
        assert signal2.type == SignalType.HOLD

    @pytest.mark.asyncio
    async def test_strong_surprise_high_confidence(self):
        """Strong surprise (>= 1.0%) → high confidence."""
        strategy = _make_strategy()
        feed = MacroEventFeed()
        feed.push_event(
            MacroEvent(
                name="FOMC",
                timestamp=time.time(),
                surprise_pct=1.5,
                direction="above",
            )
        )
        strategy.set_event_feed(feed)
        signal = await strategy.evaluate("BTCUSDT", _indicators())
        assert signal.type == SignalType.BUY
        assert signal.confidence >= 0.85

    @pytest.mark.asyncio
    async def test_momentum_confirmation_blocks_conflicting_signal(self):
        """With momentum confirmation, positive surprise + bearish RSI → HOLD."""
        strategy = _make_strategy(require_momentum_confirmation=True)
        feed = MacroEventFeed()
        feed.push_event(
            MacroEvent(
                name="CPI",
                timestamp=time.time(),
                surprise_pct=0.5,
                direction="above",
            )
        )
        strategy.set_event_feed(feed)
        signal = await strategy.evaluate("BTCUSDT", _indicators(rsi=30.0))
        assert signal.type == SignalType.HOLD

    @pytest.mark.asyncio
    async def test_expired_event_returns_hold(self):
        """Event outside reaction window → HOLD."""
        strategy = _make_strategy()
        feed = MacroEventFeed()
        feed.push_event(
            MacroEvent(
                name="CPI",
                timestamp=time.time() - 7200,  # 2 hours ago (outside 1h window)
                surprise_pct=0.5,
                direction="above",
            )
        )
        strategy.set_event_feed(feed)
        signal = await strategy.evaluate("BTCUSDT", _indicators())
        assert signal.type == SignalType.HOLD

    @pytest.mark.asyncio
    async def test_macro_surprise_in_indicators(self):
        """Signal indicators include macro_surprise_pct."""
        strategy = _make_strategy()
        feed = MacroEventFeed()
        feed.push_event(
            MacroEvent(
                name="CPI",
                timestamp=time.time(),
                surprise_pct=0.7,
                direction="above",
            )
        )
        strategy.set_event_feed(feed)
        signal = await strategy.evaluate("BTCUSDT", _indicators())
        assert signal.indicators.get("macro_surprise_pct") == 0.7

    @pytest.mark.asyncio
    async def test_missing_indicator_raises(self):
        """Missing required indicator raises ValueError."""
        strategy = _make_strategy()
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate("BTCUSDT", {"close_price": 50000.0})

    @pytest.mark.asyncio
    async def test_get_name(self):
        strategy = _make_strategy()
        assert strategy.get_name() == "MacroVolatility"


class TestMacroEventFeed:
    def test_push_and_get_active(self):
        feed = MacroEventFeed()
        event = MacroEvent(name="CPI", timestamp=time.time(), surprise_pct=0.5)
        feed.push_event(event)
        assert feed.get_active_event() is event

    def test_expired_event_returns_none(self):
        feed = MacroEventFeed()
        event = MacroEvent(name="CPI", timestamp=time.time() - 7200)
        feed.push_event(event)
        assert feed.get_active_event() is None

    def test_clear(self):
        feed = MacroEventFeed()
        feed.push_event(MacroEvent(name="CPI", timestamp=time.time()))
        feed.clear()
        assert feed.get_active_event() is None
