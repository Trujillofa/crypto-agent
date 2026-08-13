"""Seeded synthetic OHLCV paths for an opt-in promotion gate.

Stdlib plus ``Ohlcv`` only. No engine, DB, or async imports.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from src.ingest.models import Ohlcv

_SIGMA_FLOOR = 1e-12
_PRICE_FLOOR = 1e-12
_BASE_VOLUME = 1000.0

SUPPORTED_TIMEFRAMES = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}
_STRESS_SCENARIOS = frozenset({"march_2020_gap", "funding_blowout", "flat_wide_range"})


@dataclass(frozen=True)
class RegimeParams:
    """Two-state (calm / stress) Gaussian return parameters and Markov transitions."""

    mu_calm: float
    sigma_calm: float
    mu_stress: float
    sigma_stress: float
    p_calm_to_stress: float
    p_stress_to_calm: float
    p_start_stress: float = 0.0


def bar_delta(timeframe: str) -> timedelta:
    try:
        return SUPPORTED_TIMEFRAMES[timeframe]
    except KeyError:
        raise ValueError(f"unsupported timeframe: {timeframe}") from None


def _mean_sigma(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return 0.0, _SIGMA_FLOOR
    mu = statistics.fmean(values)
    if len(values) < 2:
        return mu, _SIGMA_FLOOR
    return mu, max(statistics.stdev(values), _SIGMA_FLOOR)


def _percentile(values: Sequence[float], p: float) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return ordered[0]
    idx = p * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _classify_states(abs_returns: Sequence[float], threshold: float) -> list[str]:
    return ["stress" if value > threshold else "calm" for value in abs_returns]


def _transition_prob(states: Sequence[str], src: str, dst: str) -> float:
    leaves = 0
    switches = 0
    for current, nxt in zip(states, states[1:], strict=False):
        if current != src:
            continue
        leaves += 1
        if nxt == dst:
            switches += 1
    if leaves == 0:
        return 0.0
    return switches / leaves


def fit_two_state_regime(returns: Sequence[float]) -> RegimeParams:
    """Fit calm/stress Gaussians and empirical Markov transitions from returns."""
    if len(returns) < 2:
        raise ValueError("returns must contain at least 2 observations")

    abs_returns = [abs(value) for value in returns]
    threshold = statistics.median(abs_returns)
    states = _classify_states(abs_returns, threshold)
    if states.count("calm") < 2 or states.count("stress") < 2:
        threshold = _percentile(abs_returns, 0.75)
        states = _classify_states(abs_returns, threshold)

    calm_rets = [value for value, state in zip(returns, states, strict=True) if state == "calm"]
    stress_rets = [value for value, state in zip(returns, states, strict=True) if state == "stress"]
    mu_calm, sigma_calm = _mean_sigma(calm_rets)
    mu_stress, sigma_stress = _mean_sigma(stress_rets)
    return RegimeParams(
        mu_calm=mu_calm,
        sigma_calm=sigma_calm,
        mu_stress=mu_stress,
        sigma_stress=sigma_stress,
        p_calm_to_stress=_transition_prob(states, "calm", "stress"),
        p_stress_to_calm=_transition_prob(states, "stress", "calm"),
        p_start_stress=states.count("stress") / len(states),
    )


def _build_candle(
    *,
    symbol: str,
    timeframe: str,
    open_time: datetime,
    close_time: datetime,
    open_price: float,
    close_price: float,
    sigma: float,
    volume: float,
) -> Ohlcv:
    body_high = max(open_price, close_price)
    body_low = min(open_price, close_price)
    high = body_high * (1.0 + 0.25 * sigma)
    low = body_low * (1.0 - 0.25 * sigma)
    high = max(high, body_high)
    low = min(low, body_low)
    if low <= 0.0:
        low = min(body_low, _PRICE_FLOOR) if body_low > 0.0 else _PRICE_FLOOR
    return Ohlcv(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        close_time=close_time,
        open_price=open_price,
        high_price=high,
        low_price=low,
        close_price=close_price,
        volume=volume,
    )


def _next_close(prev_close: float, ret: float) -> float:
    return max(_PRICE_FLOOR, prev_close * (1.0 + ret))


def generate_regime_path(
    params: RegimeParams,
    *,
    n_bars: int,
    start_price: float,
    seed: int,
    symbol: str = "SYNTH",
    timeframe: str = "1h",
    start_time: datetime | None = None,
) -> tuple[list[Ohlcv], list[str]]:
    """Two-state Markov Gaussian path. Returns (candles, per-bar calm/stress labels)."""
    rng = random.Random(seed)
    origin = start_time if start_time is not None else datetime(2020, 1, 1, tzinfo=UTC)
    delta = bar_delta(timeframe)
    state = "stress" if rng.random() < params.p_start_stress else "calm"
    candles: list[Ohlcv] = []
    states: list[str] = []
    prev_close = start_price
    open_time = origin
    for _ in range(n_bars):
        if state == "stress":
            mu, sigma = params.mu_stress, params.sigma_stress
        else:
            mu, sigma = params.mu_calm, params.sigma_calm
        close_price = _next_close(prev_close, rng.gauss(mu, sigma))
        volume = _BASE_VOLUME * (2.0 if state == "stress" else 1.0)
        candles.append(
            _build_candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=open_time,
                close_time=open_time + delta,
                open_price=prev_close,
                close_price=close_price,
                sigma=sigma,
                volume=volume,
            )
        )
        states.append(state)
        prev_close = close_price
        open_time += delta
        if state == "calm":
            if rng.random() < params.p_calm_to_stress:
                state = "stress"
        elif rng.random() < params.p_stress_to_calm:
            state = "calm"
    return candles, states


def _march_2020_gap(
    rng: random.Random,
    *,
    n_bars: int,
    start_price: float,
    symbol: str,
    timeframe: str,
    start_time: datetime,
) -> list[Ohlcv]:
    n_quiet = max(1, int(0.20 * n_bars))
    n_crash = 4
    if n_quiet + 1 + n_crash >= n_bars:
        n_crash = max(3, n_bars - n_quiet - 2)
    n_recovery = max(0, n_bars - n_quiet - 1 - n_crash)

    delta = bar_delta(timeframe)
    candles: list[Ohlcv] = []
    prev_close = start_price
    open_time = start_time

    def add_bar(open_price: float, close_price: float, sigma: float, volume: float) -> None:
        nonlocal prev_close, open_time
        candles.append(
            _build_candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=open_time,
                close_time=open_time + delta,
                open_price=open_price,
                close_price=close_price,
                sigma=sigma,
                volume=volume,
            )
        )
        prev_close = close_price
        open_time += delta

    quiet_sigma = 0.004
    for _ in range(n_quiet):
        ret = rng.gauss(0.0, 0.001)
        add_bar(prev_close, _next_close(prev_close, ret), quiet_sigma, _BASE_VOLUME)

    pre_gap_close = prev_close
    gap_open = pre_gap_close * 0.85
    gap_close = _next_close(gap_open, rng.gauss(-0.02, 0.005))
    add_bar(gap_open, gap_close, 0.06, _BASE_VOLUME * 2.0)

    target = pre_gap_close * 0.70
    for remaining in range(n_crash, 0, -1):
        step = (target / prev_close) ** (1.0 / remaining) - 1.0 if prev_close > 0.0 else -0.05
        close_price = _next_close(prev_close, step + rng.gauss(0.0, 0.003))
        add_bar(prev_close, close_price, 0.05, _BASE_VOLUME * 2.0)

    for _ in range(n_recovery):
        ret = 0.008 + rng.gauss(0.0, 0.006)
        add_bar(prev_close, _next_close(prev_close, ret), 0.025, _BASE_VOLUME * 2.0)

    return candles


def _funding_blowout(
    rng: random.Random,
    *,
    n_bars: int,
    start_price: float,
    symbol: str,
    timeframe: str,
    start_time: datetime,
) -> list[Ohlcv]:
    n_grind = n_bars // 3
    n_blowout = n_bars // 3
    n_chop = n_bars - n_grind - n_blowout
    delta = bar_delta(timeframe)
    candles: list[Ohlcv] = []
    prev_close = start_price
    open_time = start_time

    def add_bar(ret: float, sigma: float, volume: float) -> None:
        nonlocal prev_close, open_time
        close_price = _next_close(prev_close, ret)
        candles.append(
            _build_candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=open_time,
                close_time=open_time + delta,
                open_price=prev_close,
                close_price=close_price,
                sigma=sigma,
                volume=volume,
            )
        )
        prev_close = close_price
        open_time += delta

    for _ in range(n_grind):
        add_bar(0.004 + rng.gauss(0.0, 0.0005), 0.008, _BASE_VOLUME)

    # Hand-placed +8% then -12% style shock; remaining mid-third is two-way noise.
    shock_down = 1 if n_blowout > 1 else 0
    for i in range(n_blowout):
        if i == 0:
            ret = 0.08 + rng.gauss(0.0, 0.002)
        elif i == shock_down:
            ret = -0.12 + rng.gauss(0.0, 0.002)
        else:
            ret = rng.gauss(0.0, 0.012)
        add_bar(ret, 0.06, _BASE_VOLUME * 2.0)

    mean_px = start_price * (1.0 + 0.004 * n_grind)
    for _ in range(n_chop):
        pull = (mean_px / prev_close) - 1.0 if prev_close > 0.0 else 0.0
        add_bar(0.35 * pull + rng.gauss(0.0, 0.003), 0.015, _BASE_VOLUME)

    return candles


def _flat_wide_range(
    rng: random.Random,
    *,
    n_bars: int,
    start_price: float,
    symbol: str,
    timeframe: str,
    start_time: datetime,
) -> list[Ohlcv]:
    delta = bar_delta(timeframe)
    candles: list[Ohlcv] = []
    prev_close = start_price
    open_time = start_time
    # 0.5 * sigma ≈ 3% high-low when open ≈ close, above the 1.5% floor.
    spread_sigma = 0.06
    lo = start_price * (1.0 - 0.0015)
    hi = start_price * (1.0 + 0.0015)
    for _ in range(n_bars):
        noise = max(-0.0015, min(0.0015, rng.gauss(0.0, 0.0003)))
        close_price = min(hi, max(lo, start_price * (1.0 + noise)))
        close_price = max(_PRICE_FLOOR, close_price)
        candles.append(
            _build_candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=open_time,
                close_time=open_time + delta,
                open_price=prev_close,
                close_price=close_price,
                sigma=spread_sigma,
                volume=_BASE_VOLUME,
            )
        )
        prev_close = close_price
        open_time += delta
    return candles


def generate_stress_path(
    scenario: str,
    *,
    n_bars: int,
    start_price: float,
    seed: int,
    symbol: str = "SYNTH",
    timeframe: str = "1h",
    start_time: datetime | None = None,
) -> list[Ohlcv]:
    """Hand-written stress scenario. Seeded RNG is residual noise only, not the shape."""
    if n_bars < 8:
        raise ValueError("n_bars must be >= 8")
    if scenario not in _STRESS_SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario}")
    rng = random.Random(seed)
    origin = start_time if start_time is not None else datetime(2020, 1, 1, tzinfo=UTC)
    if scenario == "march_2020_gap":
        return _march_2020_gap(
            rng,
            n_bars=n_bars,
            start_price=start_price,
            symbol=symbol,
            timeframe=timeframe,
            start_time=origin,
        )
    if scenario == "funding_blowout":
        return _funding_blowout(
            rng,
            n_bars=n_bars,
            start_price=start_price,
            symbol=symbol,
            timeframe=timeframe,
            start_time=origin,
        )
    return _flat_wide_range(
        rng,
        n_bars=n_bars,
        start_price=start_price,
        symbol=symbol,
        timeframe=timeframe,
        start_time=origin,
    )


def close_returns_pct(candles: Sequence[Ohlcv]) -> list[float]:
    """Close-to-close percent returns. Empty or a single candle yields []."""
    if len(candles) < 2:
        return []
    out: list[float] = []
    for prev, curr in zip(candles, candles[1:], strict=False):
        if prev.close_price == 0.0:
            out.append(0.0)
        else:
            out.append((curr.close_price / prev.close_price - 1.0) * 100.0)
    return out


def synthetic_pass_rate_pct(
    path_returns_pct: Sequence[Sequence[float]],
    is_pass: Callable[[Sequence[float]], bool],
) -> float:
    """Percent of paths for which ``is_pass`` is True. Empty input yields 0.0."""
    if not path_returns_pct:
        return 0.0
    n_pass = sum(1 for path in path_returns_pct if is_pass(path))
    return (n_pass / len(path_returns_pct)) * 100.0
