#!/usr/bin/env python3
"""Run a bounded Karpathy-style autoresearch loop over config overlays."""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

DEFAULT_FAMILIES = (
    "aggregator_thresholds",
    "risk_atr_exits",
    "sentiment_filters",
    "indicator_thresholds",
    "combined_focus",
    "near_pass_expansion",
    "standard_gate_bridge",
    "near_miss_trade_lift",
    "trend_pullback_overlay",
    "breakout_retest_overlay",
    "volatility_squeeze_overlay",
    "funding_extreme_overlay",
    "regime_gated_pullback_overlay",
    "breakout_retest_bridge",
    "regime_gated_pullback_bridge",
    "trend_pullback_standalone",
    "breakout_retest_standalone",
    "volatility_squeeze_standalone",
    "mtf_breakout_standalone",
    "range_reversion_bounded",
    "funding_primary_standalone",
)

BASE_STRATEGY_CONFIGS: tuple[dict[str, Any], ...] = (
    {
        "name": "rsi_reversal",
        "config": {"rsi_period": 7, "oversold_threshold": 35, "overbought_threshold": 65},
    },
    {
        "name": "macd_histogram",
        "config": {"min_histogram_threshold": 0.0001, "use_atr_filter": True, "atr_min_pct": 0.003},
    },
    {
        "name": "bollinger_bounce",
        "config": {"band_distance_threshold": 0.003, "rsi_oversold": 35, "rsi_overbought": 65},
    },
    {
        "name": "cci_breakout",
        "config": {"cci_buy_threshold": 100, "cci_sell_threshold": -100, "atr_min_pct": 0.005},
    },
    {
        "name": "vwap_reversion",
        "config": {"vwap_atr_multiplier": 1.5, "rsi_oversold": 40, "rsi_overbought": 60},
    },
)


@dataclass(frozen=True)
class Candidate:
    """One generated config-only research candidate."""

    run_index: int
    seed: int
    family: str
    description: str
    overlay: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a bounded autoresearch loop that generates config overlays."
    )
    parser.add_argument("--config", default="config/settings.autoresearch.yaml")
    parser.add_argument("--output-dir", default="research")
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--timeframe", default="4h")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--train-months", type=int, default=6)
    parser.add_argument("--test-months", type=int, default=3)
    parser.add_argument("--bootstrap", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-runs", type=int, default=10)
    parser.add_argument("--max-hours", type=float)
    parser.add_argument("--gate-profile", default="standard")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument(
        "--families",
        default=",".join(DEFAULT_FAMILIES),
        help=(
            "Comma-separated candidate families to sample. Supported: "
            + ", ".join(DEFAULT_FAMILIES)
        ),
    )
    parser.add_argument(
        "--aggregator-focus",
        action="store_true",
        help="Bias aggregator candidates around the best observed strict-threshold region.",
    )
    parser.add_argument(
        "--include-baseline",
        action="store_true",
        help="Run the frozen base config before generated candidates.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write candidate overlays and session plan without running evaluations.",
    )
    return parser.parse_args()


def _candidate_path(output_dir: Path, candidate: Candidate) -> Path:
    safe_family = candidate.family.replace("/", "-")
    return output_dir / "candidates" / f"{candidate.run_index:04d}-{safe_family}.yaml"


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)


