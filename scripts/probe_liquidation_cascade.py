#!/usr/bin/env python3
"""Gate-1 cheap probe: forced liquidation / cascade flow (A1).

STEP 0: data audit — REST allForceOrders (deprecated), websocket forceOrder, UM metrics.
STEP 1: frozen cascade-proxy events from official UM metrics; forward returns from 1m klines;
#118 null (phase-randomized timestamps + block bootstrap), concentration, cost gate.

See docs/specs/liquidation-cascade-probe-v0.md.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import random
import statistics
import sys
import time
import zipfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.logger import configure_logger, get_logger

FAPI_BASE = "https://fapi.binance.com"
VISION_BASE = "https://data.binance.vision/data/futures/um/daily"
WS_MARKET_BASE = "wss://fstream.binance.com/market/ws"

BLOCKED_ON_DATA = "BLOCKED_ON_DATA"
HAS_PULSE = "HAS_PULSE"
WEAK_EDGE = "WEAK_EDGE"
NO_PULSE = "NO_PULSE"

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
HORIZONS_MIN = (5, 30, 120)
TAKER_RT_BPS = 10.0
OI_DROP_PCT = -0.15
TAKER_LONG_CASCADE = 0.55
TAKER_SHORT_CASCADE = 1.80
EVENT_GAP_MIN = 30
MIN_EVENTS_PER_SYMBOL = 8
MIN_SYMBOLS_PASS = 2
BOOTSTRAP_RESAMPLES = 1000
ALPHA = 0.05
CONCENTRATION_CAP = 0.25
QUIET_EXCLUSION_MIN = 60
RANDOM_SEED = 42

ALL_FORCE_ORDERS_URL = f"{FAPI_BASE}/fapi/v1/allForceOrders"
KLINES_URL = f"{FAPI_BASE}/fapi/v1/klines"


@dataclass(frozen=True)
class ProbeConfig:
    symbols: tuple[str, ...]
    window_days: int
    end_lag_days: int
    horizons_min: tuple[int, ...]
    taker_rt_bps: float
    oi_drop_pct: float
    taker_long_cascade: float
    taker_short_cascade: float
    event_gap_min: int
    min_events_per_symbol: int
    min_symbols_pass: int
    bootstrap_resamples: int
    collect_seconds: int
    cache_dir: Path
    seed: int


@dataclass(frozen=True)
class MetricsRow:
    time: datetime
    symbol: str
    open_interest: float
    oi_value: float
    taker_ratio: float


@dataclass(frozen=True)
class CascadeEvent:
    symbol: str
    time: datetime
    side: str  # LONG_CASCADE (sell pressure) or SHORT_CASCADE
    oi_change_pct: float
    taker_ratio: float
    source: str


@dataclass(frozen=True)
class MinuteBar:
    time: datetime
    close: float


@dataclass(frozen=True)
class HorizonResult:
    horizon_min: int
    orientation: str
    event_count: int
    mean_oriented_bps: float
    baseline_bps: float
    excess_bps: float
    net_edge_bps: float
    null_median_excess_bps: float
    beats_null: bool
    bootstrap_p: float
    bootstrap_p_adj: float
    max_day_concentration: float
    concentration_ok: bool
    symbols_passing: int
    gate_pass: bool


@dataclass(frozen=True)
class SymbolResult:
    symbol: str
    event_count: int
    horizons: tuple[HorizonResult, ...]


@dataclass(frozen=True)
class DataAudit:
    rest_all_force_orders: str
    websocket_path: str
    metrics_days_loaded: int
    force_orders_collected: int
    force_orders_cached: int
    events_per_symbol: dict[str, int]
    blocked: bool
    blocked_reason: str | None


@dataclass(frozen=True)
class ProbeReport:
    config: ProbeConfig
    window_start: datetime
    window_end: datetime
    data_audit: DataAudit
    events: tuple[CascadeEvent, ...]
    symbol_results: tuple[SymbolResult, ...]
    best_horizon: HorizonResult | None
    verdict: str
    reasons: tuple[str, ...]


def default_config() -> ProbeConfig:
    return ProbeConfig(
        symbols=DEFAULT_SYMBOLS,
        window_days=14,
        end_lag_days=2,
        horizons_min=HORIZONS_MIN,
        taker_rt_bps=TAKER_RT_BPS,
        oi_drop_pct=OI_DROP_PCT,
        taker_long_cascade=TAKER_LONG_CASCADE,
        taker_short_cascade=TAKER_SHORT_CASCADE,
        event_gap_min=EVENT_GAP_MIN,
        min_events_per_symbol=MIN_EVENTS_PER_SYMBOL,
        min_symbols_pass=MIN_SYMBOLS_PASS,
        bootstrap_resamples=BOOTSTRAP_RESAMPLES,
        collect_seconds=0,
        cache_dir=Path("data/liquidation_cascade"),
        seed=RANDOM_SEED,
    )


def _parse_metrics_time(raw: str) -> datetime:
    parsed = datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=UTC)


def _daterange(start: date, end: date) -> list[date]:
    days: list[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


async def audit_rest_force_orders(session: aiohttp.ClientSession) -> str:
    params = {"symbol": "BTCUSDT", "limit": 5}
    try:
        async with session.get(ALL_FORCE_ORDERS_URL, params=params) as resp:
            body = await resp.text()
            if resp.status == 200:
                return "OK"
            return f"HTTP {resp.status}: {body[:120]}"
    except aiohttp.ClientError as exc:
        return f"error: {exc}"


async def download_metrics_day(
    session: aiohttp.ClientSession,
    symbol: str,
    day: date,
) -> list[MetricsRow]:
    url = f"{VISION_BASE}/metrics/{symbol}/{symbol}-metrics-{day.isoformat()}.zip"
    async with session.get(url) as resp:
        if resp.status != 200:
            return []
        payload = await resp.read()
    rows: list[MetricsRow] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        name = next(iter(zf.namelist()))
        with zf.open(name) as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8")
            reader = csv.DictReader(text)
            for row in reader:
                rows.append(
                    MetricsRow(
                        time=_parse_metrics_time(row["create_time"]),
                        symbol=row["symbol"],
                        open_interest=float(row["sum_open_interest"]),
                        oi_value=float(row["sum_open_interest_value"]),
                        taker_ratio=float(row["sum_taker_long_short_vol_ratio"]),
                    )
                )
    return rows


def detect_cascade_events(
    metrics: Sequence[MetricsRow],
    config: ProbeConfig,
) -> list[CascadeEvent]:
    if len(metrics) < 2:
        return []
    sorted_rows = sorted(metrics, key=lambda r: r.time)
    raw: list[CascadeEvent] = []
    for prev, cur in zip(sorted_rows, sorted_rows[1:], strict=False):
        if prev.open_interest <= 0:
            continue
        oi_chg_pct = (cur.open_interest - prev.open_interest) / prev.open_interest * 100.0
        if oi_chg_pct > config.oi_drop_pct:
            continue
        side: str | None = None
        if cur.taker_ratio <= config.taker_long_cascade:
            side = "LONG_CASCADE"
        elif cur.taker_ratio >= config.taker_short_cascade:
            side = "SHORT_CASCADE"
        if side is None:
            continue
        raw.append(
            CascadeEvent(
                symbol=cur.symbol,
                time=cur.time,
                side=side,
                oi_change_pct=oi_chg_pct,
                taker_ratio=cur.taker_ratio,
                source="um_metrics_proxy",
            )
        )
    raw.sort(key=lambda e: e.time)
    deduped: list[CascadeEvent] = []
    last_by_symbol: dict[str, datetime] = {}
    for event in raw:
        last = last_by_symbol.get(event.symbol)
        if last is not None and (event.time - last) < timedelta(minutes=config.event_gap_min):
            if deduped and deduped[-1].symbol == event.symbol:
                if abs(event.oi_change_pct) > abs(deduped[-1].oi_change_pct):
                    deduped[-1] = event
                last_by_symbol[event.symbol] = deduped[-1].time
            continue
        deduped.append(event)
        last_by_symbol[event.symbol] = event.time
    return deduped


async def collect_force_orders_ws(
    symbols: Sequence[str],
    seconds: int,
    cache_dir: Path,
) -> int:
    if seconds <= 0:
        return 0
    try:
        import websockets
    except ImportError:
        return 0

    cache_dir.mkdir(parents=True, exist_ok=True)
    streams = "/".join(f"{s.lower()}@forceOrder" for s in symbols)
    url = f"{WS_MARKET_BASE}/{streams}"
    collected = 0
    deadline = time.time() + seconds
    handles = {
        sym: (cache_dir / f"force_orders_{sym}.jsonl").open("a", encoding="utf-8")
        for sym in symbols
    }
    try:
        async with websockets.connect(url, ping_interval=20) as ws:
            while time.time() < deadline:
                try:
                    msg = await asyncio.wait_for(
                        ws.recv(), timeout=min(5.0, deadline - time.time())
                    )
                except TimeoutError:
                    continue
                payload = json.loads(msg)
                if payload.get("e") != "forceOrder":
                    continue
                order = payload.get("o") or {}
                sym = str(order.get("s", "")).upper()
                if sym not in handles:
                    continue
                handles[sym].write(json.dumps(payload) + "\n")
                collected += 1
    finally:
        for handle in handles.values():
            handle.close()
    return collected


def load_cached_force_orders(cache_dir: Path, symbols: Sequence[str]) -> list[CascadeEvent]:
    events: list[CascadeEvent] = []
    for symbol in symbols:
        path = cache_dir / f"force_orders_{symbol}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            order = payload.get("o") or {}
            side_raw = str(order.get("S", "")).upper()
            side = "LONG_CASCADE" if side_raw == "SELL" else "SHORT_CASCADE"
            ts_ms = int(order.get("T") or payload.get("E") or 0)
            if ts_ms <= 0:
                continue
            events.append(
                CascadeEvent(
                    symbol=symbol,
                    time=datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC),
                    side=side,
                    oi_change_pct=0.0,
                    taker_ratio=0.0,
                    source="websocket_force_order",
                )
            )
    return sorted(events, key=lambda e: e.time)


async def fetch_klines_1m(
    session: aiohttp.ClientSession,
    symbol: str,
    start: datetime,
    end: datetime,
) -> list[MinuteBar]:
    bars: list[MinuteBar] = []
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": "1m",
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1500,
        }
        async with session.get(KLINES_URL, params=params) as resp:
            if resp.status != 200:
                break
            chunk = await resp.json()
        if not chunk:
            break
        for row in chunk:
            open_ms = int(row[0])
            close_px = float(row[4])
            bars.append(
                MinuteBar(
                    time=datetime.fromtimestamp(open_ms / 1000.0, tz=UTC),
                    close=close_px,
                )
            )
        last_open = int(chunk[-1][0])
        next_cursor = last_open + 60_000
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        await asyncio.sleep(0.05)
    return bars


def _forward_return_bps(
    bars: Sequence[MinuteBar], event_time: datetime, horizon_min: int
) -> float | None:
    if not bars:
        return None
    times = [b.time for b in bars]
    try:
        idx = times.index(event_time)
    except ValueError:
        idx = min(range(len(times)), key=lambda i: abs((times[i] - event_time).total_seconds()))
    end_idx = idx + horizon_min
    if end_idx >= len(bars):
        return None
    p0 = bars[idx].close
    p1 = bars[end_idx].close
    if p0 <= 0:
        return None
    return (p1 / p0 - 1.0) * 10_000.0


def _oriented_return(side: str, orientation: str, raw_bps: float) -> float:
    if side == "LONG_CASCADE":
        return raw_bps if orientation == "fade" else -raw_bps
    return -raw_bps if orientation == "fade" else raw_bps


def _matched_baseline_returns(
    bars: Sequence[MinuteBar],
    events: Sequence[CascadeEvent],
    horizon_min: int,
    *,
    seed: int,
) -> list[float]:
    if len(bars) <= horizon_min + 1:
        return []
    event_times = {e.time for e in events}
    candidates = [
        b.time
        for b in bars[:-horizon_min]
        if b.time not in event_times
        and all(
            abs((b.time - et).total_seconds()) >= QUIET_EXCLUSION_MIN * 60 for et in event_times
        )
    ]
    if not candidates:
        return []
    rng = random.Random(seed)
    sample_times = rng.sample(candidates, k=min(len(candidates), max(len(events), 20)))
    out: list[float] = []
    for ts in sample_times:
        ret = _forward_return_bps(bars, ts, horizon_min)
        if ret is not None:
            out.append(ret)
    return out


def _day_concentration(oriented_edges: Sequence[tuple[datetime, float]]) -> float:
    if not oriented_edges:
        return 0.0
    by_day: dict[str, float] = {}
    for ts, edge in oriented_edges:
        day = ts.date().isoformat()
        by_day[day] = by_day.get(day, 0.0) + abs(edge)
    total = sum(by_day.values())
    if total <= 0:
        return 0.0
    return max(by_day.values()) / total


def _phase_randomized_null_excess(
    events: Sequence[CascadeEvent],
    bars: Sequence[MinuteBar],
    horizon_min: int,
    orientation: str,
    *,
    seed: int,
    resamples: int,
) -> tuple[float, float]:
    if not events or not bars:
        return 0.0, 0.0
    real_oriented = [
        _oriented_return(e.side, orientation, r)
        for e in events
        if (r := _forward_return_bps(bars, e.time, horizon_min)) is not None
    ]
    if not real_oriented:
        return 0.0, 0.0
    real_mean = statistics.mean(real_oriented)
    baseline = _matched_baseline_returns(bars, events, horizon_min, seed=seed)
    base_mean = statistics.mean(baseline) if baseline else 0.0
    real_excess = real_mean - base_mean

    candidate_times = [b.time for b in bars[:-horizon_min]]
    if len(candidate_times) < len(events):
        return real_excess, 1.0

    rng = random.Random(seed + horizon_min)
    null_excesses: list[float] = []
    for _ in range(resamples):
        shuffled = rng.sample(candidate_times, k=len(events))
        pseudo = [
            _oriented_return(events[i].side, orientation, r)
            for i, ts in enumerate(shuffled)
            if (r := _forward_return_bps(bars, ts, horizon_min)) is not None
        ]
        if not pseudo:
            continue
        null_excesses.append(statistics.mean(pseudo) - base_mean)
    if not null_excesses:
        return real_excess, 1.0
    null_median = statistics.median(null_excesses)
    count_ge = sum(1 for x in null_excesses if x >= real_excess)
    p_null = (count_ge + 1) / (len(null_excesses) + 1)
    return null_median, p_null


def _block_bootstrap_p(
    events: Sequence[CascadeEvent],
    bars: Sequence[MinuteBar],
    horizon_min: int,
    orientation: str,
    baseline_mean: float,
    *,
    seed: int,
    resamples: int,
) -> float:
    pairs = [
        (e, _oriented_return(e.side, orientation, r))
        for e in events
        if (r := _forward_return_bps(bars, e.time, horizon_min)) is not None
    ]
    if len(pairs) < 4:
        return 1.0
    observed = statistics.mean(v for _, v in pairs) - baseline_mean
    by_day: dict[str, list[tuple[CascadeEvent, float]]] = {}
    for event, val in pairs:
        by_day.setdefault(event.time.date().isoformat(), []).append((event, val))
    days = sorted(by_day)
    if not days:
        return 1.0
    rng = random.Random(seed + horizon_min * 17)
    count_le = 0
    for _ in range(resamples):
        sample: list[float] = []
        for _day in range(len(days)):
            bucket = by_day[rng.choice(days)]
            sample.extend(v for _, v in bucket)
        if len(sample) < 4:
            continue
        if statistics.mean(sample) - baseline_mean <= observed:
            count_le += 1
    return (count_le + 1) / (resamples + 1)


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    m = len(p_values)
    adjusted = [1.0] * m
    prev = 0.0
    for rank, (idx, p) in enumerate(indexed, start=1):
        adj = min(1.0, p * (m - rank + 1))
        adj = max(adj, prev)
        prev = adj
        adjusted[idx] = adj
    return adjusted


def analyze_symbol(
    symbol: str,
    events: Sequence[CascadeEvent],
    bars: Sequence[MinuteBar],
    config: ProbeConfig,
) -> SymbolResult:
    sym_events = [e for e in events if e.symbol == symbol]
    horizon_rows: list[HorizonResult] = []
    for horizon in config.horizons_min:
        for orientation in ("fade", "continuation"):
            oriented_edges: list[tuple[datetime, float]] = []
            raw_returns: list[float] = []
            for event in sym_events:
                raw = _forward_return_bps(bars, event.time, horizon)
                if raw is None:
                    continue
                raw_returns.append(raw)
                oriented_edges.append((event.time, _oriented_return(event.side, orientation, raw)))
            if not oriented_edges:
                continue
            mean_oriented = statistics.mean(v for _, v in oriented_edges)
            baseline = _matched_baseline_returns(bars, sym_events, horizon, seed=config.seed)
            baseline_mean = statistics.mean(baseline) if baseline else 0.0
            excess = mean_oriented - baseline_mean
            net_edge = excess - config.taker_rt_bps
            null_median, p_null = _phase_randomized_null_excess(
                sym_events,
                bars,
                horizon,
                orientation,
                seed=config.seed,
                resamples=500,
            )
            beats_null = excess > null_median
            p_boot = _block_bootstrap_p(
                sym_events,
                bars,
                horizon,
                orientation,
                baseline_mean,
                seed=config.seed,
                resamples=config.bootstrap_resamples,
            )
            conc = _day_concentration(oriented_edges)
            conc_ok = conc <= CONCENTRATION_CAP
            gate = (
                net_edge > 0
                and beats_null
                and p_boot < ALPHA
                and conc_ok
                and len(sym_events) >= config.min_events_per_symbol
            )
            horizon_rows.append(
                HorizonResult(
                    horizon_min=horizon,
                    orientation=orientation,
                    event_count=len(sym_events),
                    mean_oriented_bps=mean_oriented,
                    baseline_bps=baseline_mean,
                    excess_bps=excess,
                    net_edge_bps=net_edge,
                    null_median_excess_bps=null_median,
                    beats_null=beats_null,
                    bootstrap_p=p_boot,
                    bootstrap_p_adj=p_boot,
                    max_day_concentration=conc,
                    concentration_ok=conc_ok,
                    symbols_passing=0,
                    gate_pass=gate,
                )
            )
    return SymbolResult(symbol=symbol, event_count=len(sym_events), horizons=tuple(horizon_rows))


def apply_holm_and_pick_best(
    symbol_results: Sequence[SymbolResult],
    config: ProbeConfig,
) -> tuple[list[SymbolResult], HorizonResult | None]:
    updated_results: list[SymbolResult] = []
    best_pass: HorizonResult | None = None

    for sym_result in symbol_results:
        new_horizons: list[HorizonResult] = []
        for h in sym_result.horizons:
            peer_rows = [
                (other.symbol, oh)
                for other in symbol_results
                for oh in other.horizons
                if oh.horizon_min == h.horizon_min and oh.orientation == h.orientation
            ]
            peer_stats = [oh for _, oh in peer_rows]
            adjusted = holm_adjust([p.bootstrap_p for p in peer_stats])
            peer_index = next(
                i for i, (sym, oh) in enumerate(peer_rows) if sym == sym_result.symbol and oh is h
            )
            p_adj = adjusted[peer_index]
            sym_pass = sum(
                1
                for p in peer_stats
                if p.gate_pass and p.net_edge_bps > 0 and p.beats_null and p.concentration_ok
            )
            row = HorizonResult(
                **{
                    **asdict(h),
                    "bootstrap_p_adj": p_adj,
                    "symbols_passing": sym_pass,
                    "gate_pass": h.gate_pass
                    and p_adj < ALPHA
                    and sym_pass >= config.min_symbols_pass,
                }
            )
            new_horizons.append(row)
            if row.gate_pass and (best_pass is None or row.net_edge_bps > best_pass.net_edge_bps):
                best_pass = row
        updated_results.append(
            SymbolResult(
                symbol=sym_result.symbol,
                event_count=sym_result.event_count,
                horizons=tuple(new_horizons),
            )
        )
    return updated_results, best_pass


def decide_verdict(
    symbol_results: Sequence[SymbolResult],
    data_audit: DataAudit,
    config: ProbeConfig,
) -> tuple[str, tuple[str, ...], HorizonResult | None]:
    if data_audit.blocked:
        return (
            BLOCKED_ON_DATA,
            (data_audit.blocked_reason or "data gate failed",),
            None,
        )

    best_pass = max(
        (h for s in symbol_results for h in s.horizons if h.gate_pass),
        key=lambda h: h.net_edge_bps,
        default=None,
    )
    if best_pass is not None:
        return (
            HAS_PULSE,
            (
                f"H1 pass: {best_pass.orientation} @ +{best_pass.horizon_min}m "
                f"net {best_pass.net_edge_bps:+.2f}bps on {best_pass.symbols_passing} symbols "
                f"(p_adj={best_pass.bootstrap_p_adj:.4f})",
            ),
            best_pass,
        )

    reasons: list[str] = []
    for sym in symbol_results:
        if sym.event_count < config.min_events_per_symbol:
            reasons.append(
                f"{sym.symbol}: only {sym.event_count} events (<{config.min_events_per_symbol})"
            )
        best_net = max((h.net_edge_bps for h in sym.horizons), default=float("-inf"))
        reasons.append(
            f"{sym.symbol}: best net edge {best_net:+.2f}bps (RT cost {config.taker_rt_bps}bps)"
        )

    weak = any(
        h.beats_null and h.excess_bps > 0 and h.net_edge_bps <= 0
        for s in symbol_results
        for h in s.horizons
    )
    verdict = WEAK_EDGE if weak else NO_PULSE
    reasons.append("no horizon/orientation clears Holm-adjusted bootstrap + breadth + cost gates")
    if verdict == NO_PULSE:
        reasons.append(
            "measured dead: cascade-proxy excess does not survive 10bps RT cost and/or #118 null"
        )
    return verdict, tuple(reasons), None


def render_report(report: ProbeReport) -> str:
    lines = [
        "# Forced Liquidation / Cascade Flow Probe — Report",
        "",
        f"**Verdict:** **{report.verdict}**",
        f"**Date:** {datetime.now(UTC).date().isoformat()}",
        "**Script:** `scripts/probe_liquidation_cascade.py`",
        "**Spec:** [liquidation-cascade-probe-v0.md](../specs/liquidation-cascade-probe-v0.md)",
        "",
        f"**Window:** {report.window_start.date()} → {report.window_end.date()} (UTC)",
        "",
        "## STEP 0 — Data audit",
        "",
        f"- REST `allForceOrders`: {report.data_audit.rest_all_force_orders}",
        f"- WebSocket path: `{report.data_audit.websocket_path}`",
        f"- UM metrics days loaded: {report.data_audit.metrics_days_loaded}",
        f"- Force orders collected (this run): {report.data_audit.force_orders_collected}",
        f"- Force orders cached (loaded): {report.data_audit.force_orders_cached}",
        f"- Events per symbol: {report.data_audit.events_per_symbol}",
        "",
        "## Frozen cascade-proxy definition",
        "",
        f"- OI drop ≤ {report.config.oi_drop_pct}% (5m)",
        f"- Long-cascade: taker ratio ≤ {report.config.taker_long_cascade}",
        f"- Short-cascade: taker ratio ≥ {report.config.taker_short_cascade}",
        f"- Dedup gap: {report.config.event_gap_min}m",
        "",
        "## Results by symbol",
        "",
    ]
    for sym in report.symbol_results:
        lines.append(f"### {sym.symbol} ({sym.event_count} events)")
        lines.append(
            "| Horizon | Orient | Excess bps | Net bps | p_adj | beats null | conc | pass |"
        )
        lines.append(
            "|---------|--------|----------:|--------:|------:|:----------:|:----:|:----:|"
        )
        for h in sym.horizons:
            lines.append(
                f"| +{h.horizon_min}m | {h.orientation} | {h.excess_bps:+.2f} | "
                f"{h.net_edge_bps:+.2f} | {h.bootstrap_p_adj:.3f} | "
                f"{'Y' if h.beats_null else 'n'} | {h.max_day_concentration:.0%} | "
                f"{'Y' if h.gate_pass else 'n'} |"
            )
        lines.append("")

    lines.append("## Verdict rationale")
    for reason in report.reasons:
        lines.append(f"- {reason}")
    if report.best_horizon:
        bh = report.best_horizon
        lines.append(
            f"- Best passing cell: {bh.orientation} +{bh.horizon_min}m "
            f"({bh.symbols_passing} symbols, net {bh.net_edge_bps:+.2f}bps)"
        )
    return "\n".join(lines)


async def run_probe(config: ProbeConfig) -> ProbeReport:
    configure_logger("INFO")
    logger = get_logger("probe.liquidation_cascade")

    window_end = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=config.end_lag_days
    )
    window_start = window_end - timedelta(days=config.window_days)

    timeout = aiohttp.ClientTimeout(total=120, connect=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        rest_status = await audit_rest_force_orders(session)
        logger.info("REST allForceOrders: %s", rest_status)

        force_collected = await collect_force_orders_ws(
            config.symbols,
            config.collect_seconds,
            config.cache_dir,
        )
        cached_force = load_cached_force_orders(config.cache_dir, config.symbols)
        logger.info("force orders: collected=%d cached=%d", force_collected, len(cached_force))

        all_metrics: list[MetricsRow] = []
        days = _daterange(window_start.date(), (window_end - timedelta(days=1)).date())
        for symbol in config.symbols:
            for day in days:
                rows = await download_metrics_day(session, symbol, day)
                all_metrics.extend(rows)
                await asyncio.sleep(0.02)
        logger.info("metrics rows loaded: %d", len(all_metrics))

        events: list[CascadeEvent] = []
        for symbol in config.symbols:
            sym_metrics = [row for row in all_metrics if row.symbol == symbol]
            events.extend(detect_cascade_events(sym_metrics, config))
        events = [e for e in events if window_start <= e.time < window_end]
        logger.info("cascade-proxy events in window: %d", len(events))

        events_per_symbol = {s: sum(1 for e in events if e.symbol == s) for s in config.symbols}
        blocked = any(events_per_symbol[s] < config.min_events_per_symbol for s in config.symbols)
        blocked_reason: str | None = None
        if blocked:
            blocked_reason = "insufficient cascade events for one or more symbols: " + ", ".join(
                f"{s}={events_per_symbol[s]}" for s in config.symbols
            )

        data_audit = DataAudit(
            rest_all_force_orders=rest_status,
            websocket_path=f"{WS_MARKET_BASE}/<symbol>@forceOrder",
            metrics_days_loaded=len(days),
            force_orders_collected=force_collected,
            force_orders_cached=len(cached_force),
            events_per_symbol=events_per_symbol,
            blocked=blocked,
            blocked_reason=blocked_reason,
        )

        if blocked:
            return ProbeReport(
                config=config,
                window_start=window_start,
                window_end=window_end,
                data_audit=data_audit,
                events=tuple(events),
                symbol_results=(),
                best_horizon=None,
                verdict=BLOCKED_ON_DATA,
                reasons=(blocked_reason or "blocked",),
            )

        symbol_results: list[SymbolResult] = []
        for symbol in config.symbols:
            sym_events = [e for e in events if e.symbol == symbol]
            bars = await fetch_klines_1m(
                session,
                symbol,
                window_start - timedelta(hours=2),
                window_end + timedelta(hours=3),
            )
            logger.info("%s: %d events, %d 1m bars", symbol, len(sym_events), len(bars))
            symbol_results.append(analyze_symbol(symbol, sym_events, bars, config))

    adjusted_results, _ = apply_holm_and_pick_best(symbol_results, config)
    verdict, reasons, best = decide_verdict(adjusted_results, data_audit, config)
    return ProbeReport(
        config=config,
        window_start=window_start,
        window_end=window_end,
        data_audit=data_audit,
        events=tuple(events),
        symbol_results=tuple(adjusted_results),
        best_horizon=best,
        verdict=verdict,
        reasons=reasons,
    )


@dataclass(frozen=True)
class CliArgs:
    config: ProbeConfig
    output_json: Path | None
    output_report: Path | None


def parse_args() -> CliArgs:
    parser = argparse.ArgumentParser(description="A1 liquidation/cascade Gate-1 probe")
    parser.add_argument("--window-days", type=int, default=14)
    parser.add_argument("--collect-seconds", type=int, default=0)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-report", type=Path, default=None)
    args = parser.parse_args()
    cfg = default_config()
    config = ProbeConfig(
        symbols=cfg.symbols,
        window_days=args.window_days,
        end_lag_days=cfg.end_lag_days,
        horizons_min=cfg.horizons_min,
        taker_rt_bps=cfg.taker_rt_bps,
        oi_drop_pct=cfg.oi_drop_pct,
        taker_long_cascade=cfg.taker_long_cascade,
        taker_short_cascade=cfg.taker_short_cascade,
        event_gap_min=cfg.event_gap_min,
        min_events_per_symbol=cfg.min_events_per_symbol,
        min_symbols_pass=cfg.min_symbols_pass,
        bootstrap_resamples=cfg.bootstrap_resamples,
        collect_seconds=args.collect_seconds,
        cache_dir=cfg.cache_dir,
        seed=cfg.seed,
    )
    return CliArgs(config=config, output_json=args.output_json, output_report=args.output_report)


async def main_async() -> int:
    cli = parse_args()
    config = cli.config
    report = await run_probe(config)

    out_dir = Path("research/rbi_loop/liquidation-cascade-v0")
    out_dir.mkdir(parents=True, exist_ok=True)

    json_out = cli.output_json or out_dir / "probe-verdict.json"
    md_out = cli.output_report or Path("docs/reports/liquidation-cascade-probe-v0.md")

    payload = {
        "verdict": report.verdict,
        "window_start": report.window_start.isoformat(),
        "window_end": report.window_end.isoformat(),
        "data_audit": asdict(report.data_audit),
        "events_per_symbol": report.data_audit.events_per_symbol,
        "reasons": list(report.reasons),
        "best_horizon": asdict(report.best_horizon) if report.best_horizon else None,
    }
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_out.write_text(render_report(report) + "\n", encoding="utf-8")

    print(render_report(report))
    print(f"\nWrote {json_out}")
    print(f"Wrote {md_out}")
    print(f"\nVERDICT: {report.verdict}")
    return 0 if report.verdict in {HAS_PULSE, NO_PULSE, WEAK_EDGE} else 1


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
