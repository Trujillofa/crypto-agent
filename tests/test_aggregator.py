import pytest
from src.strategy.aggregator import SignalAggregator
from src.strategy.signals import Signal, SignalType


class TestSignalAggregator:
    @pytest.fixture
    def aggregator(self):
        return SignalAggregator(
            {"buy_threshold": 0.5, "sell_threshold": -0.5, "min_agreement": 1}
        )

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
