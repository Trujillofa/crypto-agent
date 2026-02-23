from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass

from src.strategy.base import BaseStrategy
from src.strategy.signals import Signal, SignalType


@dataclass
class _OpenSpreadPosition:
    side: str
    entry_bar: int
    entry_z: float


class MeanReversionStrategy(BaseStrategy):
    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        super().__init__(config)

        self._lookback = int(self._config.get("lookback", 120))
        self._adf_min_samples = int(self._config.get("adf_min_samples", 80))
        self._adf_critical_value = float(self._config.get("adf_critical_value", -2.86))
        self._z_entry_threshold = float(self._config.get("z_entry_threshold", 2.0))
        self._z_exit_threshold = float(self._config.get("z_exit_threshold", 0.25))
        self._max_hold_bars = int(self._config.get("max_hold_bars", 24))
        self._price_key = str(self._config.get("price_key", "close_price"))
        self._pair_price_key = str(
            self._config.get("pair_price_key", "pair_close_price")
        )
        self._pair_symbol = str(self._config.get("pair_symbol", ""))
        self._use_log_prices = bool(self._config.get("use_log_prices", True))
        self._min_std = float(self._config.get("min_spread_std", 1e-8))

        if self._lookback < 30:
            raise ValueError("lookback must be >= 30")
        if self._adf_min_samples < 30:
            raise ValueError("adf_min_samples must be >= 30")
        if self._adf_min_samples > self._lookback:
            raise ValueError("adf_min_samples must be <= lookback")
        if self._z_entry_threshold <= 0:
            raise ValueError("z_entry_threshold must be > 0")
        if self._max_hold_bars < 1:
            raise ValueError("max_hold_bars must be >= 1")

        self._base_price_history: dict[str, deque[float]] = {}
        self._pair_price_history: dict[str, deque[float]] = {}
        self._position_by_symbol: dict[str, _OpenSpreadPosition] = {}
        self._bar_count_by_symbol: dict[str, int] = {}

    async def evaluate(self, symbol: str, indicators: dict[str, float]) -> Signal:
        if self._price_key not in indicators:
            raise ValueError(
                f"Missing required indicator for {symbol}: {self._price_key}"
            )

        current_price = self._coerce_float(indicators.get(self._price_key))
        if current_price is None or current_price <= 0:
            return self._hold(symbol, 0.0, "Waiting for valid base price")

        pair_price = self._coerce_float(indicators.get(self._pair_price_key))
        if pair_price is None or pair_price <= 0:
            pair_hint = f" ({self._pair_symbol})" if self._pair_symbol else ""
            return self._hold(
                symbol,
                current_price,
                f"Waiting for paired price{pair_hint}: {self._pair_price_key}",
            )

        base_series = self._base_price_history.setdefault(
            symbol,
            deque(maxlen=self._lookback),
        )
        pair_series = self._pair_price_history.setdefault(
            symbol,
            deque(maxlen=self._lookback),
        )

        bar_count = self._bar_count_by_symbol.get(symbol, 0) + 1
        self._bar_count_by_symbol[symbol] = bar_count

        if self._use_log_prices:
            base_series.append(math.log(current_price))
            pair_series.append(math.log(pair_price))
        else:
            base_series.append(current_price)
            pair_series.append(pair_price)

        if len(base_series) < self._lookback or len(pair_series) < self._lookback:
            return self._hold(
                symbol,
                current_price,
                f"Warming up pair history: {len(base_series)}/{self._lookback}",
            )

        intercept, hedge_ratio = self._fit_ols(list(base_series), list(pair_series))
        if hedge_ratio is None:
            return self._hold(
                symbol, current_price, "Pair variance too low for regression"
            )

        residuals = self._compute_residuals(
            list(base_series), list(pair_series), intercept, hedge_ratio
        )
        adf_t = self._adf_t_stat(residuals)
        if adf_t is None or adf_t >= self._adf_critical_value:
            adf_value = f"{adf_t:.3f}" if adf_t is not None else "nan"
            return self._hold(
                symbol,
                current_price,
                f"No stationary spread (ADF t={adf_value}, crit={self._adf_critical_value:.2f})",
            )

        spread_mean = sum(residuals) / len(residuals)
        spread_var = sum((value - spread_mean) ** 2 for value in residuals) / len(
            residuals
        )
        spread_std = math.sqrt(spread_var)
        if spread_std <= self._min_std:
            return self._hold(symbol, current_price, "Spread variance too low")

        spread_now = residuals[-1]
        z_score = (spread_now - spread_mean) / spread_std

        open_position = self._position_by_symbol.get(symbol)
        if open_position is not None:
            return self._handle_exit(
                symbol=symbol,
                price=current_price,
                z_score=z_score,
                bar_count=bar_count,
                spread_now=spread_now,
                hedge_ratio=hedge_ratio,
                adf_t=adf_t,
            )

        if z_score >= self._z_entry_threshold:
            self._position_by_symbol[symbol] = _OpenSpreadPosition(
                side="short_spread",
                entry_bar=bar_count,
                entry_z=z_score,
            )
            confidence = min(1.0, abs(z_score) / (self._z_entry_threshold * 1.5))
            return Signal(
                type=SignalType.SELL,
                symbol=symbol,
                price=current_price,
                confidence=confidence,
                reason=(
                    f"Enter SHORT spread: z={z_score:.2f} >= {self._z_entry_threshold:.2f}, "
                    f"ADF t={adf_t:.2f}, beta={hedge_ratio:.4f}"
                ),
                indicators={
                    "z_score": z_score,
                    "spread": spread_now,
                    "hedge_ratio": hedge_ratio,
                    "adf_t": adf_t,
                    "pair_price": pair_price,
                },
            )

        if z_score <= -self._z_entry_threshold:
            self._position_by_symbol[symbol] = _OpenSpreadPosition(
                side="long_spread",
                entry_bar=bar_count,
                entry_z=z_score,
            )
            confidence = min(1.0, abs(z_score) / (self._z_entry_threshold * 1.5))
            return Signal(
                type=SignalType.BUY,
                symbol=symbol,
                price=current_price,
                confidence=confidence,
                reason=(
                    f"Enter LONG spread: z={z_score:.2f} <= -{self._z_entry_threshold:.2f}, "
                    f"ADF t={adf_t:.2f}, beta={hedge_ratio:.4f}"
                ),
                indicators={
                    "z_score": z_score,
                    "spread": spread_now,
                    "hedge_ratio": hedge_ratio,
                    "adf_t": adf_t,
                    "pair_price": pair_price,
                },
            )

        return self._hold(
            symbol,
            current_price,
            (
                f"No entry: z={z_score:.2f} within +/-{self._z_entry_threshold:.2f}, "
                f"ADF t={adf_t:.2f}"
            ),
            indicators={
                "z_score": z_score,
                "spread": spread_now,
                "hedge_ratio": hedge_ratio,
                "adf_t": adf_t,
                "pair_price": pair_price,
            },
        )

    def get_name(self) -> str:
        pair = self._pair_symbol if self._pair_symbol else self._pair_price_key
        return f"MeanReversion({pair})"

    def _handle_exit(
        self,
        symbol: str,
        price: float,
        z_score: float,
        bar_count: int,
        spread_now: float,
        hedge_ratio: float,
        adf_t: float,
    ) -> Signal:
        open_position = self._position_by_symbol.get(symbol)
        if open_position is None:
            return self._hold(symbol, price, "No open spread position")

        bars_held = bar_count - open_position.entry_bar
        exit_by_time = bars_held >= self._max_hold_bars

        if open_position.side == "long_spread":
            exit_by_mean = z_score >= -self._z_exit_threshold
            exit_signal = SignalType.SELL
        else:
            exit_by_mean = z_score <= self._z_exit_threshold
            exit_signal = SignalType.BUY

        if exit_by_mean or exit_by_time:
            del self._position_by_symbol[symbol]
            reason = (
                f"Exit spread by {'mean reversion' if exit_by_mean else 'time-stop'}: "
                f"z={z_score:.2f}, held={bars_held} bars"
            )
            confidence = 0.8 if exit_by_mean else 0.6
            return Signal(
                type=exit_signal,
                symbol=symbol,
                price=price,
                confidence=confidence,
                reason=reason,
                indicators={
                    "z_score": z_score,
                    "spread": spread_now,
                    "hedge_ratio": hedge_ratio,
                    "adf_t": adf_t,
                    "bars_held": float(bars_held),
                },
            )

        return self._hold(
            symbol,
            price,
            (
                f"Position open ({open_position.side}): z={z_score:.2f}, "
                f"held={bars_held}/{self._max_hold_bars} bars"
            ),
            indicators={
                "z_score": z_score,
                "spread": spread_now,
                "hedge_ratio": hedge_ratio,
                "adf_t": adf_t,
                "bars_held": float(bars_held),
            },
        )

    def _fit_ols(
        self, y_series: list[float], x_series: list[float]
    ) -> tuple[float, float | None]:
        x_mean = sum(x_series) / len(x_series)
        y_mean = sum(y_series) / len(y_series)

        sxx = sum((x - x_mean) ** 2 for x in x_series)
        if sxx <= self._min_std:
            return 0.0, None

        sxy = sum(
            (x - x_mean) * (y - y_mean)
            for x, y in zip(x_series, y_series, strict=False)
        )
        beta = sxy / sxx
        intercept = y_mean - beta * x_mean
        return intercept, beta

    def _compute_residuals(
        self,
        y_series: list[float],
        x_series: list[float],
        intercept: float,
        beta: float,
    ) -> list[float]:
        return [
            y - (intercept + beta * x) for y, x in zip(y_series, x_series, strict=False)
        ]

    def _adf_t_stat(self, series: list[float]) -> float | None:
        if len(series) < self._adf_min_samples:
            return None

        x_lag = series[:-1]
        y_diff = [series[index] - series[index - 1] for index in range(1, len(series))]
        sample_count = len(x_lag)
        if sample_count < 3:
            return None

        x_mean = sum(x_lag) / sample_count
        y_mean = sum(y_diff) / sample_count

        sxx = sum((x - x_mean) ** 2 for x in x_lag)
        if sxx <= self._min_std:
            return None

        sxy = sum(
            (x - x_mean) * (y - y_mean) for x, y in zip(x_lag, y_diff, strict=False)
        )
        beta = sxy / sxx
        alpha = y_mean - beta * x_mean

        residuals = [
            y - (alpha + beta * x) for x, y in zip(x_lag, y_diff, strict=False)
        ]
        dof = sample_count - 2
        if dof <= 0:
            return None

        rss = sum(value * value for value in residuals)
        sigma_sq = rss / dof
        if sigma_sq <= self._min_std:
            return None

        se_beta = math.sqrt(sigma_sq / sxx)
        if se_beta <= self._min_std:
            return None

        return beta / se_beta

    def _coerce_float(self, value: object) -> float | None:
        if isinstance(value, (int, float)):
            numeric = float(value)
            if math.isfinite(numeric):
                return numeric
            return None
        return None

    def _hold(
        self,
        symbol: str,
        price: float,
        reason: str,
        indicators: dict[str, float] | None = None,
    ) -> Signal:
        return Signal(
            type=SignalType.HOLD,
            symbol=symbol,
            price=price,
            confidence=0.0,
            reason=reason,
            indicators=indicators or {},
        )
