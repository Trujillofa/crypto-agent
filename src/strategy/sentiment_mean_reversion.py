from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


class SentimentScorer:
    """Scores market sentiment using an LLM (xAI/Grok) or returns neutral when unavailable.

    Provides a Context Score (0-100):
    - 0-30: Bearish (FUD, crashes, regulatory crackdowns)
    - 30-50: Cautious (mixed signals, uncertainty)
    - 50-70: Neutral-to-positive (normal market conditions)
    - 70-100: Bullish (euphoria, breakouts, strong momentum)

    The strategy only takes mean reversion trades when score >= gate_threshold (default 35),
    avoiding "falling knives" during genuine FUD events.
    """

    def __init__(
        self,
        xai_client: object | None = None,
        cache_ttl_seconds: float = 300.0,
        observation_recorder: Callable[[dict[str, object]], Awaitable[None]] | None = None,
        degradation_alert: Callable[[str], Awaitable[None]] | None = None,
        degradation_window: int = 10,
        degradation_error_pct: float = 0.5,
        degradation_stuck_pct: float = 0.8,
        degradation_cooldown: float = 3600.0,
    ) -> None:
        self._xai_client = xai_client
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, float]] = {}  # symbol -> (timestamp, score)
        self._observation_recorder = observation_recorder
        self._logger = get_logger(self.__class__.__name__)

        # Degradation detection
        self._degradation_alert = degradation_alert
        self._degradation_window = degradation_window
        self._degradation_error_pct = degradation_error_pct
        self._degradation_stuck_pct = degradation_stuck_pct
        self._degradation_cooldown = degradation_cooldown
        self._recent_sources: deque[str] = deque(maxlen=degradation_window)
        self._recent_scores: deque[float] = deque(maxlen=degradation_window)
        self._last_alert_time: float = -degradation_cooldown

    async def get_score(self, symbol: str) -> float:
        """Get sentiment score for a symbol.

        Returns score 0-100. Returns 50.0 (neutral) if LLM is unavailable.
        """
        now = time.monotonic()

        cached = self._cache.get(symbol)
        if cached is not None:
            ts, score = cached
            if now - ts < self._cache_ttl:
                return score

        if self._xai_client is None:
            score = 50.0
            self._cache[symbol] = (now, score)
            await self._record_observation(symbol, score, source="neutral_fallback")
            return score

        try:
            score = await self._query_llm(symbol)
            self._cache[symbol] = (now, score)
            await self._record_observation(symbol, score, source="xai_live")
            return score
        except Exception as exc:
            self._logger.warning("Sentiment query failed for %s: %s", symbol, exc)
            await self._record_observation(
                symbol,
                50.0,
                source="xai_error_fallback",
                error=str(exc),
            )
            return 50.0

    async def _record_observation(
        self,
        symbol: str,
        score: float,
        *,
        source: str,
        error: str | None = None,
    ) -> None:
        # Track for degradation detection regardless of recorder
        self._recent_sources.append(source)
        self._recent_scores.append(score)
        await self._check_degradation()

        if self._observation_recorder is None:
            return
        payload: dict[str, object] = {
            "symbol": symbol,
            "score": round(float(score), 4),
            "source": source,
        }
        if error:
            payload["error"] = error[:500]
        try:
            await self._observation_recorder(payload)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Sentiment observation recorder failed for %s: %s", symbol, exc)

    async def _check_degradation(self) -> None:
        """Check recent observations for degradation patterns and alert once per cooldown."""
        if self._degradation_alert is None:
            return
        if len(self._recent_sources) < self._degradation_window:
            return

        now = time.monotonic()
        if now - self._last_alert_time < self._degradation_cooldown:
            return

        n = len(self._recent_sources)
        issues: list[str] = []

        # High error/fallback rate
        error_count = sum(1 for s in self._recent_sources if s != "xai_live")
        error_pct = error_count / n
        if error_pct >= self._degradation_error_pct:
            live_pct = (1 - error_pct) * 100
            issues.append(f"Only {live_pct:.0f}% live responses (last {n} obs)")

        # Scores stuck at 50 (fallback neutral)
        stuck_count = sum(1 for s in self._recent_scores if s == 50.0)
        stuck_pct = stuck_count / n
        if stuck_pct >= self._degradation_stuck_pct:
            issues.append(f"{stuck_pct * 100:.0f}% of scores are 50.0 (likely fallback)")

        if not issues:
            return

        self._last_alert_time = now
        detail = "; ".join(issues)
        message = f"Grok sentiment degraded: {detail}"
        self._logger.warning(message)
        try:
            await self._degradation_alert(message)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("Degradation alert failed: %s", exc)

    async def _query_llm(self, symbol: str) -> float:
        """Query xAI/Grok for sentiment analysis."""
        base_asset = symbol.replace("USDT", "").replace("BUSD", "")
        prompt = (
            f"Analyze the current market sentiment for {base_asset} ({symbol}) cryptocurrency. "
            f"Consider recent news, social media trends, regulatory developments, and market structure. "
            f'Respond with ONLY a JSON object: {{"score": <number 0-100>, "reason": "<brief reason>"}} '
            f"where 0=extreme fear/FUD, 50=neutral, 100=extreme greed/euphoria."
        )
        messages = [
            {
                "role": "system",
                "content": "You are a crypto market sentiment analyst. Respond only with valid JSON.",
            },
            {"role": "user", "content": prompt},
        ]

        response = await self._xai_client.chat(messages)  # type: ignore[union-attr]

        try:
            data = json.loads(response)
            score = float(data.get("score", 50))
            return max(0.0, min(100.0, score))
        except (json.JSONDecodeError, ValueError, TypeError):
            self._logger.warning("Could not parse sentiment response: %s", response[:200])
            return 50.0


