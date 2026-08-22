#!/usr/bin/env python3
"""MTF strategy parameter sweep for ETHUSDT.

Sweeps key parameters of MultiTimeframeRegimeRouter and other MTF strategies
across a config grid, running walk-forward backtests and reporting results.
"""

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

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.backtest.experiment_autopilot import build_wfo_windows
from src.backtest.factory import BacktestRequest, build_backtest_config
from src.backtest.ranking import RankedCandidate, rank_by_selection_score
from src.backtest.research_safety import refuse_live_go
from src.db import close_pool, get_pool, init_pool
from src.features.reader import IndicatorReader
from src.main import _resolve_strategy_config, load_settings
from src.utils.logger import configure_logger


@dataclass(frozen=True)
class MTFCandidate:
    """Parameter set for an MTF strategy evaluation."""

    name: str
    strategy_name: str
    # Regime classification
    trend_strength_threshold: float
    volatility_percentile_threshold: float
    trend_consistency_threshold: float
    # Entry parameters
    entry_zone_pct: float
    deep_pullback_pct: float
    rsi_oversold: float
    rsi_overbought: float
    # Confidence
    trending_confidence: float
    # Aggregator
    buy_threshold: float
    apply_global_trend_filter: bool
    # Execution
    allow_short: bool


@dataclass(frozen=True)
class MTFMetrics:
    """Summary metrics for one MTF candidate."""

    name: str
    strategy_name: str
    symbol: str
    timeframe: str
    start: str
    end: str
    total_trades: int
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    profit_factor: float
    avg_win_loss_ratio: float
    wfo_windows: int
    wfo_total_trades: int
    wfo_mean_sharpe: float
    wfo_total_return_pct: float
    selection_return_pct: float
    selection_sharpe: float
    passes_gates: bool
    failure_reasons: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MTF strategy parameter sweep")
    parser.add_argument("--config", required=True, help="Base MTF config path")
    parser.add_argument("--symbol", help="Override symbol")
    parser.add_argument("--timeframe", help="Override entry timeframe")
    parser.add_argument("--start", help="Start date (ISO 8601)")
    parser.add_argument("--end", help="End date (ISO 8601)")
    parser.add_argument(
        "--strategy",
        choices=("multi_timeframe_regime", "mtf_template", "mtf_continuation", "mtf_breakout"),
        default="multi_timeframe_regime",
        help="MTF strategy to sweep",
    )
    parser.add_argument("--train-months", type=int, default=6, help="WFO train window (months)")
    parser.add_argument("--test-months", type=int, default=3, help="WFO test window (months)")
    parser.add_argument("--max-candidates", type=int, default=0, help="Limit candidates (0=all)")
    parser.add_argument(
        "--min-wfo-trades", type=int, default=10, help="Min aggregate WFO trades gate"
    )
    parser.add_argument(
        "--min-wfo-sharpe", type=float, default=0.3, help="Min WFO mean Sharpe gate"
    )
    parser.add_argument(
        "--max-drawdown-pct", type=float, default=15.0, help="Max drawdown gate (%)"
    )
    parser.add_argument(
        "--output-prefix",
        default="docs/reports/mtf-search",
        help="Output file prefix",
    )
    return parser.parse_args()


