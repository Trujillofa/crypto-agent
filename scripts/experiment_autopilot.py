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

from src.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from src.backtest.experiment_autopilot import (  # noqa: E402
    ExperimentSummary,
    GateConfig,
    WfoWindowResult,
    bootstrap_loss_probability_pct,
    build_wfo_windows,
    compound_returns_pct,
    evaluate_gates,
    profit_concentration_pct,
)
from src.db import close_pool, get_pool, init_pool  # noqa: E402
from src.features.reader import IndicatorReader  # noqa: E402
from src.main import _resolve_strategy_config, load_settings  # noqa: E402
from src.strategy.session_liquidity import parse_session_liquidity_router
from src.utils.logger import configure_logger  # noqa: E402


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
) -> BacktestConfig:
    trading_exec = raw_config.get("trading_execution", {})
    if not isinstance(trading_exec, dict):
        trading_exec = {}

    exit_rules = trading_exec.get("exit_rules", {})
    if not isinstance(exit_rules, dict):
        exit_rules = {}

    return BacktestConfig(
        symbol=symbol,
        timeframe=timeframe,
        start_date=start,
        end_date=end,
        initial_capital=initial_capital,
        fee_rate=0.001,
        stop_loss_pct=settings.trading_execution.stop_loss_pct,
        take_profit_pct=settings.trading_execution.take_profit_pct,
        sl_atr_multiplier=float(trading_exec.get("sl_atr_multiplier", 2.0)),
        tp_atr_multiplier=float(trading_exec.get("tp_atr_multiplier", 4.5)),
        trailing_activate_atr=float(trading_exec.get("trailing_activate_atr", 1.5)),
        trailing_offset_atr=float(trading_exec.get("trailing_offset_atr", 1.0)),
        slippage_pct=0.001,
        use_atr_sizing=settings.trading_execution.use_atr_sizing,
        atr_multiplier=settings.trading_execution.atr_multiplier,
        risk_per_trade=settings.trading_execution.risk_per_trade_pct,
        apply_global_trend_filter=not disable_trend_filter,
        global_trend_filter_buffer_pct=float(
            raw_config.get("strategy", {}).get("global_trend_filter_buffer_pct", 0.0)
        ),
        session_liquidity_router=parse_session_liquidity_router(
            raw_config.get("strategy", {}).get("session_liquidity_router")
        ),
        allow_short=False,
        use_executor_exit_model=bool(exit_rules.get("backtest_use_executor_exit_model", False)),
        ignore_signal_sells=bool(exit_rules.get("backtest_ignore_signal_sells", False)),
        time_stop_minutes=float(exit_rules.get("time_stop_minutes", 0)),
        replay_sentiment_path=replay_sentiment_path,
        replay_sentiment_max_age_seconds=(
            replay_sentiment_max_age_hours * 3600
            if replay_sentiment_max_age_hours is not None
            else None
        ),
        strategy_classes=strategy_classes,
        strategy_configs=strategy_configs,
        aggregator_config=aggregator_config,
    )


async def _run_backtest(reader: IndicatorReader, config: BacktestConfig) -> BacktestResult:
    return await BacktestEngine(config, reader).run()


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
    lines.append(f"| Profit concentration | {summary.profit_concentration_pct:.2f}% |")
    lines.append(f"| Blocked BUY (session router) | {summary.blocked_buy_count} |")
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


