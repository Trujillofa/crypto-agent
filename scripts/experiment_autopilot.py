#!/usr/bin/env python3
"""Run backtest + walk-forward + bootstrap gates in one command."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from statistics import mean

import yaml

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.cost_overrides import CostProfile
from src.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from src.backtest.experiment_autopilot import (  # noqa: E402
    ExperimentSummary,
    GateConfig,
    WfoWindowResult,
    bootstrap_trade_path_metrics,
    build_wfo_windows,
    compound_returns_pct,
    evaluate_gates,
    profit_concentration_pct,
)
from src.backtest.factory import (
    BacktestRequest,
    build_backtest_config,
    resolve_global_trend_filter,
)
from src.backtest.models import ExecutionProfile
from src.backtest.synthetic_eval import bars_from_range, evaluate_synthetic_pass_rate
from src.db import close_pool, get_pool, init_pool  # noqa: E402
from src.features.reader import IndicatorReader  # noqa: E402
from src.main import _resolve_strategy_config, load_settings  # noqa: E402
from src.strategy.basis_premium_filter import (
    BasisPremiumFilterConfig,
    compute_positive_tail_threshold,
    parse_basis_premium_filter,
)
from src.strategy.cross_venue_dislocation import (
    CrossVenueDislocationConfig,
    parse_cross_venue_dislocation,
)
from src.utils.logger import configure_logger  # noqa: E402


def _resolve_global_trend_filter(
    *,
    raw_config: dict[str, object],
    disable_trend_filter: bool,
    cost_profile: CostProfile | None,
) -> tuple[bool, str, bool | None]:
    """Compatibility import path for existing research scripts and audit tests."""
    return resolve_global_trend_filter(
        raw_config=raw_config,
        disable_trend_filter=disable_trend_filter,
        cost_profile=cost_profile,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-command experiment autopilot")
    parser.add_argument("--config", default="config/settings.yaml", help="Settings file path")
    parser.add_argument("--symbol", help="Trading pair override (default from config)")
    parser.add_argument("--timeframe", help="Timeframe override (default from config)")
    parser.add_argument("--start", help="Backtest start (ISO 8601), default from DB range")
    parser.add_argument("--end", help="Backtest end (ISO 8601), default from DB range")
    parser.add_argument("--train-months", type=int, default=6, help="WFO train window in months")
    parser.add_argument("--test-months", type=int, default=3, help="WFO test window in months")
    parser.add_argument("--bootstrap", type=int, default=500, help="Bootstrap iterations")
    parser.add_argument("--seed", type=int, default=42, help="Bootstrap random seed")
    parser.add_argument("--initial-capital", type=float, default=10000.0, help="Initial capital")

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
    parser.add_argument("--min-wfo-sharpe", type=float, default=0.5)
    parser.add_argument("--max-drawdown-pct", type=float, default=10.0)
    parser.add_argument("--max-bootstrap-p-loss-pct", type=float, default=25.0)
    parser.add_argument("--max-mc-drawdown-p95-pct", type=float, default=0.0)
    parser.add_argument("--min-oos-return-pct", type=float, default=0.0)
    parser.add_argument("--max-profit-concentration-pct", type=float, default=50.0)

    parser.add_argument(
        "--output-prefix",
        default="docs/reports/experiment-autopilot",
        help="Output prefix for markdown/json artifacts",
    )
    parser.add_argument(
        "--replay-sentiment-log",
        help="Path to event_log JSONL with sentiment_score events for replay",
    )
    parser.add_argument(
        "--replay-sentiment-max-age-hours",
        type=float,
        help="Max age in hours for replayed sentiment lookup before neutral fallback",
    )
    parser.add_argument("--disable-trend-filter", action="store_true")
    parser.add_argument(
        "--execution-profile",
        choices=("legacy_v1", "execution_parity_v2"),
        default="execution_parity_v2",
        help="New research defaults to next-open execution parity",
    )
    return parser.parse_args()


def _db_config_from_settings(settings: object) -> dict[str, object]:
    return {
        "host": str(os.getenv("POSTGRES_HOST", settings.database.get("host", "localhost"))),
        "port": int(os.getenv("POSTGRES_PORT", int(settings.database.get("port", 5432)))),
        "name": str(os.getenv("POSTGRES_DB", settings.database.get("name", "marketdata"))),
        "user": str(os.getenv("POSTGRES_USER", settings.database.get("user", "trading"))),
        "password": str(os.getenv("POSTGRES_PASSWORD", settings.database.get("password", ""))),
    }


async def _resolve_data_range(symbol: str, timeframe: str) -> tuple[str, str]:
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


def _futures_mode_from_raw(raw_config: dict[str, object]) -> bool:
    futures = raw_config.get("futures", {})
    if not isinstance(futures, dict):
        return False
    return bool(futures.get("enabled", False))


def _build_backtest_config(
    *,
    settings: object,
    raw_config: dict[str, object],
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    strategy_classes: list[type],
    strategy_configs: list[dict[str, object]],
    aggregator_config: dict[str, object],
    initial_capital: float,
    disable_trend_filter: bool,
    replay_sentiment_path: str | None,
    replay_sentiment_max_age_hours: float | None,
    basis_calibrated_threshold: float | None = None,
    cross_venue_dislocation: CrossVenueDislocationConfig | None = None,
    cost_profile: CostProfile | None = None,
    execution_profile: ExecutionProfile = "legacy_v1",
) -> BacktestConfig:
    return build_backtest_config(
        request=BacktestRequest(
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            initial_capital=initial_capital,
            disable_trend_filter=disable_trend_filter,
            replay_sentiment_path=replay_sentiment_path,
            replay_sentiment_max_age_hours=replay_sentiment_max_age_hours,
            execution_profile=execution_profile,
        ),
        settings=settings,
        raw_config=raw_config,
        strategy_classes=strategy_classes,
        strategy_configs=strategy_configs,
        aggregator_config=aggregator_config,
        cost_profile=cost_profile,
        basis_calibrated_threshold=basis_calibrated_threshold,
        cross_venue_dislocation=cross_venue_dislocation,
        futures_mode=_futures_mode_from_raw(raw_config),
    )


async def _run_backtest(reader: IndicatorReader, config: BacktestConfig) -> BacktestResult:
    return await BacktestEngine(config, reader).run()


async def _calibrate_basis_threshold(
    *,
    symbol: str,
    timeframe: str,
    filter_config: BasisPremiumFilterConfig,
    train_start: str,
    train_end: str,
) -> float | None:
    """Calibrate positive tail threshold from train window only (no lookahead)."""
    if not filter_config.enabled:
        return None

    metric_column = "basis_bps" if filter_config.tail_metric == "basis_bps" else "premium_index"
    query = f"""
        SELECT {metric_column} AS metric_value
        FROM perp_basis_metrics
        WHERE exchange = $1
          AND symbol = $2
          AND timeframe = $3
          AND time >= $4
          AND time < $5
        ORDER BY time ASC
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            query,
            filter_config.exchange,
            symbol,
            timeframe,
            datetime.fromisoformat(train_start),
            datetime.fromisoformat(train_end),
        )

    values = [float(row["metric_value"]) for row in rows if row["metric_value"] is not None]
    return compute_positive_tail_threshold(values, filter_config.positive_tail_pct)