def _make_mtf_candidate_grid(strategy_name: str) -> list[MTFCandidate]:
    """Generate parameter grid for MTF strategies."""
    candidates: list[MTFCandidate] = []

    # Regime thresholds — calibrated to ETHUSDT 4h data distribution
    # ema_slope_50 |abs| percentiles: p25=0.0018, p50=0.0043, p75=0.0084
    trend_strengths = [0.002, 0.003, 0.005]
    # volatility_percentile: p25=24, p50=51, p75=76
    vol_pct_thresholds = [25.0, 40.0, 60.0]
    # trend_consistency: p25=0, p50=50, p75=75
    trend_consistency_thresholds = [25.0, 50.0]

    # Entry parameters — wider zones to catch more entries
    entry_zone_pcts = [0.01, 0.015, 0.02]
    deep_pullback_pcts = [0.015, 0.025]
    rsi_pairs = [(35.0, 65.0), (40.0, 60.0), (45.0, 55.0)]

    # Confidence
    trending_confidences = [1.0, 1.2]

    # Aggregator
    buy_thresholds = [0.6, 0.7]
    # Filters
    trend_filters = [True, False]
    allow_shorts = [False, True]

    index = 1
    for (
        trend_strength,
        vol_pct,
        trend_consistency,
        entry_zone,
        deep_pullback,
        rsi_pair,
        trending_conf,
        buy_thresh,
        trend_filter,
        allow_short,
    ) in itertools.product(
        trend_strengths,
        vol_pct_thresholds,
        trend_consistency_thresholds,
        entry_zone_pcts,
        deep_pullback_pcts,
        rsi_pairs,
        trending_confidences,
        buy_thresholds,
        trend_filters,
        allow_shorts,
    ):
        candidates.append(
            MTFCandidate(
                name=f"mtf-{index:04d}",
                strategy_name=strategy_name,
                trend_strength_threshold=trend_strength,
                volatility_percentile_threshold=vol_pct,
                trend_consistency_threshold=trend_consistency,
                entry_zone_pct=entry_zone,
                deep_pullback_pct=deep_pullback,
                rsi_oversold=rsi_pair[0],
                rsi_overbought=rsi_pair[1],
                trending_confidence=trending_conf,
                buy_threshold=buy_thresh,
                apply_global_trend_filter=trend_filter,
                allow_short=allow_short,
            )
        )
        index += 1

    return candidates


def _update_mtf_config(raw_config: dict[str, object], candidate: MTFCandidate) -> dict[str, object]:
    """Apply MTF candidate parameters to a raw YAML config."""
    updated = copy.deepcopy(raw_config)
    strategy_root = updated.setdefault("strategy", {})

    # Update aggregator
    aggregator = strategy_root.setdefault("aggregator", {})
    aggregator["buy_threshold"] = candidate.buy_threshold
    aggregator["buy_threshold_uptrend"] = candidate.buy_threshold

    # Update strategy config
    strategies = strategy_root.get("strategies", [])
    for strat in strategies:
        strat["name"] = candidate.strategy_name
        config = strat.setdefault("config", {})
        # Regime params (work for both mtf_template and multi_timeframe_regime)
        config["trend_strength_threshold"] = candidate.trend_strength_threshold
        config["regime_threshold"] = candidate.trend_strength_threshold
        config["volatility_percentile_threshold"] = candidate.volatility_percentile_threshold
        config["trend_consistency_threshold"] = candidate.trend_consistency_threshold
        # Entry params
        config["entry_zone_pct"] = candidate.entry_zone_pct
        config["entry_pullback_pct"] = candidate.entry_zone_pct
        config["deep_pullback_pct"] = candidate.deep_pullback_pct
        config["rsi_oversold"] = candidate.rsi_oversold
        config["rsi_overbought"] = candidate.rsi_overbought
        # Confidence
        config["trending_confidence"] = candidate.trending_confidence
        config["confidence_boost"] = candidate.trending_confidence

    return updated


