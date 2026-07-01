#!/usr/bin/env python3
"""Cheap feasibility probe for cross-asset / TradFi risk-regime forward drift (Gate 1).

Step 0: data-feasibility audit on frozen TradFi proxy CSVs (yfinance).
Step 1: align TradFi to BTC/ETH/SOL 1h OHLCV point-in-time in UTC.
Step 2: H1 lead-lag, H2 regime conditioning, H3 weekend gap (secondary).

Headline metric isolates FORWARD predictability vs matched baseline AND vs
contemporaneous co-movement. See docs/specs/cross-asset-risk-regime-probe-v0.md.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.probe_higher_tf_trend_following import build_db_config
from scripts.probe_macro_event_drift import (
    HourlyBar,
    forward_return_pct,
)
from src.db import close_pool, init_pool
from src.utils.logger import configure_logger, get_logger

DEFAULT_TRADFI_DIR = Path(__file__).resolve().parent.parent / "data/tradfi"

BLOCKED_ON_DATA = "BLOCKED_ON_DATA"
HORIZONS_HOURS = (6, 24)
FEE_NOISE_BAR_PCT = 0.3
MIN_SYMBOLS_PASS = 2
CONTEMP_MARGIN = 0.05
RANDOM_BASELINE_SEED = 42
SMA_LOOKBACK_DAYS = 20
US10Y_DIRECTION_DAYS = 5
VIX_HIGH_THRESHOLD = 20.0

FROZEN_PROXIES: dict[str, dict[str, str]] = {
    "equity_risk": {
        "ticker": "QQQ",
        "daily_csv": "equity_risk_1d.csv",
        "hourly_csv": "equity_risk_1h.csv",
    },
    "dxy": {"ticker": "DX-Y.NYB", "daily_csv": "dxy_1d.csv", "hourly_csv": "dxy_1h.csv"},
    "us10y": {"ticker": "^TNX", "daily_csv": "us10y_1d.csv", "hourly_csv": "us10y_1h.csv"},
    "vix": {"ticker": "^VIX", "daily_csv": "vix_1d.csv", "hourly_csv": "vix_1h.csv"},
}

# Ex-ante expected sign: prior proxy move -> crypto forward return (H1).
EXPECTED_SIGN_H1: dict[str, int] = {
    "equity_risk": +1,
    "dxy": -1,
    "us10y": -1,
    "vix": -1,
}

# Ex-ante expected sign: regime state -> crypto forward return (H2).
EXPECTED_SIGN_H2: dict[str, int] = {
    "risk_on": +1,
    "dxy_strong": -1,
    "us10y_rising": -1,
    "vix_high": -1,
}

PROBE_QUERY = """
    SELECT time, close_price
    FROM ohlcv
    WHERE symbol = $1
      AND timeframe = $2
      AND time >= $3
      AND time <= $4
    ORDER BY time ASC