def _render_markdown(
    *,
    summary: ExperimentSummary,
    windows: list[WfoWindowResult],
    gates: GateConfig,
    config_path: Path,
) -> str:
    lines: list[str] = []
    lines.append(f"# Experiment Autopilot: {summary.symbol} {summary.timeframe}")
    lines.append("")
    lines.append(f"- Config: `{config_path}`")
    lines.append(f"- Range: {summary.start} → {summary.end}")
    lines.append(f"- Gate result: {'PASS' if summary.passes_gates else 'FAIL'}")
    lines.append("")

    lines.append("## Baseline")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| Total trades | {summary.total_trades} |")
    lines.append(f"| Win rate | {summary.win_rate:.2f}% |")
    lines.append(f"| Total return | {summary.total_return_pct:.2f}% |")
    lines.append(f"| Max drawdown | {summary.max_drawdown_pct:.2f}% |")
    lines.append(f"| Sharpe ratio | {summary.sharpe_ratio:.2f} |")
    lines.append("")

    lines.append("## OOS Validation")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---:|")
    lines.append(f"| WFO windows | {summary.wfo_windows} |")
    lines.append(f"| Aggregate WFO trades | {summary.wfo_total_trades} |")
    lines.append(f"| Mean OOS Sharpe | {summary.wfo_mean_sharpe:.2f} |")
    lines.append(f"| Compound OOS return | {summary.wfo_total_return_pct:.2f}% |")
    lines.append(f"| Bootstrap P(loss) | {summary.bootstrap_p_loss_pct:.2f}% |")
    if summary.synthetic_eval_status == "inconclusive":
        lines.append(
            "| Synthetic pass rate | INCONCLUSIVE "
            f"({summary.synthetic_scored_paths}/{summary.synthetic_total_paths} paths traded) |"
        )
    else:
        lines.append(f"| Synthetic pass rate | {summary.synthetic_pass_rate_pct:.2f}% |")
    lines.append(f"| Profit concentration | {summary.profit_concentration_pct:.2f}% |")
    lines.append(f"| Blocked BUY (session router) | {summary.blocked_buy_count} |")
    lines.append(f"| Blocked BUY (basis filter) | {summary.basis_blocked_buy_count} |")
    lines.append(
        f"| Blocked BUY (cross-venue dislocation) | {summary.dislocation_blocked_buy_count} |"
    )
    lines.append("")

    if windows:
        lines.append("## WFO Windows")
        lines.append("")
        lines.append("| # | Test Start | Test End | Trades | Return | Sharpe | Max DD |")
        lines.append("|---:|---|---|---:|---:|---:|---:|")
        for window in windows:
            lines.append(
                "| "
                f"{window.window_index} | "
                f"{window.test_start[:10]} | "
                f"{window.test_end[:10]} | "
                f"{window.total_trades} | "
                f"{window.total_return_pct:.2f}% | "
                f"{window.sharpe_ratio:.2f} | "
                f"{window.max_drawdown_pct:.2f}% |"
            )
        lines.append("")

    lines.append("## Gate Thresholds")
    lines.append("")
    lines.append(f"- min_trades: {gates.min_trades}")
    lines.append(f"- min_wfo_trades: {gates.min_wfo_trades}")
    lines.append(f"- min_wfo_sharpe: {gates.min_wfo_sharpe}")
    lines.append(f"- max_drawdown_pct: {gates.max_drawdown_pct}")
    lines.append(f"- max_bootstrap_p_loss_pct: {gates.max_bootstrap_p_loss_pct}")
    lines.append(f"- max_mc_drawdown_p95_pct: {gates.max_mc_drawdown_p95_pct}")
    lines.append(f"- min_oos_return_pct: {gates.min_oos_return_pct}")
    lines.append(f"- max_profit_concentration_pct: {gates.max_profit_concentration_pct}")
    lines.append("")

    lines.append("## Failures")
    lines.append("")
    if summary.failure_reasons:
        for reason in summary.failure_reasons:
            lines.append(f"- {reason}")
    else:
        lines.append("- none")

    lines.append("")
    return "\n".join(lines)


