from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class TechnicalIndicators:
    # RSI (Relative Strength Index)
    rsi_14: float
    rsi_7: float

    # MACD (Moving Average Convergence Divergence)
    macd: float
    macd_signal: float
    macd_hist: float

    # Bollinger Bands (distance from upper/lower bands as percentage)
    bb_upper_dist: float
    bb_lower_dist: float

    # ATR (Average True Range)
    atr_14: float
    atr_pct: float

    # EMA (Exponential Moving Average) - multiple periods
    ema_12: float | None
    ema_26: float | None
    ema_50: float | None
    ema_200: float | None

    # SMA (Simple Moving Average) - multiple periods
    sma_20: float | None
    sma_50: float | None
    sma_200: float | None

    # VWAP (Volume Weighted Average Price)
    vwap: float | None

    # Stochastic Oscillator
    stoch_k: float | None
    stoch_d: float | None

    # Commodity Channel Index
    cci: float | None


def compute_indicators(data: OhlcvSeries) -> TechnicalIndicators:
    close = _as_float_list(data["close"], "close")
    high = _as_float_list(data["high"], "high")
    low = _as_float_list(data["low"], "low")
    volume = _as_float_list(data["volume"], "volume")

    # RSI
    rsi_14 = _rsi(close, 14)
    rsi_7 = _rsi(close, 7)

    # MACD
    macd, macd_signal, macd_hist = _macd(close)

    # Bollinger Bands
    bb_upper, bb_lower = _bollinger_bands(close)
    bb_upper_dist = float((bb_upper - close[-1]) / close[-1])
    bb_lower_dist = float((close[-1] - bb_lower) / close[-1])

    # ATR
    atr_14 = _atr(high, low, close, 14)
    atr_pct = float(atr_14 / close[-1]) if close[-1] != 0 else 0.0

    # EMA (multiple periods)
    ema_12 = float(_ema(close, 12)[-1]) if len(close) >= 12 else None
    ema_26 = float(_ema(close, 26)[-1]) if len(close) >= 26 else None
    ema_50 = float(_ema(close, 50)[-1]) if len(close) >= 50 else None
    ema_200 = float(_ema(close, 200)[-1]) if len(close) >= 200 else None

    # SMA (multiple periods)
    sma_20 = _sma(close, 20) if len(close) >= 20 else None
    sma_50 = _sma(close, 50) if len(close) >= 50 else None
    sma_200 = _sma(close, 200) if len(close) >= 200 else None

    # VWAP
    vwap = _vwap(high, low, close, volume)

    # Stochastic Oscillator
    stoch_k, stoch_d = _stochastic(high, low, close, 14, 3) if len(close) >= 14 else (None, None)

    # CCI
    cci = _cci(high, low, close, 20) if len(close) >= 20 else None

    return TechnicalIndicators(
        rsi_14=float(rsi_14),
        rsi_7=float(rsi_7),
        macd=float(macd),
        macd_signal=float(macd_signal),
        macd_hist=float(macd_hist),
        bb_upper_dist=float(bb_upper_dist),
        bb_lower_dist=float(bb_lower_dist),
        atr_14=float(atr_14),
        atr_pct=float(atr_pct),
        ema_12=ema_12,
        ema_26=ema_26,
        ema_50=ema_50,
        ema_200=ema_200,
        sma_20=sma_20,
        sma_50=sma_50,
        sma_200=sma_200,
        vwap=vwap,
        stoch_k=stoch_k,
        stoch_d=stoch_d,
        cci=cci,
    )


def _rsi(series: list[float], period: int) -> float:
    deltas = [series[i] - series[i - 1] for i in range(1, len(series))]
    gains = [delta if delta > 0 else 0.0 for delta in deltas]
    losses = [-delta if delta < 0 else 0.0 for delta in deltas]

    avg_gain = _rolling_mean(gains, period)
    avg_loss = _rolling_mean(losses, period)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _macd(series: list[float]) -> tuple[float, float, float]:
    exp12 = _ema(series, 12)
    exp26 = _ema(series, 26)
    macd_series = [a - b for a, b in zip(exp12, exp26, strict=False)]
    signal = _ema(macd_series, 9)
    macd_value = macd_series[-1]
    signal_value = signal[-1]
    hist_value = macd_value - signal_value
    return macd_value, signal_value, hist_value


