#!/usr/bin/env python3
"""Search configuration candidates against explicit backtest gates."""

from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import itertools
import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import yaml

sys.path.append(os.getcwd())

from src.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from src.backtest.experiment_autopilot import build_wfo_windows, wfo_inclusive_fetch_bounds
from src.backtest.factory import BacktestRequest, build_backtest_config
from src.backtest.ranking import RankedCandidate, rank_by_selection_score
from src.backtest.research_safety import refuse_live_go
from src.db import close_pool, get_pool, init_pool
from src.features.reader import IndicatorReader
from src.main import _resolve_strategy_config, load_settings
from src.utils.logger import configure_logger


@dataclass(frozen=True)
class SearchCandidate:
    """Parameter set to evaluate."""

    name: str
    buy_threshold: float
    buy_threshold_uptrend: float
    sell_threshold: float
    apply_global_trend_filter: bool
    rsi_oversold: float
    rsi_overbought: float
    macd_hist_threshold: float
    macd_atr_min_pct: float
    vwap_atr_multiplier: float
    vwap_rsi_oversold: float
    vwap_rsi_overbought: float
    trend_pullback_rsi_reclaim_level: float | None = None
    trend_pullback_min_trend_strength_pct: float | None = None
    trend_pullback_max_pullback_distance_pct: float | None = None
    trend_pullback_vwap_pullback_distance_pct: float | None = None
    trend_pullback_min_atr_pct: float | None = None
    trend_pullback_min_macd_hist: float | None = None
    trend_pullback_strong_trend_strength_pct: float | None = None
    trend_pullback_continuation_rsi_level: float | None = None
    trend_pullback_continuation_max_vwap_distance_pct: float | None = None
    trend_pullback_continuation_max_ema50_extension_pct: float | None = None
    trend_pullback_continuation_min_macd_hist: float | None = None


@dataclass(frozen=True)
class CandidateMetrics:
    """Summary metrics for one configuration candidate."""

    name: str
    symbol: str
    timeframe: str
    start: str
    end: str
    total_trades: int
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    wfo_windows: int
    wfo_total_trades: int
    wfo_mean_sharpe: float
    wfo_total_return_pct: float
    bootstrap_p_loss_pct: float
    profit_concentration_pct: float
    selection_return_pct: float
    selection_sharpe: float
    passes_gates: bool
    failure_reasons: str


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Configuration search with validation gates")
    parser.add_argument("--config", required=True, help="Base config path")
    parser.add_argument("--symbol", help="Override symbol; defaults to config trading pair")
    parser.add_argument("--timeframe", help="Override timeframe; defaults to config timeframe")
    parser.add_argument("--start", help="Start date; defaults to first indicator timestamp")
    parser.add_argument("--end", help="End date; defaults to last indicator timestamp")
    parser.add_argument(
        "--profile",
        choices=("quick", "coarse", "sol_refine", "trend_pullback_v2", "trend_pullback_v3"),
        default="quick",
        help="Candidate grid profile",
    )
    parser.add_argument(
        "--train-months",
        type=int,
        default=6,
        help="Rolling train window size in months",
    )
    parser.add_argument(
        "--test-months",
        type=int,
        default=3,
        help="Rolling test window size in months",
    )
    parser.add_argument(
        "--bootstrap",
        type=int,
        default=500,
        help="Bootstrap iterations per candidate",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="Optional limit for candidate count (0 = no limit)",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=0,
        help="Minimum full-period trades gate (0 disables this gate)",
    )
    parser.add_argument(
        "--min-wfo-trades",
        type=int,
        default=20,
        help="Minimum aggregate walk-forward trades gate",
    )
    parser.add_argument(
        "--min-wfo-sharpe",
        type=float,
        default=0.5,
        help="Minimum walk-forward mean Sharpe gate",
    )
    parser.add_argument(
        "--max-drawdown-pct",
        type=float,
        default=10.0,
        help="Maximum drawdown gate in percent",
    )
    parser.add_argument(
        "--max-bootstrap-p-loss-pct",
        type=float,
        default=25.0,
        help="Maximum allowed bootstrap loss probability in percent",
    )
    parser.add_argument(
        "--min-oos-return-pct",
        type=float,
        default=0.0,
        help="Minimum walk-forward total return gate in percent",
    )
    parser.add_argument(
        "--max-profit-concentration-pct",
        type=float,
        default=50.0,
        help="Maximum share of profit contributed by any single OOS window",
    )
    parser.add_argument(
        "--output-prefix",
        default="docs/reports/config-search",
        help="Output file prefix for CSV/JSON artifacts",
    )
    return parser.parse_args()


