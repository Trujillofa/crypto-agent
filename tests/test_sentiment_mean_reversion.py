import time

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

    @pytest.mark.asyncio
    async def test_records_neutral_observation_once_per_fresh_query(self):
        """Neutral fallback is recorded once and then served from cache."""
        observations: list[dict[str, object]] = []

        async def recorder(payload: dict[str, object]) -> None:
            observations.append(payload)

        scorer = SentimentScorer(
            xai_client=None,
            cache_ttl_seconds=300.0,
            observation_recorder=recorder,
        )

        score1 = await scorer.get_score("BTCUSDT")
        score2 = await scorer.get_score("BTCUSDT")

        assert score1 == 50.0
        assert score2 == 50.0
        assert observations == [
            {
                "symbol": "BTCUSDT",
                "score": 50.0,
                "source": "neutral_fallback",
            }
        ]

    @pytest.mark.asyncio
    async def test_records_live_observation_once_per_fresh_query(self):
        """Fresh live sentiment queries are recorded; cache hits are not duplicated."""

        class FakeXAIClient:
            def __init__(self) -> None:
                self.calls = 0

            async def chat(self, messages):
                self.calls += 1
                return '{"score": 61, "reason": "calm recovery"}'

        observations: list[dict[str, object]] = []

        async def recorder(payload: dict[str, object]) -> None:
            observations.append(payload)

        client = FakeXAIClient()
        scorer = SentimentScorer(
            xai_client=client,
            cache_ttl_seconds=300.0,
            observation_recorder=recorder,
        )

        score1 = await scorer.get_score("BTCUSDT")
        score2 = await scorer.get_score("BTCUSDT")

        assert score1 == 61.0
        assert score2 == 61.0
        assert client.calls == 1
        assert observations == [
            {
                "symbol": "BTCUSDT",
                "score": 61.0,
                "source": "xai_live",
            }
        ]

    @pytest.mark.asyncio
    async def test_records_error_fallback_on_llm_failure(self):
        """xAI failures are recorded as error fallbacks with truncated error text."""

        class FailingClient:
            async def chat(self, messages):
                raise RuntimeError("API timeout")

        observations: list[dict[str, object]] = []

        async def recorder(payload: dict[str, object]) -> None:
            observations.append(payload)

        scorer = SentimentScorer(
            xai_client=FailingClient(),
            observation_recorder=recorder,
        )

        score = await scorer.get_score("BTCUSDT")

        assert score == 50.0
        assert observations == [
            {
                "symbol": "BTCUSDT",
                "score": 50.0,
                "source": "xai_error_fallback",
                "error": "API timeout",
            }
        ]


class TestDegradationAlert:
    @pytest.mark.asyncio
    async def test_alert_fires_when_error_rate_exceeds_threshold(self):
        """Degradation alert fires when >50% of recent obs are non-live."""
        alerts: list[str] = []

        async def on_alert(msg: str) -> None:
            alerts.append(msg)

        scorer = SentimentScorer(
            xai_client=None,
            degradation_alert=on_alert,
            degradation_window=4,
            degradation_error_pct=0.5,
        )
        # 4 neutral fallbacks → 100% non-live → triggers alert
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"]:
            await scorer.get_score(sym)

        assert len(alerts) == 1
        assert "live" in alerts[0].lower() or "degraded" in alerts[0].lower()

    @pytest.mark.asyncio
    async def test_alert_fires_when_scores_stuck_at_50(self):
        """Degradation alert fires when >=80% of scores are exactly 50.0."""
        alerts: list[str] = []

        async def on_alert(msg: str) -> None:
            alerts.append(msg)

        class FlakyClient:
            def __init__(self) -> None:
                self.call_count = 0

            async def chat(self, messages):
                self.call_count += 1
                raise RuntimeError("timeout")

        scorer = SentimentScorer(
            xai_client=FlakyClient(),
            degradation_alert=on_alert,
            degradation_window=5,
            degradation_stuck_pct=0.8,
            cache_ttl_seconds=0,  # Disable cache to get fresh obs each time
        )
        for i in range(5):
            await scorer.get_score(f"SYM{i}")

        assert len(alerts) == 1
        assert "50.0" in alerts[0]

    @pytest.mark.asyncio
    async def test_no_alert_when_healthy(self):
        """No alert when all observations are live with varied scores."""
        alerts: list[str] = []

        async def on_alert(msg: str) -> None:
            alerts.append(msg)

        class HealthyClient:
            def __init__(self) -> None:
                self._scores = iter([65, 70, 55, 80, 60])

            async def chat(self, messages):
                return f'{{"score": {next(self._scores)}, "reason": "ok"}}'

        scorer = SentimentScorer(
            xai_client=HealthyClient(),
            degradation_alert=on_alert,
            degradation_window=5,
            cache_ttl_seconds=0,
        )
        for i in range(5):
            await scorer.get_score(f"SYM{i}")

        assert alerts == []

    @pytest.mark.asyncio
    async def test_cooldown_prevents_repeated_alerts(self):
        """Alert fires once, then cooldown prevents another."""
        alerts: list[str] = []

        async def on_alert(msg: str) -> None:
            alerts.append(msg)

        scorer = SentimentScorer(
            xai_client=None,
            degradation_alert=on_alert,
            degradation_window=3,
            degradation_error_pct=0.5,
            degradation_cooldown=3600.0,
        )
        # First batch → triggers alert
        for sym in ["A", "B", "C"]:
            await scorer.get_score(sym)
        assert len(alerts) == 1

        # More obs → cooldown blocks second alert
        for sym in ["D", "E", "F"]:
            await scorer.get_score(sym)
        assert len(alerts) == 1

    @pytest.mark.asyncio
    async def test_alert_fires_again_after_cooldown(self):
        """After cooldown expires, alert can fire again."""
        alerts: list[str] = []

        async def on_alert(msg: str) -> None:
            alerts.append(msg)

        scorer = SentimentScorer(
            xai_client=None,
            degradation_alert=on_alert,
            degradation_window=3,
            degradation_error_pct=0.5,
            degradation_cooldown=3600.0,
        )
        for sym in ["A", "B", "C"]:
            await scorer.get_score(sym)
        assert len(alerts) == 1

        # Simulate cooldown expired
        scorer._last_alert_time = time.monotonic() - 3601
        await scorer.get_score("D")
        assert len(alerts) == 2

    @pytest.mark.asyncio
    async def test_no_alert_below_window_size(self):
        """No alert until we have enough observations to fill the window."""
        alerts: list[str] = []

        async def on_alert(msg: str) -> None:
            alerts.append(msg)

        scorer = SentimentScorer(
            xai_client=None,
            degradation_alert=on_alert,
            degradation_window=10,
            degradation_error_pct=0.5,
        )
        # Only 3 obs, window is 10 → no alert yet
        for sym in ["A", "B", "C"]:
            await scorer.get_score(sym)
        assert alerts == []