def _round(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def _strategy_configs_with_overrides(
    *,
    macd_min: float,
    macd_atr_min: float,
    bb_distance: float,
    bb_rsi_oversold: int,
    bb_rsi_overbought: int,
    cci_atr_min: float,
    vwap_atr_multiplier: float,
) -> list[dict[str, Any]]:
    strategies = []
    for strategy in BASE_STRATEGY_CONFIGS:
        copied = {"name": strategy["name"], "config": dict(strategy["config"])}
        strategies.append(copied)

    by_name = {strategy["name"]: strategy["config"] for strategy in strategies}
    by_name["macd_histogram"]["min_histogram_threshold"] = macd_min
    by_name["macd_histogram"]["atr_min_pct"] = macd_atr_min
    by_name["bollinger_bounce"]["band_distance_threshold"] = bb_distance
    by_name["bollinger_bounce"]["rsi_oversold"] = bb_rsi_oversold
    by_name["bollinger_bounce"]["rsi_overbought"] = bb_rsi_overbought
    by_name["cci_breakout"]["atr_min_pct"] = cci_atr_min
    by_name["vwap_reversion"]["vwap_atr_multiplier"] = vwap_atr_multiplier
    return strategies


def _per_symbol_aggregator_config(
    symbol: str,
    *,
    buy: float,
    sell: float,
    buy_uptrend: float | None = None,
    min_agreement: int = 1,
    sell_min_agreement: int | None = None,
) -> dict[str, dict[str, float | int]]:
    uptrend = buy if buy_uptrend is None else buy_uptrend
    sell_min = sell_min_agreement if sell_min_agreement is not None else 2
    return {
        symbol: {
            "min_agreement": min_agreement,
            "buy_threshold": buy,
            "buy_threshold_uptrend": uptrend,
            "sell_threshold": sell,
            "sell_min_agreement": sell_min,
        }
    }


def _standalone_strategy_overlay(
    *,
    trading_symbol: str,
    rng: random.Random,
    strategy_entry: dict[str, Any],
    buy_low: float,
    buy_high: float,
    sl_atr: float,
    tp_atr: float,
) -> tuple[dict[str, Any], str]:
    """Single-strategy overlay without the five-vote technical stack."""
    buy = _round(rng.uniform(buy_low, buy_high), 2)
    buy_uptrend = _round(rng.uniform(max(0.35, buy - 0.12), buy), 2)
    sell = _round(-rng.uniform(0.45, 0.75), 2)
    strategy_name = str(strategy_entry["name"])
    overlay: dict[str, Any] = {
        "trading_execution": {
            "sl_atr_multiplier": sl_atr,
            "tp_atr_multiplier": tp_atr,
            "trailing_activate_atr": _round(rng.uniform(1.2, 2.0), 2),
            "trailing_offset_atr": _round(rng.uniform(0.65, 1.0), 2),
        },
        "strategy": {
            "strategies": [strategy_entry],
            "aggregator": {
                "buy_threshold": buy,
                "buy_threshold_uptrend": buy_uptrend,
                "sell_threshold": sell,
                "min_confidence": _round(rng.uniform(0.0, 0.25), 2),
            },
            "per_symbol_aggregator_config": _per_symbol_aggregator_config(
                trading_symbol,
                buy=buy,
                sell=sell,
                buy_uptrend=buy_uptrend,
                sell_min_agreement=1,
            ),
        },
    }
    desc = f"{strategy_name}-standalone buy={buy:.2f} sl/tp={sl_atr:.2f}/{tp_atr:.2f}"
    return overlay, desc


def _sol_winner_stack_params(rng: random.Random) -> dict[str, float | int]:
    """Parameter band around the SOL 1h trend_pullback_overlay winner."""
    return {
        "macd_min": _round(rng.uniform(0.0, 0.00025), 5),
        "macd_atr_min": _round(rng.uniform(0.0075, 0.0092), 4),
        "bb_distance": _round(rng.uniform(0.0045, 0.0058), 4),
        "bb_rsi_oversold": rng.choice([30, 35]),
        "bb_rsi_overbought": rng.choice([65, 70]),
        "cci_atr_min": _round(rng.uniform(0.0105, 0.0118), 4),
        "vwap_atr_multiplier": _round(rng.uniform(1.85, 2.12), 2),
    }


def _parse_families(raw: str) -> tuple[str, ...]:
    families = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not families:
        raise ValueError("At least one candidate family is required")
    invalid = sorted(set(families) - set(DEFAULT_FAMILIES))
    if invalid:
        raise ValueError(f"Unsupported candidate families: {', '.join(invalid)}")
    return families


def generate_candidate(
    run_index: int,
    *,
    seed: int,
    symbol: str = "SOLUSDT",
    families: tuple[str, ...] = DEFAULT_FAMILIES,
    aggregator_focus: bool = False,
) -> Candidate:
    """Generate a bounded strategy config overlay.

    The ranges are intentionally conservative and limited to research config
    knobs. They do not affect production unless an operator manually promotes a
    candidate after separate review.
    """

    rng = random.Random(seed + run_index * 7919)
    family = rng.choice(families)
    trading_symbol = symbol.upper()

    if family == "aggregator_thresholds":
        if aggregator_focus:
            buy = _round(rng.uniform(0.98, 1.14), 2)
            buy_uptrend = _round(max(0.90, buy - rng.uniform(0.00, 0.08)), 2)
            sell = _round(-rng.uniform(0.72, 0.92), 2)
            min_agreement = 1
            sell_min_agreement = rng.choice([1, 2])
        else:
            buy = _round(rng.uniform(0.65, 1.10), 2)
            buy_uptrend = _round(max(0.55, buy - rng.uniform(0.0, 0.15)), 2)
            sell = _round(-rng.uniform(0.50, 0.90), 2)
            min_agreement = rng.choice([1, 2])
            sell_min_agreement = rng.choice([1, 2])
        overlay = {
            "strategy": {
                "aggregator": {
                    "buy_threshold": buy,
                    "buy_threshold_uptrend": buy_uptrend,
                    "sell_threshold": sell,
                },
                "per_symbol_aggregator_config": _per_symbol_aggregator_config(
                    trading_symbol,
                    buy=buy,
                    sell=sell,
                    buy_uptrend=buy_uptrend,
                    min_agreement=min_agreement,
                    sell_min_agreement=sell_min_agreement,
                ),
            }
        }
        desc = (
            f"aggregator buy={buy:.2f} uptrend={buy_uptrend:.2f} "
            f"sell={sell:.2f} agree={min_agreement}"
        )
    elif family == "risk_atr_exits":
        sl_atr = _round(rng.uniform(1.4, 2.8), 2)
        tp_atr = _round(rng.uniform(2.2, 5.0), 2)
        trail_activate = _round(rng.uniform(1.0, 2.4), 2)
        trail_offset = _round(rng.uniform(0.6, 1.5), 2)
        overlay = {
            "trading_execution": {
                "sl_atr_multiplier": sl_atr,
                "tp_atr_multiplier": tp_atr,
                "trailing_activate_atr": trail_activate,
                "trailing_offset_atr": trail_offset,
            }
        }
        desc = (
            f"atr exits sl={sl_atr:.2f} tp={tp_atr:.2f} "
            f"trail={trail_activate:.2f}/{trail_offset:.2f}"
        )
    elif family == "sentiment_filters":
        gate = _round(rng.uniform(25.0, 55.0), 1)
        panic = _round(rng.uniform(10.0, min(35.0, gate - 1.0)), 1)
        boost = _round(rng.uniform(max(60.0, gate + 10.0), 80.0), 1)
        overlay = {
            "strategy": {
                "strategies": [
                    {
                        "name": "sentiment_mean_reversion",
                        "config": {
                            "sentiment_gate_threshold": gate,
                            "sentiment_panic_threshold": panic,
                            "sentiment_boost_threshold": boost,
                        },
                    }
                ]
            }
        }
        desc = f"sentiment gate={gate:.1f} panic={panic:.1f} boost={boost:.1f}"
    elif family == "indicator_thresholds":
        bb_distance = _round(rng.uniform(0.002, 0.012), 4)
        atr_min = _round(rng.uniform(0.002, 0.010), 4)
        macd_min = _round(rng.uniform(0.0000, 0.0005), 5)
        overlay = {
            "strategy": {
                "strategies": [
                    {
                        "name": "bollinger_bounce",
                        "config": {
                            "band_distance_threshold": bb_distance,
                            "rsi_oversold": rng.choice([30, 35, 40]),
                            "rsi_overbought": rng.choice([60, 65, 70]),
                        },
                    },
                    {
                        "name": "macd_histogram",
                        "config": {
                            "min_histogram_threshold": macd_min,
                            "use_atr_filter": True,
                            "atr_min_pct": atr_min,
                        },
                    },
                ]
            }
        }
        desc = f"indicator bb={bb_distance:.4f} atr_min={atr_min:.4f} macd={macd_min:.5f}"
    elif family == "combined_focus":
        buy = _round(rng.uniform(1.04, 1.16), 2)
        buy_uptrend = _round(max(0.98, buy - rng.uniform(0.00, 0.08)), 2)
        sell = _round(-rng.uniform(0.72, 0.92), 2)
        bb_distance = _round(rng.uniform(0.004, 0.010), 4)
        macd_atr_min = _round(rng.uniform(0.004, 0.010), 4)
        cci_atr_min = _round(rng.uniform(0.004, 0.012), 4)
        vwap_atr = _round(rng.uniform(1.2, 2.2), 2)
        sl_atr = _round(rng.uniform(1.5, 2.4), 2)
        tp_atr = _round(rng.uniform(2.8, 4.8), 2)
        overlay = {
            "trading_execution": {
                "sl_atr_multiplier": sl_atr,
                "tp_atr_multiplier": tp_atr,
                "trailing_activate_atr": _round(rng.uniform(1.0, 2.0), 2),
                "trailing_offset_atr": _round(rng.uniform(0.6, 1.2), 2),
            },
            "strategy": {
                "strategies": _strategy_configs_with_overrides(
                    macd_min=_round(rng.uniform(0.0, 0.00025), 5),
                    macd_atr_min=macd_atr_min,
                    bb_distance=bb_distance,
                    bb_rsi_oversold=rng.choice([30, 35, 40]),
                    bb_rsi_overbought=rng.choice([60, 65, 70]),
                    cci_atr_min=cci_atr_min,
                    vwap_atr_multiplier=vwap_atr,
                ),
                "aggregator": {
                    "buy_threshold": buy,
                    "buy_threshold_uptrend": buy_uptrend,
                    "sell_threshold": sell,
                },
            },
        }
        combined_sell_min = rng.choice([1, 2])
        overlay["strategy"]["per_symbol_aggregator_config"] = _per_symbol_aggregator_config(
            trading_symbol,
            buy=buy,
            sell=sell,
            buy_uptrend=buy_uptrend,
            sell_min_agreement=combined_sell_min,
        )
        desc = (
            f"combined buy={buy:.2f} uptrend={buy_uptrend:.2f} sell={sell:.2f} "
            f"bb={bb_distance:.4f} macd_atr={macd_atr_min:.4f} "
            f"cci_atr={cci_atr_min:.4f} vwap_atr={vwap_atr:.2f} "
            f"sl/tp={sl_atr:.2f}/{tp_atr:.2f}"
        )
    elif family == "standard_gate_bridge":
        buy = _round(rng.uniform(1.045, 1.085), 2)
        buy_uptrend = _round(rng.uniform(1.015, min(1.065, buy)), 2)
        sell = _round(-rng.uniform(0.71, 0.78), 2)
        bb_distance = _round(rng.uniform(0.0045, 0.0065), 4)
        macd_atr_min = _round(rng.uniform(0.0047, 0.0089), 4)
        cci_atr_min = _round(rng.uniform(0.0108, 0.0124), 4)
        vwap_atr = _round(rng.uniform(1.25, 2.10), 2)
        sl_atr = _round(rng.uniform(2.05, 2.35), 2)
        tp_atr = _round(rng.uniform(3.20, 4.75), 2)
        overlay = {
            "trading_execution": {
                "sl_atr_multiplier": sl_atr,
                "tp_atr_multiplier": tp_atr,
                "trailing_activate_atr": _round(rng.uniform(1.5, 2.1), 2),
                "trailing_offset_atr": _round(rng.uniform(0.75, 1.05), 2),
            },
            "strategy": {
                "strategies": _strategy_configs_with_overrides(
                    macd_min=_round(rng.uniform(0.0, 0.00025), 5),
                    macd_atr_min=macd_atr_min,
                    bb_distance=bb_distance,
                    bb_rsi_oversold=rng.choice([30, 35]),
                    bb_rsi_overbought=rng.choice([65, 70]),
                    cci_atr_min=cci_atr_min,
                    vwap_atr_multiplier=vwap_atr,
                ),
                "aggregator": {
                    "buy_threshold": buy,
                    "buy_threshold_uptrend": buy_uptrend,
                    "sell_threshold": sell,
                },
                "per_symbol_aggregator_config": _per_symbol_aggregator_config(
                    trading_symbol,
                    buy=buy,
                    sell=sell,
                    buy_uptrend=buy_uptrend,
                    sell_min_agreement=rng.choice([1, 2]),
                ),
            },
        }
        desc = (
            f"standard-bridge buy={buy:.2f} uptrend={buy_uptrend:.2f} sell={sell:.2f} "
            f"bb={bb_distance:.4f} macd_atr={macd_atr_min:.4f} "
            f"cci_atr={cci_atr_min:.4f} vwap_atr={vwap_atr:.2f} "
            f"sl/tp={sl_atr:.2f}/{tp_atr:.2f}"
        )
    elif family == "near_miss_trade_lift":
        buy = _round(rng.uniform(1.055, 1.095), 2)
        buy_uptrend = _round(rng.uniform(1.030, min(1.065, buy)), 2)
        sell = _round(-rng.uniform(0.74, 0.80), 2)
        bb_distance = _round(rng.uniform(0.0045, 0.0058), 4)
        macd_atr_min = _round(rng.uniform(0.0075, 0.0092), 4)
        cci_atr_min = _round(rng.uniform(0.0105, 0.0118), 4)
        vwap_atr = _round(rng.uniform(1.85, 2.12), 2)
        sl_atr = _round(rng.uniform(2.15, 2.35), 2)
        tp_atr = _round(rng.uniform(3.70, 4.20), 2)
        overlay = {
            "trading_execution": {
                "sl_atr_multiplier": sl_atr,
                "tp_atr_multiplier": tp_atr,
                "trailing_activate_atr": _round(rng.uniform(1.65, 2.05), 2),
                "trailing_offset_atr": _round(rng.uniform(0.80, 1.05), 2),
            },
            "strategy": {
                "strategies": _strategy_configs_with_overrides(
                    macd_min=_round(rng.uniform(0.0, 0.00025), 5),
                    macd_atr_min=macd_atr_min,
                    bb_distance=bb_distance,
                    bb_rsi_oversold=rng.choice([30, 35]),
                    bb_rsi_overbought=rng.choice([65, 70]),
                    cci_atr_min=cci_atr_min,
                    vwap_atr_multiplier=vwap_atr,
                ),
                "aggregator": {
                    "buy_threshold": buy,
                    "buy_threshold_uptrend": buy_uptrend,
                    "sell_threshold": sell,
                },
                "per_symbol_aggregator_config": _per_symbol_aggregator_config(
                    trading_symbol,
                    buy=buy,
                    sell=sell,
                    buy_uptrend=buy_uptrend,
                    sell_min_agreement=rng.choice([1, 2]),
                ),
            },
        }
        desc = (
            f"near-miss-lift buy={buy:.2f} uptrend={buy_uptrend:.2f} sell={sell:.2f} "
            f"bb={bb_distance:.4f} macd_atr={macd_atr_min:.4f} "
            f"cci_atr={cci_atr_min:.4f} vwap_atr={vwap_atr:.2f} "
            f"sl/tp={sl_atr:.2f}/{tp_atr:.2f}"
        )
    elif family == "trend_pullback_overlay":
        buy = _round(rng.uniform(1.10, 1.35), 2)
        buy_uptrend = _round(rng.uniform(0.95, min(1.15, buy)), 2)
        sell = _round(-rng.uniform(0.74, 0.86), 2)
        trend_pullback = {
            "name": "trend_pullback",
            "config": {
                "rsi_reclaim_level": rng.choice([48, 50, 52]),
                "min_trend_strength_pct": _round(rng.uniform(0.006, 0.012), 4),
                "max_pullback_distance_pct": _round(rng.uniform(0.015, 0.030), 4),
                "vwap_pullback_distance_pct": _round(rng.uniform(0.015, 0.035), 4),
                "min_atr_pct": _round(rng.uniform(0.006, 0.012), 4),
                "min_macd_hist": _round(rng.uniform(-0.012, 0.002), 4),
                "strong_trend_strength_pct": _round(rng.uniform(0.012, 0.020), 4),
                "continuation_rsi_level": rng.choice([52, 54, 56]),
                "continuation_max_vwap_distance_pct": _round(rng.uniform(0.030, 0.050), 4),
                "continuation_max_ema50_extension_pct": _round(rng.uniform(0.020, 0.040), 4),
                "continuation_min_macd_hist": _round(rng.uniform(-0.012, 0.002), 4),
            },
        }
        overlay = {
            "trading_execution": {
                "sl_atr_multiplier": _round(rng.uniform(2.10, 2.45), 2),
                "tp_atr_multiplier": _round(rng.uniform(3.60, 4.40), 2),
                "trailing_activate_atr": _round(rng.uniform(1.55, 2.10), 2),
                "trailing_offset_atr": _round(rng.uniform(0.80, 1.10), 2),
            },
            "strategy": {
                "strategies": [
                    *_strategy_configs_with_overrides(
                        macd_min=_round(rng.uniform(0.0, 0.00025), 5),
                        macd_atr_min=_round(rng.uniform(0.0075, 0.0092), 4),
                        bb_distance=_round(rng.uniform(0.0045, 0.0058), 4),
                        bb_rsi_oversold=rng.choice([30, 35]),
                        bb_rsi_overbought=rng.choice([65, 70]),
                        cci_atr_min=_round(rng.uniform(0.0105, 0.0118), 4),
                        vwap_atr_multiplier=_round(rng.uniform(1.85, 2.12), 2),
                    ),
                    trend_pullback,
                ],
                "aggregator": {
                    "buy_threshold": buy,
                    "buy_threshold_uptrend": buy_uptrend,
                    "sell_threshold": sell,
                    "min_confidence": _round(rng.uniform(0.0, 0.45), 2),
                },
                "per_symbol_aggregator_config": _per_symbol_aggregator_config(
                    trading_symbol,
                    buy=buy,
                    sell=sell,
                    buy_uptrend=buy_uptrend,
                    sell_min_agreement=rng.choice([1, 2]),
                ),
            },
        }
        desc = (
            f"trend-pullback-overlay buy={buy:.2f} uptrend={buy_uptrend:.2f} "
            f"sell={sell:.2f} rsi={trend_pullback['config']['rsi_reclaim_level']} "
            f"trend={trend_pullback['config']['min_trend_strength_pct']:.4f}"
        )
    elif family == "breakout_retest_overlay":
        buy = _round(rng.uniform(1.12, 1.38), 2)
        buy_uptrend = _round(rng.uniform(0.98, min(1.18, buy)), 2)
        sell = _round(-rng.uniform(0.74, 0.86), 2)
        stack = _sol_winner_stack_params(rng)
        breakout_retest = {
            "name": "breakout_retest",
            "config": {
                "min_trend_strength_pct": _round(rng.uniform(0.006, 0.010), 4),
                "min_atr_pct": _round(rng.uniform(0.008, 0.011), 4),
                "breakout_rsi_level": _round(rng.uniform(56.0, 60.0), 1),
                "breakout_min_macd_hist": _round(rng.uniform(-0.002, 0.002), 4),
                "breakout_band_distance_threshold": _round(rng.uniform(0.008, 0.012), 4),
                "breakout_min_vwap_extension_pct": _round(rng.uniform(0.008, 0.014), 4),
                "retest_window_bars": rng.choice([4, 5, 6]),
                "retest_vwap_distance_pct": _round(rng.uniform(0.012, 0.020), 4),
                "retest_ema50_distance_pct": _round(rng.uniform(0.012, 0.020), 4),
                "reclaim_rsi_level": rng.choice([48, 50, 52]),
                "retest_min_macd_hist": _round(rng.uniform(-0.012, 0.0), 4),
                "max_extension_after_retest_pct": _round(rng.uniform(0.020, 0.030), 4),
            },
        }
        overlay = {
            "trading_execution": {
                "sl_atr_multiplier": _round(rng.uniform(2.10, 2.45), 2),
                "tp_atr_multiplier": _round(rng.uniform(3.60, 4.40), 2),
                "trailing_activate_atr": _round(rng.uniform(1.55, 2.10), 2),
                "trailing_offset_atr": _round(rng.uniform(0.80, 1.10), 2),
            },
            "strategy": {
                "strategies": [
                    *_strategy_configs_with_overrides(**stack),
                    breakout_retest,
                ],
                "aggregator": {
                    "buy_threshold": buy,
                    "buy_threshold_uptrend": buy_uptrend,
                    "sell_threshold": sell,
                    "min_confidence": _round(rng.uniform(0.0, 0.40), 2),
                },
                "per_symbol_aggregator_config": _per_symbol_aggregator_config(
                    trading_symbol,
                    buy=buy,
                    sell=sell,
                    buy_uptrend=buy_uptrend,
                    sell_min_agreement=rng.choice([1, 2]),
                ),
            },
        }
        desc = (
            f"breakout-retest-overlay buy={buy:.2f} window="
            f"{breakout_retest['config']['retest_window_bars']} "
            f"reclaim={breakout_retest['config']['reclaim_rsi_level']}"
        )
    elif family == "volatility_squeeze_overlay":
        buy = _round(rng.uniform(1.05, 1.28), 2)
        buy_uptrend = _round(rng.uniform(0.92, min(1.12, buy)), 2)
        sell = _round(-rng.uniform(0.74, 0.88), 2)
        stack = _sol_winner_stack_params(rng)
        volatility_squeeze = {
            "name": "volatility_squeeze",
            "config": {
                "squeeze_lookback": rng.choice([40, 50, 60]),
                "squeeze_percentile": _round(rng.uniform(0.15, 0.25), 2),
                "sma_period": 20,
                "momentum_period": rng.choice([8, 10, 12]),
                "atr_trail_multiplier": _round(rng.uniform(2.5, 3.5), 2),
                "max_hold_bars": rng.choice([20, 30, 40]),
                "min_atr_pct": _round(rng.uniform(0.005, 0.009), 4),
            },
        }
        overlay = {
            "trading_execution": {
                "sl_atr_multiplier": _round(rng.uniform(2.0, 2.6), 2),
                "tp_atr_multiplier": _round(rng.uniform(3.2, 4.8), 2),
                "trailing_activate_atr": _round(rng.uniform(1.4, 2.0), 2),
                "trailing_offset_atr": _round(rng.uniform(0.75, 1.05), 2),
            },
            "strategy": {
                "strategies": [
                    *_strategy_configs_with_overrides(**stack),
                    volatility_squeeze,
                ],
                "aggregator": {
                    "buy_threshold": buy,
                    "buy_threshold_uptrend": buy_uptrend,
                    "sell_threshold": sell,
                    "min_confidence": _round(rng.uniform(0.0, 0.35), 2),
                },
                "per_symbol_aggregator_config": _per_symbol_aggregator_config(
                    trading_symbol,
                    buy=buy,
                    sell=sell,
                    buy_uptrend=buy_uptrend,
                    sell_min_agreement=rng.choice([1, 2]),
                ),
            },
        }
        desc = (
            f"vol-squeeze-overlay buy={buy:.2f} pctile="
            f"{volatility_squeeze['config']['squeeze_percentile']:.2f} "
            f"hold={volatility_squeeze['config']['max_hold_bars']}"
        )
    elif family == "funding_extreme_overlay":
        buy = _round(rng.uniform(0.85, 1.15), 2)
        buy_uptrend = _round(rng.uniform(0.80, min(1.05, buy)), 2)
        sell = _round(-rng.uniform(0.70, 0.85), 2)
        stack = _sol_winner_stack_params(rng)
        funding_rate = {
            "name": "funding_rate",
            "config": {
                "entry_threshold": _round(rng.uniform(0.0003, 0.0008), 5),
                "exit_threshold": _round(rng.uniform(0.00005, 0.00015), 5),
                "lookback_periods": 1,
            },
        }
        overlay = {
            "trading_execution": {
                "sl_atr_multiplier": _round(rng.uniform(2.0, 2.5), 2),
                "tp_atr_multiplier": _round(rng.uniform(3.4, 4.2), 2),
                "trailing_activate_atr": _round(rng.uniform(1.5, 2.0), 2),
                "trailing_offset_atr": _round(rng.uniform(0.80, 1.05), 2),
            },
            "strategy": {
                "strategies": [
                    *_strategy_configs_with_overrides(**stack),
                    funding_rate,
                ],
                "aggregator": {
                    "buy_threshold": buy,
                    "buy_threshold_uptrend": buy_uptrend,
                    "sell_threshold": sell,
                    "min_confidence": _round(rng.uniform(0.0, 0.30), 2),
                },
                "per_symbol_aggregator_config": _per_symbol_aggregator_config(
                    trading_symbol,
                    buy=buy,
                    sell=sell,
                    buy_uptrend=buy_uptrend,
                    sell_min_agreement=1,
                ),
            },
        }
        desc = (
            f"funding-extreme-overlay entry={funding_rate['config']['entry_threshold']:.5f} "
            f"buy={buy:.2f}"
        )
    elif family == "regime_gated_pullback_overlay":
        buy = _round(rng.uniform(1.15, 1.40), 2)
        buy_uptrend = _round(rng.uniform(1.00, min(1.20, buy)), 2)
        sell = _round(-rng.uniform(0.74, 0.86), 2)
        stack = _sol_winner_stack_params(rng)
        stack["macd_atr_min"] = _round(rng.uniform(0.0085, 0.0120), 4)
        stack["cci_atr_min"] = _round(rng.uniform(0.0115, 0.0140), 4)
        trend_pullback = {
            "name": "trend_pullback",
            "config": {
                "rsi_reclaim_level": rng.choice([48, 50, 52]),
                "min_trend_strength_pct": _round(rng.uniform(0.008, 0.014), 4),
                "max_pullback_distance_pct": _round(rng.uniform(0.012, 0.025), 4),
                "vwap_pullback_distance_pct": _round(rng.uniform(0.012, 0.030), 4),
                "min_atr_pct": _round(rng.uniform(0.008, 0.014), 4),
                "min_macd_hist": _round(rng.uniform(-0.010, 0.002), 4),
                "strong_trend_strength_pct": _round(rng.uniform(0.014, 0.022), 4),
                "continuation_rsi_level": rng.choice([52, 54, 56]),
                "continuation_max_vwap_distance_pct": _round(rng.uniform(0.025, 0.045), 4),
                "continuation_max_ema50_extension_pct": _round(rng.uniform(0.018, 0.035), 4),
                "continuation_min_macd_hist": _round(rng.uniform(-0.010, 0.002), 4),
            },
        }
        overlay = {
            "trading_execution": {
                "sl_atr_multiplier": _round(rng.uniform(2.15, 2.50), 2),
                "tp_atr_multiplier": _round(rng.uniform(3.70, 4.50), 2),
                "trailing_activate_atr": _round(rng.uniform(1.60, 2.15), 2),
                "trailing_offset_atr": _round(rng.uniform(0.85, 1.10), 2),
            },
            "strategy": {
                "global_trend_filter_buffer_pct": _round(rng.uniform(0.0, 0.01), 4),
                "strategies": [
                    *_strategy_configs_with_overrides(**stack),
                    trend_pullback,
                ],
                "aggregator": {
                    "buy_threshold": buy,
                    "buy_threshold_uptrend": buy_uptrend,
                    "sell_threshold": sell,
                    "min_confidence": _round(rng.uniform(0.10, 0.50), 2),
                    "btc_regime_filter_enabled": True,
                    "btc_dump_threshold_pct": _round(rng.uniform(-1.2, -0.8), 2),
                    "btc_dump_require_below_ema200": True,
                },
                "per_symbol_aggregator_config": _per_symbol_aggregator_config(
                    trading_symbol,
                    buy=buy,
                    sell=sell,
                    buy_uptrend=buy_uptrend,
                    sell_min_agreement=rng.choice([1, 2]),
                ),
            },
        }
        desc = (
            f"regime-gated-pullback buy={buy:.2f} atr="
            f"{trend_pullback['config']['min_atr_pct']:.4f} "
            f"trend={trend_pullback['config']['min_trend_strength_pct']:.4f}"
        )
    elif family == "breakout_retest_bridge":
        buy = _round(rng.uniform(1.045, 1.095), 2)
        buy_uptrend = _round(rng.uniform(1.015, min(1.065, buy)), 2)
        sell = _round(-rng.uniform(0.71, 0.78), 2)
        sl_atr = _round(rng.uniform(2.05, 2.35), 2)
        tp_atr = _round(rng.uniform(3.20, 4.75), 2)
        breakout_retest = {
            "name": "breakout_retest",
            "config": {
                "min_trend_strength_pct": _round(rng.uniform(0.007, 0.010), 4),
                "min_atr_pct": _round(rng.uniform(0.008, 0.010), 4),
                "breakout_rsi_level": _round(rng.uniform(57.0, 59.0), 1),
                "retest_window_bars": rng.choice([4, 5]),
                "retest_vwap_distance_pct": _round(rng.uniform(0.013, 0.018), 4),
                "retest_ema50_distance_pct": _round(rng.uniform(0.013, 0.018), 4),
                "reclaim_rsi_level": rng.choice([48, 50]),
                "max_extension_after_retest_pct": _round(rng.uniform(0.022, 0.028), 4),
            },
        }
        overlay = {
            "trading_execution": {
                "sl_atr_multiplier": sl_atr,
                "tp_atr_multiplier": tp_atr,
                "trailing_activate_atr": _round(rng.uniform(1.5, 2.1), 2),
                "trailing_offset_atr": _round(rng.uniform(0.75, 1.05), 2),
            },
            "strategy": {
                "strategies": [
                    *_strategy_configs_with_overrides(
                        macd_min=_round(rng.uniform(0.0, 0.00025), 5),
                        macd_atr_min=_round(rng.uniform(0.0047, 0.0089), 4),
                        bb_distance=_round(rng.uniform(0.0045, 0.0065), 4),
                        bb_rsi_oversold=rng.choice([30, 35]),
                        bb_rsi_overbought=rng.choice([65, 70]),
                        cci_atr_min=_round(rng.uniform(0.0108, 0.0124), 4),
                        vwap_atr_multiplier=_round(rng.uniform(1.25, 2.10), 2),
                    ),
                    breakout_retest,
                ],
                "aggregator": {
                    "buy_threshold": buy,
                    "buy_threshold_uptrend": buy_uptrend,
                    "sell_threshold": sell,
                    "min_confidence": _round(rng.uniform(0.0, 0.35), 2),
                },
                "per_symbol_aggregator_config": _per_symbol_aggregator_config(
                    trading_symbol,
                    buy=buy,
                    sell=sell,
                    buy_uptrend=buy_uptrend,
                    sell_min_agreement=rng.choice([1, 2]),
                ),
            },
        }
        desc = (
            f"breakout-retest-bridge buy={buy:.2f} window="
            f"{breakout_retest['config']['retest_window_bars']} sl/tp={sl_atr:.2f}/{tp_atr:.2f}"
        )
    elif family == "regime_gated_pullback_bridge":
        buy = _round(rng.uniform(1.05, 1.12), 2)
        buy_uptrend = _round(rng.uniform(1.02, min(1.10, buy)), 2)
        sell = _round(-rng.uniform(0.72, 0.80), 2)
        stack = _sol_winner_stack_params(rng)
        stack["macd_atr_min"] = _round(rng.uniform(0.0080, 0.0110), 4)
        stack["cci_atr_min"] = _round(rng.uniform(0.0108, 0.0130), 4)
        trend_pullback = {
            "name": "trend_pullback",
            "config": {
                "rsi_reclaim_level": rng.choice([48, 50, 52]),
                "min_trend_strength_pct": _round(rng.uniform(0.007, 0.012), 4),
                "max_pullback_distance_pct": _round(rng.uniform(0.014, 0.028), 4),
                "vwap_pullback_distance_pct": _round(rng.uniform(0.014, 0.032), 4),
                "min_atr_pct": _round(rng.uniform(0.007, 0.012), 4),
                "min_macd_hist": _round(rng.uniform(-0.011, 0.002), 4),
                "strong_trend_strength_pct": _round(rng.uniform(0.012, 0.020), 4),
                "continuation_rsi_level": rng.choice([52, 54]),
                "continuation_max_vwap_distance_pct": _round(rng.uniform(0.028, 0.048), 4),
                "continuation_max_ema50_extension_pct": _round(rng.uniform(0.020, 0.038), 4),
                "continuation_min_macd_hist": _round(rng.uniform(-0.011, 0.002), 4),
            },
        }
        overlay = {
            "trading_execution": {
                "sl_atr_multiplier": _round(rng.uniform(2.10, 2.40), 2),
                "tp_atr_multiplier": _round(rng.uniform(3.50, 4.30), 2),
                "trailing_activate_atr": _round(rng.uniform(1.55, 2.05), 2),
                "trailing_offset_atr": _round(rng.uniform(0.80, 1.05), 2),
            },
            "strategy": {
                "global_trend_filter_buffer_pct": _round(rng.uniform(0.0, 0.008), 4),
                "strategies": [
                    *_strategy_configs_with_overrides(**stack),
                    trend_pullback,
                ],
                "aggregator": {
                    "buy_threshold": buy,
                    "buy_threshold_uptrend": buy_uptrend,
                    "sell_threshold": sell,
                    "min_confidence": _round(rng.uniform(0.05, 0.40), 2),
                    "btc_regime_filter_enabled": True,
                    "btc_dump_threshold_pct": _round(rng.uniform(-1.15, -0.85), 2),
                    "btc_dump_require_below_ema200": True,
                },
                "per_symbol_aggregator_config": _per_symbol_aggregator_config(
                    trading_symbol,
                    buy=buy,
                    sell=sell,
                    buy_uptrend=buy_uptrend,
                    sell_min_agreement=rng.choice([1, 2]),
                ),
            },
        }
        desc = (
            f"regime-gated-pullback-bridge buy={buy:.2f} atr="
            f"{trend_pullback['config']['min_atr_pct']:.4f}"
        )
    elif family == "trend_pullback_standalone":
        overlay, desc = _standalone_strategy_overlay(
            trading_symbol=trading_symbol,
            rng=rng,
            strategy_entry={
                "name": "trend_pullback",
                "config": {
                    "rsi_reclaim_level": rng.choice([48, 50, 52]),
                    "min_trend_strength_pct": _round(rng.uniform(0.006, 0.012), 4),
                    "max_pullback_distance_pct": _round(rng.uniform(0.015, 0.030), 4),
                    "vwap_pullback_distance_pct": _round(rng.uniform(0.015, 0.035), 4),
                    "min_atr_pct": _round(rng.uniform(0.006, 0.012), 4),
                    "min_macd_hist": _round(rng.uniform(-0.012, 0.002), 4),
                    "strong_trend_strength_pct": _round(rng.uniform(0.012, 0.020), 4),
                    "continuation_rsi_level": rng.choice([52, 54, 56]),
                    "continuation_max_vwap_distance_pct": _round(rng.uniform(0.030, 0.050), 4),
                    "continuation_max_ema50_extension_pct": _round(rng.uniform(0.020, 0.040), 4),
                    "continuation_min_macd_hist": _round(rng.uniform(-0.012, 0.002), 4),
                },
            },
            buy_low=0.42,
            buy_high=0.72,
            sl_atr=_round(rng.uniform(2.0, 2.6), 2),
            tp_atr=_round(rng.uniform(3.2, 4.8), 2),
        )
    elif family == "breakout_retest_standalone":
        overlay, desc = _standalone_strategy_overlay(
            trading_symbol=trading_symbol,
            rng=rng,
            strategy_entry={
                "name": "breakout_retest",
                "config": {
                    "min_trend_strength_pct": _round(rng.uniform(0.006, 0.010), 4),
                    "min_atr_pct": _round(rng.uniform(0.008, 0.011), 4),
                    "breakout_rsi_level": _round(rng.uniform(56.0, 60.0), 1),
                    "retest_window_bars": rng.choice([4, 5, 6]),
                    "retest_vwap_distance_pct": _round(rng.uniform(0.012, 0.020), 4),
                    "retest_ema50_distance_pct": _round(rng.uniform(0.012, 0.020), 4),
                    "reclaim_rsi_level": rng.choice([48, 50, 52]),
                    "max_extension_after_retest_pct": _round(rng.uniform(0.020, 0.030), 4),
                },
            },
            buy_low=0.45,
            buy_high=0.75,
            sl_atr=_round(rng.uniform(2.0, 2.5), 2),
            tp_atr=_round(rng.uniform(3.2, 4.5), 2),
        )
    elif family == "volatility_squeeze_standalone":
        overlay, desc = _standalone_strategy_overlay(
            trading_symbol=trading_symbol,
            rng=rng,
            strategy_entry={
                "name": "volatility_squeeze",
                "config": {
                    "squeeze_lookback": rng.choice([40, 50, 60]),
                    "squeeze_percentile": _round(rng.uniform(0.15, 0.28), 2),
                    "sma_period": 20,
                    "momentum_period": rng.choice([8, 10, 12]),
                    "atr_trail_multiplier": _round(rng.uniform(2.5, 3.5), 2),
                    "max_hold_bars": rng.choice([20, 30, 40]),
                    "min_atr_pct": _round(rng.uniform(0.005, 0.009), 4),
                },
            },
            buy_low=0.40,
            buy_high=0.70,
            sl_atr=_round(rng.uniform(2.2, 2.8), 2),
            tp_atr=_round(rng.uniform(3.0, 5.0), 2),
        )
    elif family == "mtf_breakout_standalone":
        buy = _round(rng.uniform(0.48, 0.72), 2)
        buy_uptrend = _round(rng.uniform(0.42, buy), 2)
        sell = _round(-rng.uniform(0.50, 0.72), 2)
        mtf_breakout = {
            "name": "mtf_breakout",
            "config": {
                "volatility_threshold": _round(rng.uniform(50.0, 65.0), 1),
                "breakout_threshold": _round(rng.uniform(0.015, 0.030), 4),
                "trend_slope_threshold": _round(rng.uniform(0.0015, 0.0035), 4),
                "reclaim_rsi_level": rng.choice([48, 50, 52]),
                "reclaim_vwap_distance_pct": _round(rng.uniform(0.010, 0.020), 4),
                "reclaim_ema50_distance_pct": _round(rng.uniform(0.010, 0.020), 4),
                "min_atr_pct": _round(rng.uniform(0.006, 0.010), 4),
            },
        }
        overlay = {
            "trading": {"timeframe": "1h"},
            "trading_execution": {
                "sl_atr_multiplier": _round(rng.uniform(2.0, 2.5), 2),
                "tp_atr_multiplier": _round(rng.uniform(3.2, 4.5), 2),
                "trailing_activate_atr": _round(rng.uniform(1.4, 2.0), 2),
                "trailing_offset_atr": _round(rng.uniform(0.70, 1.0), 2),
            },
            "strategy": {
                "strategies": [mtf_breakout],
                "aggregator": {
                    "buy_threshold": buy,
                    "buy_threshold_uptrend": buy_uptrend,
                    "sell_threshold": sell,
                    "min_confidence": _round(rng.uniform(0.0, 0.30), 2),
                },
                "per_symbol_aggregator_config": _per_symbol_aggregator_config(
                    trading_symbol,
                    buy=buy,
                    sell=sell,
                    buy_uptrend=buy_uptrend,
                    sell_min_agreement=1,
                ),
            },
        }
        desc = (
            f"mtf-breakout-standalone vol={mtf_breakout['config']['volatility_threshold']:.1f} "
            f"buy={buy:.2f}"
        )
    elif family == "range_reversion_bounded":
        overlay, desc = _standalone_strategy_overlay(
            trading_symbol=trading_symbol,
            rng=rng,
            strategy_entry={
                "name": "bollinger_bounce",
                "config": {
                    "band_distance_threshold": _round(rng.uniform(0.002, 0.007), 4),
                    "rsi_oversold": float(rng.choice([35, 38, 42])),
                    "rsi_overbought": float(rng.choice([58, 62, 65])),
                },
            },
            buy_low=0.48,
            buy_high=0.72,
            sl_atr=_round(rng.uniform(1.8, 2.4), 2),
            tp_atr=_round(rng.uniform(2.4, 3.6), 2),
        )
        overlay["trading_execution"]["exit_rules"] = {
            "backtest_use_executor_exit_model": True,
            "time_stop_minutes": float(rng.choice([2880, 4320, 5760])),
        }
        overlay["strategy"]["global_trend_filter_buffer_pct"] = _round(rng.uniform(0.0, 0.005), 4)
        desc = (
            f"range-reversion-bounded bb={overlay['strategy']['strategies'][0]['config']['band_distance_threshold']:.4f} "
            f"time_stop={overlay['trading_execution']['exit_rules']['time_stop_minutes']:.0f}m"
        )
    elif family == "funding_primary_standalone":
        entry_thresh = _round(rng.uniform(0.00035, 0.0009), 5)
        exit_thresh = _round(rng.uniform(entry_thresh * 0.25, entry_thresh * 0.65), 5)
        overlay, desc = _standalone_strategy_overlay(
            trading_symbol=trading_symbol,
            rng=rng,
            strategy_entry={
                "name": "funding_rate",
                "config": {
                    "entry_threshold": entry_thresh,
                    "exit_threshold": exit_thresh,
                    "lookback_periods": rng.choice([1, 3, 6]),
                },
            },
            buy_low=0.42,
            buy_high=0.68,
            sl_atr=_round(rng.uniform(2.0, 2.6), 2),
            tp_atr=_round(rng.uniform(3.0, 4.5), 2),
        )
        overlay["trading_execution"]["exit_rules"] = {
            "backtest_use_executor_exit_model": True,
            "time_stop_minutes": float(rng.choice([1440, 2880, 4320])),
        }
        desc = (
            f"funding-primary entry={entry_thresh:.5f} exit={exit_thresh:.5f} "
            f"lookback={overlay['strategy']['strategies'][0]['config']['lookback_periods']}"
        )
    else:
        buy = _round(rng.uniform(1.00, 1.10), 2)
        buy_uptrend = _round(max(0.94, buy - rng.uniform(0.02, 0.10)), 2)
        sell = _round(-rng.uniform(0.74, 0.90), 2)
        bb_distance = _round(rng.uniform(0.008, 0.014), 4)
        macd_atr_min = _round(rng.uniform(0.0035, 0.0070), 4)
        cci_atr_min = _round(rng.uniform(0.0060, 0.0110), 4)
        vwap_atr = _round(rng.uniform(1.8, 2.4), 2)
        sl_atr = _round(rng.uniform(1.45, 1.85), 2)
        tp_atr = _round(rng.uniform(2.6, 3.4), 2)
        overlay = {
            "trading_execution": {
                "sl_atr_multiplier": sl_atr,
                "tp_atr_multiplier": tp_atr,
                "trailing_activate_atr": _round(rng.uniform(1.4, 2.2), 2),
                "trailing_offset_atr": _round(rng.uniform(0.7, 1.1), 2),
            },
            "strategy": {
                "strategies": _strategy_configs_with_overrides(
                    macd_min=_round(rng.uniform(0.0, 0.00025), 5),
                    macd_atr_min=macd_atr_min,
                    bb_distance=bb_distance,
                    bb_rsi_oversold=rng.choice([30, 35, 40]),
                    bb_rsi_overbought=rng.choice([60, 65, 70]),
                    cci_atr_min=cci_atr_min,
                    vwap_atr_multiplier=vwap_atr,
                ),
                "aggregator": {
                    "buy_threshold": buy,
                    "buy_threshold_uptrend": buy_uptrend,
                    "sell_threshold": sell,
                },
                "per_symbol_aggregator_config": _per_symbol_aggregator_config(
                    trading_symbol,
                    buy=buy,
                    sell=sell,
                    buy_uptrend=buy_uptrend,
                    sell_min_agreement=2,
                ),
            },
        }
        desc = (
            f"near-pass buy={buy:.2f} uptrend={buy_uptrend:.2f} sell={sell:.2f} "
            f"bb={bb_distance:.4f} macd_atr={macd_atr_min:.4f} "
            f"cci_atr={cci_atr_min:.4f} vwap_atr={vwap_atr:.2f} "
            f"sl/tp={sl_atr:.2f}/{tp_atr:.2f}"
        )

    return Candidate(
        run_index=run_index,
        seed=seed,
        family=family,
        description=desc,
        overlay=overlay,
    )


def build_run_command(
    args: argparse.Namespace, *, overlay_path: Path | None, description: str
) -> list[str]:
    cmd = [
        sys.executable,
        "scripts/run_autoresearch.py",
        "--config",
        args.config,
        "--description",
        description,
        "--output-dir",
        args.output_dir,
        "--symbol",
        args.symbol,
        "--timeframe",
        args.timeframe,
        "--train-months",
        str(args.train_months),
        "--test-months",
        str(args.test_months),
        "--bootstrap",
        str(args.bootstrap),
        "--seed",
        str(args.seed),
        "--gate-profile",
        args.gate_profile,
        "--timeout-seconds",
        str(args.timeout_seconds),
    ]
    if overlay_path is not None:
        cmd.extend(["--overlay", str(overlay_path)])
    if args.start:
        cmd.extend(["--start", args.start])
    if args.end:
        cmd.extend(["--end", args.end])
    return cmd


def _run_command(cmd: list[str], *, log_path: Path) -> int:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"$ {' '.join(cmd)}\n")
        handle.write(f"returncode: {result.returncode}\n")
        handle.write("[stdout]\n")
        handle.write(result.stdout.rstrip())
        handle.write("\n[stderr]\n")
        handle.write(result.stderr.rstrip())
        handle.write("\n\n")
    return int(result.returncode)


