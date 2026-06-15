#!/usr/bin/env python3
"""
Cheap feasibility probe for higher-timeframe (4h/1d) regime label predictive content
for 1h forward returns (SOL 1h base, also BTC/ETH for robustness).

Reuses existing regime feature columns (no new ingestion) via IndicatorReader
for strict no-lookahead MTF joins. Tests whether "favorable" regime bars
(trailing higher-TF regime state) exhibit materially better net-of-cost
forward expectancy than "unfavorable" bars at a small fixed horizon.

See docs/specs/higher-tf-regime-allocator-brief-v0.md for Gate 0/1 and
the explicit post-pulse (A) allocator vs (B) standalone fork.

Usage (dry / guard-driven, via manifest only after merge + human go-ahead):
  uv run python scripts/probe_higher_tf_regime.py \
    --symbols SOLUSDT,BTCUSDT,ETHUSDT \
    --base-timeframe 1h \
    --regime-timeframes 4h,1d \
    --start 2024-01-01 \
    --fee-pct 0.08 \
    --slippage-pct 0.02 \
    --verdict-output research/rbi_loop/higher-tf-regime-allocator-v0/probe-verdict.json

--smoke for tests (no DB). Never pass --execute here; real execution is post-merge
via rbi_loop_from_manifest with explicit user approval. No manifest edits in this change.
No strategy code, no autoresearch artifacts.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import asyncio
import json
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class HigherTFRegimeProbeConfig:
    symbols: tuple[str, ...]
    base_timeframe: str
    regime_timeframes: tuple[str, ...]
    start: str
    end: str
    fee_pct: float
    slippage_pct: float
    horizon_bars: int


@dataclass(frozen=True)
class SmokeReport:
    verdict: str
    note: str


@dataclass(frozen=True)
class HigherTFRegimeProbeReport:
    verdict: str
    note: str
    passing_scenarios: tuple[str, ...]
    per_bucket_stats: dict[str, Any]
    thresholds: dict[str, Any]
    config: HigherTFRegimeProbeConfig


def _cheap_smoke_test() -> SmokeReport:
    """No-DB, no-network safe default used by --smoke and tests."""
    return SmokeReport(
        verdict="NO_PULSE",
        note=(
            "SMOKE: no data path exercised. "
            "This is the expected safe default before any real probe execution "
            "(real run only post-merge via manifest with explicit user go-ahead)."
        ),
    )


def _mean(values: Sequence[float]) -> float:
    materialized = list(values)
    if not materialized:
        return 0.0
    return sum(materialized) / len(materialized)


def _median(values: Sequence[float]) -> float:
    materialized = list(values)
    if not materialized:
        return 0.0
    return float(statistics.median(materialized))


def _std(values: Sequence[float]) -> float:
    materialized = list(values)
    n = len(materialized)
    if n < 2:
        return 0.0
    m = _mean(materialized)
    var = sum((x - m) ** 2 for x in materialized) / (n - 1)
    return math.sqrt(var) if var > 0 else 0.0


def compute_bucket_expectancy(
    is_favorable: Sequence[bool | None],
    closes: Sequence[float],
    horizon: int = 6,
    fee_pct: float = 0.08,
    slippage_pct: float = 0.02,
) -> dict[str, Any]:
    """
    Pure function: given per-bar favorable labels (True/False/None) and closes,
    compute net forward returns (at horizon) for favorable vs unfavorable buckets.

    Skips bars with None label or insufficient future data. Net = gross_pct - (fee+slip).
    Returns bucket stats + delta. Used for both live probe and unit tests.
    """
    cost = fee_pct + slippage_pct
    fav_nets: list[float] = []
    unfav_nets: list[float] = []
    n = len(closes)
    for i in range(n):
        if i + horizon >= n:
            continue
        label = is_favorable[i] if i < len(is_favorable) else None
        if label is None:
            continue
        if closes[i] <= 0 or closes[i + horizon] <= 0:
            continue
        gross = (closes[i + horizon] / closes[i] - 1.0) * 100.0
        net = gross - cost
        if label:
            fav_nets.append(net)
        else:
            unfav_nets.append(net)

    def _bucket_stats(nets: list[float]) -> dict[str, float]:
        n = len(nets)
        if n == 0:
            return {
                "count": 0,
                "mean": 0.0,
                "median": 0.0,
                "sharpe": 0.0,
                "p_loss": 0.0,
                "win_rate": 0.0,
            }
        mean = _mean(nets)
        med = _median(nets)
        std = _std(nets)
        sharpe = mean / std if std > 0 else 0.0
        p_loss = (sum(1 for x in nets if x < 0) / n) * 100.0 if n else 0.0
        wr = (sum(1 for x in nets if x > 0) / n) * 100.0 if n else 0.0
        return {
            "count": float(n),
            "mean": mean,
            "median": med,
            "sharpe": sharpe,
            "p_loss": p_loss,
            "win_rate": wr,
        }

    sf = _bucket_stats(fav_nets)
    su = _bucket_stats(unfav_nets)
    delta = sf["mean"] - su["mean"]
    return {
        "favorable": sf,
        "unfavorable": su,
        "delta_mean_net": delta,
        "favorable_count": sf["count"],
        "unfavorable_count": su["count"],
    }


def _classify_favorable(row: dict[str, Any], regime_tf: str) -> bool | None:
    """No-lookahead regime label using higher-TF features (suffixed by reader join)."""
    suf = f"_{regime_tf}"
    ema_slope = row.get(f"ema_slope_50{suf}")
    vol_pct = row.get(f"volatility_percentile{suf}")
    trend_cons = row.get(f"trend_consistency{suf}")
    if ema_slope is None or vol_pct is None or trend_cons is None:
        return None
    # Mirror thresholds from regime_router / multi_timeframe_regime (defaults)
    is_trending = (
        abs(float(ema_slope)) > 0.005 and float(trend_cons) > 60.0 and float(vol_pct) > 60.0
    )
    return bool(is_trending)


def _analyze_symbol_regime(
    rows: list[dict[str, Any]],
    symbol: str,
    regime_tf: str,
    horizon: int,
    fee_pct: float,
    slippage_pct: float,
) -> dict[str, Any]:
    """Compute bucket stats + Gate 1 numeric check for one (symbol, regime_tf)."""
    if not rows:
        return {
            "symbol": symbol,
            "regime_tf": regime_tf,
            "labeled_bars": 0,
            "bucket": {
                "favorable": {"count": 0},
                "unfavorable": {"count": 0},
                "delta_mean_net": 0.0,
            },
            "meets_sample": False,
            "meets_separation": False,
            "passing": False,
        }

    closes = [float(r["close_price"]) for r in rows]
    labels: list[bool | None] = [_classify_favorable(r, regime_tf) for r in rows]

    labeled = sum(1 for lab in labels if lab is not None)
    bucket = compute_bucket_expectancy(labels, closes, horizon, fee_pct, slippage_pct)

    fav_c = int(bucket["favorable_count"])
    unf_c = int(bucket["unfavorable_count"])
    delta = float(bucket["delta_mean_net"])
    sf = bucket["favorable"]
    su = bucket["unfavorable"]

    meets_sample = fav_c >= 200 and unf_c >= 200
    # Separation: delta >= 0.15 (15 bps in our % units) + (sharpe sign or p_loss 8pp)
    sep_delta = delta >= 0.15
    sharpe_sign = sf["sharpe"] > 0 and su["sharpe"] <= 0
    ploss_diff = (su["p_loss"] - sf["p_loss"]) >= 8.0
    meets_separation = sep_delta and (sharpe_sign or ploss_diff)

    passing = meets_sample and meets_separation

    return {
        "symbol": symbol,
        "regime_tf": regime_tf,
        "labeled_bars": labeled,
        "bucket": bucket,
        "meets_sample": meets_sample,
        "meets_separation": meets_separation,
        "passing": passing,
        "delta_bps_like": delta * 100.0,  # for reporting if wanted
    }


def analyze_higher_tf_regime(
    rows_by_sym_rtf: dict[tuple[str, str], list[dict[str, Any]]],
    config: HigherTFRegimeProbeConfig,
) -> HigherTFRegimeProbeReport:
    """Core analysis over pre-fetched joined rows. Pure of DB after fetch."""
    all_passing: list[str] = []
    all_stats: dict[str, Any] = {}
    per_symbol_passing_count: dict[str, int] = dict.fromkeys(config.symbols, 0)

    thresholds = {
        "min_bucket_bars": 200,
        "min_mean_delta_pct": 0.15,
        "min_p_loss_diff_pp": 8.0,
        "horizon_bars": config.horizon_bars,
    }

    for (symbol, rtf), rows in rows_by_sym_rtf.items():
        res = _analyze_symbol_regime(
            rows, symbol, rtf, config.horizon_bars, config.fee_pct, config.slippage_pct
        )
        key = f"{symbol}:{rtf}"
        all_stats[key] = res
        if res["passing"]:
            all_passing.append(
                f"{symbol}:{rtf}:delta_{res['delta_mean_net']:.4f}:h{config.horizon_bars}"
            )
            per_symbol_passing_count[symbol] = per_symbol_passing_count.get(symbol, 0) + 1

    # Aggregate verdict
    # Robustness: >=2 symbols have at least one passing rtf, or (for primary) subperiods (simplified: use symbol count)
    symbols_with_pulse = sum(1 for c in per_symbol_passing_count.values() if c > 0)
    robustness = symbols_with_pulse >= 2

    has_any_passing = len(all_passing) > 0
    # For full HAS we also need sample+sep already encoded in passing + robustness
    if has_any_passing and robustness:
        verdict = "HAS_PULSE"
        note = "Higher-TF regime probe: favorable bucket shows material net edge vs unfavorable with adequate sample and cross-symbol robustness (see brief)."
    elif has_any_passing:
        verdict = "WEAK_EDGE"
        note = "Separation observed but sample <200 in a bucket or insufficient robustness (>=2 symbols or sub-periods)."
    else:
        verdict = "NO_PULSE"
        note = (
            "No (symbol, regime_tf) met the full Gate 1 numeric thresholds (sample + separation)."
        )

    return HigherTFRegimeProbeReport(
        verdict=verdict,
        note=note,
        passing_scenarios=tuple(sorted(set(all_passing))),
        per_bucket_stats=all_stats,
        thresholds=thresholds,
        config=config,
    )


def print_higher_tf_regime_report(
    report: HigherTFRegimeProbeReport, config: HigherTFRegimeProbeConfig
) -> None:
    """Human-readable report block only (prints allowed here per coding rules)."""
    print("Higher-TF Regime Allocator Probe (Gate 0/1 pre-pulse)")
    print(f"Symbols:          {', '.join(config.symbols)}")
    print(f"Base timeframe:   {config.base_timeframe}")
    print(f"Regime TFs:       {', '.join(config.regime_timeframes)}")
    print(f"Window:           {config.start} -> {config.end}")
    print(f"Fee+slip:         {config.fee_pct + config.slippage_pct:.2f}%")
    print(f"Horizon (bars):   {config.horizon_bars}")
    print(f"Verdict:          {report.verdict}")
    print(f"Note:             {report.note}")
    if report.passing_scenarios:
        print("Passing scenarios (met sample + separation for this horizon):")
        for label in report.passing_scenarios:
            print(f"  {label}")
    else:
        print("No scenarios passed full Gate 1 thresholds.")
    # Compact per-bucket summary
    print("\nPer (symbol:rtf) bucket summary (counts after labeling; delta in net % points):")
    for key, res in sorted(report.per_bucket_stats.items()):
        b = res["bucket"]
        print(
            f"  {key}: labeled={res['labeled_bars']}, "
            f"fav={int(b['favorable']['count'])}, unfav={int(b['unfavorable']['count'])}, "
            f"delta={b['delta_mean_net']:.4f}, "
            f"sample_ok={res['meets_sample']}, sep_ok={res['meets_separation']}"
        )


def _write_verdict(
    verdict: str,
    note: str,
    passing: tuple[str, ...],
    per_bucket_stats: dict[str, Any],
    thresholds: dict[str, Any],
    config: HigherTFRegimeProbeConfig,
    path: str | None,
) -> None:
    if not path:
        return
    payload = {
        "verdict": verdict,
        "note": note,
        "passing_scenarios": list(passing),
        "per_bucket_stats": per_bucket_stats,
        "thresholds": thresholds,
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {
            "symbols": list(config.symbols),
            "base_timeframe": config.base_timeframe,
            "regime_timeframes": list(config.regime_timeframes),
            "start": config.start,
            "end": config.end,
            "fee_pct": config.fee_pct,
            "slippage_pct": config.slippage_pct,
            "horizon_bars": config.horizon_bars,
        },
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote guard-consumable verdict to {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Higher-TF regime allocator cheap probe (RBI Gate 0/1 only)."
    )
    parser.add_argument("--symbols", default="SOLUSDT,BTCUSDT,ETHUSDT")
    parser.add_argument("--base-timeframe", default="1h")
    parser.add_argument("--regime-timeframes", default="4h,1d")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--fee-pct", type=float, default=0.08)
    parser.add_argument("--slippage-pct", type=float, default=0.02)
    parser.add_argument("--horizon-bars", type=int, default=6)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="No-DB safe default path (always NO_PULSE); used by tests",
    )
    parser.add_argument(
        "--verdict-output",
        default=None,
        help="Write guard-consumable verdict JSON to this path",
    )
    return parser.parse_args()


def _config_from_args(args: argparse.Namespace) -> HigherTFRegimeProbeConfig:
    symbols = tuple(s.strip() for s in args.symbols.split(",") if s.strip())
    rtfs = tuple(r.strip() for r in args.regime_timeframes.split(",") if r.strip())
    end = args.end or datetime.now(UTC).date().isoformat()
    return HigherTFRegimeProbeConfig(
        symbols=symbols,
        base_timeframe=args.base_timeframe,
        regime_timeframes=rtfs,
        start=args.start,
        end=end,
        fee_pct=args.fee_pct,
        slippage_pct=args.slippage_pct,
        horizon_bars=args.horizon_bars,
    )


async def main_async() -> None:
    args = parse_args()
    config = _config_from_args(args)

    if args.smoke:
        report = _cheap_smoke_test()
        print(f"Verdict:   {report.verdict}")
        print(f"Note:      {report.note}")
        _write_verdict(
            report.verdict,
            report.note,
            (),
            {},
            {
                "min_bucket_bars": 200,
                "min_mean_delta_pct": 0.15,
                "horizon_bars": config.horizon_bars,
            },
            config,
            args.verdict_output,
        )
        return

    # Lazy heavy imports only on real path
    from scripts.probe_basis_premium import build_db_config
    from src.db import close_pool, init_pool
    from src.features.reader import IndicatorReader
    from src.utils.logger import configure_logger

    configure_logger("WARNING")
    db_cfg = build_db_config()
    await init_pool(db_cfg)
    try:
        reader = IndicatorReader(db_cfg)
        start_dt = datetime.fromisoformat(config.start)
        end_dt = datetime.fromisoformat(config.end)

        rows_by_sym_rtf: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for symbol in config.symbols:
            for rtf in config.regime_timeframes:
                try:
                    rows = await reader.fetch_multi_timeframe(
                        symbol, config.base_timeframe, rtf, start_dt, end_dt
                    )
                    rows_by_sym_rtf[(symbol, rtf)] = rows
                    logger.info(
                        "Fetched %d joined rows for %s base=%s regime=%s",
                        len(rows),
                        symbol,
                        config.base_timeframe,
                        rtf,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Fetch failed for %s/%s: %s", symbol, rtf, exc)
                    rows_by_sym_rtf[(symbol, rtf)] = []

        report = analyze_higher_tf_regime(rows_by_sym_rtf, config)
        print_higher_tf_regime_report(report, config)
        _write_verdict(
            report.verdict,
            report.note,
            report.passing_scenarios,
            report.per_bucket_stats,
            report.thresholds,
            config,
            args.verdict_output,
        )
    finally:
        await close_pool()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
