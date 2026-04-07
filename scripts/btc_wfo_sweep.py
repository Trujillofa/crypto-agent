#!/usr/bin/env python3
"""BTCUSDT 4h Walk-Forward Optimization — Strategy Selection Script.

Comprehensive WFO sweep to find the best strategy for BTCUSDT 4h paper trading.
Tests: simple_ma, CCI breakout, MA+CCI combo, MTF regime.
Each strategy is run with time_stop and exit parameter sweeps.

Run on Hetzner (where the DB is):
    python scripts/btc_wfo_sweep.py --strategy all --output docs/reports/btc-wfo

Individual strategies:
    python scripts/btc_wfo_sweep.py --strategy simple_ma
    python scripts/btc_wfo_sweep.py --strategy cci
    python scripts/btc_wfo_sweep.py --strategy ma_cci
    python scripts/btc_wfo_sweep.py --strategy mtf_regime
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import itertools
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import yaml

sys.path.append(os.getcwd())

from src.backtest.engine import BacktestConfig, BacktestEngine
from src.db import close_pool, get_pool, init_pool
from src.features.reader import IndicatorReader
from src.main import load_settings
from src.utils.logger import configure_logger

# ─── Gate Thresholds ────────────────────────────────────────────────────────

BTC_GATES = {
    "min_test_trades": 10,  # Min trades per test window
    "min_oos_sharpe": 0.3,  # Min mean OOS Sharpe ratio
    "min_oos_win_rate": 0.40,  # Min mean OOS win rate
    "max_drawdown_pct": 15.0,  # Max drawdown on full period (%)
    "min_oos_trades": 20,  # Min total OOS trades across all windows
}


# ─── Strategy Configurations ────────────────────────────────────────────────


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    strategy_names: list[str]  # passed to aggregator
    strategy_classes: list[type]  # actual class refs (filled by resolve)
    strategy_params: list[dict | None]  # per-strategy param dicts
    time_stop_minutes: int
    # Exit config
    sl_atr: float
    tp_atr: float
    trailing_act: float
    trailing_off: float
    use_executor_exit: bool
    allow_short: bool
    apply_trend_filter: bool


def _ema_period_grid() -> list[tuple[int, int]]:
    """EMA (short, long) period pairs to sweep."""
    return [
        (12, 26),  # EMA12/26 matches DB columns (ema_12, ema_26)
    ]


def _time_stop_grid() -> list[int]:
    """time_stop_minutes values to test."""
    return [240, 720, 1440]


def _exit_config_grid() -> list[tuple[float, float, float, float]]:
    """(sl_atr, tp_atr, trailing_act, trailing_off) tuples."""
    return [
        (2.5, 3.0, 2.5, 1.5),  # Phase 2 winner
        (2.0, 3.0, 2.0, 1.0),  # tighter
        (2.0, 4.5, 1.5, 1.0),  # wider TP
        (1.5, 3.0, 2.0, 1.0),  # tight SL
        (3.0, 4.5, 2.0, 1.5),  # loose
    ]


def _cci_threshold_grid() -> list[tuple[float, float, float]]:
    """(buy_threshold, sell_threshold, atr_min_pct) for CCI."""
    return [
        (100.0, -100.0, 0.005),
        (100.0, -100.0, 0.008),
        (80.0, -80.0, 0.005),
        (80.0, -80.0, 0.008),
        (50.0, -50.0, 0.005),
    ]


# ─── Result Dataclass ────────────────────────────────────────────────────────


@dataclass
class WFOMetrics:
    name: str
    strategy_label: str
    symbol: str
    timeframe: str
    start: str
    end: str
    # Full period
    total_trades: int
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    profit_factor: float
    # WFO OOS aggregate
    wfo_windows: int
    oos_total_trades: int
    oos_mean_sharpe: float
    oos_mean_win_rate: float
    oos_total_return_pct: float
    oos_profit_factor: float
    # Config
    time_stop_minutes: int
    sl_atr: float
    tp_atr: float
    trailing_act: float
    trailing_off: float
    # Gates
    passes_gates: bool
    failure_reasons: str


# ─── Strategy Resolution ─────────────────────────────────────────────────────


STRATEGY_CLASSES: dict[str, type] = {}


def _resolve_all_strategies() -> None:
    """Import and cache all strategy classes."""
    if STRATEGY_CLASSES:
        return
    from src.strategy.cci_strategy import CCIBreakoutStrategy
    from src.strategy.macd_strategy import MACDHistogramStrategy
    from src.strategy.mtf_template import MTFStrategyTemplate
    from src.strategy.multi_timeframe_regime import MultiTimeframeRegimeRouter
    from src.strategy.simple_ma import SimpleMACrossoverStrategy
    from src.strategy.trend_pullback import TrendPullbackStrategy

    STRATEGY_CLASSES.update(
        {
            "simple_ma": SimpleMACrossoverStrategy,
            "cci_breakout": CCIBreakoutStrategy,
            "macd_histogram": MACDHistogramStrategy,
            "multi_timeframe_regime": MultiTimeframeRegimeRouter,
            "mtf_template": MTFStrategyTemplate,
            "trend_pullback": TrendPullbackStrategy,
        }
    )


# ─── BacktestConfig Builder ─────────────────────────────────────────────────


def _build_engine_config(
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    strategy_classes: list,
    strategy_configs: list,
    aggregator_config: dict,
    sl_atr: float,
    tp_atr: float,
    trailing_act: float,
    trailing_off: float,
    use_executor_exit: bool,
    time_stop_minutes: int,
    allow_short: bool,
    apply_trend_filter: bool,
    futures_mode: bool = True,
    futures_leverage: int = 3,
) -> BacktestConfig:
    return BacktestConfig(
        symbol=symbol,
        timeframe=timeframe,
        start_date=start,
        end_date=end,
        initial_capital=10_000.0,
        fee_rate=0.001,
        stop_loss_pct=0.0,
        take_profit_pct=0.0,
        sl_atr_multiplier=sl_atr,
        tp_atr_multiplier=tp_atr,
        trailing_activate_atr=trailing_act,
        trailing_offset_atr=trailing_off,
        use_atr_sizing=True,
        atr_multiplier=1.0,
        risk_per_trade=0.02,
        apply_global_trend_filter=apply_trend_filter,
        global_trend_filter_buffer_pct=0.05,
        allow_short=allow_short,
        use_executor_exit_model=use_executor_exit,
        ignore_signal_sells=False,
        strategy_classes=strategy_classes,
        strategy_configs=strategy_configs,
        aggregator_config=aggregator_config,
        time_stop_minutes=time_stop_minutes,
        futures_mode=futures_mode,
        futures_leverage=futures_leverage,
    )


# ─── WFO Runner ─────────────────────────────────────────────────────────────


async def _run_wfo(
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    strategy_classes: list,
    strategy_configs: list,
    aggregator_config: dict,
    sl_atr: float,
    tp_atr: float,
    trailing_act: float,
    trailing_off: float,
    use_executor_exit: bool,
    time_stop_minutes: int,
    allow_short: bool,
    apply_trend_filter: bool,
    train_months: int = 6,
    test_months: int = 3,
    reader: IndicatorReader | None = None,
    db_config: dict | None = None,
) -> tuple[int, int, float, float, float]:
    """Run WFO, return (windows, total_oos_trades, mean_sharpe, mean_win_rate, oos_return_pct)."""
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    current = start_dt
    window_sharpes: list[float] = []
    window_win_rates: list[float] = []
    window_trade_counts: list[int] = []
    window_returns: list[float] = []

    local_reader = reader

    while current + timedelta(days=(train_months + test_months) * 30 + 5) < end_dt:
        train_end = current + timedelta(days=train_months * 30)
        test_start = train_end
        test_end = min(test_start + timedelta(days=test_months * 30), end_dt)

        cfg = _build_engine_config(
            symbol=symbol,
            timeframe=timeframe,
            start=test_start.isoformat(),
            end=test_end.isoformat(),
            strategy_classes=strategy_classes,
            strategy_configs=strategy_configs,
            aggregator_config=aggregator_config,
            sl_atr=sl_atr,
            tp_atr=tp_atr,
            trailing_act=trailing_act,
            trailing_off=trailing_off,
            use_executor_exit=use_executor_exit,
            time_stop_minutes=time_stop_minutes,
            allow_short=allow_short,
            apply_trend_filter=apply_trend_filter,
        )

        r = await BacktestEngine(cfg, local_reader).run()
        window_sharpes.append(r.sharpe_ratio)
        window_win_rates.append(r.win_rate / 100.0)
        window_trade_counts.append(r.total_trades)
        window_returns.append(r.total_return_pct)
        current = train_end

    if not window_sharpes:
        return 0, 0, 0.0, 0.0, 0.0

    n = len(window_sharpes)
    compound = 1.0
    for v in window_returns:
        compound *= 1.0 + v / 100.0
    oos_return = (compound - 1.0) * 100.0

    return (
        n,
        sum(window_trade_counts),
        sum(window_sharpes) / n,
        sum(window_win_rates) / n,
        oos_return,
    )


# ─── Strategy Config Generators ─────────────────────────────────────────────


async def _generate_simple_ma_configs(
    base_settings: object,
    base_raw_config: dict,
    symbol: str,
    timeframe: str,
    reader: IndicatorReader,
    db_config: dict,
) -> list[StrategyConfig]:
    """Generate simple_ma configs across EMA periods, time_stops, exit configs."""
    configs: list[StrategyConfig] = []
    _resolve_all_strategies()

    for (ema_s, ema_l), ts_min, (sl, tp, ta, toff) in itertools.product(
        _ema_period_grid(), _time_stop_grid(), _exit_config_grid()
    ):
        strategy_params = {
            "ema_short_period": ema_s,
            "ema_long_period": ema_l,
            "confidence_threshold": 0.6,
        }

        cfg = StrategyConfig(
            name=f"MA_{ema_s}_{ema_l}_TS{ts_min}_SL{sl}_TP{tp}",
            strategy_names=["simple_ma"],
            strategy_classes=[STRATEGY_CLASSES["simple_ma"]],
            strategy_params=[strategy_params],
            time_stop_minutes=ts_min,
            sl_atr=sl,
            tp_atr=tp,
            trailing_act=ta,
            trailing_off=toff,
            use_executor_exit=True,
            allow_short=False,
            apply_trend_filter=True,
        )
        configs.append(cfg)

    return configs


async def _generate_cci_configs(
    base_settings: object,
    base_raw_config: dict,
    symbol: str,
    timeframe: str,
    reader: IndicatorReader,
    db_config: dict,
) -> list[StrategyConfig]:
    """Generate CCI configs across thresholds, ATR mins, time_stops, exits."""
    configs: list[StrategyConfig] = []
    _resolve_all_strategies()

    for (cci_buy, cci_sell, atr_min), ts_min, (sl, tp, ta, toff) in itertools.product(
        _cci_threshold_grid(), _time_stop_grid(), _exit_config_grid()
    ):
        strategy_params = {
            "cci_buy_threshold": cci_buy,
            "cci_sell_threshold": cci_sell,
            "atr_min_pct": atr_min,
        }

        cfg = StrategyConfig(
            name=f"CCI_{int(cci_buy)}_{int(cci_sell)}_ATR{atr_min}_TS{ts_min}_SL{sl}_TP{tp}",
            strategy_names=["cci_breakout"],
            strategy_classes=[STRATEGY_CLASSES["cci_breakout"]],
            strategy_params=[strategy_params],
            time_stop_minutes=ts_min,
            sl_atr=sl,
            tp_atr=tp,
            trailing_act=ta,
            trailing_off=toff,
            use_executor_exit=True,
            allow_short=False,
            apply_trend_filter=True,
        )
        configs.append(cfg)

    return configs


async def _generate_ma_cci_configs(
    base_settings: object,
    base_raw_config: dict,
    symbol: str,
    timeframe: str,
    reader: IndicatorReader,
    db_config: dict,
) -> list[StrategyConfig]:
    """MA+CCI combo (top Phase 3 performer: agree=1, both Long)."""
    configs: list[StrategyConfig] = []
    _resolve_all_strategies()

    # Use top exit config from Phase 2 (SL2.5/TP3.0/T2.5,1.5)
    top_exit = (2.5, 3.0, 2.5, 1.5)

    # Sweep: agree thresholds and time_stops
    agree_values = [1, 2]
    trend_filters = [True, False]

    for agree, ts_min, trend_filter in itertools.product(
        agree_values, _time_stop_grid(), trend_filters
    ):
        ma_params = {
            "ema_short_period": 12,
            "ema_long_period": 26,
            "confidence_threshold": 0.6,
        }
        cci_params = {
            "cci_buy_threshold": 100.0,
            "cci_sell_threshold": -100.0,
            "atr_min_pct": 0.005,
        }

        cfg = StrategyConfig(
            name=f"MA_CCI_A{agree}_TS{ts_min}_FILTER{int(trend_filter)}",
            strategy_names=["simple_ma", "cci_breakout"],
            strategy_classes=[STRATEGY_CLASSES["simple_ma"], STRATEGY_CLASSES["cci_breakout"]],
            strategy_params=[ma_params, cci_params],
            time_stop_minutes=ts_min,
            sl_atr=top_exit[0],
            tp_atr=top_exit[1],
            trailing_act=top_exit[2],
            trailing_off=top_exit[3],
            use_executor_exit=True,
            allow_short=False,
            apply_trend_filter=trend_filter,
        )
        configs.append(cfg)

    return configs


async def _generate_mtf_regime_configs(
    base_settings: object,
    base_raw_config: dict,
    symbol: str,
    timeframe: str,
    reader: IndicatorReader,
    db_config: dict,
) -> list[StrategyConfig]:
    """Multi-timeframe regime strategy configs for BTC 4h."""
    configs: list[StrategyConfig] = []
    _resolve_all_strategies()

    # Sweep key MTF parameters + time_stops
    trend_thresholds = [0.003, 0.005, 0.008]
    vol_thresholds = [40.0, 60.0]
    rsi_pairs = [(40.0, 60.0), (35.0, 65.0)]
    time_stops = [720, 1440]
    # Use best exit config
    top_exit = (2.5, 4.5, 1.5, 1.0)

    for tthresh, vol_thresh, rsi_pair, ts_min in itertools.product(
        trend_thresholds, vol_thresholds, rsi_pairs, time_stops
    ):
        mtf_params = {
            "trend_strength_threshold": tthresh,
            "volatility_percentile_threshold": vol_thresh,
            "trend_consistency_threshold": 50.0,
            "entry_window_bars": 6,
            "pullback_threshold": 0.01,
            "reclaim_threshold": 0.005,
            "entry_zone_pct": 0.01,
            "deep_pullback_pct": 0.02,
            "rsi_oversold": rsi_pair[0],
            "rsi_overbought": rsi_pair[1],
            "trending_confidence": 1.0,
            "ranging_confidence": 1.0,
            "uncertain_confidence": 0.0,
            "futures_enabled": True,
        }

        cfg = StrategyConfig(
            name=f"MTF_T{tthresh}_V{vol_thresh}_RSI{int(rsi_pair[0])}_{int(rsi_pair[1])}_TS{ts_min}",
            strategy_names=["multi_timeframe_regime"],
            strategy_classes=[STRATEGY_CLASSES["multi_timeframe_regime"]],
            strategy_params=[mtf_params],
            time_stop_minutes=ts_min,
            sl_atr=top_exit[0],
            tp_atr=top_exit[1],
            trailing_act=top_exit[2],
            trailing_off=top_exit[3],
            use_executor_exit=True,
            allow_short=False,
            apply_trend_filter=True,
        )
        configs.append(cfg)

    return configs


def _trend_pullback_param_grid() -> list[dict]:
    """Parameter grid for trend_pullback strategy."""
    return [
        # rsi_reclaim, min_trend_strength, max_pullback, continuation_rsi
        {
            "rsi_reclaim_level": 45.0,
            "min_trend_strength_pct": 0.005,
            "max_pullback_distance_pct": 0.015,
            "continuation_rsi_level": 50.0,
        },
        {
            "rsi_reclaim_level": 45.0,
            "min_trend_strength_pct": 0.008,
            "max_pullback_distance_pct": 0.020,
            "continuation_rsi_level": 54.0,
        },
        {
            "rsi_reclaim_level": 48.0,
            "min_trend_strength_pct": 0.008,
            "max_pullback_distance_pct": 0.020,
            "continuation_rsi_level": 54.0,
        },
        {
            "rsi_reclaim_level": 50.0,
            "min_trend_strength_pct": 0.010,
            "max_pullback_distance_pct": 0.025,
            "continuation_rsi_level": 54.0,
        },
        {
            "rsi_reclaim_level": 48.0,
            "min_trend_strength_pct": 0.005,
            "max_pullback_distance_pct": 0.015,
            "continuation_rsi_level": 50.0,
        },
        {
            "rsi_reclaim_level": 50.0,
            "min_trend_strength_pct": 0.008,
            "max_pullback_distance_pct": 0.020,
            "continuation_rsi_level": 58.0,
        },
        # Wider pullback variants
        {
            "rsi_reclaim_level": 45.0,
            "min_trend_strength_pct": 0.010,
            "max_pullback_distance_pct": 0.030,
            "continuation_rsi_level": 54.0,
        },
        {
            "rsi_reclaim_level": 48.0,
            "min_trend_strength_pct": 0.012,
            "max_pullback_distance_pct": 0.025,
            "continuation_rsi_level": 56.0,
        },
    ]


async def _generate_trend_pullback_configs(
    base_settings: object,
    base_raw_config: dict,
    symbol: str,
    timeframe: str,
    reader: IndicatorReader,
    db_config: dict,
) -> list[StrategyConfig]:
    """Generate trend_pullback configs across key parameter combos, time_stops, exits."""
    configs: list[StrategyConfig] = []
    _resolve_all_strategies()

    # Use best exit config from Phase 2
    top_exit = (2.5, 3.0, 2.5, 1.5)
    time_stops = [240, 720, 1440]

    for params, ts_min in itertools.product(_trend_pullback_param_grid(), time_stops):
        strategy_params = {
            "rsi_reclaim_level": params["rsi_reclaim_level"],
            "min_trend_strength_pct": params["min_trend_strength_pct"],
            "max_pullback_distance_pct": params["max_pullback_distance_pct"],
            "vwap_pullback_distance_pct": 0.03,
            "min_atr_pct": 0.008,
            "min_macd_hist": -0.01,
            "strong_trend_strength_pct": params["min_trend_strength_pct"] * 1.5,
            "continuation_rsi_level": params["continuation_rsi_level"],
            "continuation_max_vwap_distance_pct": 0.04,
            "continuation_max_ema50_extension_pct": 0.03,
            "continuation_min_macd_hist": -0.01,
        }

        cfg = StrategyConfig(
            name=f"TP_RSI{int(params['rsi_reclaim_level'])}_TS{ts_min}_STR{int(params['min_trend_strength_pct'] * 1000)}",
            strategy_names=["trend_pullback"],
            strategy_classes=[STRATEGY_CLASSES["trend_pullback"]],
            strategy_params=[strategy_params],
            time_stop_minutes=ts_min,
            sl_atr=top_exit[0],
            tp_atr=top_exit[1],
            trailing_act=top_exit[2],
            trailing_off=top_exit[3],
            use_executor_exit=True,
            allow_short=False,
            apply_trend_filter=True,
        )
        configs.append(cfg)

    return configs


# ─── Main Evaluator ─────────────────────────────────────────────────────────


async def _evaluate_candidate(
    sc: StrategyConfig,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    gates: dict,
    reader: IndicatorReader,
    db_config: dict,
    train_months: int = 6,
    test_months: int = 3,
) -> WFOMetrics:
    """Evaluate one StrategyConfig across full period + WFO."""

    # Build aggregator config
    agg_config = {
        "min_agreement": 1,
        "buy_threshold": 0.5,
        "buy_threshold_uptrend": 0.5,
        "sell_threshold": -0.5,
    }

    # Full-period backtest
    full_cfg = _build_engine_config(
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        strategy_classes=sc.strategy_classes,
        strategy_configs=sc.strategy_params,
        aggregator_config=agg_config,
        sl_atr=sc.sl_atr,
        tp_atr=sc.tp_atr,
        trailing_act=sc.trailing_act,
        trailing_off=sc.trailing_off,
        use_executor_exit=sc.use_executor_exit,
        time_stop_minutes=sc.time_stop_minutes,
        allow_short=sc.allow_short,
        apply_trend_filter=sc.apply_trend_filter,
    )
    full_result = await BacktestEngine(full_cfg, reader).run()

    # WFO OOS
    (
        wfo_windows,
        oos_trades,
        oos_sharpe,
        oos_win_rate,
        oos_return,
    ) = await _run_wfo(
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        strategy_classes=sc.strategy_classes,
        strategy_configs=sc.strategy_params,
        aggregator_config=agg_config,
        sl_atr=sc.sl_atr,
        tp_atr=sc.tp_atr,
        trailing_act=sc.trailing_act,
        trailing_off=sc.trailing_off,
        use_executor_exit=sc.use_executor_exit,
        time_stop_minutes=sc.time_stop_minutes,
        allow_short=sc.allow_short,
        apply_trend_filter=sc.apply_trend_filter,
        train_months=train_months,
        test_months=test_months,
        reader=reader,
        db_config=db_config,
    )

    # Compute OOS profit factor from individual windows
    oos_pf = 0.0

    # Gates
    failures: list[str] = []
    if oos_trades < int(gates["min_oos_trades"]):
        failures.append("oos_trades")
    if full_result.max_drawdown * 100 > gates["max_drawdown_pct"]:
        failures.append("drawdown")
    if oos_sharpe < gates["min_oos_sharpe"]:
        failures.append("oos_sharpe")
    if oos_win_rate < gates["min_oos_win_rate"]:
        failures.append("oos_win_rate")

    return WFOMetrics(
        name=sc.name,
        strategy_label=sc.strategy_names[0],
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        total_trades=full_result.total_trades,
        win_rate=full_result.win_rate / 100.0,
        total_return_pct=full_result.total_return_pct,
        max_drawdown_pct=full_result.max_drawdown * 100,
        sharpe_ratio=full_result.sharpe_ratio,
        sortino_ratio=full_result.sortino_ratio,
        profit_factor=full_result.profit_factor,
        wfo_windows=wfo_windows,
        oos_total_trades=oos_trades,
        oos_mean_sharpe=oos_sharpe,
        oos_mean_win_rate=oos_win_rate,
        oos_total_return_pct=oos_return,
        oos_profit_factor=oos_pf,
        time_stop_minutes=sc.time_stop_minutes,
        sl_atr=sc.sl_atr,
        tp_atr=sc.tp_atr,
        trailing_act=sc.trailing_act,
        trailing_off=sc.trailing_off,
        passes_gates=not failures,
        failure_reasons=",".join(failures),
    )


# ─── Output ──────────────────────────────────────────────────────────────────


def _write_results(prefix: str, results: list[WFOMetrics], strategy_label: str) -> Path:
    date_tag = datetime.now().strftime("%Y%m%d-%H%M%S")
    csv_path = Path(f"{prefix}-{strategy_label}-{date_tag}.csv")
    json_path = Path(f"{prefix}-{strategy_label}-{date_tag}.json")
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    rows = [asdict(m) for m in results]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
    return csv_path


# ─── Strategy Generator Map ─────────────────────────────────────────────────


STRATEGY_GENERATORS: dict[str, callable] = {}


def _build_generator_map(base_settings, base_raw_config, symbol, timeframe, reader, db_config):
    return {
        "simple_ma": lambda: _generate_simple_ma_configs(
            base_settings, base_raw_config, symbol, timeframe, reader, db_config
        ),
        "cci": lambda: _generate_cci_configs(
            base_settings, base_raw_config, symbol, timeframe, reader, db_config
        ),
        "ma_cci": lambda: _generate_ma_cci_configs(
            base_settings, base_raw_config, symbol, timeframe, reader, db_config
        ),
        "mtf_regime": lambda: _generate_mtf_regime_configs(
            base_settings, base_raw_config, symbol, timeframe, reader, db_config
        ),
        "trend_pullback": lambda: _generate_trend_pullback_configs(
            base_settings, base_raw_config, symbol, timeframe, reader, db_config
        ),
        "all": None,  # Special: run all
    }


# ─── CLI ─────────────────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Multi-pair 4h WFO Strategy Sweep")
    parser.add_argument(
        "--strategy",
        choices=["simple_ma", "cci", "ma_cci", "mtf_regime", "trend_pullback", "all"],
        default="all",
    )
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        help="Base config for strategy resolution",
    )
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
    )
    parser.add_argument(
        "--timeframe",
        default="4h",
    )
    parser.add_argument(
        "--start",
        default=None,  # Auto-resolved from DB
    )
    parser.add_argument(
        "--end",
        default=None,  # Auto-resolved from DB
    )
    parser.add_argument(
        "--train-months",
        type=int,
        default=6,
    )
    parser.add_argument(
        "--test-months",
        type=int,
        default=3,
    )
    parser.add_argument(
        "--output-prefix",
        default="docs/reports/btc-wfo",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=0,
        help="Limit candidates per strategy (0=all)",
    )
    parser.add_argument(
        "--min-oos-trades",
        type=int,
        default=None,
        help="Override min OOS trades gate",
    )
    return parser.parse_args()


async def _resolve_data_range(symbol: str, timeframe: str) -> tuple[str, str]:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT MIN(time) AS s, MAX(time) AS e FROM indicators WHERE symbol=$1 AND timeframe=$2",
            symbol,
            timeframe,
        )
    if row is None or row["s"] is None:
        raise RuntimeError(f"No indicator data for {symbol} {timeframe}")
    return row["s"].isoformat(), row["e"].isoformat()


# ─── Main ────────────────────────────────────────────────────────────────────


async def main() -> None:
    configure_logger("WARNING")
    args = parse_args()

    # Resolve DB config — prefer POSTGRES_* env vars (set in Docker containers)
    base_settings = load_settings(Path(args.config))
    db_config = {
        "host": str(os.getenv("POSTGRES_HOST", base_settings.database.get("host", "localhost"))),
        "port": int(os.getenv("POSTGRES_PORT", int(base_settings.database.get("port", 5432)))),
        "name": str(os.getenv("POSTGRES_DB", base_settings.database.get("name", "marketdata"))),
        "user": str(os.getenv("POSTGRES_USER", base_settings.database.get("user", "trading"))),
        "password": str(os.getenv("POSTGRES_PASSWORD", base_settings.database.get("password", ""))),
    }

    with Path(args.config).open("r", encoding="utf-8") as f:
        base_raw_config = yaml.safe_load(f) or {}

    await init_pool(db_config)

    try:
        start, end = await _resolve_data_range(args.symbol, args.timeframe)
        start = args.start or start
        end = args.end or end
        print(f"Data range: {start} to {end}")
        print(f"WFO: {args.train_months}mo train / {args.test_months}mo test windows")

        reader = IndicatorReader(db_config)
        async with reader:
            gen_map = _build_generator_map(
                base_settings, base_raw_config, args.symbol, args.timeframe, reader, db_config
            )

            gates = dict(BTC_GATES)
            if args.min_oos_trades is not None:
                gates["min_oos_trades"] = args.min_oos_trades

            strategies_to_run = (
                ["simple_ma", "cci", "ma_cci", "mtf_regime"]
                if args.strategy == "all"
                else [args.strategy]
            )

            all_results: dict[str, list[WFOMetrics]] = {}

            for strat_name in strategies_to_run:
                print(f"\n{'=' * 60}")
                print(f"  STRATEGY: {strat_name.upper()}")
                print(f"{'=' * 60}")

                generator = gen_map.get(strat_name)
                if generator is None:
                    print(f"Unknown strategy: {strat_name}")
                    continue

                raw_configs = await generator()

                if args.max_candidates > 0:
                    raw_configs = raw_configs[: args.max_candidates]

                print(f"  {len(raw_configs)} candidates to evaluate")
                print(f"  Gates: {gates}")

                results: list[WFOMetrics] = []
                for idx, sc in enumerate(raw_configs, 1):
                    m = await _evaluate_candidate(
                        sc=sc,
                        symbol=args.symbol,
                        timeframe=args.timeframe,
                        start=start,
                        end=end,
                        gates=gates,
                        reader=reader,
                        db_config=db_config,
                        train_months=args.train_months,
                        test_months=args.test_months,
                    )
                    results.append(m)
                    print(
                        f"  [{idx}/{len(raw_configs)}] {sc.name[:60]:<60} "
                        f"pass={m.passes_gates} "
                        f"oos_sharpe={m.oos_mean_sharpe:+.2f} "
                        f"oos_wr={m.oos_mean_win_rate:.1%} "
                        f"oos_trades={m.oos_total_trades} "
                        f"return={m.oos_total_return_pct:+.1f}% "
                        f"full_sharpe={m.sharpe_ratio:+.2f} "
                        f"dd={m.max_drawdown_pct:.1f}% "
                        f"fail={m.failure_reasons or 'none'}"
                    )

                # Sort: passing first, then by OOS return
                results.sort(
                    key=lambda m: (
                        m.passes_gates,
                        m.oos_total_return_pct,
                        m.oos_mean_sharpe,
                    ),
                    reverse=True,
                )

                csv_path = _write_results(args.output_prefix, results, strat_name)
                print(f"\n  Results → {csv_path}")

                # Top 5
                print(f"\n  TOP 5 ({strat_name}):")
                for m in results[:5]:
                    print(
                        f"    {'✅' if m.passes_gates else '❌'} {m.name[:55]:<55} "
                        f"oos_sharpe={m.oos_mean_sharpe:+.2f} "
                        f"oos_wr={m.oos_mean_win_rate:.1%} "
                        f"return={m.oos_total_return_pct:+.1f}%"
                    )

                all_results[strat_name] = results

            # ── Final Cross-Strategy Summary ───────────────────────────────
            print(f"\n{'=' * 60}")
            print("  CROSS-STRATEGY SUMMARY")
            print(f"{'=' * 60}")
            passing_total = []
            for strat_name, results in all_results.items():
                passing = [m for m in results if m.passes_gates]
                if passing:
                    best = max(passing, key=lambda m: m.oos_total_return_pct)
                    print(f"\n  {strat_name.upper()}: {len(passing)}/{len(results)} passing")
                    print(
                        f"    Best: {best.name[:55]} "
                        f"oos_sharpe={best.oos_mean_sharpe:+.2f} "
                        f"return={best.oos_total_return_pct:+.1f}%"
                    )
                    passing_total.extend(passing)

            if passing_total:
                overall_best = max(passing_total, key=lambda m: m.oos_total_return_pct)
                print(f"\n  🏆 OVERALL BEST: {overall_best.name}")
                print(
                    f"    Strategy: {overall_best.strategy_label} | "
                    f"OOS Sharpe: {overall_best.oos_mean_sharpe:+.2f} | "
                    f"OOS Return: {overall_best.oos_total_return_pct:+.1f}% | "
                    f"OOS Win Rate: {overall_best.oos_mean_win_rate:.1%} | "
                    f"Full Sharpe: {overall_best.sharpe_ratio:+.2f} | "
                    f"Full DD: {overall_best.max_drawdown_pct:.1f}%"
                )
                print(
                    f"    time_stop={overall_best.time_stop_minutes}min | "
                    f"SL={overall_best.sl_atr} ATR | "
                    f"TP={overall_best.tp_atr} ATR"
                )
            else:
                print("\n  ⚠️  NO STRATEGY PASSED ALL GATES")
                # Still report the best attempt
                all_sorted = sorted(
                    [m for results in all_results.values() for m in results],
                    key=lambda m: m.oos_mean_sharpe,
                    reverse=True,
                )
                if all_sorted:
                    best = all_sorted[0]
                    print(f"  Best effort: {best.name}")
                    print(
                        f"    OOS Sharpe: {best.oos_mean_sharpe:+.2f} | "
                        f"OOS Return: {best.oos_total_return_pct:+.1f}% | "
                        f"Failures: {best.failure_reasons}"
                    )

    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
