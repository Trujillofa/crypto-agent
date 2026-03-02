from __future__ import annotations

from collections.abc import Mapping

from src.strategy.signals import Signal, SignalType
from src.utils.logger import get_logger


class SignalAggregator:
    """Aggregates multiple trading signals into a single consensus signal."""

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        default_trading_mode: str = "spot",
    ) -> None:
        self._config = config or {}
        self._default_trading_mode = default_trading_mode
        self._logger = get_logger(self.__class__.__name__)

        self._buy_threshold = float(self._config.get("buy_threshold", 0.5))
        self._buy_threshold_uptrend = float(
            self._config.get("buy_threshold_uptrend", self._buy_threshold)
        )
        self._sell_threshold = float(self._config.get("sell_threshold", -0.5))
        self._min_agreement = int(self._config.get("min_agreement", 1))

    def aggregate(
        self,
        symbol: str,
        signals: list[Signal],
        ema_200: float | None = None,
        symbol_config: Mapping[str, object] | None = None,
    ) -> Signal:
        """Process a list of signals and return a single consolidated signal.

        Args:
            symbol: Trading pair symbol.
            signals: List of signals from individual strategies.
            ema_200: EMA200 value for trend detection.
            symbol_config: Per-symbol config overrides for aggregator thresholds.
        """
        if not signals:
            return self._create_hold(symbol, 0.0, "No signals provided")

        reference_signal = signals[0]
        current_price = reference_signal.price
        trading_mode = self._resolve_trading_mode(signals)

        # Apply per-symbol overrides if provided, otherwise use defaults
        buy_threshold = self._buy_threshold
        buy_threshold_uptrend = self._buy_threshold_uptrend
        sell_threshold = self._sell_threshold
        min_agreement = self._min_agreement

        if symbol_config:
            buy_threshold = float(symbol_config.get("buy_threshold", buy_threshold))
            buy_threshold_uptrend = float(
                symbol_config.get("buy_threshold_uptrend", buy_threshold_uptrend)
            )
            sell_threshold = float(symbol_config.get("sell_threshold", sell_threshold))
            min_agreement = int(symbol_config.get("min_agreement", min_agreement))

        # Dynamic buy threshold: more aggressive in confirmed uptrend (price > EMA200)
        in_uptrend = ema_200 is not None and current_price > ema_200
        effective_buy_threshold = buy_threshold_uptrend if in_uptrend else buy_threshold

        total_score = 0.0
        active_signals = 0
        reasons = []
        all_indicators = {}

        for sig in signals:
            score = 0.0
            if sig.type == SignalType.BUY:
                score = 1.0 * sig.confidence
                active_signals += 1
            elif sig.type == SignalType.SELL:
                score = -1.0 * sig.confidence
                active_signals += 1

            total_score += score

            if sig.type != SignalType.HOLD:
                reasons.append(f"{sig.reason} ({sig.confidence:.2f})")

            all_indicators.update(sig.indicators)

        final_type = SignalType.HOLD
        final_confidence = 0.0
        final_reason = f"Score: {total_score:.2f} (Active: {active_signals})"

        if active_signals >= min_agreement:
            if total_score >= effective_buy_threshold:
                final_type = SignalType.BUY
                final_confidence = min(abs(total_score), 1.0)
                final_reason = (
                    f"Consensus BUY | Score: {total_score:.2f} | Sources: {', '.join(reasons)}"
                )

            elif total_score <= sell_threshold:
                final_type = SignalType.SELL
                final_confidence = min(abs(total_score), 1.0)
                final_reason = (
                    f"Consensus SELL | Score: {total_score:.2f} | Sources: {', '.join(reasons)}"
                )
            else:
                if reasons:
                    final_reason += f" | Mixed/Weak Signals: {', '.join(reasons)}"
        else:
            if reasons:
                final_reason += " | Insufficient agreement"
        return Signal(
            type=final_type,
            symbol=symbol,
            price=current_price,
            confidence=final_confidence,
            reason=final_reason,
            indicators=all_indicators,
            trading_mode=trading_mode,
        )

    def _create_hold(self, symbol: str, price: float, reason: str) -> Signal:
        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            confidence=0.0,
            reason=reason,
            indicators={},
            trading_mode=self._default_trading_mode,
        )

    def _resolve_trading_mode(self, signals: list[Signal]) -> str:
        modes = {signal.trading_mode for signal in signals}
        if len(modes) == 1:
            return next(iter(modes))
        if len(modes) > 1:
            self._logger.warning(
                "Mixed trading_mode values detected (%s). Falling back to default '%s'.",
                ", ".join(sorted(modes)),
                self._default_trading_mode,
            )
        return self._default_trading_mode