def _bollinger_bands(
    series: list[float], window: int = 20, num_std: float = 2.0
) -> tuple[float, float]:
    mean = _rolling_mean(series, window)
    std = _rolling_std(series, window)
    upper = mean + (std * num_std)
    lower = mean - (std * num_std)
    return upper, lower


def _atr(high: list[float], low: list[float], close: list[float], period: int) -> float:
    true_ranges: list[float] = []
    for index in range(1, len(close)):
        range_high_low = high[index] - low[index]
        range_high_close = abs(high[index] - close[index - 1])
        range_low_close = abs(low[index] - close[index - 1])
        true_ranges.append(max(range_high_low, range_high_close, range_low_close))
    return _rolling_mean(true_ranges, period)


OhlcvSeries = dict[str, Iterable[float]]


def _as_float_list(values: Iterable[float], field: str) -> list[float]:
    result = [float(value) for value in values]
    if len(result) < 30:
        raise ValueError(f"Not enough data points for {field}")
    return result


def _ema(series: list[float], period: int) -> list[float]:
    smoothing = 2 / (period + 1)
    ema_values: list[float] = [series[0]]
    for value in series[1:]:
        ema_values.append((value - ema_values[-1]) * smoothing + ema_values[-1])
    return ema_values


def _rolling_mean(series: list[float], window: int) -> float:
    if len(series) < window:
        raise ValueError("Not enough data points for rolling mean")
    return sum(series[-window:]) / window


def _rolling_std(series: list[float], window: int) -> float:
    if len(series) < window:
        raise ValueError("Not enough data points for rolling std")
    mean = _rolling_mean(series, window)
    variance = sum((value - mean) ** 2 for value in series[-window:]) / window
    return variance**0.5


def _sma(series: list[float], period: int) -> float:
    """Simple Moving Average."""
    if len(series) < period:
        return None
    return sum(series[-period:]) / period


def _vwap(
    high: list[float], low: list[float], close: list[float], volume: list[float]
) -> float | None:
    """Volume Weighted Average Price."""
    if len(high) < 1 or len(high) != len(low) != len(close) != len(volume):
        return None

    typical_prices = [
        (high_price + low_price + close_price) / 3
        for high_price, low_price, close_price in zip(high, low, close, strict=False)
    ]
    total_pv = sum(tp * vol for tp, vol in zip(typical_prices, volume, strict=False))
    total_vol = sum(volume)

    return total_pv / total_vol if total_vol != 0 else None


def _stochastic(
    high: list[float],
    low: list[float],
    close: list[float],
    k_period: int,
    d_period: int,
) -> tuple[float | None, float | None]:
    """Stochastic Oscillator."""
    if len(close) < k_period:
        return None, None

    k_values = []
    for i in range(k_period - 1, len(close)):
        window_high = max(high[i - k_period + 1 : i + 1])
        window_low = min(low[i - k_period + 1 : i + 1])

        if window_high == window_low:
            k_values.append(50.0)  # Handle edge case
        else:
            k = 100 * (close[i] - window_low) / (window_high - window_low)
            k_values.append(k)

    if len(k_values) < d_period:
        return None, None

    k = k_values[-1]
    d = _sma(k_values, d_period)

    return k, d


def _cci(high: list[float], low: list[float], close: list[float], period: int) -> float | None:
    """Commodity Channel Index."""
    if len(close) < period:
        return None

    # Calculate typical price for each period
    typical_prices = [
        (high_price + low_price + close_price) / 3
        for high_price, low_price, close_price in zip(high, low, close, strict=False)
    ]

    # Calculate SMA of typical prices
    tp_sma = _sma(typical_prices, period)
    if tp_sma is None:
        return None

    # Calculate Mean Deviation
    md = sum(abs(tp - tp_sma) for tp in typical_prices[-period:]) / period

    if md == 0:
        return None

    # Calculate CCI
    cci = (typical_prices[-1] - tp_sma) / (0.015 * md)
    return cci