def _session_summary_path(output_dir: Path) -> Path:
    return output_dir / "autoresearch_session.json"


def main() -> None:
    args = parse_args()
    if args.max_runs < 1:
        raise SystemExit("--max-runs must be >= 1")
    try:
        families = _parse_families(args.families)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    output_dir = Path(args.output_dir)
    (output_dir / "candidates").mkdir(parents=True, exist_ok=True)
    loop_log_path = output_dir / "autoresearch_loop.log"
    started_at = datetime.now(UTC)
    deadline = time.monotonic() + args.max_hours * 3600 if args.max_hours else None
    runs: list[dict[str, Any]] = []

    if args.include_baseline:
        baseline_cmd = build_run_command(args, overlay_path=None, description="baseline")
        runs.append({"type": "baseline", "command": baseline_cmd})
        if not args.dry_run:
            code = _run_command(baseline_cmd, log_path=loop_log_path)
            if code not in {0, 1, 124}:
                raise SystemExit(code)

    for run_index in range(1, args.max_runs + 1):
        if deadline is not None and time.monotonic() >= deadline:
            break

        candidate = generate_candidate(
            run_index,
            seed=args.seed,
            symbol=args.symbol,
            families=families,
            aggregator_focus=args.aggregator_focus,
        )
        overlay_path = _candidate_path(output_dir, candidate)
        _write_yaml(overlay_path, candidate.overlay)
        command = build_run_command(
            args,
            overlay_path=overlay_path,
            description=f"{candidate.family}: {candidate.description}",
        )
        run_record = {
            "type": "candidate",
            "run_index": run_index,
            "seed": candidate.seed,
            "family": candidate.family,
            "description": candidate.description,
            "overlay_path": str(overlay_path),
            "command": command,
        }
        runs.append(run_record)

        if args.dry_run:
            continue

        code = _run_command(command, log_path=loop_log_path)
        run_record["returncode"] = code
        if code not in {0, 1, 124}:
            raise SystemExit(code)

    summary = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "config": args.config,
        "output_dir": args.output_dir,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "seed": args.seed,
        "max_runs": args.max_runs,
        "families": list(families),
        "aggregator_focus": args.aggregator_focus,
        "dry_run": args.dry_run,
        "loop_log_path": str(loop_log_path),
        "runs": runs,
    }
    summary_path = _session_summary_path(output_dir)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({"session_path": str(summary_path), "runs": len(runs)}, indent=2))


if __name__ == "__main__":
    main()