async def run_experiment_evaluation(
    *,
    settings_path: Path,
    symbol: str | None = None,
    timeframe: str | None = None,
    start: str | None = None,
    end: str | None = None,
    train_months: int = 6,
    test_months: int = 3,
    bootstrap: int = 500,
    seed: int = 42,
    initial_capital: float = 10000.0,
    gates: GateConfig,
    disable_trend_filter: bool = False,
    replay_sentiment_path: str | None = None,
    replay_sentiment_max_age_hours: float | None = None,
    cost_profile: CostProfile | None = None,
    db_config: dict[str, object] | None = None,
    manage_pool: bool = True,
    execution_profile: ExecutionProfile = "legacy_v1",
) -> tuple[ExperimentSummary, GateConfig, list[WfoWindowResult], BacktestConfig, dict[str, object]]:
    """Run baseline + WFO + bootstrap gates; return summary and resolved baseline config."""
    settings = load_settings(settings_path)

    result = _resolve_strategy_config(settings.strategy)
    strategy_classes = result[0]
    strategy_configs = result[1]
    aggregator_config = dict(result[2])

    with settings_path.open("r", encoding="utf-8") as handle:
        raw_config = yaml.safe_load(handle) or {}
    if not isinstance(raw_config, dict):
        raise RuntimeError("Root config must be a mapping")

    resolved_symbol = symbol or settings.trading_pairs[0]
    resolved_timeframe = timeframe or settings.timeframe
    resolved_db = db_config or _db_config_from_settings(settings)

    if manage_pool:
        await init_pool(resolved_db)

    try:
        range_start, range_end = await _resolve_data_range(resolved_symbol, resolved_timeframe)
        resolved_start = start or range_start
        resolved_end = end or range_end

        basis_filter = parse_basis_premium_filter(
            raw_config.get("strategy", {}).get("basis_premium_filter")
        )
        cross_venue_disloc = parse_cross_venue_dislocation(
            raw_config.get("strategy", {}).get("cross_venue_dislocation")
        )

        windows = build_wfo_windows(
            start=resolved_start,
            end=resolved_end,
            train_months=train_months,
            test_months=test_months,
        )

        baseline_threshold: float | None = None
        if basis_filter.enabled and windows:
            baseline_threshold = await _calibrate_basis_threshold(
                symbol=resolved_symbol,
                timeframe=resolved_timeframe,
                filter_config=basis_filter,
                train_start=windows[0].train_start,
                train_end=windows[0].train_end,
            )

        base_config = _build_backtest_config(
            settings=settings,
            raw_config=raw_config,
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            start=resolved_start,
            end=resolved_end,
            strategy_classes=strategy_classes,
            strategy_configs=strategy_configs,
            aggregator_config=aggregator_config,
            initial_capital=initial_capital,
            disable_trend_filter=disable_trend_filter,
            replay_sentiment_path=replay_sentiment_path,
            replay_sentiment_max_age_hours=replay_sentiment_max_age_hours,
            basis_calibrated_threshold=baseline_threshold,
            cross_venue_dislocation=cross_venue_disloc,
            cost_profile=cost_profile,
            execution_profile=execution_profile,
        )

        reader = IndicatorReader(resolved_db)
        async with reader:
            baseline = await _run_backtest(reader, base_config)

            window_results: list[WfoWindowResult] = []
            for index, window in enumerate(windows, start=1):
                window_threshold: float | None = None
                if basis_filter.enabled:
                    window_threshold = await _calibrate_basis_threshold(
                        symbol=resolved_symbol,
                        timeframe=resolved_timeframe,
                        filter_config=basis_filter,
                        train_start=window.train_start,
                        train_end=window.train_end,
                    )
                window_config = _build_backtest_config(
                    settings=settings,
                    raw_config=raw_config,
                    symbol=resolved_symbol,
                    timeframe=resolved_timeframe,
                    start=window.test_start,
                    end=window.test_end,
                    strategy_classes=strategy_classes,
                    strategy_configs=strategy_configs,
                    aggregator_config=aggregator_config,
                    initial_capital=initial_capital,
                    disable_trend_filter=disable_trend_filter,
                    replay_sentiment_path=replay_sentiment_path,
                    replay_sentiment_max_age_hours=replay_sentiment_max_age_hours,
                    basis_calibrated_threshold=window_threshold,
                    cross_venue_dislocation=cross_venue_disloc,
                    cost_profile=cost_profile,
                    execution_profile=execution_profile,
                )
                window_backtest = await _run_backtest(reader, window_config)
                window_results.append(
                    WfoWindowResult(
                        window_index=index,
                        train_start=window.train_start,
                        train_end=window.train_end,
                        test_start=window.test_start,
                        test_end=window.test_end,
                        total_trades=window_backtest.total_trades,
                        win_rate=window_backtest.win_rate,
                        total_return_pct=window_backtest.total_return_pct,
                        max_drawdown_pct=window_backtest.max_drawdown * 100.0,
                        sharpe_ratio=window_backtest.sharpe_ratio,
                    )
                )

        trade_returns = [trade.return_pct for trade in baseline.trades]
        path_metrics = bootstrap_trade_path_metrics(
            trade_returns_pct=trade_returns,
            iterations=bootstrap,
            seed=seed,
        )

        synthetic_result = await evaluate_synthetic_pass_rate(
            base_config,
            seed=seed,
            historical_trades=baseline.total_trades,
            historical_bars=bars_from_range(resolved_start, resolved_end, resolved_timeframe),
        )

        oos_returns = [window.total_return_pct for window in window_results]
        oos_sharpes = [window.sharpe_ratio for window in window_results]
        wfo_total_trades = sum(window.total_trades for window in window_results)

        summary_seed = ExperimentSummary(
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
            start=resolved_start,
            end=resolved_end,
            total_trades=baseline.total_trades,
            win_rate=baseline.win_rate,
            total_return_pct=baseline.total_return_pct,
            max_drawdown_pct=baseline.max_drawdown * 100.0,
            sharpe_ratio=baseline.sharpe_ratio,
            wfo_windows=len(window_results),
            wfo_total_trades=wfo_total_trades,
            wfo_mean_sharpe=mean(oos_sharpes) if oos_sharpes else 0.0,
            wfo_total_return_pct=compound_returns_pct(oos_returns),
            bootstrap_p_loss_pct=path_metrics["p_loss_pct"],
            mc_drawdown_p95_pct=path_metrics["drawdown_p95_pct"],
            mc_drawdown_p50_pct=path_metrics["drawdown_p50_pct"],
            synthetic_pass_rate_pct=synthetic_result.pass_rate_pct,
            synthetic_eval_status=synthetic_result.status,
            synthetic_scored_paths=synthetic_result.scored_paths,
            synthetic_total_paths=synthetic_result.total_paths,
            profit_concentration_pct=profit_concentration_pct(oos_returns),
            blocked_buy_count=baseline.blocked_buy_count,
            basis_blocked_buy_count=baseline.basis_blocked_buy_count,
            dislocation_blocked_buy_count=getattr(baseline, "dislocation_blocked_buy_count", 0),
            passes_gates=False,
            failure_reasons=[],
        )

        failures = evaluate_gates(summary_seed, gates)
        summary_payload = asdict(summary_seed)
        summary_payload["passes_gates"] = not failures
        summary_payload["failure_reasons"] = failures
        summary = ExperimentSummary(**summary_payload)

        config_snapshot = asdict(base_config)
        config_snapshot.pop("strategy_classes", None)

        audit_payload = {
            "backtest_config": config_snapshot,
            "cost_profile": (
                cost_profile.to_audit_dict(
                    timeframe=resolved_timeframe,
                    futures_mode=base_config.futures_mode,
                )
                if cost_profile is not None
                else None
            ),
            "global_trend_filter": {
                "active": base_config.apply_global_trend_filter,
                "buffer_pct": base_config.global_trend_filter_buffer_pct,
                "source": base_config.global_trend_filter_source,
                "config_explicit": base_config.config_global_trend_filter_enabled,
            },
        }
        return summary, gates, window_results, base_config, audit_payload
    finally:
        if manage_pool:
            await close_pool()