async def main() -> None:
    args = parse_args()
    configure_logger("INFO")

    settings_path = Path(args.config)
    settings = load_settings(settings_path)

    result = _resolve_strategy_config(settings.strategy)
    strategy_classes = result[0]
    strategy_configs = result[1]
    aggregator_config = dict(result[2])

    with settings_path.open("r", encoding="utf-8") as handle:
        raw_config = yaml.safe_load(handle) or {}
    if not isinstance(raw_config, dict):
        raise RuntimeError("Root config must be a mapping")

    symbol = args.symbol or settings.trading_pairs[0]
    timeframe = args.timeframe or settings.timeframe

    db_config = _db_config_from_settings(settings)
    await init_pool(db_config)
    try:
        range_start, range_end = await _resolve_data_range(symbol, timeframe)

        start = args.start or range_start
        end = args.end or range_end

        base_config = _build_backtest_config(
            settings=settings,
            raw_config=raw_config,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            strategy_classes=strategy_classes,
            strategy_configs=strategy_configs,
            aggregator_config=aggregator_config,
            initial_capital=args.initial_capital,
            disable_trend_filter=args.disable_trend_filter,
            replay_sentiment_path=args.replay_sentiment_log,
            replay_sentiment_max_age_hours=args.replay_sentiment_max_age_hours,
        )

        reader = IndicatorReader(db_config)
        async with reader:
            baseline = await _run_backtest(reader, base_config)

            windows = build_wfo_windows(
                start=start,
                end=end,
                train_months=args.train_months,
                test_months=args.test_months,
            )

            window_results: list[WfoWindowResult] = []
            for index, window in enumerate(windows, start=1):
                window_config = _build_backtest_config(
                    settings=settings,
                    raw_config=raw_config,
                    symbol=symbol,
                    timeframe=timeframe,
                    start=window.test_start,
                    end=window.test_end,
                    strategy_classes=strategy_classes,
                    strategy_configs=strategy_configs,
                    aggregator_config=aggregator_config,
                    initial_capital=args.initial_capital,
                    disable_trend_filter=args.disable_trend_filter,
                    replay_sentiment_path=args.replay_sentiment_log,
                    replay_sentiment_max_age_hours=args.replay_sentiment_max_age_hours,
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
        bootstrap_p_loss_pct = bootstrap_loss_probability_pct(
            trade_returns_pct=trade_returns,
            iterations=args.bootstrap,
            seed=args.seed,
        )

        oos_returns = [window.total_return_pct for window in window_results]
        oos_sharpes = [window.sharpe_ratio for window in window_results]
        wfo_total_trades = sum(window.total_trades for window in window_results)

        summary_seed = ExperimentSummary(
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            total_trades=baseline.total_trades,
            win_rate=baseline.win_rate,
            total_return_pct=baseline.total_return_pct,
            max_drawdown_pct=baseline.max_drawdown * 100.0,
            sharpe_ratio=baseline.sharpe_ratio,
            wfo_windows=len(window_results),
            wfo_total_trades=wfo_total_trades,
            wfo_mean_sharpe=mean(oos_sharpes) if oos_sharpes else 0.0,
            wfo_total_return_pct=compound_returns_pct(oos_returns),
            bootstrap_p_loss_pct=bootstrap_p_loss_pct,
            profit_concentration_pct=profit_concentration_pct(oos_returns),
            blocked_buy_count=baseline.blocked_buy_count,
            passes_gates=False,
            failure_reasons=[],
        )

        gates = GateConfig(
            min_trades=args.min_trades,
            min_wfo_trades=args.min_wfo_trades,
            min_wfo_sharpe=args.min_wfo_sharpe,
            max_drawdown_pct=args.max_drawdown_pct,
            max_bootstrap_p_loss_pct=args.max_bootstrap_p_loss_pct,
            min_oos_return_pct=args.min_oos_return_pct,
            max_profit_concentration_pct=args.max_profit_concentration_pct,
        )

        failures = evaluate_gates(summary_seed, gates)
        summary_payload = asdict(summary_seed)
        summary_payload["passes_gates"] = not failures
        summary_payload["failure_reasons"] = failures
        summary = ExperimentSummary(**summary_payload)

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        prefix = Path(args.output_prefix)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        json_path = prefix.parent / f"{prefix.name}-{timestamp}.json"
        markdown_path = prefix.parent / f"{prefix.name}-{timestamp}.md"

        payload = {
            "summary": asdict(summary),
            "gates": asdict(gates),
            "windows": [asdict(window) for window in window_results],
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

    finally:
        await close_pool()

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
    if summary.failure_reasons:
        print("Failures:")
        for reason in summary.failure_reasons:
            print(f"  - {reason}")
    print(f"Report: {markdown_path}")
    print(f"JSON:   {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
