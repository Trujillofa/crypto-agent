#!/usr/bin/env python3
import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.append(os.getcwd())

import yaml

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.db import close_pool, init_pool
from src.features.reader import IndicatorReader
from src.main import _resolve_strategy_config, load_settings
from src.utils.logger import configure_logger


async def main():
    configure_logger("INFO")
    parser = argparse.ArgumentParser(description="Run crypto strategy backtest")
    parser.add_argument("--symbol", type=str, required=True, help="Trading pair (e.g. BTCUSDT)")
    parser.add_argument("--timeframe", type=str, default="1m", help="Timeframe (e.g. 1m, 5m, 1h)")
    parser.add_argument("--start", type=str, required=True, help="Start date (ISO 8601)")
    parser.add_argument("--end", type=str, required=True, help="End date (ISO 8601)")
    parser.add_argument("--capital", type=float, default=10000.0, help="Initial capital")
    parser.add_argument("--fee", type=float, default=0.001, help="Trading fee rate (0.001 = 0.1%%)")
    parser.add_argument(
        "--sl", type=float, default=None, help="Stop loss percentage (e.g. 0.01 for 1%%)"
    )
    parser.add_argument(
        "--tp", type=float, default=None, help="Take profit percentage (e.g. 0.02 for 2%%)"
    )
    parser.add_argument(
        "--config", type=str, default="config/settings.yaml", help="Path to config file"
    )
    parser.add_argument(
        "--allow-short",
        action="store_true",
        help="Enable short trading",
    )
    parser.add_argument(
        "--disable-trend-filter",
        action="store_true",
        help="Disable the EMA200 global trend filter for this backtest run",
    )
    parser.add_argument(
        "--replay-sentiment-log",
        type=str,
        default=None,
        help="Path to event_log JSONL with sentiment_score events for replay during backtest",
    )
    parser.add_argument(
        "--replay-sentiment-max-age-hours",
        type=float,
        default=None,
        help="Max age in hours for replayed sentiment lookup before falling back to neutral",
    )

    args = parser.parse_args()

    # Load settings from config file
    try:
        settings = load_settings(Path(args.config))
        result = _resolve_strategy_config(settings.strategy)
        strategy_classes = result[0]
        strategy_configs = result[1]
        aggregator_config = result[2]
        # result[3] is per_symbol_aggregator_config, not used in backtest
        print(f"Loaded configuration from {args.config}")
    except Exception as e:
        print(f"Failed to load config from {args.config}: {e}")
        raise SystemExit(1) from e

    db_config = {
        "host": str(os.getenv("POSTGRES_HOST", settings.database.get("host", "localhost"))),
        "port": int(os.getenv("POSTGRES_PORT", int(settings.database.get("port", 5432)))),
        "name": str(os.getenv("POSTGRES_DB", settings.database.get("name", "marketdata"))),
        "user": str(os.getenv("POSTGRES_USER", settings.database.get("user", "trading"))),
        "password": str(os.getenv("POSTGRES_PASSWORD", settings.database.get("password", ""))),
    }
    stop_loss_pct = settings.trading_execution.stop_loss_pct if args.sl is None else args.sl
    take_profit_pct = settings.trading_execution.take_profit_pct if args.tp is None else args.tp
    with Path(args.config).open("r", encoding="utf-8") as file_handle:
        raw_config = yaml.safe_load(file_handle) or {}
    trading_exec = raw_config.get("trading_execution", {})
    exit_rules = trading_exec.get("exit_rules", {}) or {}

    config = BacktestConfig(
        symbol=args.symbol,
        timeframe=args.timeframe,
        start_date=datetime.fromisoformat(args.start),
        end_date=datetime.fromisoformat(args.end),
        initial_capital=args.capital,
        fee_rate=args.fee,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        sl_atr_multiplier=float(trading_exec.get("sl_atr_multiplier", 2.0)),
        tp_atr_multiplier=float(trading_exec.get("tp_atr_multiplier", 4.5)),
        trailing_activate_atr=float(trading_exec.get("trailing_activate_atr", 1.5)),
        trailing_offset_atr=float(trading_exec.get("trailing_offset_atr", 1.0)),
        use_atr_sizing=settings.trading_execution.use_atr_sizing,
        atr_multiplier=settings.trading_execution.atr_multiplier,
        risk_per_trade=settings.trading_execution.risk_per_trade_pct,
        apply_global_trend_filter=not args.disable_trend_filter,
        global_trend_filter_buffer_pct=float(
            raw_config.get("strategy", {}).get("global_trend_filter_buffer_pct", 0.05)
        ),
        time_stop_minutes=float(exit_rules.get("time_stop_minutes", 0)),
        use_executor_exit_model=bool(exit_rules.get("backtest_use_executor_exit_model", False)),
        ignore_signal_sells=bool(exit_rules.get("backtest_ignore_signal_sells", False)),
        strategy_classes=strategy_classes,
        strategy_configs=strategy_configs,
        aggregator_config=aggregator_config,
        allow_short=args.allow_short,
        replay_sentiment_path=args.replay_sentiment_log,
        replay_sentiment_max_age_seconds=(
            args.replay_sentiment_max_age_hours * 3600
            if args.replay_sentiment_max_age_hours is not None
            else None
        ),
    )

    print(f"Starting backtest for {args.symbol} from {args.start} to {args.end}...")
    print(f"Strategies: {[s.__name__ for s in strategy_classes]}")
    print(f"Aggregator Config: {aggregator_config}")
    print(
        f"Risk Config: SL={stop_loss_pct:.4f}, TP={take_profit_pct:.4f}, "
        f"ATR sizing={settings.trading_execution.use_atr_sizing}, "
        f"ATR multiplier={settings.trading_execution.atr_multiplier:.2f}, "
        f"Risk/trade={settings.trading_execution.risk_per_trade_pct:.4f}"
    )
    print(
        "Exit Model: "
        f"executor_like={config.use_executor_exit_model}, "
        f"ignore_signal_sells={config.ignore_signal_sells}, "
        f"atr_sl={config.sl_atr_multiplier:.2f}, "
        f"atr_tp={config.tp_atr_multiplier:.2f}, "
        f"trail_activate={config.trailing_activate_atr:.2f}, "
        f"trail_offset={config.trailing_offset_atr:.2f}"
    )
    print(f"Trend Filter: {not args.disable_trend_filter}")
    print(f"Allow Short: {args.allow_short}")
    print(f"Replay Sentiment Log: {args.replay_sentiment_log or 'disabled'}")
    print(
        "Replay Sentiment Max Age (hours): "
        f"{args.replay_sentiment_max_age_hours if args.replay_sentiment_max_age_hours is not None else 'unbounded'}"
    )

    await init_pool(db_config)
    try:
        reader = IndicatorReader(db_config)
        async with reader:
            result = await BacktestEngine(config, reader).run()
    finally:
        await close_pool()

    print("\n" + "=" * 40)
    print("BACKTEST RESULTS")
    print("=" * 40)
    print(f"Total Trades: {result.total_trades}")
    print(f"Win Rate:     {result.win_rate:.2f}%")
    print(f"Total Return: ${result.total_return:.2f} ({result.total_return_pct:.2f}%)")
    print(f"Max Drawdown: {result.max_drawdown * 100:.2f}%")
    print(f"Final Equity: ${result.final_equity:.2f}")
    print(f"Sharpe Ratio: {result.sharpe_ratio:.2f}")
    wins = [t.pnl for t in result.trades if t.pnl > 0]
    losses = [abs(t.pnl) for t in result.trades if t.pnl <= 0]
    pf = sum(wins) / sum(losses) if sum(losses) > 0 else float("inf")
    print(f"Profit Factor: {pf:.2f}")
    print("=" * 40)

    if result.trades:
        print("\nLast 5 Trades:")
        for t in result.trades[-5:]:
            print(
                f"{t.entry_time} -> {t.exit_time} | Type: {t.side} | PnL: ${t.pnl:.2f} ({t.return_pct:.2f}%)"
            )


if __name__ == "__main__":
    asyncio.run(main())
