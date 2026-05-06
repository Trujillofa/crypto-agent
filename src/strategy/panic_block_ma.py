from __future__ import annotations

from collections.abc import Mapping

from src.strategy.signals import Signal, SignalType
from src.strategy.simple_ma import SimpleMACrossoverStrategy


class PanicBlockMACrossoverStrategy(SimpleMACrossoverStrategy):
    """EMA crossover with a conservative risk-off BUY blocker.

    The overlay is intentionally asymmetric: it can veto new BUY entries during
    panic-like regimes, but it does not suppress SELL exits.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)
        self._panic_rsi_threshold = float(self._config.get("panic_rsi_threshold", 35.0))
        self._panic_atr_pct_threshold = float(self._config.get("panic_atr_pct_threshold", 0.08))
        self._require_below_ema200 = bool(self._config.get("panic_require_below_ema200", True))

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        signal = await super().evaluate(symbol, indicators)
        if signal.type is not SignalType.BUY:
            return signal

        required_overlay_indicators = {"rsi_14", "atr_pct", "ema_200"}
        missing = [key for key in required_overlay_indicators if key not in indicators]
        if missing:
            raise ValueError(
                f"Missing required panic-block indicators for {symbol}: {', '.join(missing)}"
            )

        close_price = indicators["close_price"]
        rsi_14 = indicators["rsi_14"]
        atr_pct = indicators["atr_pct"]
        ema_200 = indicators["ema_200"]

        if rsi_14 is None or atr_pct is None or ema_200 is None:
            return Signal(
                type=SignalType.HOLD,
                symbol=symbol,
                price=signal.price,
                confidence=0.0,
                reason="Waiting for panic-block overlay data (RSI14/ATR%/EMA200)",
                indicators=signal.indicators,
                trading_mode=signal.trading_mode,
            )

        below_ema200 = close_price < ema_200
        risk_off_momentum = rsi_14 <= self._panic_rsi_threshold
        high_volatility = atr_pct >= self._panic_atr_pct_threshold
        trend_gate_passed = below_ema200 if self._require_below_ema200 else True
        panic_blocked = trend_gate_passed and (risk_off_momentum or high_volatility)

        if not panic_blocked:
            return signal

        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=signal.price,
            confidence=0.0,
            reason=(
                "Panic-block vetoed EMA BUY "
                f"(RSI14={rsi_14:.2f}, ATR%={atr_pct:.4f}, close<EMA200={below_ema200})"
            ),
            indicators={
                **signal.indicators,
                "rsi_14": rsi_14,
                "atr_pct": atr_pct,
                "ema_200": ema_200,
            },
            trading_mode=signal.trading_mode,
        )

    def get_name(self) -> str:
        return f"PanicBlock{super().get_name()}"
