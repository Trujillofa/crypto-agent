from __future__ import annotations

import json
import time
from collections.abc import Mapping

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
    ) -> None:
        self._xai_client = xai_client
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, float]] = {}  # symbol -> (timestamp, score)
        self._logger = get_logger(self.__class__.__name__)

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
            return 50.0

        try:
            score = await self._query_llm(symbol)
            self._cache[symbol] = (now, score)
            return score
        except Exception as exc:
            self._logger.warning("Sentiment query failed for %s: %s", symbol, exc)
            return 50.0

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

        # Sentiment scorer (injected or created)
        self._scorer: SentimentScorer | None = None

        # State tracking
        self._previous_rsi: dict[str, float] = {}
        self._last_sentiment: dict[str, float] = {}

    def set_scorer(self, scorer: SentimentScorer) -> None:
        """Inject a SentimentScorer instance."""
        self._scorer = scorer

    async def _get_sentiment(self, symbol: str) -> float:
        """Get sentiment score, defaulting to neutral if no scorer."""
        if self._scorer is None:
            return 50.0
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

        sentiment = await self._get_sentiment(symbol)

        # Default: HOLD
        signal = Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=close,
            confidence=0.0,
            reason=f"RSI={rsi:.1f} Sentiment={sentiment:.0f}",
            indicators={"rsi_14": rsi, "sentiment_score": sentiment, "close_price": close},
        )

        # Emergency SELL: sentiment in panic zone
        if sentiment < self._panic_threshold:
            signal = Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=close,
                confidence=0.85,
                reason=f"Sentiment panic ({sentiment:.0f} < {self._panic_threshold})",
                indicators={"rsi_14": rsi, "sentiment_score": sentiment, "close_price": close},
            )

        # BUY: Oversold + near lower band + sentiment not in FUD
        elif (
            rsi < self._rsi_oversold
            and bb_lower_dist <= self._bb_distance_threshold
            and sentiment >= self._gate_threshold
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
                indicators={"rsi_14": rsi, "sentiment_score": sentiment, "close_price": close},
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