def _parse_db_config(settings: object) -> dict[str, object]:
    """Build DB config from settings and environment."""
    return {
        "host": str(os.getenv("DB_HOST", settings.database.get("host", "localhost"))),
        "port": int(os.getenv("DB_PORT", int(settings.database.get("port", 5432)))),
        "name": str(os.getenv("DB_NAME", settings.database.get("name", "marketdata"))),
        "user": str(os.getenv("DB_USER", settings.database.get("user", "trading"))),
        "password": str(os.getenv("DB_PASSWORD", settings.database.get("password", ""))),
    }


async def _resolve_range(symbol: str, timeframe: str) -> tuple[str, str]:
    """Resolve the full available indicator range for a symbol/timeframe."""
    query = """
        SELECT min(time) AS start_time, max(time) AS end_time
        FROM indicators
        WHERE symbol = $1 AND timeframe = $2
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, symbol, timeframe)
    if row is None or row["start_time"] is None or row["end_time"] is None:
        raise RuntimeError(f"No indicator data found for {symbol} {timeframe}")
    return row["start_time"].isoformat(), row["end_time"].isoformat()


def _make_candidate_grid(profile: str) -> list[SearchCandidate]:
    """Generate the parameter grid for the selected profile."""
    candidates: list[SearchCandidate] = []
    if profile == "trend_pullback_v3":
        buy_thresholds = [0.45, 0.55]
        rsi_reclaim_levels = [46.0, 48.0]
        min_trend_strength_pcts = [0.006, 0.008]
        vwap_pullback_distance_pcts = [0.03, 0.05]
        strong_trend_strength_pcts = [0.012, 0.015]
        continuation_rsi_levels = [52.0, 54.0]
        continuation_max_vwap_distance_pcts = [0.04, 0.06]
        index = 1
        for (
            buy_threshold,
            rsi_reclaim_level,
            min_trend_strength_pct,
            vwap_pullback_distance_pct,
            strong_trend_strength_pct,
            continuation_rsi_level,
            continuation_max_vwap_distance_pct,
        ) in itertools.product(
            buy_thresholds,
            rsi_reclaim_levels,
            min_trend_strength_pcts,
            vwap_pullback_distance_pcts,
            strong_trend_strength_pcts,
            continuation_rsi_levels,
            continuation_max_vwap_distance_pcts,
        ):
            candidates.append(
                SearchCandidate(
                    name=f"trend-pullback-v3-{index:03d}",
                    buy_threshold=buy_threshold,
                    buy_threshold_uptrend=buy_threshold,
                    sell_threshold=-1.0,
                    apply_global_trend_filter=True,
                    rsi_oversold=35.0,
                    rsi_overbought=65.0,
                    macd_hist_threshold=0.0,
                    macd_atr_min_pct=0.002,
                    vwap_atr_multiplier=1.25,
                    vwap_rsi_oversold=40.0,
                    vwap_rsi_overbought=60.0,
                    trend_pullback_rsi_reclaim_level=rsi_reclaim_level,
                    trend_pullback_min_trend_strength_pct=min_trend_strength_pct,
                    trend_pullback_max_pullback_distance_pct=0.02,
                    trend_pullback_vwap_pullback_distance_pct=vwap_pullback_distance_pct,
                    trend_pullback_min_atr_pct=0.008,
                    trend_pullback_min_macd_hist=-0.01,
                    trend_pullback_strong_trend_strength_pct=strong_trend_strength_pct,
                    trend_pullback_continuation_rsi_level=continuation_rsi_level,
                    trend_pullback_continuation_max_vwap_distance_pct=(
                        continuation_max_vwap_distance_pct
                    ),
                    trend_pullback_continuation_max_ema50_extension_pct=0.03,
                    trend_pullback_continuation_min_macd_hist=-0.01,
                )
            )
            index += 1
        return candidates

    if profile == "trend_pullback_v2":
        buy_thresholds = [0.45, 0.55]
        rsi_reclaim_levels = [46.0, 48.0, 50.0]
        min_trend_strength_pcts = [0.006, 0.008, 0.01]
        max_pullback_distance_pcts = [0.015, 0.02]
        vwap_pullback_distance_pcts = [0.02, 0.03, 0.05]
        min_atr_pcts = [0.008, 0.01]
        min_macd_hists = [-0.01, 0.0]
        index = 1
        for (
            buy_threshold,
            rsi_reclaim_level,
            min_trend_strength_pct,
            max_pullback_distance_pct,
            vwap_pullback_distance_pct,
            min_atr_pct,
            min_macd_hist,
        ) in itertools.product(
            buy_thresholds,
            rsi_reclaim_levels,
            min_trend_strength_pcts,
            max_pullback_distance_pcts,
            vwap_pullback_distance_pcts,
            min_atr_pcts,
            min_macd_hists,
        ):
            candidates.append(
                SearchCandidate(
                    name=f"trend-pullback-v2-{index:03d}",
                    buy_threshold=buy_threshold,
                    buy_threshold_uptrend=buy_threshold,
                    sell_threshold=-1.0,
                    apply_global_trend_filter=True,
                    rsi_oversold=35.0,
                    rsi_overbought=65.0,
                    macd_hist_threshold=0.0,
                    macd_atr_min_pct=0.002,
                    vwap_atr_multiplier=1.25,
                    vwap_rsi_oversold=40.0,
                    vwap_rsi_overbought=60.0,
                    trend_pullback_rsi_reclaim_level=rsi_reclaim_level,
                    trend_pullback_min_trend_strength_pct=min_trend_strength_pct,
                    trend_pullback_max_pullback_distance_pct=max_pullback_distance_pct,
                    trend_pullback_vwap_pullback_distance_pct=vwap_pullback_distance_pct,
                    trend_pullback_min_atr_pct=min_atr_pct,
                    trend_pullback_min_macd_hist=min_macd_hist,
                )
            )
            index += 1
        return candidates

    if profile == "quick":
        buy_thresholds = [0.8, 1.0, 1.1]
        sell_thresholds = [-0.8, -1.0]
        trend_filters = [True, False]
        rsi_pairs = [(35.0, 65.0), (40.0, 60.0)]
        macd_hist_thresholds = [0.0, 0.0001]
        macd_atr_thresholds = [0.002, 0.003]
        vwap_atr_multipliers = [1.25, 1.5]
        vwap_rsi_pairs = [(40.0, 60.0)]
        index = 1
        for (
            buy_threshold,
            sell_threshold,
            trend_filter,
            rsi_pair,
            macd_hist_threshold,
            macd_atr_min_pct,
            vwap_atr_multiplier,
            vwap_rsi_pair,
        ) in itertools.product(
            buy_thresholds,
            sell_thresholds,
            trend_filters,
            rsi_pairs,
            macd_hist_thresholds,
            macd_atr_thresholds,
            vwap_atr_multipliers,
            vwap_rsi_pairs,
        ):
            candidates.append(
                SearchCandidate(
                    name=f"quick-{index:03d}",
                    buy_threshold=buy_threshold,
                    buy_threshold_uptrend=buy_threshold,
                    sell_threshold=sell_threshold,
                    apply_global_trend_filter=trend_filter,
                    rsi_oversold=rsi_pair[0],
                    rsi_overbought=rsi_pair[1],
                    macd_hist_threshold=macd_hist_threshold,
                    macd_atr_min_pct=macd_atr_min_pct,
                    vwap_atr_multiplier=vwap_atr_multiplier,
                    vwap_rsi_oversold=vwap_rsi_pair[0],
                    vwap_rsi_overbought=vwap_rsi_pair[1],
                )
            )
            index += 1
        return candidates

    if profile == "sol_refine":
        buy_thresholds = [1.1, 1.2]
        buy_threshold_uptrends = [1.0, 1.1]
        sell_thresholds = [-1.0, -1.2]
        trend_filters = [False, True]
        rsi_pairs = [(39.0, 61.0), (40.0, 60.0)]
        macd_hist_thresholds = [0.0, 0.0001]
        macd_atr_thresholds = [0.002, 0.003]
        vwap_atr_multipliers = [1.5, 1.75]
        vwap_rsi_pairs = [(39.0, 61.0), (40.0, 60.0)]
        index = 1
        for (
            buy_threshold,
            buy_threshold_uptrend,
            sell_threshold,
            trend_filter,
            rsi_pair,
            macd_hist_threshold,
            macd_atr_min_pct,
            vwap_atr_multiplier,
            vwap_rsi_pair,
        ) in itertools.product(
            buy_thresholds,
            buy_threshold_uptrends,
            sell_thresholds,
            trend_filters,
            rsi_pairs,
            macd_hist_thresholds,
            macd_atr_thresholds,
            vwap_atr_multipliers,
            vwap_rsi_pairs,
        ):
            candidates.append(
                SearchCandidate(
                    name=f"sol-refine-{index:03d}",
                    buy_threshold=buy_threshold,
                    buy_threshold_uptrend=buy_threshold_uptrend,
                    sell_threshold=sell_threshold,
                    apply_global_trend_filter=trend_filter,
                    rsi_oversold=rsi_pair[0],
                    rsi_overbought=rsi_pair[1],
                    macd_hist_threshold=macd_hist_threshold,
                    macd_atr_min_pct=macd_atr_min_pct,
                    vwap_atr_multiplier=vwap_atr_multiplier,
                    vwap_rsi_oversold=vwap_rsi_pair[0],
                    vwap_rsi_overbought=vwap_rsi_pair[1],
                )
            )
            index += 1
        return candidates

    buy_thresholds = [0.8, 1.0, 1.1]
    buy_threshold_uptrends = [0.8, 1.0, 1.1]
    sell_thresholds = [-0.8, -1.0]
    trend_filters = [True, False]
    rsi_pairs = [(35.0, 65.0), (40.0, 60.0)]
    macd_hist_thresholds = [0.0, 0.00005, 0.0001]
    macd_atr_thresholds = [0.002, 0.003]
    vwap_atr_multipliers = [1.25, 1.5]
    vwap_rsi_pairs = [(35.0, 65.0), (40.0, 60.0)]
    index = 1
    for (
        buy_threshold,
        buy_threshold_uptrend,
        sell_threshold,
        trend_filter,
        rsi_pair,
        macd_hist_threshold,
        macd_atr_min_pct,
        vwap_atr_multiplier,
        vwap_rsi_pair,
    ) in itertools.product(
        buy_thresholds,
        buy_threshold_uptrends,
        sell_thresholds,
        trend_filters,
        rsi_pairs,
        macd_hist_thresholds,
        macd_atr_thresholds,
        vwap_atr_multipliers,
        vwap_rsi_pairs,
    ):
        candidates.append(
            SearchCandidate(
                name=f"coarse-{index:03d}",
                buy_threshold=buy_threshold,
                buy_threshold_uptrend=buy_threshold_uptrend,
                sell_threshold=sell_threshold,
                apply_global_trend_filter=trend_filter,
                rsi_oversold=rsi_pair[0],
                rsi_overbought=rsi_pair[1],
                macd_hist_threshold=macd_hist_threshold,
                macd_atr_min_pct=macd_atr_min_pct,
                vwap_atr_multiplier=vwap_atr_multiplier,
                vwap_rsi_oversold=vwap_rsi_pair[0],
                vwap_rsi_overbought=vwap_rsi_pair[1],
            )
        )
        index += 1
    return candidates


def _update_strategy_config(
    raw_config: dict[str, object], candidate: SearchCandidate
) -> dict[str, object]:
    """Apply candidate parameters to a raw YAML config."""
    updated = copy.deepcopy(raw_config)
    strategy_root = updated.setdefault("strategy", {})
    aggregator = strategy_root.setdefault("aggregator", {})
    aggregator["buy_threshold"] = candidate.buy_threshold
    aggregator["buy_threshold_uptrend"] = candidate.buy_threshold_uptrend
    aggregator["sell_threshold"] = candidate.sell_threshold

    strategies = strategy_root.get("strategies", [])
    for strategy in strategies:
        if strategy.get("name") == "rsi_reversal":
            strategy.setdefault("config", {})
            strategy["config"]["oversold_threshold"] = candidate.rsi_oversold
            strategy["config"]["overbought_threshold"] = candidate.rsi_overbought
        elif strategy.get("name") == "macd_histogram":
            strategy.setdefault("config", {})
            strategy["config"]["min_histogram_threshold"] = candidate.macd_hist_threshold
            strategy["config"]["atr_min_pct"] = candidate.macd_atr_min_pct
        elif strategy.get("name") == "vwap_reversion":
            strategy.setdefault("config", {})
            strategy["config"]["vwap_atr_multiplier"] = candidate.vwap_atr_multiplier
            strategy["config"]["rsi_oversold"] = candidate.vwap_rsi_oversold
            strategy["config"]["rsi_overbought"] = candidate.vwap_rsi_overbought
        elif strategy.get("name") == "trend_pullback":
            strategy.setdefault("config", {})
            if candidate.trend_pullback_rsi_reclaim_level is not None:
                strategy["config"]["rsi_reclaim_level"] = candidate.trend_pullback_rsi_reclaim_level
            if candidate.trend_pullback_min_trend_strength_pct is not None:
                strategy["config"]["min_trend_strength_pct"] = (
                    candidate.trend_pullback_min_trend_strength_pct
                )
            if candidate.trend_pullback_max_pullback_distance_pct is not None:
                strategy["config"]["max_pullback_distance_pct"] = (
                    candidate.trend_pullback_max_pullback_distance_pct
                )
            if candidate.trend_pullback_vwap_pullback_distance_pct is not None:
                strategy["config"]["vwap_pullback_distance_pct"] = (
                    candidate.trend_pullback_vwap_pullback_distance_pct
                )
            if candidate.trend_pullback_min_atr_pct is not None:
                strategy["config"]["min_atr_pct"] = candidate.trend_pullback_min_atr_pct
            if candidate.trend_pullback_min_macd_hist is not None:
                strategy["config"]["min_macd_hist"] = candidate.trend_pullback_min_macd_hist
            if candidate.trend_pullback_strong_trend_strength_pct is not None:
                strategy["config"]["strong_trend_strength_pct"] = (
                    candidate.trend_pullback_strong_trend_strength_pct
                )
            if candidate.trend_pullback_continuation_rsi_level is not None:
                strategy["config"]["continuation_rsi_level"] = (
                    candidate.trend_pullback_continuation_rsi_level
                )
            if candidate.trend_pullback_continuation_max_vwap_distance_pct is not None:
                strategy["config"]["continuation_max_vwap_distance_pct"] = (
                    candidate.trend_pullback_continuation_max_vwap_distance_pct
                )
            if candidate.trend_pullback_continuation_max_ema50_extension_pct is not None:
                strategy["config"]["continuation_max_ema50_extension_pct"] = (
                    candidate.trend_pullback_continuation_max_ema50_extension_pct
                )
            if candidate.trend_pullback_continuation_min_macd_hist is not None:
                strategy["config"]["continuation_min_macd_hist"] = (
                    candidate.trend_pullback_continuation_min_macd_hist
                )
    return updated


def _write_temp_config(raw_config: dict[str, object]) -> Path:
    """Write a temporary YAML config and return its path."""
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    with handle:
        yaml.safe_dump(raw_config, handle, sort_keys=False)
    return Path(handle.name)


def _build_backtest_config(
    settings: object,
    strategy_classes: list[type[object]],
    strategy_configs: list[dict[str, object] | None],
    aggregator_config: dict[str, object],
    raw_config: dict[str, object],
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    apply_global_trend_filter: bool,
) -> BacktestConfig:
    """Translate runtime settings into a BacktestConfig."""
    return build_backtest_config(
        request=BacktestRequest(
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            trend_filter_override=apply_global_trend_filter,
            execution_profile="execution_parity_v2",
        ),
        settings=settings,
        raw_config=raw_config,
        strategy_classes=strategy_classes,
        strategy_configs=strategy_configs,
        aggregator_config=aggregator_config,
    )


async def _run_backtest(backtest_config: BacktestConfig, reader: IndicatorReader) -> BacktestResult:
    """Run a single backtest."""
    return await BacktestEngine(backtest_config, reader).run()


def _compute_bootstrap_loss_probability(trade_returns: list[float], iterations: int) -> float:
    """Estimate the percent of bootstrap samples with negative total return."""
    if not trade_returns:
        return 100.0
    rng = __import__("random").Random(42)
    losses = 0
    trade_count = len(trade_returns)
    for _ in range(iterations):
        sample = rng.choices(trade_returns, k=trade_count)
        compound = 1.0
        for value in sample:
            compound *= 1.0 + value / 100.0
        total_return_pct = (compound - 1.0) * 100.0
        if total_return_pct < 0:
            losses += 1
    return losses / iterations * 100.0


async def _run_wfo_windows(
    settings: object,
    strategy_classes: list[type[object]],
    strategy_configs: list[dict[str, object] | None],
    aggregator_config: dict[str, object],
    raw_config: dict[str, object],
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    apply_global_trend_filter: bool,
    train_months: int,
    test_months: int,
    reader: IndicatorReader,
) -> tuple[int, float, float, float]:
    """Run rolling out-of-sample windows and return summary metrics."""
    windows = build_wfo_windows(start, end, train_months, test_months)
    window_returns: list[float] = []
    window_sharpes: list[float] = []
    window_trade_counts: list[int] = []

    for window in windows:
        _, _, test_start, test_end = wfo_inclusive_fetch_bounds(window)
        cfg = _build_backtest_config(
            settings,
            strategy_classes,
            strategy_configs,
            aggregator_config,
            raw_config,
            symbol,
            timeframe,
            test_start,
            test_end,
            apply_global_trend_filter,
        )
        result = await _run_backtest(cfg, reader)
        window_returns.append(result.total_return_pct)
        window_sharpes.append(result.sharpe_ratio)
        window_trade_counts.append(result.total_trades)

    if not window_returns:
        return 0, 0, 0.0, 0.0, 100.0

    compound = 1.0
    for value in window_returns:
        compound *= 1.0 + value / 100.0
    total_return_pct = (compound - 1.0) * 100.0
    mean_sharpe = sum(window_sharpes) / len(window_sharpes)
    positive_returns = [value for value in window_returns if value > 0]
    if positive_returns and sum(positive_returns) > 0:
        concentration = max(positive_returns) / sum(positive_returns) * 100.0
    else:
        concentration = 100.0
    return (
        len(window_returns),
        sum(window_trade_counts),
        mean_sharpe,
        total_return_pct,
        concentration,
    )


async def _evaluate_candidate(
    base_raw_config: dict[str, object],
    candidate: SearchCandidate,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    bootstrap_iterations: int,
    train_months: int,
    test_months: int,
    gates: dict[str, float],
    reader: IndicatorReader,
) -> CandidateMetrics:
    """Run full validation for one candidate."""
    temp_path: Path | None = None
    try:
        updated_raw_config = _update_strategy_config(base_raw_config, candidate)
        temp_path = _write_temp_config(updated_raw_config)
        settings = load_settings(temp_path)
        resolved = _resolve_strategy_config(settings.strategy)
        strategy_classes = resolved[0]
        strategy_configs = resolved[1]
        aggregator_config = resolved[2]

        full_config = _build_backtest_config(
            settings,
            strategy_classes,
            strategy_configs,
            aggregator_config,
            updated_raw_config,
            symbol,
            timeframe,
            start,
            end,
            candidate.apply_global_trend_filter,
        )
        full_result = await _run_backtest(full_config, reader)
        windows = build_wfo_windows(start, end, train_months, test_months)
        if windows:
            train_start, train_end, _, _ = wfo_inclusive_fetch_bounds(windows[0])
            train_config = _build_backtest_config(
                settings,
                strategy_classes,
                strategy_configs,
                aggregator_config,
                updated_raw_config,
                symbol,
                timeframe,
                train_start,
                train_end,
                candidate.apply_global_trend_filter,
            )
            train_result = await _run_backtest(train_config, reader)
            selection_return_pct = train_result.total_return_pct
            selection_sharpe = train_result.sharpe_ratio
        else:
            selection_return_pct = 0.0
            selection_sharpe = 0.0
        trade_returns = [trade.return_pct for trade in full_result.trades]
        bootstrap_p_loss_pct = _compute_bootstrap_loss_probability(
            trade_returns, bootstrap_iterations
        )
        (
            wfo_windows,
            wfo_total_trades,
            wfo_mean_sharpe,
            wfo_total_return_pct,
            profit_concentration_pct,
        ) = await _run_wfo_windows(
            settings,
            strategy_classes,
            strategy_configs,
            aggregator_config,
            updated_raw_config,
            symbol,
            timeframe,
            start,
            end,
            candidate.apply_global_trend_filter,
            train_months,
            test_months,
            reader,
        )

        failure_reasons: list[str] = []
        if gates["min_trades"] > 0 and full_result.total_trades < int(gates["min_trades"]):
            failure_reasons.append("trades")
        if wfo_total_trades < int(gates["min_wfo_trades"]):
            failure_reasons.append("wfo_trades")
        if full_result.max_drawdown * 100 > gates["max_drawdown_pct"]:
            failure_reasons.append("drawdown")
        if wfo_total_return_pct <= gates["min_oos_return_pct"]:
            failure_reasons.append("oos_return")
        if wfo_mean_sharpe < gates["min_wfo_sharpe"]:
            failure_reasons.append("wfo_sharpe")
        if bootstrap_p_loss_pct >= gates["max_bootstrap_p_loss_pct"]:
            failure_reasons.append("bootstrap")
        if profit_concentration_pct > gates["max_profit_concentration_pct"]:
            failure_reasons.append("concentration")

        return CandidateMetrics(
            name=candidate.name,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            total_trades=full_result.total_trades,
            win_rate=full_result.win_rate,
            total_return_pct=full_result.total_return_pct,
            max_drawdown_pct=full_result.max_drawdown * 100,
            sharpe_ratio=full_result.sharpe_ratio,
            wfo_windows=wfo_windows,
            wfo_total_trades=wfo_total_trades,
            wfo_mean_sharpe=wfo_mean_sharpe,
            wfo_total_return_pct=wfo_total_return_pct,
            bootstrap_p_loss_pct=bootstrap_p_loss_pct,
            profit_concentration_pct=profit_concentration_pct,
            selection_return_pct=selection_return_pct,
            selection_sharpe=selection_sharpe,
            passes_gates=not failure_reasons,
            failure_reasons=",".join(failure_reasons),
        )
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _write_artifacts(output_prefix: str, metrics: list[CandidateMetrics]) -> tuple[Path, Path]:
    """Write CSV and JSON result artifacts."""
    date_tag = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = Path(f"{output_prefix}-{date_tag}.csv")
    json_path = Path(f"{output_prefix}-{date_tag}.json")
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    rows = [asdict(metric) for metric in metrics]
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        csv_path.write_text("", encoding="utf-8")

    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(rows, handle, indent=2)

    return csv_path, json_path


async def main() -> None:
    """Entry point."""
    configure_logger("WARNING")
    args = parse_args()
    refuse_live_go(argv=sys.argv[1:], flags=vars(args))

    config_path = Path(args.config)
    base_settings = load_settings(config_path)
    symbol = args.symbol or base_settings.trading_pairs[0]
    timeframe = args.timeframe or base_settings.timeframe
    db_config = _parse_db_config(base_settings)
    with config_path.open("r", encoding="utf-8") as handle:
        base_raw_config = yaml.safe_load(handle)

    await init_pool(db_config)
    try:
        resolved_start, resolved_end = await _resolve_range(symbol, timeframe)
        start = args.start or resolved_start
        end = args.end or resolved_end
        candidates = _make_candidate_grid(args.profile)
        if args.max_candidates > 0:
            candidates = candidates[: args.max_candidates]

        gates = {
            "min_trades": float(args.min_trades),
            "min_wfo_trades": float(args.min_wfo_trades),
            "min_wfo_sharpe": args.min_wfo_sharpe,
            "max_drawdown_pct": args.max_drawdown_pct,
            "max_bootstrap_p_loss_pct": args.max_bootstrap_p_loss_pct,
            "min_oos_return_pct": args.min_oos_return_pct,
            "max_profit_concentration_pct": args.max_profit_concentration_pct,
        }

        print(
            f"Searching {len(candidates)} candidates for {symbol} {timeframe} from {start} to {end}"
        )
        print(
            "Gates: "
            f"min_trades={args.min_trades}, "
            f"min_wfo_trades={args.min_wfo_trades}, "
            f"min_wfo_sharpe={args.min_wfo_sharpe}, "
            f"max_drawdown_pct={args.max_drawdown_pct}, "
            f"max_bootstrap_p_loss_pct={args.max_bootstrap_p_loss_pct}, "
            f"min_oos_return_pct={args.min_oos_return_pct}, "
            f"max_profit_concentration_pct={args.max_profit_concentration_pct}"
        )

        metrics: list[CandidateMetrics] = []
        reader = IndicatorReader(db_config)
        async with reader:
            for index, candidate in enumerate(candidates, start=1):
                result = await _evaluate_candidate(
                    base_raw_config,
                    candidate,
                    symbol,
                    timeframe,
                    start,
                    end,
                    args.bootstrap,
                    args.train_months,
                    args.test_months,
                    gates,
                    reader,
                )
                metrics.append(result)
                print(
                    f"[{index}/{len(candidates)}] {result.name}: "
                    f"pass={result.passes_gates} "
                    f"wfo_trades={result.wfo_total_trades} "
                    f"return={result.total_return_pct:.2f}% "
                    f"wfo={result.wfo_total_return_pct:.2f}% "
                    f"sharpe={result.sharpe_ratio:.2f} "
                    f"ploss={result.bootstrap_p_loss_pct:.1f}% "
                    f"fail={result.failure_reasons or 'none'}"
                )

        csv_path, json_path = _write_artifacts(args.output_prefix, metrics)
    finally:
        await close_pool()

    passing = [metric for metric in metrics if metric.passes_gates]
    ranked_names = {
        item.name: item
        for item in rank_by_selection_score(
            [
                RankedCandidate(
                    name=metric.name,
                    selection_score=metric.selection_sharpe,
                    holdout_score=metric.wfo_mean_sharpe,
                )
                for metric in metrics
            ]
        )
    }
    ranking = list(ranked_names.values())
    metric_by_name = {metric.name: metric for metric in metrics}

    print("\nTop candidates (selection-window rank; holdout reported only):")
    for ranked in ranking[:10]:
        metric = metric_by_name[ranked.name]
        print(
            f"{metric.name}: pass={metric.passes_gates} "
            f"trades={metric.total_trades} "
            f"wfo_trades={metric.wfo_total_trades} "
            f"return={metric.total_return_pct:.2f}% "
            f"wfo={metric.wfo_total_return_pct:.2f}% "
            f"wfo_sharpe={metric.wfo_mean_sharpe:.2f} "
            f"dd={metric.max_drawdown_pct:.2f}% "
            f"ploss={metric.bootstrap_p_loss_pct:.1f}% "
            f"fail={metric.failure_reasons or 'none'}"
        )

    print(f"\nPassing candidates: {len(passing)}/{len(metrics)}")
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
