import pytest

from src.strategy.aggregator import SignalAggregator
from src.strategy.signals import Signal, SignalType


class TestSignalAggregator:
    @pytest.fixture
    def aggregator(self):
        return SignalAggregator({"buy_threshold": 0.5, "sell_threshold": -0.5, "min_agreement": 1})

    def _create_signal(
        self,
        signal_type: SignalType,
        confidence: float = 1.0,
        reason: str = "Test",
        trading_mode: str = "spot",
    ) -> Signal:
        return Signal(
            type=signal_type,
            symbol="BTCUSDT",
            price=50000.0,
            confidence=confidence,
            reason=reason,
            indicators={},
            trading_mode=trading_mode,
        )

    def test_empty_input(self, aggregator):
        result = aggregator.aggregate("BTCUSDT", [])
        assert result.type == SignalType.HOLD
        assert "No signals" in result.reason

    def test_single_buy_consensus(self, aggregator):
        signals = [self._create_signal(SignalType.BUY, 0.8)]
        result = aggregator.aggregate("BTCUSDT", signals)
        assert result.type == SignalType.BUY
        assert result.confidence == 0.8
        assert "Consensus BUY" in result.reason

    def test_mixed_signals_cancel_out(self, aggregator):
        signals = [
            self._create_signal(SignalType.BUY, 0.8),
            self._create_signal(SignalType.SELL, 0.8),
        ]
        result = aggregator.aggregate("BTCUSDT", signals)
        assert result.type == SignalType.HOLD
        assert "Mixed/Weak Signals" in result.reason

    def test_strong_sell_consensus(self, aggregator):
        signals = [
            self._create_signal(SignalType.SELL, 1.0),
            self._create_signal(SignalType.SELL, 0.5),
            self._create_signal(SignalType.HOLD),
        ]
        result = aggregator.aggregate("BTCUSDT", signals)
        assert result.type == SignalType.SELL
        assert result.confidence == 1.0
        assert "Consensus SELL" in result.reason

    def test_min_agreement_filter(self):
        agg = SignalAggregator({"min_agreement": 2})
        signals = [self._create_signal(SignalType.BUY, 1.0)]
        result = agg.aggregate("BTCUSDT", signals)
        assert result.type == SignalType.HOLD
        assert "Insufficient agreement" in result.reason

        signals.append(self._create_signal(SignalType.BUY, 1.0))
        result = agg.aggregate("BTCUSDT", signals)
        assert result.type == SignalType.BUY

    def test_trading_mode_respects_uniform_signals(self):
        agg = SignalAggregator()
        signals = [
            self._create_signal(SignalType.BUY, trading_mode="futures"),
            self._create_signal(SignalType.SELL, trading_mode="futures"),
        ]
        result = agg.aggregate("BTCUSDT", signals)
        assert result.trading_mode == "futures"

    def test_trading_mode_defaults_on_mixed_signals(self):
        agg = SignalAggregator(default_trading_mode="spot")
        signals = [
            self._create_signal(SignalType.BUY, trading_mode="spot"),
            self._create_signal(SignalType.SELL, trading_mode="futures"),
        ]
        result = agg.aggregate("BTCUSDT", signals)
        assert result.trading_mode == "spot"

    def test_per_symbol_buy_threshold_override(self, aggregator):
        """Per-symbol buy_threshold should override default."""
        signals = [self._create_signal(SignalType.BUY, 0.8)]
        # Default buy_threshold is 0.5, so 0.8 should trigger BUY
        result = aggregator.aggregate("BTCUSDT", signals)
        assert result.type == SignalType.BUY

        # Override with higher threshold - same signal should now HOLD
        symbol_config = {"buy_threshold": 1.0}
        result = aggregator.aggregate("BTCUSDT", signals, symbol_config=symbol_config)
        assert result.type == SignalType.HOLD

    def test_per_symbol_sell_threshold_override(self, aggregator):
        """Per-symbol sell_threshold should override default."""
        signals = [
            self._create_signal(SignalType.SELL, 0.8),
            self._create_signal(SignalType.SELL, 0.3),
        ]
        # Default sell_threshold is -0.5, so -1.1 should trigger SELL
        result = aggregator.aggregate("BTCUSDT", signals)
        assert result.type == SignalType.SELL

        # Override with more conservative threshold - same signal should now HOLD
        symbol_config = {"sell_threshold": -2.0}
        result = aggregator.aggregate("BTCUSDT", signals, symbol_config=symbol_config)
        assert result.type == SignalType.HOLD

    def test_per_symbol_min_agreement_override(self, aggregator):
        """Per-symbol min_agreement should override default."""
        signals = [self._create_signal(SignalType.BUY, 1.0)]
        # Default min_agreement is 1, so single BUY should trigger
        result = aggregator.aggregate("BTCUSDT", signals)
        assert result.type == SignalType.BUY

        # Override with min_agreement=2 - single BUY should now HOLD
        symbol_config = {"min_agreement": 2}
        result = aggregator.aggregate("BTCUSDT", signals, symbol_config=symbol_config)
        assert result.type == SignalType.HOLD
        assert "Insufficient agreement" in result.reason

    def test_per_symbol_buy_threshold_uptrend_override(self, aggregator):
        """Per-symbol buy_threshold_uptrend should override default when in uptrend."""
        signals = [self._create_signal(SignalType.BUY, 0.6)]
        # Default buy_threshold is 0.5, 0.6 should trigger BUY in uptrend
        result = aggregator.aggregate("BTCUSDT", signals, ema_200=40000.0)
        assert result.type == SignalType.BUY

        # Override with higher uptrend threshold - same signal should now HOLD
        symbol_config = {"buy_threshold_uptrend": 1.0}
        result = aggregator.aggregate("BTCUSDT", signals, ema_200=40000.0, symbol_config=symbol_config)
        assert result.type == SignalType.HOLD
        # Should still respect default threshold when not in uptrend (price < EMA200)
        # Override both thresholds to ensure HOLD
        symbol_config = {"buy_threshold_uptrend": 1.0, "buy_threshold": 1.0}
        result = aggregator.aggregate("BTCUSDT", signals, ema_200=60000.0, symbol_config=symbol_config)
        assert result.type == SignalType.HOLD
    def test_empty_symbol_config_uses_defaults(self, aggregator):
        """Empty symbol_config should use all default thresholds."""
        signals = [self._create_signal(SignalType.BUY, 0.8)]
        result = aggregator.aggregate("BTCUSDT", signals, symbol_config={})
        assert result.type == SignalType.BUY

    def test_none_symbol_config_uses_defaults(self, aggregator):
        """None symbol_config should use all default thresholds."""
        signals = [self._create_signal(SignalType.BUY, 0.8)]
        result = aggregator.aggregate("BTCUSDT", signals, symbol_config=None)
        assert result.type == SignalType.BUY

    def test_partial_symbol_config_mixed_with_defaults(self, aggregator):
        """Partial symbol_config should override only specified values."""
        signals = [self._create_signal(SignalType.BUY, 0.8)]
        # Override only min_agreement, use defaults for thresholds
        symbol_config = {"min_agreement": 2}
        result = aggregator.aggregate("BTCUSDT", signals, symbol_config=symbol_config)
        assert result.type == SignalType.HOLD
        assert "Insufficient agreement" in result.reason
