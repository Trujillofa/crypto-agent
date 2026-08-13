"""Run the real backtest engine over seeded synthetic paths and score pass rate."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal

from src.backtest.engine import BacktestEngine
from src.backtest.experiment_autopilot import compound_returns_pct, max_drawdown_from_returns
from src.backtest.models import BacktestConfig
from src.backtest.synthetic import (
    RegimeParams,
    generate_regime_path,
    generate_stress_path,
)
from src.backtest.synthetic_reader import DEFAULT_WARMUP_BARS, SyntheticIndicatorReader
from src.ingest.models import Ohlcv

DEFAULT_EVAL_BARS = 240
MIN_SCORED_PATHS = 3
TARGET_TRADES_PER_PATH = 4
MIN_EVAL_BARS = 480
MAX_EVAL_BARS = 4000
_TIMEFRAME_HOURS = {"15m": 0.25, "1h": 1.0, "4h": 4.0, "1d": 24.0}
DEFAULT_REGIME_PARAMS = RegimeParams(
    mu_calm=0.0,
    sigma_calm=0.008,
    mu_stress=-0.001,
    sigma_stress=0.025,
    p_calm_to_stress=0.08,
    p_stress_to_calm=0.2,
    p_start_stress=0.15,
)
STRESS_SCENARIOS = ("march_2020_gap", "funding_blowout", "flat_wide_spread")


@dataclass(frozen=True)
class SyntheticEvalResult:
    status: str  # 'scored' | 'inconclusive'
    pass_rate_pct: float  # 0.0 when inconclusive — not a verdict
    scored_paths: int
    total_paths: int
    zero_trade_paths: int


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def bars_from_range(start: str, end: str, timeframe: str) -> int:
    """Count bars between ISO datetimes for 1h/4h/1d/15m (naive timestamps = UTC)."""
    elapsed_hours = (_parse_iso(end) - _parse_iso(start)).total_seconds() / 3600.0
    bar_hours = _TIMEFRAME_HOURS.get(timeframe)
    if bar_hours is None:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    return max(1, math.floor(elapsed_hours / bar_hours))


def eval_bars_from_trade_rate(
    *,
    historical_trades: int,
    historical_bars: int,
    target_trades_per_path: int = TARGET_TRADES_PER_PATH,
) -> int:
    """Size the eval window from historical trades-per-bar, clamped to production bounds."""
    if historical_trades <= 0 or historical_bars <= 0:
        return MIN_EVAL_BARS
    needed = math.ceil(target_trades_per_path / (historical_trades / historical_bars))
    return min(MAX_EVAL_BARS, max(MIN_EVAL_BARS, needed))


def score_path(
    returns: Sequence[float],
    *,
    kind: Literal["regime", "stress"],
    max_drawdown_pct: float = 10.0,
    min_return_pct: float = 0.0,
) -> bool | None:
    """Score one path. Empty (no-trade) returns None and leaves the denominator."""
    if not returns:
        return None
    values = list(returns)
    if kind == "regime":
        return compound_returns_pct(values) >= min_return_pct
    return max_drawdown_from_returns(values) <= max_drawdown_pct


def _rate_from_outcomes(outcomes: Sequence[bool | None]) -> SyntheticEvalResult:
    scored = [outcome for outcome in outcomes if outcome is not None]
    total = len(outcomes)
    zeros = sum(1 for outcome in outcomes if outcome is None)
    if len(scored) < MIN_SCORED_PATHS:
        return SyntheticEvalResult("inconclusive", 0.0, len(scored), total, zeros)
    rate = 100.0 * sum(1 for outcome in scored if outcome) / len(scored)
    return SyntheticEvalResult("scored", rate, len(scored), total, zeros)


def _config_for_window(config: BacktestConfig, start: str, end: str) -> BacktestConfig:
    return replace(config, start_date=start, end_date=end)


async def _run_path(
    config: BacktestConfig,
    candles: Sequence[Ohlcv],
    warmup_bars: int,
) -> list[float]:
    reader = SyntheticIndicatorReader(candles, warmup_bars=warmup_bars)
    cfg = _config_for_window(config, reader.eval_start.isoformat(), reader.eval_end.isoformat())
    result = await BacktestEngine(cfg, reader).run()
    return [trade.return_pct for trade in result.trades]


async def evaluate_synthetic_pass_rate(
    config: BacktestConfig,
    *,
    n_regime_paths: int = 3,
    include_stress: bool = True,
    warmup_bars: int = DEFAULT_WARMUP_BARS,
    eval_bars: int | None = None,
    historical_trades: int = 0,
    historical_bars: int = 0,
    seed: int = 42,
    start_price: float = 100.0,
    regime_params: RegimeParams | None = None,
    max_drawdown_pct: float = 10.0,
    min_return_pct: float = 0.0,
) -> SyntheticEvalResult:
    """Score regime (return floor) and stress (drawdown) paths. Zero-trade paths leave the rate."""
    if eval_bars is None:
        eval_bars = eval_bars_from_trade_rate(
            historical_trades=historical_trades,
            historical_bars=historical_bars,
        )
    params = regime_params if regime_params is not None else DEFAULT_REGIME_PARAMS
    n_bars = warmup_bars + eval_bars
    outcomes: list[bool | None] = []

    for i in range(n_regime_paths):
        candles, _states = generate_regime_path(
            params,
            n_bars=n_bars,
            start_price=start_price,
            seed=seed + i,
        )
        outcomes.append(
            score_path(
                await _run_path(config, candles, warmup_bars),
                kind="regime",
                max_drawdown_pct=max_drawdown_pct,
                min_return_pct=min_return_pct,
            )
        )

    if include_stress:
        for j, scenario in enumerate(STRESS_SCENARIOS):
            candles = generate_stress_path(
                scenario,
                n_bars=n_bars,
                start_price=start_price,
                seed=seed + 100 + j,
            )
            outcomes.append(
                score_path(
                    await _run_path(config, candles, warmup_bars),
                    kind="stress",
                    max_drawdown_pct=max_drawdown_pct,
                    min_return_pct=min_return_pct,
                )
            )

    return _rate_from_outcomes(outcomes)
