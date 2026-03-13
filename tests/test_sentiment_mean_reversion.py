import pytest

from src.strategy.sentiment_mean_reversion import (
    SentimentMeanReversionStrategy,
    SentimentScorer,
)
from src.strategy.signals import SignalType


class FakeScorer(SentimentScorer):
    """Test scorer that returns a fixed score."""

    def __init__(self, score: float = 50.0) -> None:
        super().__init__(xai_client=None)
        self._fixed_score = score

    async def get_score(self, symbol: str) -> float:
        return self._fixed_score


def _make_strategy(**overrides) -> SentimentMeanReversionStrategy:
    config = {
        "rsi_oversold": 35.0,
        "rsi_overbought": 65.0,
        "bb_distance_threshold": 0.005,
        "sentiment_gate_threshold": 35.0,
        "sentiment_panic_threshold": 20.0,
        "sentiment_boost_threshold": 65.0,
    }
    config.update(overrides)
    return SentimentMeanReversionStrategy(config)


def _indicators(
    rsi: float = 50.0,
    bb_lower_dist: float = 0.05,
    bb_upper_dist: float = 0.05,
    close: float = 50000.0,
) -> dict[str, float]:
    return {
        "rsi_14": rsi,
        "bb_lower_dist": bb_lower_dist,
        "bb_upper_dist": bb_upper_dist,
        "close_price": close,
    }


class TestSentimentMeanReversion:
    @pytest.mark.asyncio
    async def test_hold_when_indicators_neutral(self):
        """Neutral RSI and no band proximity → HOLD."""
        strategy = _make_strategy()
        signal = await strategy.evaluate("BTCUSDT", _indicators(rsi=50.0))
        assert signal.type == SignalType.HOLD

    @pytest.mark.asyncio
    async def test_buy_oversold_near_lower_band_good_sentiment(self):
        """Oversold RSI + near lower band + positive sentiment → BUY."""
        strategy = _make_strategy()
        strategy.set_scorer(FakeScorer(score=60.0))
        signal = await strategy.evaluate(
            "BTCUSDT",
            _indicators(rsi=25.0, bb_lower_dist=0.001),
        )
        assert signal.type == SignalType.BUY
        assert signal.confidence >= 0.5
        assert "sentiment=60" in signal.reason

    @pytest.mark.asyncio
    async def test_buy_blocked_by_fud_sentiment(self):
        """Oversold RSI + near lower band BUT low sentiment → HOLD (falling knife)."""
        strategy = _make_strategy()
        strategy.set_scorer(FakeScorer(score=25.0))  # Below gate_threshold (35)
        signal = await strategy.evaluate(
            "BTCUSDT",
            _indicators(rsi=25.0, bb_lower_dist=0.001),
        )
        assert signal.type == SignalType.HOLD

    @pytest.mark.asyncio
    async def test_panic_sell_on_extreme_fud(self):
        """Sentiment below panic threshold → emergency SELL."""
        strategy = _make_strategy()
        strategy.set_scorer(FakeScorer(score=15.0))  # Below panic_threshold (20)
        signal = await strategy.evaluate(
            "BTCUSDT",
            _indicators(rsi=50.0),  # Even with neutral RSI
        )
        assert signal.type == SignalType.SELL
        assert signal.confidence >= 0.8
        assert "panic" in signal.reason.lower()

    @pytest.mark.asyncio
    async def test_sell_overbought_near_upper_band(self):
        """Overbought RSI + near upper band → SELL."""
        strategy = _make_strategy()
        strategy.set_scorer(FakeScorer(score=60.0))
        signal = await strategy.evaluate(
            "BTCUSDT",
            _indicators(rsi=75.0, bb_upper_dist=0.001),
        )
        assert signal.type == SignalType.SELL
        assert signal.confidence >= 0.5

    @pytest.mark.asyncio
    async def test_confidence_boost_with_high_sentiment(self):
        """High sentiment boosts BUY confidence."""
        strategy = _make_strategy()
        strategy.set_scorer(FakeScorer(score=80.0))  # Above boost_threshold
        signal = await strategy.evaluate(
            "BTCUSDT",
            _indicators(rsi=20.0, bb_lower_dist=0.001),
        )
        assert signal.type == SignalType.BUY
        assert signal.confidence >= 0.65  # Base + boost

    @pytest.mark.asyncio
    async def test_no_scorer_defaults_neutral(self):
        """Without a scorer, sentiment defaults to 50 (neutral) → allows trading."""
        strategy = _make_strategy()
        # No scorer set — should default to 50
        signal = await strategy.evaluate(
            "BTCUSDT",
            _indicators(rsi=25.0, bb_lower_dist=0.001),
        )
        assert signal.type == SignalType.BUY

    @pytest.mark.asyncio
    async def test_missing_indicator_raises(self):
        """Missing required indicator raises ValueError."""
        strategy = _make_strategy()
        with pytest.raises(ValueError, match="Missing required indicator"):
            await strategy.evaluate("BTCUSDT", {"close_price": 50000.0})

    @pytest.mark.asyncio
    async def test_get_name(self):
        strategy = _make_strategy()
        assert strategy.get_name() == "SentimentMeanReversion"

    @pytest.mark.asyncio
    async def test_sentiment_score_in_indicators(self):
        """Signal indicators include the sentiment score."""
        strategy = _make_strategy()
        strategy.set_scorer(FakeScorer(score=72.0))
        signal = await strategy.evaluate(
            "BTCUSDT",
            _indicators(rsi=25.0, bb_lower_dist=0.001),
        )
        assert signal.indicators.get("sentiment_score") == 72.0


class TestSentimentScorer:
    @pytest.mark.asyncio
    async def test_no_client_returns_neutral(self):
        """Without xAI client, returns neutral 50.0."""
        scorer = SentimentScorer(xai_client=None)
        score = await scorer.get_score("BTCUSDT")
        assert score == 50.0

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """Cached score returned within TTL."""
        scorer = SentimentScorer(xai_client=None, cache_ttl_seconds=300.0)
        # First call populates cache with neutral
        score1 = await scorer.get_score("BTCUSDT")
        assert score1 == 50.0
        # Second call should hit cache
        score2 = await scorer.get_score("BTCUSDT")
        assert score2 == 50.0