class SentimentMeanReversionStrategy(BaseStrategy):
    """AI-Contextual Mean Reversion Strategy for 2026.

    Core idea: Pure technical mean reversion (RSI + Bollinger + VWAP) is "noisy" in crypto.
    This strategy gates mean reversion entries with an AI sentiment score, only taking
    dip-buying trades when sentiment is neutral-to-positive, avoiding "falling knives"
    during genuine FUD events.

    Signal logic:
    - BUY: Price is oversold (RSI < threshold AND near lower Bollinger band)
           AND sentiment score >= gate_threshold (not in FUD territory)
    - SELL: Price is overbought (RSI > threshold AND near upper Bollinger band)
            OR sentiment drops below panic_threshold (emergency exit)
    - HOLD: Otherwise

    Confidence scales with both technical extremity and sentiment alignment.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._logger = get_logger(self.__class__.__name__)

        # Technical thresholds
        self._rsi_oversold = float(self._config.get("rsi_oversold", 35.0))
        self._rsi_overbought = float(self._config.get("rsi_overbought", 65.0))
        self._bb_distance_threshold = float(self._config.get("bb_distance_threshold", 0.005))

        # Sentiment gates
        self._gate_threshold = float(self._config.get("sentiment_gate_threshold", 35.0))
        self._panic_threshold = float(self._config.get("sentiment_panic_threshold", 20.0))
        self._boost_threshold = float(self._config.get("sentiment_boost_threshold", 65.0))

        # Volatility regime filter
        self._volatility_regime_filter = bool(self._config.get("volatility_regime_filter", True))
        self._atr_pct_threshold = float(self._config.get("atr_pct_threshold", 0.006))

        # Sentiment scorer (injected or created)
        self._scorer: SentimentScorer | None = None

        # State tracking
        self._previous_rsi: dict[str, float] = {}
        self._last_sentiment: dict[str, float] = {}

    def set_scorer(self, scorer: SentimentScorer) -> None:
        """Inject a SentimentScorer instance."""
        self._scorer = scorer

    async def _get_sentiment(self, symbol: str, indicators: Mapping[str, float]) -> float:
        """Get sentiment score, defaulting to neutral if no scorer.

        If the injected scorer supports replay lookup with a candle timestamp,
        pass the current row time so backtests can use historical sentiment.
        """
        if self._scorer is None:
            return 50.0

        row_time = indicators.get("time")
        if row_time is not None:
            try:
                score = await self._scorer.get_score(symbol, row_time)
            except TypeError:
                score = await self._scorer.get_score(symbol)
        else:
            score = await self._scorer.get_score(symbol)
        self._last_sentiment[symbol] = score
        return score

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        """Evaluate indicators with sentiment gating."""
        required = {"rsi_14", "close_price", "bb_lower_dist", "bb_upper_dist"}
        for k in required:
            if k not in indicators:
                raise ValueError(f"Missing required indicator for {symbol}: {k}")

        rsi = indicators["rsi_14"]
        close = indicators["close_price"]
        bb_lower_dist = indicators["bb_lower_dist"]
        bb_upper_dist = indicators["bb_upper_dist"]

        self._previous_rsi[symbol] = rsi

        sentiment = await self._get_sentiment(symbol, indicators)
        atr_pct = indicators.get("atr_pct")

        # Volatility regime filter: suppress BUYs in high-volatility regimes
        high_volatility = (
            self._volatility_regime_filter
            and atr_pct is not None
            and atr_pct > self._atr_pct_threshold
        )

        # Default: HOLD
        hold_reason = f"RSI={rsi:.1f} Sentiment={sentiment:.0f}"
        if high_volatility:
            hold_reason += f" | HighVol (ATR%={atr_pct:.4f}>{self._atr_pct_threshold:.4f})"

        signal = Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close,
            confidence=0.0,
            reason=hold_reason,
            indicators={"rsi_14": rsi, "sentiment_score": sentiment, "close_price": close},
        )

        # Emergency SELL: sentiment in panic zone (always fires regardless of volatility)
        if sentiment < self._panic_threshold:
            signal = Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=close,
                confidence=0.85,
                reason=f"Sentiment panic ({sentiment:.0f} < {self._panic_threshold})",
                indicators={"rsi_14": rsi, "sentiment_score": sentiment, "close_price": close},
            )

        # BUY: Oversold + near lower band + sentiment not in FUD + low volatility
        elif (
            rsi < self._rsi_oversold
            and bb_lower_dist <= self._bb_distance_threshold
            and sentiment >= self._gate_threshold
            and not high_volatility
        ):
            # Base confidence from RSI depth
            rsi_depth = self._rsi_oversold - rsi
            base_conf = 0.5 + min(0.3, rsi_depth / 30.0 * 0.3)

            # Sentiment boost: higher sentiment = more confidence
            if sentiment >= self._boost_threshold:
                sentiment_bonus = 0.15
            elif sentiment >= 50:
                sentiment_bonus = 0.05
            else:
                sentiment_bonus = 0.0

            confidence = min(0.95, base_conf + sentiment_bonus)

            signal = Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=close,
                confidence=confidence,
                reason=(
                    f"Oversold RSI={rsi:.1f} near lower BB (dist={bb_lower_dist:.4f}), "
                    f"sentiment={sentiment:.0f} (safe to buy dip)"
                ),
                indicators={
                    "rsi_14": rsi,
                    "sentiment_score": sentiment,
                    "close_price": close,
                    "atr_pct": atr_pct,
                },
            )

        # SELL: Overbought + near upper band
        elif rsi > self._rsi_overbought and bb_upper_dist <= self._bb_distance_threshold:
            rsi_excess = rsi - self._rsi_overbought
            confidence = 0.5 + min(0.4, rsi_excess / 30.0 * 0.4)

            signal = Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=close,
                confidence=confidence,
                reason=(f"Overbought RSI={rsi:.1f} near upper BB (dist={bb_upper_dist:.4f})"),
                indicators={"rsi_14": rsi, "sentiment_score": sentiment, "close_price": close},
            )

        self._logger.debug("%s generated %s for %s", self.get_name(), signal, symbol)
        return signal

    def get_name(self) -> str:
        return "SentimentMeanReversion"