async def main() -> None:
    args = parse_args()
    configure_logger("INFO")

    settings_path = Path(args.config)
    gates = GateConfig(
        min_trades=args.min_trades,
        min_wfo_trades=args.min_wfo_trades,
        min_wfo_sharpe=args.min_wfo_sharpe,
        max_drawdown_pct=args.max_drawdown_pct,
        max_bootstrap_p_loss_pct=args.max_bootstrap_p_loss_pct,
        max_mc_drawdown_p95_pct=args.max_mc_drawdown_p95_pct,
        min_oos_return_pct=args.min_oos_return_pct,
        max_profit_concentration_pct=args.max_profit_concentration_pct,
    )

    summary, gates, window_results, base_config, audit_payload = await run_experiment_evaluation(
        settings_path=settings_path,
        symbol=args.symbol,
        timeframe=args.timeframe,
        start=args.start,
        end=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        bootstrap=args.bootstrap,
        seed=args.seed,
        initial_capital=args.initial_capital,
        gates=gates,
        disable_trend_filter=args.disable_trend_filter,
        replay_sentiment_path=args.replay_sentiment_log,
        replay_sentiment_max_age_hours=args.replay_sentiment_max_age_hours,
        execution_profile=args.execution_profile,
    )

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    prefix = Path(args.output_prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = prefix.parent / f"{prefix.name}-{timestamp}.json"
    markdown_path = prefix.parent / f"{prefix.name}-{timestamp}.md"

    payload = {
        "summary": asdict(summary),
        "gates": asdict(gates),
        "windows": [asdict(window) for window in window_results],
        "execution_profile": base_config.execution_profile,
        "audit": audit_payload,
    }
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    markdown = _render_markdown(
        summary=summary,
        windows=window_results,
        gates=gates,
        config_path=settings_path,
    )
    with markdown_path.open("w", encoding="utf-8") as handle:
        handle.write(markdown)

    print("Experiment Autopilot complete")
    print(f"Symbol/Timeframe: {summary.symbol} {summary.timeframe}")
    print(f"Range: {summary.start} -> {summary.end}")
    print(f"Gate result: {'PASS' if summary.passes_gates else 'FAIL'}")
    print(f"Baseline trades: {summary.total_trades}")
    print(f"Baseline return: {summary.total_return_pct:.2f}%")
    print(f"OOS windows: {summary.wfo_windows}")
    print(f"Aggregate WFO trades: {summary.wfo_total_trades}")
    print(f"OOS mean Sharpe: {summary.wfo_mean_sharpe:.2f}")
    print(f"Bootstrap P(loss): {summary.bootstrap_p_loss_pct:.2f}%")
    print(f"Profit concentration: {summary.profit_concentration_pct:.2f}%")
    print(f"Blocked BUY (session router): {summary.blocked_buy_count}")
    print(f"Blocked BUY (basis filter): {summary.basis_blocked_buy_count}")
    print(f"Blocked BUY (cross-venue dislocation): {summary.dislocation_blocked_buy_count}")
    if summary.failure_reasons:
        print("Failures:")
        for reason in summary.failure_reasons:
            print(f"  - {reason}")
    print(f"Report: {markdown_path}")
    print(f"JSON:   {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