async def _resolve_range(symbol: str, timeframe: str) -> tuple[str, str]:
    """Resolve indicator range for symbol/timeframe."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT min(time) AS s, max(time) AS e FROM indicators WHERE symbol=$1 AND timeframe=$2",
            symbol,
            timeframe,
        )
    if row is None or row["s"] is None:
        raise RuntimeError(f"No indicator data for {symbol} {timeframe}")
    return row["s"].isoformat(), row["e"].isoformat()


def _build_backtest_config(
    settings: object,
    strategy_classes: list,
    strategy_configs: list,
    aggregator_config: dict,
    raw_config: dict,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    apply_trend_filter: bool,
    allow_short: bool,
) -> BacktestConfig:
    return build_backtest_config(
        request=BacktestRequest(
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            allow_short=allow_short,
            trend_filter_override=apply_trend_filter,
            execution_profile="execution_parity_v2",
        ),
        settings=settings,
        raw_config=raw_config,
        strategy_classes=strategy_classes,
        strategy_configs=strategy_configs,
        aggregator_config=aggregator_config,
    )


async def _run_wfo_windows(
    settings: object,
    strategy_classes: list,
    strategy_configs: list,
    aggregator_config: dict,
    raw_config: dict,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    apply_trend_filter: bool,
    allow_short: bool,
    train_months: int,
    test_months: int,
    reader: IndicatorReader,
) -> tuple[int, int, float, float]:
    """Run walk-forward out-of-sample windows."""
    windows = build_wfo_windows(start, end, train_months, test_months)
    window_returns: list[float] = []
    window_sharpes: list[float] = []
    window_trade_counts: list[int] = []

    for window in windows:
        cfg = _build_backtest_config(
            settings,
            strategy_classes,
            strategy_configs,
            aggregator_config,
            raw_config,
            symbol,
            timeframe,
            window.test_start,
            window.test_end,
            apply_trend_filter,
            allow_short,
        )
        result = await BacktestEngine(cfg, reader).run()
        window_returns.append(result.total_return_pct)
        window_sharpes.append(result.sharpe_ratio)
        window_trade_counts.append(result.total_trades)

    if not window_returns:
        return 0, 0, 0.0, 0.0

    compound = 1.0
    for v in window_returns:
        compound *= 1.0 + v / 100.0
    total_return_pct = (compound - 1.0) * 100.0
    mean_sharpe = sum(window_sharpes) / len(window_sharpes)
    return len(window_returns), sum(window_trade_counts), mean_sharpe, total_return_pct


async def _evaluate_candidate(
    base_raw_config: dict,
    candidate: MTFCandidate,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    train_months: int,
    test_months: int,
    gates: dict[str, float],
    reader: IndicatorReader,
) -> MTFMetrics:
    """Run full evaluation for one MTF candidate."""
    temp_path: Path | None = None
    try:
        updated = _update_mtf_config(base_raw_config, candidate)
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        with handle:
            yaml.safe_dump(updated, handle, sort_keys=False)
        temp_path = Path(handle.name)

        settings = load_settings(temp_path)
        resolved = _resolve_strategy_config(settings.strategy)
        strategy_classes = resolved[0]
        strategy_configs = resolved[1]
        aggregator_config = resolved[2]

        # Full-period backtest
        full_cfg = _build_backtest_config(
            settings,
            strategy_classes,
            strategy_configs,
            aggregator_config,
            updated,
            symbol,
            timeframe,
            start,
            end,
            candidate.apply_global_trend_filter,
            candidate.allow_short,
        )
        full_result = await BacktestEngine(full_cfg, reader).run()
        windows = build_wfo_windows(start, end, train_months, test_months)
        if windows:
            train_cfg = _build_backtest_config(
                settings,
                strategy_classes,
                strategy_configs,
                aggregator_config,
                updated,
                symbol,
                timeframe,
                windows[0].train_start,
                windows[0].train_end,
                candidate.apply_global_trend_filter,
                candidate.allow_short,
            )
            train_result = await BacktestEngine(train_cfg, reader).run()
            selection_return_pct = train_result.total_return_pct
            selection_sharpe = train_result.sharpe_ratio
        else:
            selection_return_pct = 0.0
            selection_sharpe = 0.0

        # Walk-forward
        (
            wfo_windows,
            wfo_total_trades,
            wfo_mean_sharpe,
            wfo_total_return_pct,
        ) = await _run_wfo_windows(
            settings,
            strategy_classes,
            strategy_configs,
            aggregator_config,
            updated,
            symbol,
            timeframe,
            start,
            end,
            candidate.apply_global_trend_filter,
            candidate.allow_short,
            train_months,
            test_months,
            reader,
        )

        # Gates
        failures: list[str] = []
        if wfo_total_trades < int(gates["min_wfo_trades"]):
            failures.append("wfo_trades")
        if full_result.max_drawdown * 100 > gates["max_drawdown_pct"]:
            failures.append("drawdown")
        if wfo_mean_sharpe < gates["min_wfo_sharpe"]:
            failures.append("wfo_sharpe")

        return MTFMetrics(
            name=candidate.name,
            strategy_name=candidate.strategy_name,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            total_trades=full_result.total_trades,
            win_rate=full_result.win_rate,
            total_return_pct=full_result.total_return_pct,
            max_drawdown_pct=full_result.max_drawdown * 100,
            sharpe_ratio=full_result.sharpe_ratio,
            sortino_ratio=full_result.sortino_ratio,
            profit_factor=full_result.profit_factor,
            avg_win_loss_ratio=full_result.avg_win_loss_ratio,
            wfo_windows=wfo_windows,
            wfo_total_trades=wfo_total_trades,
            wfo_mean_sharpe=wfo_mean_sharpe,
            wfo_total_return_pct=wfo_total_return_pct,
            selection_return_pct=selection_return_pct,
            selection_sharpe=selection_sharpe,
            passes_gates=not failures,
            failure_reasons=",".join(failures),
        )
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _write_artifacts(output_prefix: str, metrics: list[MTFMetrics]) -> tuple[Path, Path]:
    date_tag = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = Path(f"{output_prefix}-{date_tag}.csv")
    json_path = Path(f"{output_prefix}-{date_tag}.json")
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    rows = [asdict(m) for m in metrics]
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        csv_path.write_text("", encoding="utf-8")

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)

    return csv_path, json_path


async def main() -> None:
    configure_logger("WARNING")
    args = parse_args()
    refuse_live_go(argv=sys.argv[1:], flags=vars(args))

    config_path = Path(args.config)
    base_settings = load_settings(config_path)
    symbol = args.symbol or base_settings.trading_pairs[0]
    timeframe = args.timeframe or base_settings.timeframe

    db_config = {
        "host": str(os.getenv("DB_HOST", base_settings.database.get("host", "localhost"))),
        "port": int(os.getenv("DB_PORT", int(base_settings.database.get("port", 5432)))),
        "name": str(os.getenv("DB_NAME", base_settings.database.get("name", "marketdata"))),
        "user": str(os.getenv("DB_USER", base_settings.database.get("user", "trading"))),
        "password": str(os.getenv("DB_PASSWORD", base_settings.database.get("password", ""))),
    }

    with config_path.open("r", encoding="utf-8") as f:
        base_raw_config = yaml.safe_load(f)

    await init_pool(db_config)
    try:
        resolved_start, resolved_end = await _resolve_range(symbol, timeframe)
        start = args.start or resolved_start
        end = args.end or resolved_end

        candidates = _make_mtf_candidate_grid(args.strategy)
        if args.max_candidates > 0:
            candidates = candidates[: args.max_candidates]

        gates = {
            "min_wfo_trades": float(args.min_wfo_trades),
            "min_wfo_sharpe": args.min_wfo_sharpe,
            "max_drawdown_pct": args.max_drawdown_pct,
        }

        print(
            f"MTF Sweep: {len(candidates)} candidates for {symbol} {timeframe} "
            f"({args.strategy}) from {start} to {end}"
        )
        print(
            f"Gates: min_wfo_trades={args.min_wfo_trades}, "
            f"min_wfo_sharpe={args.min_wfo_sharpe}, "
            f"max_drawdown_pct={args.max_drawdown_pct}"
        )

        metrics: list[MTFMetrics] = []
        reader = IndicatorReader(db_config)
        async with reader:
            for idx, candidate in enumerate(candidates, start=1):
                result = await _evaluate_candidate(
                    base_raw_config,
                    candidate,
                    symbol,
                    timeframe,
                    start,
                    end,
                    args.train_months,
                    args.test_months,
                    gates,
                    reader,
                )
                metrics.append(result)
                print(
                    f"[{idx}/{len(candidates)}] {result.name}: "
                    f"pass={result.passes_gates} "
                    f"trades={result.total_trades} "
                    f"wfo_trades={result.wfo_total_trades} "
                    f"return={result.total_return_pct:.2f}% "
                    f"wfo={result.wfo_total_return_pct:.2f}% "
                    f"sharpe={result.sharpe_ratio:.2f} "
                    f"dd={result.max_drawdown_pct:.2f}% "
                    f"pf={result.profit_factor:.2f} "
                    f"fail={result.failure_reasons or 'none'}"
                )

        csv_path, json_path = _write_artifacts(args.output_prefix, metrics)
    finally:
        await close_pool()

    passing = [m for m in metrics if m.passes_gates]
    metric_by_name = {metric.name: metric for metric in metrics}
    ranking = [
        metric_by_name[item.name]
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
    ]

    print("\nTop 10 candidates (selection-window rank; holdout reported only):")
    for m in ranking[:10]:
        print(
            f"  {m.name}: pass={m.passes_gates} "
            f"trades={m.total_trades} "
            f"wfo_trades={m.wfo_total_trades} "
            f"return={m.total_return_pct:.2f}% "
            f"wfo={m.wfo_total_return_pct:.2f}% "
            f"sharpe={m.sharpe_ratio:.2f} "
            f"sortino={m.sortino_ratio:.2f} "
            f"dd={m.max_drawdown_pct:.2f}% "
            f"pf={m.profit_factor:.2f} "
            f"fail={m.failure_reasons or 'none'}"
        )

    print(f"\nPassing: {len(passing)}/{len(metrics)}")
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
