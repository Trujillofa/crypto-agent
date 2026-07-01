#!/usr/bin/env python3
"""Cheap feasibility probe for order-flow microstructure (Gate 1).

Thesis: signed aggTrade order-flow imbalance (OFI) predicts short-horizon forward
return beyond taker cost — but only matters if the edge survives at h >= 60s.

STEP 0: backfill public Binance /api/v3/aggTrades (read-only, disk cache).
STEP 1: rolling normalized OFI → decile forward-return study with train/forward split,
block bootstrap, shuffled-sign baseline, concentration cap, and cost gate.

Verdict semantics: HAS_PULSE | WEAK_EDGE | NO_PULSE_FOR_STACK | NO_PULSE | BLOCKED_ON_DATA.
See docs/specs/microstructure-orderflow-probe-v0.md.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import math
import random
import statistics
import sys
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.logger import configure_logger, get_logger

BINANCE_BASE = "https://api.binance.com"
AGG_TRADES_URL = f"{BINANCE_BASE}/api/v3/aggTrades"
KLINES_URL = f"{BINANCE_BASE}/api/v3/klines"

BLOCKED_ON_DATA = "BLOCKED_ON_DATA"
HAS_PULSE = "HAS_PULSE"
WEAK_EDGE = "WEAK_EDGE"
NO_PULSE_FOR_STACK = "NO_PULSE_FOR_STACK"
NO_PULSE = "NO_PULSE"

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
HORIZONS_SEC = (1, 5, 10, 30, 60, 300)
SUB_10S_HORIZONS = frozenset({1, 5, 10})
TRADEABLE_HORIZONS = frozenset({60, 300})

DEFAULT_TAKER_FEE_BPS = 10.0  # Binance spot default without VIP discount
MIN_SYMBOLS_H1 = 2
MIN_FORWARD_SAMPLES = 500
MIN_TRADES_PER_SYMBOL = 50_000
MIN_COVERAGE_FRACTION = 0.90
BOOTSTRAP_RESAMPLES = 1000
ALPHA = 0.05
CONCENTRATION_CAP = 0.25
OFI_WINDOW_SEC = 30
SIGNAL_STRIDE_SEC = 30
BAR_SEC = 1
TRAIN_FRACTION = 0.70
DECILES = 10
BOOTSTRAP_MAX_SAMPLES = 8000
CACHE_FLUSH_EVERY = 100_000
_MS_PER_UTC_DAY = 86_400_000
_EPOCH_DATE = date(1970, 1, 1)

AGG_LIMIT = 1000
MAX_RETRIES = 8
RETRY_BASE_SEC = 1.0
REQUEST_DELAY_SEC = 0.12


@dataclass(frozen=True)
class ProbeConfig:
    symbols: tuple[str, ...]
    start: str | None
    end: str | None
    window_days: int
    ofi_window_sec: int
    signal_stride_sec: int
    taker_fee_bps: float
    bootstrap_resamples: int
    train_fraction: float
    min_symbols_h1: int
    min_forward_samples: int
    min_trades_per_symbol: int
    min_coverage_fraction: float
    cache_dir: Path
    refresh_cache: bool
    analysis_only: bool
    seed: int


@dataclass(frozen=True)
class AggTrade:
    agg_id: int
    price: float
    qty: float
    timestamp_ms: int
    is_buyer_maker: bool


@dataclass(frozen=True)
class SecondBar:
    timestamp_ms: int
    price_vwap: float
    qty_total: float
    signed_qty: float
    high: float
    low: float
    trade_count: int


@dataclass(frozen=True)
class PreparedBars:
    """Pre-indexed bar arrays for O(1) forward VWAP via prefix sums."""

    times_ms: tuple[int, ...]
    price_vwap: tuple[float, ...]
    qty_total: tuple[float, ...]
    price_qty_cumsum: tuple[float, ...]
    qty_cumsum: tuple[float, ...]

    @classmethod
    def from_bars(cls, bars: Sequence[SecondBar]) -> PreparedBars:
        times_ms: list[int] = []
        price_vwap: list[float] = []
        qty_total: list[float] = []
        price_qty_cumsum: list[float] = []
        qty_cumsum: list[float] = []
        running_pq = 0.0
        running_q = 0.0
        for bar in bars:
            times_ms.append(bar.timestamp_ms)
            price_vwap.append(bar.price_vwap)
            qty_total.append(bar.qty_total)
            running_pq += bar.price_vwap * bar.qty_total
            running_q += bar.qty_total
            price_qty_cumsum.append(running_pq)
            qty_cumsum.append(running_q)
        return cls(
            times_ms=tuple(times_ms),
            price_vwap=tuple(price_vwap),
            qty_total=tuple(qty_total),
            price_qty_cumsum=tuple(price_qty_cumsum),
            qty_cumsum=tuple(qty_cumsum),
        )


@dataclass(frozen=True)
class RegimeWindow:
    start: datetime
    end: datetime
    anchor_symbol: str
    elevated_vol_days: tuple[str, ...]
    quiet_vol_days: tuple[str, ...]
    daily_vol_bps: dict[str, float]
    selection_note: str


@dataclass(frozen=True)
class SymbolDataAudit:
    symbol: str
    trades_fetched: int
    span_hours: float
    coverage_fraction: float
    cache_path: str
    fetch_complete: bool
    sign_sanity_correlation: float
    sign_inverted: bool
    half_spread_bps: float
    usable: bool
    blocked_reason: str | None


@dataclass(frozen=True)
class DataAudit:
    regime_window: RegimeWindow
    symbols: tuple[SymbolDataAudit, ...]
    blocked: bool
    blocked_reason: str | None


@dataclass(frozen=True)
class DecileStats:
    decile: int
    count: int
    mean_forward_return_bps: float


@dataclass(frozen=True)
class HorizonResult:
    horizon_sec: int
    deciles: tuple[DecileStats, ...]
    top_minus_bottom_bps: float
    monotonic: bool
    monotonic_violations: int
    bootstrap_p_value: float
    bootstrap_p_adj: float
    shuffled_spread_bps: float
    beats_shuffled: bool
    concentration_ok: bool
    max_day_concentration: float
    cost_bps: float
    net_edge_bps: float
    cost_survives: bool
    train_top_minus_bottom_bps: float
    forward_top_minus_bottom_bps: float
    forward_samples: int
    significant: bool
    h1_pass: bool
    h3_pass: bool


@dataclass(frozen=True)
class SymbolProbeResult:
    symbol: str
    forward_samples: int
    horizons: tuple[HorizonResult, ...]
    tradeable_h1: bool
    sub10s_h1_only: bool
    any_h1: bool


@dataclass(frozen=True)
class ProbeReport:
    config: dict[str, object]
    regime_window: RegimeWindow
    data_audit: DataAudit
    symbol_results: tuple[SymbolProbeResult, ...]
    status: str
    verdict: str
    reasons: tuple[str, ...]
    horizon_curve: dict[str, dict[str, float]]


def _parse_dt(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def sign_trade_qty(trade: AggTrade) -> float:
    """Binance m flag: buyer-is-maker => aggressor is seller => negative signed qty."""
    return -trade.qty if trade.is_buyer_maker else trade.qty


def parse_agg_trade(raw: dict[str, object]) -> AggTrade:
    return AggTrade(
        agg_id=int(raw["a"]),
        price=float(raw["p"]),
        qty=float(raw["q"]),
        timestamp_ms=int(raw["T"]),
        is_buyer_maker=bool(raw["m"]),
    )


def trades_to_jsonl(trades: Sequence[AggTrade]) -> str:
    lines: list[str] = []
    for trade in trades:
        lines.append(
            json.dumps(
                {
                    "a": trade.agg_id,
                    "p": trade.price,
                    "q": trade.qty,
                    "T": trade.timestamp_ms,
                    "m": trade.is_buyer_maker,
                }
            )
        )
    return "\n".join(lines) + ("\n" if lines else "")


def load_trades_from_cache(path: Path) -> list[AggTrade]:
    trades: list[AggTrade] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            trades.append(parse_agg_trade(json.loads(line)))
    trades.sort(key=lambda item: (item.timestamp_ms, item.agg_id))
    return trades


@dataclass
class _BarAccumulator:
    price_qty: float = 0.0
    qty_total: float = 0.0
    signed_qty: float = 0.0
    high: float = -math.inf
    low: float = math.inf
    trade_count: int = 0

    def add(self, price: float, qty: float, signed: float) -> None:
        self.price_qty += price * qty
        self.qty_total += qty
        self.signed_qty += signed
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.trade_count += 1

    def to_bar(self, timestamp_ms: int) -> SecondBar:
        return SecondBar(
            timestamp_ms=timestamp_ms,
            price_vwap=self.price_qty / self.qty_total,
            qty_total=self.qty_total,
            signed_qty=self.signed_qty,
            high=self.high,
            low=self.low,
            trade_count=self.trade_count,
        )


@dataclass(frozen=True)
class CacheStats:
    trade_count: int
    first_ms: int
    last_ms: int
    sign_sanity_correlation: float
    sign_inverted: bool
    half_spread_bps: float
    fetch_complete: bool


def scan_cache_stats(path: Path, *, sample_size: int = 5000) -> CacheStats:
    """Lightweight single-pass cache scan for audit (no full trade list)."""
    trade_count = 0
    first_ms = 0
    last_ms = 0
    spread_samples: list[float] = []
    sample_deltas: list[float] = []
    sample_buys: list[float] = []
    prev_price: float | None = None
    prev_sample_price: float | None = None

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            trade_count += 1
            ts = int(raw["T"])
            price = float(raw["p"])
            qty = float(raw["q"])
            signed = -qty if bool(raw["m"]) else qty
            if trade_count == 1:
                first_ms = ts
            last_ms = ts
            if prev_price is not None and trade_count % 50 == 0:
                mid = (price + prev_price) / 2.0
                if mid > 0:
                    spread_samples.append(abs(price - prev_price) / mid * 10_000.0 / 2.0)
            prev_price = price
            if sample_size > 0 and len(sample_deltas) < sample_size:
                step = max(1, trade_count // sample_size)
                if trade_count % step == 0:
                    if prev_sample_price is not None:
                        sample_deltas.append(price - prev_sample_price)
                        sample_buys.append(signed if signed > 0 else 0.0)
                    prev_sample_price = price

    if trade_count == 0:
        return CacheStats(0, 0, 0, 0.0, False, 0.0, False)

    corr, inverted = 0.0, False
    if len(sample_deltas) >= 10:
        corr, inverted = sanity_check_sign_from_samples(sample_buys, sample_deltas)
    half_spread = float(statistics.median(spread_samples)) if spread_samples else 0.0
    return CacheStats(
        trade_count=trade_count,
        first_ms=first_ms,
        last_ms=last_ms,
        sign_sanity_correlation=corr,
        sign_inverted=inverted,
        half_spread_bps=half_spread,
        fetch_complete=True,
    )


def sanity_check_sign_from_samples(
    buy_volumes: Sequence[float], price_changes: Sequence[float]
) -> tuple[float, bool]:
    corr = _pearson_correlation(buy_volumes, price_changes)
    return corr, corr < -0.02


def load_bars_from_cache(path: Path) -> list[SecondBar]:
    """Stream aggTrade cache into 1s bars without materializing the full trade list."""
    bucket_ms = BAR_SEC * 1000
    buckets: dict[int, _BarAccumulator] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            raw = json.loads(line)
            bucket = (int(raw["T"]) // bucket_ms) * bucket_ms
            qty = float(raw["q"])
            price = float(raw["p"])
            signed = -qty if bool(raw["m"]) else qty
            acc = buckets.get(bucket)
            if acc is None:
                acc = _BarAccumulator()
                buckets[bucket] = acc
            acc.add(price, qty, signed)
    return [buckets[ts].to_bar(ts) for ts in sorted(buckets)]


def sanity_check_sign_convention(
    trades: Sequence[AggTrade], sample_size: int = 5000
) -> tuple[float, bool]:
    """Correlate aggressor-buy volume with up-ticks; inverted sign yields negative correlation."""
    if len(trades) < 3:
        return 0.0, False
    step = max(1, len(trades) // sample_size)
    sample = trades[::step][:sample_size]
    price_changes: list[float] = []
    buy_volumes: list[float] = []
    for prev, curr in zip(sample, sample[1:], strict=False):
        delta = curr.price - prev.price
        signed = sign_trade_qty(curr)
        buy_vol = signed if signed > 0 else 0.0
        price_changes.append(delta)
        buy_volumes.append(buy_vol)
    if len(price_changes) < 10:
        return 0.0, False
    corr = _pearson_correlation(buy_volumes, price_changes)
    inverted = corr < -0.02
    return corr, inverted


def _pearson_correlation(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = len(xs)
    if n < 2 or n != len(ys):
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x <= 0 or den_y <= 0:
        return 0.0
    return num / (den_x * den_y)


def build_second_bars(trades: Sequence[AggTrade]) -> list[SecondBar]:
    if not trades:
        return []
    bucket_ms = BAR_SEC * 1000
    buckets: dict[int, _BarAccumulator] = {}
    for trade in trades:
        bucket = (trade.timestamp_ms // bucket_ms) * bucket_ms
        signed = sign_trade_qty(trade)
        acc = buckets.get(bucket)
        if acc is None:
            acc = _BarAccumulator()
            buckets[bucket] = acc
        acc.add(trade.price, trade.qty, signed)
    return [buckets[ts].to_bar(ts) for ts in sorted(buckets)]


def measure_half_spread_bps(bars: Sequence[SecondBar]) -> float:
    if not bars:
        return 0.0
    spreads: list[float] = []
    for bar in bars:
        if bar.price_vwap <= 0:
            continue
        spread_bps = (bar.high - bar.low) / bar.price_vwap * 10_000.0 / 2.0
        if spread_bps > 0:
            spreads.append(spread_bps)
    if not spreads:
        return 0.0
    return float(statistics.median(spreads))


@dataclass
class SignalObservation:
    timestamp_ms: int
    normalized_ofi: float
    ofi_zscore: float
    reference_price: float
    utc_day: str


def _utc_day_str(timestamp_ms: int) -> str:
    return (_EPOCH_DATE + timedelta(days=timestamp_ms // _MS_PER_UTC_DAY)).isoformat()


def compute_signal_observations(
    bars: Sequence[SecondBar],
    *,
    ofi_window_sec: int,
    signal_stride_sec: int,
    signed_qty: Sequence[float] | None = None,
) -> list[SignalObservation]:
    if len(bars) < ofi_window_sec + 1:
        return []
    window = ofi_window_sec
    stride = max(1, signal_stride_sec)
    if signed_qty is None:
        signed_values = [bar.signed_qty for bar in bars]
    else:
        signed_values = signed_qty
    signed_roll = [0.0] * len(bars)
    abs_roll = [0.0] * len(bars)
    running_signed = 0.0
    running_abs = 0.0
    for idx, sq in enumerate(signed_values):
        running_signed += sq
        running_abs += abs(sq)
        if idx >= window:
            prev = signed_values[idx - window]
            running_signed -= prev
            running_abs -= abs(prev)
        signed_roll[idx] = running_signed
        abs_roll[idx] = running_abs

    raw_values: list[float] = []
    indices: list[int] = []
    for idx in range(window, len(bars), stride):
        denom = abs_roll[idx]
        if denom <= 0:
            continue
        norm = signed_roll[idx] / denom
        raw_values.append(norm)
        indices.append(idx)

    if not raw_values:
        return []

    mean_val = sum(raw_values) / len(raw_values)
    std_val = statistics.pstdev(raw_values) if len(raw_values) > 1 else 1.0
    if std_val <= 1e-12:
        std_val = 1.0

    observations: list[SignalObservation] = []
    for raw, idx in zip(raw_values, indices, strict=True):
        bar = bars[idx]
        observations.append(
            SignalObservation(
                timestamp_ms=bar.timestamp_ms,
                normalized_ofi=raw,
                ofi_zscore=(raw - mean_val) / std_val,
                reference_price=bar.price_vwap,
                utc_day=_utc_day_str(bar.timestamp_ms),
            )
        )
    return observations


def forward_vwap_return_bps_prepared(
    prepared: PreparedBars,
    signal_ts_ms: int,
    reference_price: float,
    horizon_sec: int,
) -> float | None:
    if reference_price <= 0:
        return None
    times = prepared.times_ms
    if not times:
        return None
    start_ms = signal_ts_ms + BAR_SEC * 1000
    end_ms = signal_ts_ms + horizon_sec * 1000
    start_idx = bisect_left(times, start_ms)
    end_idx = bisect_left(times, end_ms)
    if start_idx >= len(times) or end_idx >= len(times):
        return None
    pq_hi = prepared.price_qty_cumsum[end_idx]
    q_hi = prepared.qty_cumsum[end_idx]
    if start_idx > 0:
        pq_hi -= prepared.price_qty_cumsum[start_idx - 1]
        q_hi -= prepared.qty_cumsum[start_idx - 1]
    if q_hi <= 0:
        return None
    vwap = pq_hi / q_hi
    return (vwap / reference_price - 1.0) * 10_000.0


def forward_vwap_return_bps(
    bars: Sequence[SecondBar],
    signal_ts_ms: int,
    reference_price: float,
    horizon_sec: int,
) -> float | None:
    return forward_vwap_return_bps_prepared(
        PreparedBars.from_bars(bars),
        signal_ts_ms,
        reference_price,
        horizon_sec,
    )


def precompute_forward_returns(
    prepared: PreparedBars,
    observations: Sequence[SignalObservation],
    horizons: Sequence[int],
) -> list[dict[int, float | None]]:
    times = prepared.times_ms
    out: list[dict[int, float | None]] = []
    none_row = dict.fromkeys(horizons)
    for obs in observations:
        if obs.reference_price <= 0 or not times:
            out.append(dict(none_row))
            continue
        start_ms = obs.timestamp_ms + BAR_SEC * 1000
        start_idx = bisect_left(times, start_ms)
        if start_idx >= len(times):
            out.append(dict(none_row))
            continue
        pq_base = prepared.price_qty_cumsum[start_idx - 1] if start_idx > 0 else 0.0
        q_base = prepared.qty_cumsum[start_idx - 1] if start_idx > 0 else 0.0
        ref = obs.reference_price
        row: dict[int, float | None] = {}
        for horizon in horizons:
            end_idx = bisect_left(times, obs.timestamp_ms + horizon * 1000)
            if end_idx >= len(times):
                row[horizon] = None
                continue
            qty = prepared.qty_cumsum[end_idx] - q_base
            if qty <= 0:
                row[horizon] = None
                continue
            vwap = (prepared.price_qty_cumsum[end_idx] - pq_base) / qty
            row[horizon] = (vwap / ref - 1.0) * 10_000.0
        out.append(row)
    return out


def assign_decile(value: float, boundaries: Sequence[float]) -> int:
    return bisect_left(boundaries, value)


def fit_decile_boundaries(values: Sequence[float], deciles: int = DECILES) -> list[float]:
    if not values:
        return [0.0] * (deciles - 1)
    ordered = sorted(values)
    boundaries: list[float] = []
    for pct in range(1, deciles):
        boundaries.append(_quantile(ordered, pct / deciles))
    return boundaries


def _quantile(ordered: Sequence[float], q: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    weight = pos - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def check_monotonic(decile_means: Sequence[float]) -> tuple[bool, int]:
    violations = 0
    for left, right in zip(decile_means, decile_means[1:], strict=False):
        if left > right + 1e-9:
            violations += 1
    return violations == 0, violations


def concentration_fraction(
    observations: Sequence[SignalObservation],
    decile_assignments: Sequence[int],
    forward_returns_bps: Sequence[float],
    top_decile: int,
) -> float:
    day_pnl: dict[str, float] = {}
    total_positive = 0.0
    for obs, decile, ret in zip(observations, decile_assignments, forward_returns_bps, strict=True):
        if decile != top_decile:
            continue
        if ret <= 0:
            continue
        total_positive += ret
        day_pnl[obs.utc_day] = day_pnl.get(obs.utc_day, 0.0) + ret
    if total_positive <= 0:
        return 0.0
    return max(day_pnl.values()) / total_positive


def _top_minus_bottom_spread_indices(
    decile_assignments: Sequence[int],
    forward_returns_bps: Sequence[float],
    indices: Sequence[int],
) -> float | None:
    bucket_sum = [0.0] * DECILES
    bucket_count = [0] * DECILES
    for idx in indices:
        decile = decile_assignments[idx]
        bucket_sum[decile] += forward_returns_bps[idx]
        bucket_count[decile] += 1
    if any(count == 0 for count in bucket_count):
        return None
    top_mean = bucket_sum[DECILES - 1] / bucket_count[DECILES - 1]
    bottom_mean = bucket_sum[0] / bucket_count[0]
    return top_mean - bottom_mean


def block_bootstrap_p_value(
    observations: Sequence[SignalObservation],
    decile_assignments: Sequence[int],
    forward_returns_bps: Sequence[float],
    *,
    resamples: int,
    seed: int,
    max_samples: int = BOOTSTRAP_MAX_SAMPLES,
) -> float:
    observed = _top_minus_bottom_spread_indices(
        decile_assignments,
        forward_returns_bps,
        range(len(decile_assignments)),
    )
    if observed is None:
        return 1.0
    day_blocks: dict[str, list[int]] = {}
    for idx, obs in enumerate(observations):
        day_blocks.setdefault(obs.utc_day, []).append(idx)
    if not day_blocks:
        return 1.0
    rng = random.Random(seed)
    active_indices = list(range(len(observations)))
    if len(active_indices) > max_samples:
        active_indices = sorted(rng.sample(active_indices, max_samples))
    bootstrap_days = sorted({observations[i].utc_day for i in active_indices})
    day_index_lists = [day_blocks[day] for day in bootstrap_days]
    day_count = len(bootstrap_days)
    count_le = 0
    sample_buf: list[int] = []
    for _ in range(resamples):
        sample_buf.clear()
        for _ in range(day_count):
            sample_buf.extend(day_index_lists[rng.randrange(day_count)])
        if len(sample_buf) > max_samples:
            rng.shuffle(sample_buf)
            del sample_buf[max_samples:]
        if len(sample_buf) < DECILES * 2:
            continue
        spread = _top_minus_bottom_spread_indices(
            decile_assignments,
            forward_returns_bps,
            sample_buf,
        )
        if spread is not None and spread <= observed:
            count_le += 1
    return (count_le + 1) / (resamples + 1)


def build_shuffled_zscore_map(
    bars: Sequence[SecondBar],
    *,
    ofi_window_sec: int,
    signal_stride_sec: int,
    seed: int,
) -> dict[int, float]:
    rng = random.Random(seed)
    shuffled_signed = [bar.signed_qty for bar in bars]
    rng.shuffle(shuffled_signed)
    shuffled_signals = compute_signal_observations(
        bars,
        ofi_window_sec=ofi_window_sec,
        signal_stride_sec=signal_stride_sec,
        signed_qty=shuffled_signed,
    )
    return {item.timestamp_ms: item.ofi_zscore for item in shuffled_signals}


def shuffled_sign_baseline_spread(
    shuffled_zscore_map: dict[int, float],
    forward_returns: Sequence[float | None],
    train_obs: Sequence[SignalObservation],
    forward_obs: Sequence[SignalObservation],
    boundaries: Sequence[float],
) -> float:
    deciles: list[int] = []
    returns: list[float] = []
    for obs, ret in zip(forward_obs, forward_returns, strict=True):
        if ret is None:
            continue
        z = shuffled_zscore_map.get(obs.timestamp_ms)
        if z is None:
            continue
        deciles.append(assign_decile(z, boundaries))
        returns.append(ret)
    spread = _top_minus_bottom_spread_indices(deciles, returns, range(len(deciles)))
    return spread if spread is not None else 0.0


def analyze_horizon(
    observations: Sequence[SignalObservation],
    horizon_sec: int,
    config: ProbeConfig,
    half_spread_bps: float,
    shuffled_zscore_map: dict[int, float],
    forward_returns: Sequence[float | None],
    shuffled_boundaries: Sequence[float],
) -> HorizonResult | None:
    if len(observations) < config.min_forward_samples * 2:
        return None
    split_idx = int(len(observations) * config.train_fraction)
    if split_idx < DECILES * 5 or len(observations) - split_idx < config.min_forward_samples:
        return None
    train_obs = observations[:split_idx]
    forward_obs = observations[split_idx:]
    forward_slice_returns = forward_returns[split_idx:]
    boundaries = fit_decile_boundaries([item.ofi_zscore for item in train_obs])

    deciles: list[int] = []
    returns: list[float] = []
    forward_rows: list[SignalObservation] = []
    for obs, ret in zip(forward_obs, forward_slice_returns, strict=True):
        if ret is None:
            continue
        deciles.append(assign_decile(obs.ofi_zscore, boundaries))
        returns.append(ret)
        forward_rows.append(obs)
    if len(returns) < config.min_forward_samples:
        return None

    buckets: list[list[float]] = [[] for _ in range(DECILES)]
    for decile, ret in zip(deciles, returns, strict=True):
        buckets[decile].append(ret)
    decile_stats = tuple(
        DecileStats(decile=idx, count=len(bucket), mean_forward_return_bps=_mean(bucket))
        for idx, bucket in enumerate(buckets)
    )
    means = [stat.mean_forward_return_bps for stat in decile_stats]
    monotonic, violations = check_monotonic(means)
    spread = means[-1] - means[0]

    train_returns = forward_returns[:split_idx]
    train_bucket_values: list[list[float]] = [[] for _ in range(DECILES)]
    for obs, ret in zip(train_obs, train_returns, strict=True):
        if ret is None:
            continue
        train_bucket_values[assign_decile(obs.ofi_zscore, boundaries)].append(ret)
    train_spread = 0.0
    if all(train_bucket_values):
        train_spread = _mean(train_bucket_values[-1]) - _mean(train_bucket_values[0])

    p_value = block_bootstrap_p_value(
        forward_rows,
        deciles,
        returns,
        resamples=config.bootstrap_resamples,
        seed=config.seed + horizon_sec,
    )
    p_adj = min(1.0, p_value * len(HORIZONS_SEC))
    shuffled_spread = shuffled_sign_baseline_spread(
        shuffled_zscore_map,
        forward_slice_returns,
        train_obs,
        forward_obs,
        shuffled_boundaries,
    )
    beats_shuffled = spread > shuffled_spread
    max_day_share = concentration_fraction(forward_rows, deciles, returns, top_decile=DECILES - 1)
    concentration_ok = max_day_share <= CONCENTRATION_CAP
    cost_bps = half_spread_bps + config.taker_fee_bps
    net_edge = spread - cost_bps
    cost_survives = net_edge > 0
    significant = monotonic and p_adj < ALPHA and beats_shuffled and concentration_ok
    h1_pass = significant
    h3_pass = significant and cost_survives

    return HorizonResult(
        horizon_sec=horizon_sec,
        deciles=decile_stats,
        top_minus_bottom_bps=spread,
        monotonic=monotonic,
        monotonic_violations=violations,
        bootstrap_p_value=p_value,
        bootstrap_p_adj=p_adj,
        shuffled_spread_bps=shuffled_spread,
        beats_shuffled=beats_shuffled,
        concentration_ok=concentration_ok,
        max_day_concentration=max_day_share,
        cost_bps=cost_bps,
        net_edge_bps=net_edge,
        cost_survives=cost_survives,
        train_top_minus_bottom_bps=train_spread,
        forward_top_minus_bottom_bps=spread,
        forward_samples=len(returns),
        significant=significant,
        h1_pass=h1_pass,
        h3_pass=h3_pass,
    )


def probe_symbol_bars(
    symbol: str,
    bars: Sequence[SecondBar],
    config: ProbeConfig,
    *,
    trade_count: int = 0,
) -> SymbolProbeResult:
    logger = get_logger("probe.orderflow")
    logger.info("%s: analyzing %d bars (%d trades)", symbol, len(bars), trade_count)
    half_spread = measure_half_spread_bps(bars)
    prepared = PreparedBars.from_bars(bars)
    observations = compute_signal_observations(
        bars,
        ofi_window_sec=config.ofi_window_sec,
        signal_stride_sec=config.signal_stride_sec,
    )
    shuffled_map = build_shuffled_zscore_map(
        bars,
        ofi_window_sec=config.ofi_window_sec,
        signal_stride_sec=config.signal_stride_sec,
        seed=config.seed + 17,
    )
    split_idx = int(len(observations) * config.train_fraction)
    shuffled_train_values = [
        shuffled_map.get(item.timestamp_ms, 0.0) for item in observations[:split_idx]
    ]
    shuffled_boundaries = fit_decile_boundaries(shuffled_train_values)
    returns_matrix = precompute_forward_returns(prepared, observations, HORIZONS_SEC)
    returns_by_horizon = {
        horizon: [row[horizon] for row in returns_matrix] for horizon in HORIZONS_SEC
    }
    horizons: list[HorizonResult] = []
    for horizon in HORIZONS_SEC:
        result = analyze_horizon(
            observations,
            horizon,
            config,
            half_spread,
            shuffled_map,
            returns_by_horizon[horizon],
            shuffled_boundaries,
        )
        if result is not None:
            horizons.append(result)
            logger.info(
                "%s: h=%ds spread=%+.2f bps p_adj=%.4f h1=%s",
                symbol,
                horizon,
                result.forward_top_minus_bottom_bps,
                result.bootstrap_p_adj,
                result.h1_pass,
            )

    sub10s_pass = any(h.h1_pass for h in horizons if h.horizon_sec in SUB_10S_HORIZONS)
    tradeable_pass = any(h.h1_pass for h in horizons if h.horizon_sec in TRADEABLE_HORIZONS)
    any_pass = any(h.h1_pass for h in horizons)
    sub10s_only = sub10s_pass and not tradeable_pass

    return SymbolProbeResult(
        symbol=symbol,
        forward_samples=horizons[0].forward_samples if horizons else 0,
        horizons=tuple(horizons),
        tradeable_h1=tradeable_pass,
        sub10s_h1_only=sub10s_only,
        any_h1=any_pass,
    )


def probe_symbol(
    symbol: str,
    trades: Sequence[AggTrade],
    config: ProbeConfig,
) -> SymbolProbeResult:
    bars = build_second_bars(trades)
    return probe_symbol_bars(symbol, bars, config, trade_count=len(trades))


def _mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


async def _request_json(
    session: aiohttp.ClientSession,
    url: str,
    params: dict[str, object],
    logger,
) -> object:
    for attempt in range(MAX_RETRIES):
        try:
            async with session.get(url, params=params) as response:
                if response.status == 429:
                    retry_after = float(
                        response.headers.get("Retry-After", RETRY_BASE_SEC * 2**attempt)
                    )
                    logger.warning("rate limited; sleeping %.1fs", retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                if response.status >= 500:
                    await asyncio.sleep(RETRY_BASE_SEC * 2**attempt)
                    continue
                response.raise_for_status()
                return await response.json()
        except (TimeoutError, aiohttp.ClientError):
            await asyncio.sleep(RETRY_BASE_SEC * 2**attempt)
    raise RuntimeError(f"failed to fetch {url} after {MAX_RETRIES} retries")


async def fetch_daily_klines(
    session: aiohttp.ClientSession,
    symbol: str,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, float]]:
    logger = get_logger("probe.orderflow")
    out: list[tuple[datetime, float]] = []
    cursor_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    while cursor_ms < end_ms:
        params = {
            "symbol": symbol,
            "interval": "1d",
            "startTime": cursor_ms,
            "endTime": end_ms,
            "limit": 1000,
        }
        payload = await _request_json(session, KLINES_URL, params, logger)
        if not isinstance(payload, list) or not payload:
            break
        for row in payload:
            day = datetime.fromtimestamp(int(row[0]) / 1000, tz=UTC)
            high = float(row[2])
            low = float(row[3])
            close = float(row[4])
            vol_bps = ((high - low) / close * 10_000.0) if close > 0 else 0.0
            out.append((day, vol_bps))
        last_open = int(payload[-1][0])
        if last_open <= cursor_ms:
            break
        cursor_ms = last_open + 86_400_000
        await asyncio.sleep(REQUEST_DELAY_SEC)
    return out


def select_regime_window(
    daily_vols: Sequence[tuple[datetime, float]],
    *,
    window_days: int,
) -> RegimeWindow | None:
    if len(daily_vols) < window_days:
        return None
    vols = [vol for _, vol in daily_vols]
    p25 = _quantile(sorted(vols), 0.25)
    p75 = _quantile(sorted(vols), 0.75)
    best: tuple[int, int, int, int] | None = None
    for start_idx in range(0, len(daily_vols) - window_days + 1):
        window = daily_vols[start_idx : start_idx + window_days]
        elevated = [day for day, vol in window if vol >= p75]
        quiet = [day for day, vol in window if vol <= p25]
        if not elevated or not quiet:
            continue
        score = len(elevated) + len(quiet)
        if best is None or score > best[0]:
            best = (score, start_idx, len(elevated), len(quiet))
    if best is None:
        return None
    _, start_idx, elev_n, quiet_n = best
    window = daily_vols[start_idx : start_idx + window_days]
    start_day = window[0][0]
    end_day = window[-1][0] + timedelta(days=1)
    elevated_days = tuple(day.strftime("%Y-%m-%d") for day, vol in window if vol >= p75)
    quiet_days = tuple(day.strftime("%Y-%m-%d") for day, vol in window if vol <= p25)
    vol_map = {day.strftime("%Y-%m-%d"): vol for day, vol in window}
    note = (
        f"Selected {window_days}d window with {elev_n} elevated-vol days (>=p75={p75:.1f}bps) "
        f"and {quiet_n} quiet-vol days (<=p25={p25:.1f}bps) from daily (high-low)/close range."
    )
    return RegimeWindow(
        start=start_day,
        end=end_day,
        anchor_symbol="BTCUSDT",
        elevated_vol_days=elevated_days,
        quiet_vol_days=quiet_days,
        daily_vol_bps=vol_map,
        selection_note=note,
    )


def _append_trades_cache(path: Path, trades: Sequence[AggTrade]) -> None:
    if not trades:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(trades_to_jsonl(trades))


async def fetch_agg_trades(
    session: aiohttp.ClientSession,
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    cache_path: Path | None = None,
) -> tuple[list[AggTrade], bool]:
    logger = get_logger("probe.orderflow")
    buffer: list[AggTrade] = []
    total_fetched = 0
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    from_id: int | None = None
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("", encoding="utf-8")

    def flush_buffer() -> None:
        nonlocal buffer
        if cache_path is not None:
            _append_trades_cache(cache_path, buffer)
        buffer = []

    while True:
        params: dict[str, object] = {
            "symbol": symbol,
            "limit": AGG_LIMIT,
        }
        if from_id is not None:
            params["fromId"] = from_id
        else:
            params["startTime"] = start_ms
            params["endTime"] = end_ms
        payload = await _request_json(session, AGG_TRADES_URL, params, logger)
        if not isinstance(payload, list) or not payload:
            break
        batch = [parse_agg_trade(item) for item in payload if isinstance(item, dict)]
        if not batch:
            break
        for trade in batch:
            if trade.timestamp_ms < start_ms:
                continue
            if trade.timestamp_ms > end_ms:
                flush_buffer()
                if cache_path is not None:
                    return load_trades_from_cache(cache_path), True
                return buffer, True
            buffer.append(trade)
            total_fetched += 1
        last = batch[-1]
        next_id = last.agg_id + 1
        if next_id <= (from_id or 0):
            break
        from_id = next_id
        if len(buffer) >= CACHE_FLUSH_EVERY:
            flush_buffer()
        if total_fetched % 50_000 < AGG_LIMIT:
            logger.info("%s: %d aggTrades fetched", symbol, total_fetched)
        await asyncio.sleep(REQUEST_DELAY_SEC)

    flush_buffer()
    if cache_path is not None and cache_path.is_file():
        return load_trades_from_cache(cache_path), True
    return buffer, bool(buffer)


def cache_path_for(config: ProbeConfig, symbol: str, start: datetime, end: datetime) -> Path:
    label = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
    return config.cache_dir / symbol / f"aggtrades_{label}.jsonl"


def audit_symbol_data(
    symbol: str,
    stats: CacheStats,
    *,
    start: datetime,
    end: datetime,
    cache_file: Path,
    config: ProbeConfig,
) -> SymbolDataAudit:
    expected_hours = (end - start).total_seconds() / 3600.0
    if stats.trade_count == 0:
        return SymbolDataAudit(
            symbol=symbol,
            trades_fetched=0,
            span_hours=0.0,
            coverage_fraction=0.0,
            cache_path=str(cache_file),
            fetch_complete=False,
            sign_sanity_correlation=0.0,
            sign_inverted=False,
            half_spread_bps=0.0,
            usable=False,
            blocked_reason="no trades fetched",
        )
    span_hours = (stats.last_ms - stats.first_ms) / 3_600_000.0
    coverage = min(1.0, span_hours / expected_hours) if expected_hours > 0 else 0.0
    usable = (
        stats.fetch_complete
        and stats.trade_count >= config.min_trades_per_symbol
        and coverage >= config.min_coverage_fraction
        and not stats.sign_inverted
    )
    blocked_reason: str | None = None
    if stats.sign_inverted:
        blocked_reason = (
            f"sign convention sanity check failed (corr={stats.sign_sanity_correlation:.4f})"
        )
    elif not stats.fetch_complete:
        blocked_reason = "incomplete aggTrade backfill"
    elif stats.trade_count < config.min_trades_per_symbol:
        blocked_reason = f"only {stats.trade_count} trades (<{config.min_trades_per_symbol})"
    elif coverage < config.min_coverage_fraction:
        blocked_reason = f"coverage {coverage:.1%} < {config.min_coverage_fraction:.0%}"
    return SymbolDataAudit(
        symbol=symbol,
        trades_fetched=stats.trade_count,
        span_hours=span_hours,
        coverage_fraction=coverage,
        cache_path=str(cache_file),
        fetch_complete=stats.fetch_complete,
        sign_sanity_correlation=stats.sign_sanity_correlation,
        sign_inverted=stats.sign_inverted,
        half_spread_bps=stats.half_spread_bps,
        usable=usable,
        blocked_reason=blocked_reason,
    )


def decide_verdict(
    audit: DataAudit,
    symbol_results: Sequence[SymbolProbeResult],
    config: ProbeConfig,
) -> tuple[str, str, tuple[str, ...]]:
    if audit.blocked:
        return (BLOCKED_ON_DATA, BLOCKED_ON_DATA, (audit.blocked_reason or "data gate failed",))

    usable = [result for result in symbol_results if result.forward_samples > 0]
    if len(usable) < config.min_symbols_h1:
        return (
            BLOCKED_ON_DATA,
            BLOCKED_ON_DATA,
            (f"only {len(usable)} symbols produced forward samples",),
        )

    symbols_h1_any = sum(1 for result in usable if result.any_h1)
    symbols_h1_tradeable = sum(1 for result in usable if result.tradeable_h1)
    symbols_sub10s_only = sum(1 for result in usable if result.sub10s_h1_only)

    h3_symbols = 0
    for result in usable:
        if any(h.h3_pass for h in result.horizons if h.horizon_sec in TRADEABLE_HORIZONS):
            h3_symbols += 1

    h1_ok = symbols_h1_any >= config.min_symbols_h1
    h2_ok = symbols_h1_tradeable >= config.min_symbols_h1
    h3_ok = h3_symbols >= config.min_symbols_h1

    reasons: list[str] = []

    if h1_ok and h2_ok and h3_ok:
        reasons.append(
            f"H1+H2+H3 pass on >={config.min_symbols_h1} symbols: "
            f"monotonic significant cost-surviving edge at h>=60s "
            f"(symbols H1={symbols_h1_any}, tradeable={symbols_h1_tradeable}, H3={h3_symbols})"
        )
        return ("OK", HAS_PULSE, tuple(reasons))

    if h1_ok and symbols_sub10s_only >= config.min_symbols_h1 and not h2_ok:
        reasons.append(
            f"significant OFI edge only at sub-10s horizons on {symbols_sub10s_only} symbols — "
            "operationally dead for candle-cadence stack (Path-2 latency decision)"
        )
        return ("OK", NO_PULSE_FOR_STACK, tuple(reasons))

    if h1_ok:
        if not h2_ok:
            reasons.append(
                f"H1 on {symbols_h1_any} symbols but tradeable horizon (h>=60s) fails "
                f"({symbols_h1_tradeable} symbols)"
            )
        elif not h3_ok:
            reasons.append(
                f"tradeable horizon edge does not survive taker cost on enough symbols "
                f"(H3={h3_symbols}, need >={config.min_symbols_h1})"
            )
        else:
            reasons.append("H1 holds but edge is marginal vs cost at tradeable horizons")
        return ("OK", WEAK_EDGE, tuple(reasons))

    reasons.append(
        f"no monotonic significant OFI-return relationship on >={config.min_symbols_h1} symbols "
        f"(symbols with any H1={symbols_h1_any})"
    )
    return ("OK", NO_PULSE, tuple(reasons))


def build_horizon_curve(symbol_results: Sequence[SymbolProbeResult]) -> dict[str, dict[str, float]]:
    curve: dict[str, dict[str, float]] = {}
    for result in symbol_results:
        curve[result.symbol] = {
            str(h.horizon_sec): h.forward_top_minus_bottom_bps for h in result.horizons
        }
    return curve


def render_report(report: ProbeReport) -> str:
    lines: list[str] = ["# Order-Flow Microstructure Probe — Report", ""]
    lines.append(f"**Verdict:** **{report.verdict}**")
    lines.append("**Script:** `scripts/probe_orderflow_microstructure.py`")
    lines.append(
        "**Framing:** aggTrade signed-flow (OFI) → forward return by horizon; "
        "sub-10s-only vs ≥60s is the decisive stack gate."
    )
    lines.append("")
    rw = report.regime_window
    lines.append("## Regime window")
    lines.append(f"- Period: {rw.start.date()} → {rw.end.date()} (anchor: {rw.anchor_symbol})")
    lines.append(f"- Elevated-vol UTC days: {', '.join(rw.elevated_vol_days) or 'n/a'}")
    lines.append(f"- Quiet-vol UTC days: {', '.join(rw.quiet_vol_days) or 'n/a'}")
    lines.append(f"- Selection: {rw.selection_note}")
    lines.append("")
    lines.append("## STEP 0 — Data feasibility")
    for sym in report.data_audit.symbols:
        lines.append(
            f"- **{sym.symbol}**: trades={sym.trades_fetched:,}, coverage={sym.coverage_fraction:.1%}, "
            f"half_spread={sym.half_spread_bps:.2f}bps, sign_corr={sym.sign_sanity_correlation:.4f}"
            f"{' **SIGN INVERTED**' if sym.sign_inverted else ''}"
            f"{f' — {sym.blocked_reason}' if sym.blocked_reason else ''}"
        )
    if report.data_audit.blocked:
        lines.append(f"- **Blocked:** {report.data_audit.blocked_reason}")
    lines.append("")
    lines.append("## Horizon curve (top − bottom decile spread, bps)")
    header = "| Symbol | " + " | ".join(f"{h}s" for h in HORIZONS_SEC) + " |"
    sep = "|--------|" + "|".join("------:" for _ in HORIZONS_SEC) + "|"
    lines.append(header)
    lines.append(sep)
    for result in report.symbol_results:
        by_h = {h.horizon_sec: h.forward_top_minus_bottom_bps for h in result.horizons}
        cells = " | ".join(f"{by_h.get(h, float('nan')):+.2f}" for h in HORIZONS_SEC)
        lines.append(f"| {result.symbol} | {cells} |")
    lines.append("")
    lines.append("## Per-symbol detail")
    for result in report.symbol_results:
        lines.append(f"### {result.symbol}")
        lines.append("| h(s) | spread | monotonic | p_adj | shuffled | net edge | H1 | H3 | conc |")
        lines.append("|------|-------:|-----------|------:|---------:|---------:|:--:|:--:|:--:|")
        for h in result.horizons:
            lines.append(
                f"| {h.horizon_sec} | {h.forward_top_minus_bottom_bps:+.2f} | "
                f"{'Y' if h.monotonic else 'n'} | {h.bootstrap_p_adj:.4f} | "
                f"{h.shuffled_spread_bps:+.2f} | {h.net_edge_bps:+.2f} | "
                f"{'Y' if h.h1_pass else 'n'} | {'Y' if h.h3_pass else 'n'} | "
                f"{h.max_day_concentration:.0%} |"
            )
        lines.append("")
    lines.append("## Verdict rationale")
    for reason in report.reasons:
        lines.append(f"- {reason}")
    return "\n".join(lines)


async def run_probe(config: ProbeConfig) -> ProbeReport:
    configure_logger("INFO")
    logger = get_logger("probe.orderflow")

    lookback_end = datetime.now(UTC) - timedelta(days=2)
    lookback_start = lookback_end - timedelta(days=60)

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=60, connect=10),
        headers={"Accept": "application/json"},
    ) as session:
        daily = await fetch_daily_klines(session, "BTCUSDT", lookback_start, lookback_end)
        regime = select_regime_window(daily, window_days=config.window_days)
        if regime is None:
            audit = DataAudit(
                regime_window=RegimeWindow(
                    start=lookback_start,
                    end=lookback_end,
                    anchor_symbol="BTCUSDT",
                    elevated_vol_days=(),
                    quiet_vol_days=(),
                    daily_vol_bps={},
                    selection_note="failed to find window with elevated + quiet vol regimes",
                ),
                symbols=(),
                blocked=True,
                blocked_reason="could not select 2-regime window from daily vol",
            )
            return ProbeReport(
                config=_config_payload(config),
                regime_window=audit.regime_window,
                data_audit=audit,
                symbol_results=(),
                status=BLOCKED_ON_DATA,
                verdict=BLOCKED_ON_DATA,
                reasons=(audit.blocked_reason or "",),
                horizon_curve={},
            )

        start = regime.start
        end = regime.end
        if config.start:
            start = _parse_dt(config.start)
        if config.end:
            end = _parse_dt(config.end)

        logger.info(
            "regime window %s → %s (%s elevated, %s quiet days)",
            start.date(),
            end.date(),
            len(regime.elevated_vol_days),
            len(regime.quiet_vol_days),
        )

        symbol_audits: list[SymbolDataAudit] = []
        for symbol in config.symbols:
            cache_file = cache_path_for(config, symbol, start, end)
            stats = CacheStats(0, 0, 0, 0.0, False, 0.0, False)
            if cache_file.is_file() and not config.refresh_cache:
                stats = scan_cache_stats(cache_file)
                logger.info("%s: scanned %d trades from cache (audit)", symbol, stats.trade_count)
            elif config.analysis_only:
                logger.warning("%s: cache missing in analysis-only mode (%s)", symbol, cache_file)
            else:
                try:
                    _, fetch_complete = await fetch_agg_trades(
                        session, symbol, start, end, cache_path=cache_file
                    )
                    if fetch_complete and cache_file.is_file():
                        stats = scan_cache_stats(cache_file)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("%s: aggTrade fetch failed (%s)", symbol, exc)
            audit = audit_symbol_data(
                symbol,
                stats,
                start=start,
                end=end,
                cache_file=cache_file,
                config=config,
            )
            if audit.sign_inverted:
                logger.warning(
                    "%s: SIGN CONVENTION MAY BE INVERTED (corr=%.4f)",
                    symbol,
                    audit.sign_sanity_correlation,
                )
            symbol_audits.append(audit)
            gc.collect()

        usable_count = sum(1 for item in symbol_audits if item.usable)
        blocked = usable_count < config.min_symbols_h1
        blocked_reason = None
        if blocked:
            blocked_reason = (
                f"only {usable_count} symbols passed data gate (need >={config.min_symbols_h1})"
            )
        data_audit = DataAudit(
            regime_window=RegimeWindow(
                start=start,
                end=end,
                anchor_symbol=regime.anchor_symbol,
                elevated_vol_days=regime.elevated_vol_days,
                quiet_vol_days=regime.quiet_vol_days,
                daily_vol_bps=regime.daily_vol_bps,
                selection_note=regime.selection_note,
            ),
            symbols=tuple(symbol_audits),
            blocked=blocked,
            blocked_reason=blocked_reason,
        )

        if blocked:
            status, verdict, reasons = decide_verdict(data_audit, (), config)
            return ProbeReport(
                config=_config_payload(config),
                regime_window=data_audit.regime_window,
                data_audit=data_audit,
                symbol_results=(),
                status=status,
                verdict=verdict,
                reasons=reasons,
                horizon_curve={},
            )

        symbol_results: list[SymbolProbeResult] = []
        for symbol in config.symbols:
            audit = next((item for item in symbol_audits if item.symbol == symbol), None)
            if audit is None or not audit.usable:
                continue
            cache_file = Path(audit.cache_path)
            logger.info("%s: streaming cache to bars for analysis", symbol)
            bars = load_bars_from_cache(cache_file)
            result = probe_symbol_bars(symbol, bars, config, trade_count=audit.trades_fetched)
            symbol_results.append(result)
            del bars
            gc.collect()
            logger.info("%s: analysis complete — memory released", symbol)
        status, verdict, reasons = decide_verdict(data_audit, symbol_results, config)
        return ProbeReport(
            config=_config_payload(config),
            regime_window=data_audit.regime_window,
            data_audit=data_audit,
            symbol_results=tuple(symbol_results),
            status=status,
            verdict=verdict,
            reasons=reasons,
            horizon_curve=build_horizon_curve(symbol_results),
        )


def _config_payload(config: ProbeConfig) -> dict[str, object]:
    return {
        "symbols": list(config.symbols),
        "start": config.start,
        "end": config.end,
        "window_days": config.window_days,
        "ofi_window_sec": config.ofi_window_sec,
        "signal_stride_sec": config.signal_stride_sec,
        "taker_fee_bps": config.taker_fee_bps,
        "horizons_sec": list(HORIZONS_SEC),
        "bootstrap_resamples": config.bootstrap_resamples,
        "train_fraction": config.train_fraction,
        "min_symbols_h1": config.min_symbols_h1,
    }


def _serialize_report(report: ProbeReport) -> dict[str, object]:
    return {
        "config": report.config,
        "regime_window": {
            "start": report.regime_window.start.isoformat(),
            "end": report.regime_window.end.isoformat(),
            "anchor_symbol": report.regime_window.anchor_symbol,
            "elevated_vol_days": list(report.regime_window.elevated_vol_days),
            "quiet_vol_days": list(report.regime_window.quiet_vol_days),
            "daily_vol_bps": report.regime_window.daily_vol_bps,
            "selection_note": report.regime_window.selection_note,
        },
        "data_audit": {
            "blocked": report.data_audit.blocked,
            "blocked_reason": report.data_audit.blocked_reason,
            "symbols": [asdict(item) for item in report.data_audit.symbols],
        },
        "symbol_results": [
            {
                "symbol": item.symbol,
                "forward_samples": item.forward_samples,
                "tradeable_h1": item.tradeable_h1,
                "sub10s_h1_only": item.sub10s_h1_only,
                "any_h1": item.any_h1,
                "horizons": [asdict(h) for h in item.horizons],
            }
            for item in report.symbol_results
        ],
        "horizon_curve": report.horizon_curve,
        "status": report.status,
        "verdict": report.verdict,
        "reasons": list(report.reasons),
        "generated_at": datetime.now(UTC).isoformat(),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--start", default=None, help="Override auto-selected window start (UTC)")
    parser.add_argument("--end", default=None, help="Override auto-selected window end (UTC)")
    parser.add_argument("--window-days", type=int, default=14, help="2–4 week window length")
    parser.add_argument("--ofi-window-sec", type=int, default=OFI_WINDOW_SEC)
    parser.add_argument("--signal-stride-sec", type=int, default=SIGNAL_STRIDE_SEC)
    parser.add_argument("--taker-fee-bps", type=float, default=DEFAULT_TAKER_FEE_BPS)
    parser.add_argument("--bootstrap-resamples", type=int, default=BOOTSTRAP_RESAMPLES)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cache-dir", default="data/microstructure")
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument(
        "--analysis-only",
        action="store_true",
        help="Skip network fetch; require cached aggTrades (memory-safe re-run)",
    )
    parser.add_argument("--output-dir", default="research/rbi_loop/microstructure-orderflow-v0")
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = ProbeConfig(
        symbols=tuple(args.symbols),
        start=args.start,
        end=args.end,
        window_days=args.window_days,
        ofi_window_sec=args.ofi_window_sec,
        signal_stride_sec=args.signal_stride_sec,
        taker_fee_bps=args.taker_fee_bps,
        bootstrap_resamples=args.bootstrap_resamples,
        train_fraction=TRAIN_FRACTION,
        min_symbols_h1=MIN_SYMBOLS_H1,
        min_forward_samples=MIN_FORWARD_SAMPLES,
        min_trades_per_symbol=MIN_TRADES_PER_SYMBOL,
        min_coverage_fraction=MIN_COVERAGE_FRACTION,
        cache_dir=Path(args.cache_dir),
        refresh_cache=args.refresh_cache,
        analysis_only=args.analysis_only,
        seed=args.seed,
    )
    report = await run_probe(config)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = _serialize_report(report)
    (out_dir / "probe_result.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report_md = render_report(report)
    (out_dir / "probe_report.md").write_text(report_md + "\n", encoding="utf-8")

    print(report_md)
    print(f"\nVERDICT: {report.verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
