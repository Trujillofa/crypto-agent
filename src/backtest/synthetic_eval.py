"""Run the real backtest engine over seeded synthetic paths and score pass rate."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from src.backtest.engine import BacktestEngine
from src.backtest.experiment_autopilot import compound_returns_pct, max_drawdown_from_returns
from src.backtest.models import BacktestConfig
from src.backtest.synthetic import (
    RegimeParams,
    generate_regime_path,
    generate_stress_path,
    synthetic_pass_rate_pct,
)
from src.backtest.synthetic_reader import DEFAULT_WARMUP_BARS, SyntheticIndicatorReader
from src.ingest.models import Ohlcv

DEFAULT_EVAL_BARS = 240
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


def path_passes(
    trade_returns_pct: Sequence[float],
    *,
    max_drawdown_pct: float = 10.0,
    min_return_pct: float = 0.0,
) -> bool:
    """Empty (no-trade) paths fail. Otherwise require return and drawdown floors."""
    if not trade_returns_pct:
        return False
    returns = list(trade_returns_pct)
    return (
        compound_returns_pct(returns) >= min_return_pct
        and max_drawdown_from_returns(returns) <= max_drawdown_pct
    )


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
    eval_bars: int = DEFAULT_EVAL_BARS,
    seed: int = 42,
    start_price: float = 100.0,
    regime_params: RegimeParams | None = None,
    max_drawdown_pct: float = 10.0,
    min_return_pct: float = 0.0,
) -> float:
    """Percent of synthetic paths that pass return/drawdown checks. Computes only."""
    params = regime_params if regime_params is not None else DEFAULT_REGIME_PARAMS
    n_bars = warmup_bars + eval_bars
    path_returns: list[list[float]] = []

    for i in range(n_regime_paths):
        candles, _states = generate_regime_path(
            params,
            n_bars=n_bars,
            start_price=start_price,
            seed=seed + i,
        )
        path_returns.append(await _run_path(config, candles, warmup_bars))

    if include_stress:
        for j, scenario in enumerate(STRESS_SCENARIOS):
            candles = generate_stress_path(
                scenario,
                n_bars=n_bars,
                start_price=start_price,
                seed=seed + 100 + j,
            )
            path_returns.append(await _run_path(config, candles, warmup_bars))

    return synthetic_pass_rate_pct(
        path_returns,
        lambda returns: path_passes(
            returns,
            max_drawdown_pct=max_drawdown_pct,
            min_return_pct=min_return_pct,
        ),
    )