"""


@dataclass(frozen=True)
class TradFiBar:
    proxy: str
    source_ticker: str
    granularity: str
    bar_open_utc: datetime
    close_ts_utc: datetime
    close: float
    is_weekend_gap_after: bool


@dataclass(frozen=True)
class ProbeConfig:
    symbols: tuple[str, ...]
    timeframe: str
    start: str
    end: str
    tradfi_dir: Path
    horizons_hours: tuple[int, ...]
    fee_noise_bar_pct: float
    min_symbols_pass: int
    random_baseline_seed: int
    sma_lookback_days: int
    vix_high_threshold: float


@dataclass(frozen=True)
class ProxySeriesAudit:
    proxy: str
    source_ticker: str
    daily_rows: int
    hourly_rows: int
    daily_start: str
    daily_end: str
    hourly_start: str | None
    hourly_end: str | None
    close_convention: str
    weekend_gaps_daily: int
    weekend_gaps_hourly: int


@dataclass(frozen=True)
class DataAudit:
    proxies: tuple[ProxySeriesAudit, ...]
    equity_risk_available: bool
    blocked: bool
    blocked_reason: str | None
    notes: tuple[str, ...]


@dataclass(frozen=True)
class H1HorizonMetrics:
    horizon_hours: int
    observation_count: int
    predictive_rank_corr: float
    contemporaneous_rank_corr: float
    oriented_bucket_spread_pct: float
    baseline_spread_pct: float
    excess_vs_baseline_pct: float
    excess_vs_contemporaneous: float
    high_bucket_mean_pct: float
    low_bucket_mean_pct: float
    h1_pass: bool


@dataclass(frozen=True)
class H1SymbolResult:
    proxy: str
    symbol: str
    granularity: str
    horizons: tuple[H1HorizonMetrics, ...]


@dataclass(frozen=True)
class H2HorizonMetrics:
    horizon_hours: int
    regime: str
    favorable_count: int
    unfavorable_count: int
    favorable_mean_pct: float
    unfavorable_mean_pct: float
    oriented_spread_pct: float
    baseline_spread_pct: float
    excess_vs_baseline_pct: float
    h2_pass: bool


@dataclass(frozen=True)
class H2SymbolResult:
    regime: str
    symbol: str
    horizons: tuple[H2HorizonMetrics, ...]


@dataclass(frozen=True)
class H3Metrics:
    observation_count: int
    friday_risk_off_mean_weekend_pct: float
    friday_risk_on_mean_weekend_pct: float
    oriented_spread_pct: float
    expected_sign: int


@dataclass(frozen=True)
class ProbeReport:
    config: ProbeConfig
    data_audit: DataAudit
    h1_results: tuple[H1SymbolResult, ...]
    h2_results: tuple[H2SymbolResult, ...]
    h3: H3Metrics | None
    status: str
    verdict: str
    reasons: tuple[str, ...]


def _to_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _parse_ts(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return _to_utc(datetime.fromisoformat(text))


def _mean(values: Sequence[float]) -> float:
    materialized = list(values)
    if not materialized:
        return 0.0
    return sum(materialized) / len(materialized)


def load_tradfi_bars(csv_path: Path) -> tuple[TradFiBar, ...]:
    if not csv_path.is_file():
        return ()
    rows: list[TradFiBar] = []
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append(
                TradFiBar(
                    proxy=row["proxy"].strip(),
                    source_ticker=row["source_ticker"].strip(),
                    granularity=row["granularity"].strip(),
                    bar_open_utc=_parse_ts(row["bar_open_utc"]),
                    close_ts_utc=_parse_ts(row["close_ts_utc"]),
                    close=float(row["close"]),
                    is_weekend_gap_after=row["is_weekend_gap_after"].strip().lower() == "true",
                )
            )
    return tuple(sorted(rows, key=lambda bar: bar.close_ts_utc))


def audit_tradfi_data(tradfi_dir: Path) -> DataAudit:
    audits: list[ProxySeriesAudit] = []
    notes: list[str] = []
    equity_ok = False

    close_conventions = {
        "equity_risk": "QQQ daily close 16:00 America/New_York -> UTC",
        "dxy": "DXY daily close 17:00 America/New_York -> UTC",
        "us10y": "^TNX daily close 16:00 America/Chicago -> UTC",
        "vix": "^VIX daily close 16:00 America/Chicago -> UTC",
    }

    for proxy, meta in FROZEN_PROXIES.items():
        daily = load_tradfi_bars(tradfi_dir / meta["daily_csv"])
        hourly = load_tradfi_bars(tradfi_dir / meta["hourly_csv"])
        if proxy == "equity_risk" and len(daily) >= 400:
            equity_ok = True
        audits.append(
            ProxySeriesAudit(
                proxy=proxy,
                source_ticker=meta["ticker"],
                daily_rows=len(daily),
                hourly_rows=len(hourly),
                daily_start=daily[0].close_ts_utc.date().isoformat() if daily else "none",
                daily_end=daily[-1].close_ts_utc.date().isoformat() if daily else "none",
                hourly_start=hourly[0].close_ts_utc.date().isoformat() if hourly else None,
                hourly_end=hourly[-1].close_ts_utc.date().isoformat() if hourly else None,
                close_convention=close_conventions[proxy],
                weekend_gaps_daily=sum(1 for bar in daily if bar.is_weekend_gap_after),
                weekend_gaps_hourly=sum(1 for bar in hourly if bar.is_weekend_gap_after),
            )
        )
        if hourly:
            notes.append(
                f"{proxy}: 1h Yahoo cap ~730d ({hourly[0].close_ts_utc.date()}.."
                f"{hourly[-1].close_ts_utc.date()}); daily full window {len(daily)} rows"
            )
        else:
            notes.append(f"{proxy}: daily only ({len(daily)} rows)")

    blocked = not equity_ok
    blocked_reason: str | None = None
    if blocked:
        blocked_reason = "equity risk proxy (QQQ) daily series unavailable or insufficient coverage"

    notes.append(
        "Yahoo 1h history capped at ~730d; H1/H2 primary alignment uses daily closes for full "
        "2024-01-01..2026-06-01 window. 1h series reported separately where overlapping."
    )
    notes.append(
        "Weekend/holiday gaps flagged via is_weekend_gap_after; TradFi levels are NOT "
        "forward-filled across closed sessions."
    )

    return DataAudit(
        proxies=tuple(audits),
        equity_risk_available=equity_ok,
        blocked=blocked,
        blocked_reason=blocked_reason,
        notes=tuple(notes),
    )


def crypto_entry_after_tradfi_close(bars: Sequence[HourlyBar], close_ts: datetime) -> int | None:
    """First crypto bar open strictly after TradFi close (point-in-time)."""
    cutoff = _to_utc(close_ts)
    for index, bar in enumerate(bars):
        if _to_utc(bar.time) > cutoff:
            return index
    return None


def latest_tradfi_bar_before(bars: Sequence[TradFiBar], ts: datetime) -> TradFiBar | None:
    cutoff = _to_utc(ts)
    result: TradFiBar | None = None
    for bar in bars:
        if bar.close_ts_utc < cutoff:
            result = bar
        else:
            break
    return result


def tradfi_return_during_window(
    bars: Sequence[TradFiBar],
    start_ts: datetime,
    end_ts: datetime,
) -> float | None:
    """TradFi return over (start_ts, end_ts] using only bars known by end_ts."""
    start_bar = latest_tradfi_bar_before(bars, start_ts)
    end_bar = latest_tradfi_bar_before(bars, end_ts)
    if start_bar is None or end_bar is None or start_bar.close <= 0:
        return None
    if end_bar.close_ts_utc <= start_bar.close_ts_utc:
        return 0.0
    return (end_bar.close / start_bar.close - 1.0) * 100.0


def _rank_values(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda idx: values[idx])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(values):
        start = index
        while index < len(values) and values[order[index]] == values[order[start]]:
            index += 1
        avg_rank = (start + index - 1) / 2.0 + 1.0
        for position in range(start, index):
            ranks[order[position]] = avg_rank
    return ranks


def _rank_correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
    if len(xs) < 3 or len(xs) != len(ys):
        return 0.0
    rank_x = _rank_values(xs)
    rank_y = _rank_values(values=ys)
    mean_x = sum(rank_x) / len(rank_x)
    mean_y = sum(rank_y) / len(rank_y)
    num = sum((rx - mean_x) * (ry - mean_y) for rx, ry in zip(rank_x, rank_y, strict=True))
    den_x = sum((rx - mean_x) ** 2 for rx in rank_x)
    den_y = sum((ry - mean_y) ** 2 for ry in rank_y)
    if den_x <= 0 or den_y <= 0:
        return 0.0
    return num / (den_x * den_y) ** 0.5


def _tertile_bucket_spread(
    drivers: Sequence[float],
    outcomes: Sequence[float],
    *,
    expected_sign: int,
) -> tuple[float, float, float, int]:
    if len(drivers) < 6:
        return 0.0, 0.0, 0.0, len(drivers)
    paired = sorted(zip(drivers, outcomes, strict=True), key=lambda item: item[0])
    third = len(paired) // 3
    low = [outcome for _, outcome in paired[:third]]
    high = [outcome for _, outcome in paired[-third:]]
    spread = _mean(high) - _mean(low)
    return expected_sign * spread, _mean(high), _mean(low), len(paired)


def _baseline_spread(
    crypto_bars: Sequence[HourlyBar],
    tradfi_bars: Sequence[TradFiBar],
    *,
    horizon_hours: int,
    seed: int,
    sample_count: int,
) -> float:
    closes = [bar.close_price for bar in crypto_bars]
    max_horizon = horizon_hours
    candidates = list(range(50, len(closes) - max_horizon))
    if len(candidates) < 8:
        return 0.0
    rng = random.Random(seed)
    picks = rng.sample(candidates, min(sample_count, len(candidates)))
    drivers: list[float] = []
    outcomes: list[float] = []
    for idx in picks:
        entry_ts = _to_utc(crypto_bars[idx].time)
        prior_bar = latest_tradfi_bar_before(tradfi_bars, entry_ts)
        prev_bar = (
            latest_tradfi_bar_before(tradfi_bars, prior_bar.close_ts_utc) if prior_bar else None
        )
        if prior_bar is None or prev_bar is None or prev_bar.close <= 0:
            continue
        prior_move = (prior_bar.close / prev_bar.close - 1.0) * 100.0
        fwd = forward_return_pct(closes, idx, horizon_hours)
        if fwd is None:
            continue
        drivers.append(prior_move)
        outcomes.append(fwd)
    spread, _, _, _ = _tertile_bucket_spread(drivers, outcomes, expected_sign=1)
    return abs(spread)


def _collect_h1_observations(
    crypto_bars: Sequence[HourlyBar],
    tradfi_bars: Sequence[TradFiBar],
    *,
    horizons_hours: Sequence[int],
) -> list[dict[str, float | int | datetime]]:
    closes = [bar.close_price for bar in crypto_bars]
    max_horizon = max(horizons_hours)
    observations: list[dict[str, float | int | datetime]] = []

    for index in range(1, len(tradfi_bars)):
        prior = tradfi_bars[index - 1]
        current = tradfi_bars[index]
        if prior.close <= 0:
            continue
        prior_move = (current.close / prior.close - 1.0) * 100.0
        entry_idx = crypto_entry_after_tradfi_close(crypto_bars, current.close_ts_utc)
        if entry_idx is None or entry_idx + max_horizon >= len(closes):
            continue
        entry_ts = _to_utc(crypto_bars[entry_idx].time)
        if entry_ts <= current.close_ts_utc:
            continue
        for horizon in horizons_hours:
            fwd = forward_return_pct(closes, entry_idx, horizon)
            if fwd is None:
                continue
            window_end = entry_ts + timedelta(hours=horizon)
            contemp = tradfi_return_during_window(tradfi_bars, entry_ts, window_end)
            if contemp is None:
                continue
            observations.append(
                {
                    "prior_move": prior_move,
                    "forward_return": fwd,
                    "contemporaneous": contemp,
                    "entry_idx": entry_idx,
                    "horizon": horizon,
                    "close_ts": current.close_ts_utc,
                    "weekend_gap": float(current.is_weekend_gap_after),
                }
            )
    return observations


def evaluate_h1_symbol(
    crypto_bars: Sequence[HourlyBar],
    tradfi_bars: Sequence[TradFiBar],
    config: ProbeConfig,
    *,
    proxy: str,
    symbol: str,
    granularity: str,
) -> H1SymbolResult:
    expected_sign = EXPECTED_SIGN_H1[proxy]
    observations = _collect_h1_observations(
        crypto_bars,
        tradfi_bars,
        horizons_hours=config.horizons_hours,
    )
    horizon_metrics: list[H1HorizonMetrics] = []

    for horizon in config.horizons_hours:
        subset = [item for item in observations if int(item["horizon"]) == horizon]
        prior_moves = [float(item["prior_move"]) for item in subset]
        forward_returns = [float(item["forward_return"]) for item in subset]
        contemp_returns = [float(item["contemporaneous"]) for item in subset]

        predictive_rank = _rank_correlation(prior_moves, forward_returns)
        contemporaneous_rank = _rank_correlation(contemp_returns, forward_returns)
        oriented_spread, high_mean, low_mean, count = _tertile_bucket_spread(
            prior_moves,
            forward_returns,
            expected_sign=expected_sign,
        )
        baseline = _baseline_spread(
            crypto_bars,
            tradfi_bars,
            horizon_hours=horizon,
            seed=config.random_baseline_seed + hash((symbol, proxy, granularity)) % 10_000,
            sample_count=max(len(subset), 20),
        )
        excess_baseline = oriented_spread - baseline
        excess_contemp = (predictive_rank * expected_sign) - abs(contemporaneous_rank)

        sign_consistent = (
            count >= 6 and high_mean * expected_sign > 0 and low_mean * expected_sign < 0
        )
        h1_pass = (
            sign_consistent
            and excess_baseline > config.fee_noise_bar_pct
            and excess_contemp > CONTEMP_MARGIN
        )

        horizon_metrics.append(
            H1HorizonMetrics(
                horizon_hours=horizon,
                observation_count=count,
                predictive_rank_corr=predictive_rank,
                contemporaneous_rank_corr=contemporaneous_rank,
                oriented_bucket_spread_pct=oriented_spread,
                baseline_spread_pct=baseline,
                excess_vs_baseline_pct=excess_baseline,
                excess_vs_contemporaneous=excess_contemp,
                high_bucket_mean_pct=high_mean,
                low_bucket_mean_pct=low_mean,
                h1_pass=h1_pass,
            )
        )

    return H1SymbolResult(
        proxy=proxy,
        symbol=symbol,
        granularity=granularity,
        horizons=tuple(horizon_metrics),
    )


def _sma(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _regime_states_at_close(
    close_date: str,
    *,
    equity_daily: Sequence[TradFiBar],
    dxy_daily: Sequence[TradFiBar],
    us10y_daily: Sequence[TradFiBar],
    vix_daily: Sequence[TradFiBar],
    lookback: int,
) -> dict[str, bool] | None:
    equity_closes = [bar.close for bar in equity_daily]
    dxy_closes = [bar.close for bar in dxy_daily]
    us10y_closes = [bar.close for bar in us10y_daily]
    vix_closes = [bar.close for bar in vix_daily]

    index = next(
        (
            idx
            for idx, bar in enumerate(equity_daily)
            if bar.close_ts_utc.date().isoformat() == close_date
        ),
        None,
    )
    if index is None or index < lookback:
        return None

    risk_on = equity_closes[index] > _sma(equity_closes[index - lookback : index])
    dxy_strong = dxy_closes[index] > _sma(dxy_closes[index - lookback : index])
    us10y_rising = us10y_closes[index] > us10y_closes[index - US10Y_DIRECTION_DAYS]
    vix_high = vix_closes[index] >= VIX_HIGH_THRESHOLD

    return {
        "risk_on": risk_on,
        "dxy_strong": dxy_strong,
        "us10y_rising": us10y_rising,
        "vix_high": vix_high,
    }


def evaluate_h2_symbol(
    crypto_bars: Sequence[HourlyBar],
    tradfi_by_proxy: dict[str, tuple[TradFiBar, ...]],
    config: ProbeConfig,
    *,
    regime: str,
    symbol: str,
) -> H2SymbolResult:
    equity = tradfi_by_proxy["equity_risk"]
    dxy = tradfi_by_proxy["dxy"]
    us10y = tradfi_by_proxy["us10y"]
    vix = tradfi_by_proxy["vix"]
    expected_sign = EXPECTED_SIGN_H2[regime]
    closes = [bar.close_price for bar in crypto_bars]
    max_horizon = max(config.horizons_hours)

    horizon_metrics: list[H2HorizonMetrics] = []
    for horizon in config.horizons_hours:
        favorable: list[float] = []
        unfavorable: list[float] = []

        for index in range(1, len(equity)):
            current = equity[index]
            states = _regime_states_at_close(
                current.close_ts_utc.date().isoformat(),
                equity_daily=equity,
                dxy_daily=dxy,
                us10y_daily=us10y,
                vix_daily=vix,
                lookback=config.sma_lookback_days,
            )
            if states is None:
                continue
            active = states[regime]
            entry_idx = crypto_entry_after_tradfi_close(crypto_bars, current.close_ts_utc)
            if entry_idx is None or entry_idx + max_horizon >= len(closes):
                continue
            fwd = forward_return_pct(closes, entry_idx, horizon)
            if fwd is None:
                continue
            if active:
                favorable.append(fwd)
            else:
                unfavorable.append(fwd)

        fav_mean = _mean(favorable)
        unfav_mean = _mean(unfavorable)
        oriented_spread = expected_sign * (fav_mean - unfav_mean)
        baseline = _baseline_spread(
            crypto_bars,
            equity,
            horizon_hours=horizon,
            seed=config.random_baseline_seed + hash((symbol, regime)) % 10_000,
            sample_count=max(len(favorable) + len(unfavorable), 20),
        )
        excess = oriented_spread - baseline
        sign_ok = (
            len(favorable) >= 3
            and len(unfavorable) >= 3
            and fav_mean * expected_sign > 0
            and unfav_mean * expected_sign < 0
        )
        h2_pass = sign_ok and excess > config.fee_noise_bar_pct

        horizon_metrics.append(
            H2HorizonMetrics(
                horizon_hours=horizon,
                regime=regime,
                favorable_count=len(favorable),
                unfavorable_count=len(unfavorable),
                favorable_mean_pct=fav_mean,
                unfavorable_mean_pct=unfav_mean,
                oriented_spread_pct=oriented_spread,
                baseline_spread_pct=baseline,
                excess_vs_baseline_pct=excess,
                h2_pass=h2_pass,
            )
        )

    return H2SymbolResult(regime=regime, symbol=symbol, horizons=tuple(horizon_metrics))


def evaluate_h3_weekend(
    crypto_bars: Sequence[HourlyBar],
    equity_daily: Sequence[TradFiBar],
) -> H3Metrics | None:
    """Friday equity close -> crypto weekend drift (Sat 00:00 UTC to Mon first bar)."""
    closes = [bar.close_price for bar in crypto_bars]
    weekend_returns: list[tuple[float, bool]] = []

    for index in range(1, len(equity_daily)):
        current = equity_daily[index]
        prior = equity_daily[index - 1]
        if not current.is_weekend_gap_after:
            continue
        if prior.close <= 0:
            continue
        friday_move = (current.close / prior.close - 1.0) * 100.0
        risk_off = friday_move < 0

        friday_entry = crypto_entry_after_tradfi_close(crypto_bars, current.close_ts_utc)
        if friday_entry is None:
            continue
        monday_idx = None
        friday_date = current.close_ts_utc.date()
        for scan in range(friday_entry + 1, len(crypto_bars)):
            bar_date = _to_utc(crypto_bars[scan].time).date()
            if bar_date > friday_date + timedelta(days=2):
                monday_idx = scan
                break
        if monday_idx is None or monday_idx >= len(closes):
            continue
        if closes[friday_entry] <= 0:
            continue
        weekend_ret = (closes[monday_idx] / closes[friday_entry] - 1.0) * 100.0
        weekend_returns.append((weekend_ret, risk_off))

    if len(weekend_returns) < 4:
        return None

    risk_off_rets = [ret for ret, off in weekend_returns if off]
    risk_on_rets = [ret for ret, off in weekend_returns if not off]
    spread = _mean(risk_off_rets) - _mean(risk_on_rets)
    return H3Metrics(
        observation_count=len(weekend_returns),
        friday_risk_off_mean_weekend_pct=_mean(risk_off_rets),
        friday_risk_on_mean_weekend_pct=_mean(risk_on_rets),
        oriented_spread_pct=spread,
        expected_sign=-1,
    )


def decide_verdict(
    h1_results: Sequence[H1SymbolResult],
    h2_results: Sequence[H2SymbolResult],
    *,
    data_blocked: bool,
    config: ProbeConfig,
) -> tuple[str, str, tuple[str, ...]]:
    if data_blocked:
        return (
            BLOCKED_ON_DATA,
            BLOCKED_ON_DATA,
            ("TradFi data gate failed — edge test not run",),
        )

    reasons: list[str] = []
    h1_pass_any = False
    h2_pass_any = False

    for proxy in FROZEN_PROXIES:
        for horizon in config.horizons_hours:
            symbols_pass = sum(
                1
                for result in h1_results
                if result.proxy == proxy
                and any(item.horizon_hours == horizon and item.h1_pass for item in result.horizons)
            )
            if symbols_pass >= config.min_symbols_pass:
                h1_pass_any = True
                reasons.append(
                    f"H1 pass: {proxy} +{horizon}h on {symbols_pass}/3 symbols "
                    f"(expected sign {EXPECTED_SIGN_H1[proxy]:+d})"
                )

    for regime in EXPECTED_SIGN_H2:
        for horizon in config.horizons_hours:
            symbols_pass = sum(
                1
                for result in h2_results
                if result.regime == regime
                and any(item.horizon_hours == horizon and item.h2_pass for item in result.horizons)
            )
            if symbols_pass >= config.min_symbols_pass:
                h2_pass_any = True
                reasons.append(
                    f"H2 pass: {regime} +{horizon}h on {symbols_pass}/3 symbols "
                    f"(expected sign {EXPECTED_SIGN_H2[regime]:+d})"
                )

    if h1_pass_any and h2_pass_any:
        return ("OK", "HAS_PULSE", tuple(reasons))
    if h1_pass_any:
        return ("OK", "HAS_PULSE", tuple(reasons))
    if h2_pass_any:
        reasons.append("H1 lead-lag did not clear fee bar broadly; H2 regime filter only")
        return ("OK", "WEAK_EDGE", tuple(reasons))

    weak_h1 = any(
        item.excess_vs_baseline_pct > 0
        and item.predictive_rank_corr * EXPECTED_SIGN_H1[result.proxy] > 0
        for result in h1_results
        for item in result.horizons
    )
    weak_h2 = any(
        item.oriented_spread_pct * EXPECTED_SIGN_H2[result.regime] > 0
        for result in h2_results
        for item in result.horizons
    )
    if weak_h1 or weak_h2:
        reasons.append(
            "relationship present but below fee bar or contemporaneous separation threshold"
        )
        return ("OK", "WEAK_EDGE", tuple(reasons))

    reasons.append("no forward predictability beyond contemporaneous co-movement after fee bar")
    return ("OK", "NO_PULSE", tuple(reasons))


def default_config() -> ProbeConfig:
    return ProbeConfig(
        symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        timeframe="1h",
        start="2024-01-01T00:00:00",
        end="2026-06-01T00:00:00",
        tradfi_dir=DEFAULT_TRADFI_DIR,
        horizons_hours=HORIZONS_HOURS,
        fee_noise_bar_pct=FEE_NOISE_BAR_PCT,
        min_symbols_pass=MIN_SYMBOLS_PASS,
        random_baseline_seed=RANDOM_BASELINE_SEED,
        sma_lookback_days=SMA_LOOKBACK_DAYS,
        vix_high_threshold=VIX_HIGH_THRESHOLD,
    )


async def load_symbol_bars(symbol: str, config: ProbeConfig) -> list[HourlyBar]:
    pool = await init_pool(build_db_config())
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                PROBE_QUERY,
                symbol,
                config.timeframe,
                datetime.fromisoformat(config.start),
                datetime.fromisoformat(config.end),
            )
        bars: list[HourlyBar] = []
        for row in rows:
            bar_time = row["time"]
            close = float(row["close_price"])
            if isinstance(bar_time, datetime) and close > 0:
                bars.append(HourlyBar(time=_to_utc(bar_time), close_price=close))
        return bars
    finally:
        await close_pool()


async def run_probe(config: ProbeConfig | None = None) -> ProbeReport:
    configure_logger("INFO")
    logger = get_logger("probe.cross_asset_risk_regime")
    config = config or default_config()

    data_audit = audit_tradfi_data(config.tradfi_dir)
    logger.info(
        "data audit: equity_ok=%s blocked=%s", data_audit.equity_risk_available, data_audit.blocked
    )

    if data_audit.blocked:
        status, verdict, reasons = decide_verdict((), (), data_blocked=True, config=config)
        return ProbeReport(
            config=config,
            data_audit=data_audit,
            h1_results=(),
            h2_results=(),
            h3=None,
            status=status,
            verdict=verdict,
            reasons=reasons,
        )

    tradfi_by_proxy = {
        proxy: load_tradfi_bars(config.tradfi_dir / meta["daily_csv"])
        for proxy, meta in FROZEN_PROXIES.items()
    }

    h1_results: list[H1SymbolResult] = []
    h2_results: list[H2SymbolResult] = []
    h3: H3Metrics | None = None

    for symbol in config.symbols:
        bars = await load_symbol_bars(symbol, config)
        logger.info("%s: loaded %d 1h bars", symbol, len(bars))
        for proxy in FROZEN_PROXIES:
            daily = tradfi_by_proxy[proxy]
            h1_results.append(
                evaluate_h1_symbol(
                    bars,
                    daily,
                    config,
                    proxy=proxy,
                    symbol=symbol,
                    granularity="1d",
                )
            )
        for regime in EXPECTED_SIGN_H2:
            h2_results.append(
                evaluate_h2_symbol(
                    bars,
                    tradfi_by_proxy,
                    config,
                    regime=regime,
                    symbol=symbol,
                )
            )
        if h3 is None:
            h3 = evaluate_h3_weekend(bars, tradfi_by_proxy["equity_risk"])

    status, verdict, reasons = decide_verdict(
        h1_results, h2_results, data_blocked=False, config=config
    )
    return ProbeReport(
        config=config,
        data_audit=data_audit,
        h1_results=tuple(h1_results),
        h2_results=tuple(h2_results),
        h3=h3,
        status=status,
        verdict=verdict,
        reasons=reasons,
    )


def render_report(report: ProbeReport) -> str:
    lines: list[str] = []
    lines.append("# Cross-Asset / TradFi Risk-Regime Probe — Report")
    lines.append("")
    lines.append(f"**Verdict:** **{report.verdict}**")
    if report.status == BLOCKED_ON_DATA:
        lines.append(f"**Status:** **{report.status}**")
    lines.append("**Script:** `scripts/probe_cross_asset_risk_regime.py`")
    lines.append(
        "**Spec:** [cross-asset-risk-regime-probe-v0.md](../specs/cross-asset-risk-regime-probe-v0.md)"
    )
    lines.append("")

    lines.append("## Step 0 — TradFi data audit")
    for audit in report.data_audit.proxies:
        lines.append(
            f"- **{audit.proxy}** ({audit.source_ticker}): daily **{audit.daily_rows}** "
            f"({audit.daily_start}..{audit.daily_end}), 1h **{audit.hourly_rows}**"
            + (
                f" ({audit.hourly_start}..{audit.hourly_end})"
                if audit.hourly_start
                else " (not committed / empty)"
            )
        )
        lines.append(f"  - Close convention: {audit.close_convention}")
        lines.append(
            f"  - Weekend gaps flagged: daily={audit.weekend_gaps_daily}, "
            f"1h={audit.weekend_gaps_hourly}"
        )
    for note in report.data_audit.notes:
        lines.append(f"- {note}")
    if report.data_audit.blocked:
        lines.append(f"- **Blocked:** {report.data_audit.blocked_reason}")
    else:
        lines.append("- Data gate: **PASS**")
    lines.append("")

    lines.append("## Ex-ante expected signs (frozen)")
    lines.append("### H1 lead-lag (prior proxy move → crypto forward)")
    for proxy, sign in EXPECTED_SIGN_H1.items():
        lines.append(f"- **{proxy}:** {sign:+d}")
    lines.append("### H2 regime conditioning")
    for regime, sign in EXPECTED_SIGN_H2.items():
        lines.append(f"- **{regime}:** {sign:+d}")
    lines.append("")

    if not report.h1_results:
        lines.append("## Pulse metrics")
        lines.append("")
        lines.append("Not run — data gate failed.")
        return "\n".join(lines)

    lines.append("## H1 — Lead-lag (forward vs baseline vs contemporaneous)")
    lines.append("")
    for result in report.h1_results:
        lines.append(f"### {result.proxy} / {result.symbol} ({result.granularity})")
        lines.append("")
        lines.append(
            "| Horizon | n | Pred ρ | Contemp ρ | Oriented spread % | Baseline % | "
            "Excess vs base % | Excess vs contemp | H1 pass |"
        )
        lines.append(
            "|---------|---|--------|-----------|-------------------|------------|"
            "------------------|-------------------|---------|"
        )
        for metrics in result.horizons:
            lines.append(
                f"| +{metrics.horizon_hours}h | {metrics.observation_count} | "
                f"{metrics.predictive_rank_corr:.2f} | {metrics.contemporaneous_rank_corr:.2f} | "
                f"{metrics.oriented_bucket_spread_pct:.2f} | {metrics.baseline_spread_pct:.2f} | "
                f"{metrics.excess_vs_baseline_pct:.2f} | {metrics.excess_vs_contemporaneous:.2f} | "
                f"{metrics.h1_pass} |"
            )
        lines.append("")

    lines.append("## H2 — Regime conditioning")
    lines.append("")
    for result in report.h2_results:
        lines.append(f"### {result.regime} / {result.symbol}")
        lines.append("")
        lines.append(
            "| Horizon | Fav n | Unfav n | Fav mean % | Unfav mean % | Oriented spread % | "
            "Baseline % | Excess % | H2 pass |"
        )
        lines.append(
            "|---------|-------|---------|------------|--------------|-------------------|"
            "------------|----------|---------|"
        )
        for metrics in result.horizons:
            lines.append(
                f"| +{metrics.horizon_hours}h | {metrics.favorable_count} | "
                f"{metrics.unfavorable_count} | {metrics.favorable_mean_pct:.2f} | "
                f"{metrics.unfavorable_mean_pct:.2f} | {metrics.oriented_spread_pct:.2f} | "
                f"{metrics.baseline_spread_pct:.2f} | {metrics.excess_vs_baseline_pct:.2f} | "
                f"{metrics.h2_pass} |"
            )
        lines.append("")

    if report.h3 is not None:
        lines.append("## H3 — Weekend gap (secondary)")
        lines.append(f"- Observations: **{report.h3.observation_count}**")
        lines.append(
            f"- Friday risk-off weekend mean: **{report.h3.friday_risk_off_mean_weekend_pct:.2f}%**"
        )
        lines.append(
            f"- Friday risk-on weekend mean: **{report.h3.friday_risk_on_mean_weekend_pct:.2f}%**"
        )
        lines.append(
            f"- Oriented spread (risk-off − risk-on): **{report.h3.oriented_spread_pct:.2f}%**"
        )
        lines.append(f"- Ex-ante expected sign: **{report.h3.expected_sign:+d}**")
        lines.append("")

    lines.append("## Correlation-regime-stability caveat")
    lines.append(
        "- Crypto–equity correlation is known to vary across 2024–2026; any in-sample lead-lag "
        "or regime filter must be treated as unstable until walk-forward validation."
    )
    lines.append("")

    if report.reasons:
        lines.append("## Notes")
        for reason in report.reasons:
            lines.append(f"- {reason}")
        lines.append("")

    lines.append(f"**Overall verdict:** {report.verdict}")
    return "\n".join(lines)


def report_to_json(report: ProbeReport) -> dict[str, object]:
    return {
        "verdict": report.verdict,
        "status": report.status,
        "reasons": list(report.reasons),
        "expected_sign_h1": EXPECTED_SIGN_H1,
        "expected_sign_h2": EXPECTED_SIGN_H2,
        "frozen_proxies": FROZEN_PROXIES,
        "data_audit": {
            "equity_risk_available": report.data_audit.equity_risk_available,
            "blocked": report.data_audit.blocked,
            "blocked_reason": report.data_audit.blocked_reason,
            "notes": list(report.data_audit.notes),
            "proxies": [
                {
                    "proxy": item.proxy,
                    "source_ticker": item.source_ticker,
                    "daily_rows": item.daily_rows,
                    "hourly_rows": item.hourly_rows,
                    "daily_start": item.daily_start,
                    "daily_end": item.daily_end,
                    "hourly_start": item.hourly_start,
                    "hourly_end": item.hourly_end,
                    "close_convention": item.close_convention,
                    "weekend_gaps_daily": item.weekend_gaps_daily,
                    "weekend_gaps_hourly": item.weekend_gaps_hourly,
                }
                for item in report.data_audit.proxies
            ],
        },
        "h1_results": [
            {
                "proxy": result.proxy,
                "symbol": result.symbol,
                "granularity": result.granularity,
                "horizons": [
                    {
                        "horizon_hours": metrics.horizon_hours,
                        "observation_count": metrics.observation_count,
                        "predictive_rank_corr": round(metrics.predictive_rank_corr, 3),
                        "contemporaneous_rank_corr": round(metrics.contemporaneous_rank_corr, 3),
                        "oriented_bucket_spread_pct": round(metrics.oriented_bucket_spread_pct, 3),
                        "baseline_spread_pct": round(metrics.baseline_spread_pct, 3),
                        "excess_vs_baseline_pct": round(metrics.excess_vs_baseline_pct, 3),
                        "excess_vs_contemporaneous": round(metrics.excess_vs_contemporaneous, 3),
                        "h1_pass": metrics.h1_pass,
                    }
                    for metrics in result.horizons
                ],
            }
            for result in report.h1_results
        ],
        "h2_results": [
            {
                "regime": result.regime,
                "symbol": result.symbol,
                "horizons": [
                    {
                        "horizon_hours": metrics.horizon_hours,
                        "favorable_count": metrics.favorable_count,
                        "unfavorable_count": metrics.unfavorable_count,
                        "oriented_spread_pct": round(metrics.oriented_spread_pct, 3),
                        "excess_vs_baseline_pct": round(metrics.excess_vs_baseline_pct, 3),
                        "h2_pass": metrics.h2_pass,
                    }
                    for metrics in result.horizons
                ],
            }
            for result in report.h2_results
        ],
        "h3": (
            {
                "observation_count": report.h3.observation_count,
                "oriented_spread_pct": round(report.h3.oriented_spread_pct, 3),
            }
            if report.h3
            else None
        ),
    }


async def main_async(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cross-asset TradFi risk-regime cheap probe (Gate 1)"
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument(
        "--tradfi-dir",
        type=Path,
        default=DEFAULT_TRADFI_DIR,
        help="Directory with frozen TradFi CSVs",
    )
    parser.add_argument(
        "--write-report",
        type=Path,
        help="Write markdown report to this path",
    )
    args = parser.parse_args(argv)

    config = default_config()
    if args.tradfi_dir != DEFAULT_TRADFI_DIR:
        config = replace(config, tradfi_dir=args.tradfi_dir)

    report = await run_probe(config)

    if args.write_report:
        args.write_report.parent.mkdir(parents=True, exist_ok=True)
        args.write_report.write_text(render_report(report), encoding="utf-8")

    if args.json:
        print(json.dumps(report_to_json(report), indent=2, default=str))
    else:
        print(render_report(report))

    if report.verdict == BLOCKED_ON_DATA:
        return 2
    return 0 if report.verdict == "HAS_PULSE" else 1


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
