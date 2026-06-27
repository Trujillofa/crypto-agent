#!/usr/bin/env python3
"""Gate-1 probe for advertised structural behavior in Deriv synthetic indices.

The probe is intentionally read-only. It discovers the frozen universe through
``active_symbols``, downloads one-minute candles through ``ticks_history``, and evaluates
pre-registered family hypotheses against a block-sign null.

See docs/specs/synthetic-index-structural-edge-probe-v0.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.logger import configure_logger, get_logger

DERIV_PUBLIC_WS = "wss://api.derivws.com/trading/v1/options/ws/public"

BLOCKED_ON_DATA = "BLOCKED_ON_DATA"
HAS_PULSE_COST_PROXY = "HAS_PULSE_COST_PROXY"
WEAK_EDGE = "WEAK_EDGE"
NO_PULSE = "NO_PULSE"

CALIBRATION_FRACTION = 0.40
DEFAULT_DAYS = 30
DEFAULT_GRANULARITY_SEC = 60
DEFAULT_PAGE_SIZE = 5000
DEFAULT_COST_BPS = 20.0
DEFAULT_BOOTSTRAP_RESAMPLES = 2000
MIN_BOOTSTRAP_RESAMPLES = 1000
CONCENTRATION_CAP = 0.25
ALPHA = 0.05
RANDOM_SEED = 42


@dataclass(frozen=True)
class InstrumentSpec:
    key: str
    display_name: str
    family: str
    hypothesis: str
    lookback_bars: int
    horizon_bars: int
    min_samples: int
    direction: int = 0
    api_display_name: str | None = None


INSTRUMENTS = (
    InstrumentSpec("volatility_75", "Volatility 75 Index", "volatility", "momentum", 15, 15, 100),
    InstrumentSpec("volatility_100", "Volatility 100 Index", "volatility", "momentum", 15, 15, 100),
    InstrumentSpec("volatility_50", "Volatility 50 Index", "volatility", "momentum", 15, 15, 100),
    InstrumentSpec("crash_1000", "Crash 1000 Index", "crash_boom", "drift", 1, 5, 100, 1),
    InstrumentSpec("boom_1000", "Boom 1000 Index", "crash_boom", "drift", 1, 5, 100, -1),
    InstrumentSpec("crash_500", "Crash 500 Index", "crash_boom", "drift", 1, 5, 100, 1),
    InstrumentSpec("boom_500", "Boom 500 Index", "crash_boom", "drift", 1, 5, 100, -1),
    InstrumentSpec(
        "step",
        "Step Index",
        "step",
        "reversion",
        1,
        1,
        100,
        api_display_name="Step Index 100",
    ),
    InstrumentSpec("jump_100", "Jump 100 Index", "jump", "jump_continuation", 1, 5, 30),
    InstrumentSpec(
        "range_break_100",
        "Range Break 100 Index",
        "range_break",
        "range_break_continuation",
        60,
        15,
        30,
    ),
)

FAMILY_MIN_PASS = {"volatility": 2, "crash_boom": 2, "step": 1, "jump": 1, "range_break": 1}


@dataclass(frozen=True)
class ProbeConfig:
    days: int
    granularity_sec: int
    page_size: int
    round_trip_cost_bps: float
    bootstrap_resamples: int
    calibration_fraction: float
    concentration_cap: float
    cache_dir: Path
    refresh_cache: bool
    allow_partial_universe: bool
    seed: int


@dataclass(frozen=True)
class ActiveSymbol:
    symbol: str
    display_name: str


@dataclass(frozen=True)
class Candle:
    epoch: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Observation:
    epoch: int
    signal: int
    raw_return_bps: float
    oriented_return_bps: float


@dataclass(frozen=True)
class InstrumentAudit:
    key: str
    requested_name: str
    resolved_symbol: str | None
    resolved_name: str | None
    candles: int
    start_epoch: int | None
    end_epoch: int | None
    cache_path: str | None
    error: str | None


@dataclass(frozen=True)
class InstrumentResult:
    key: str
    display_name: str
    symbol: str
    family: str
    hypothesis: str
    calibration_samples: int
    forward_samples: int
    calibration_mean_bps: float
    forward_mean_bps: float
    net_mean_bps: float
    null_median_bps: float
    p_raw: float
    p_adj: float
    max_day_concentration: float
    first_half_mean_bps: float
    second_half_mean_bps: float
    beats_null: bool
    sample_ok: bool
    concentration_ok: bool
    statistical_pass: bool
    economic_pass: bool
    family_pass_count: int
    family_breadth_ok: bool
    gate_pass: bool


@dataclass(frozen=True)
class ProbeReport:
    generated_at: str
    config: ProbeConfig
    audits: tuple[InstrumentAudit, ...]
    results: tuple[InstrumentResult, ...]
    verdict: str
    reasons: tuple[str, ...]


def default_config() -> ProbeConfig:
    return ProbeConfig(
        days=DEFAULT_DAYS,
        granularity_sec=DEFAULT_GRANULARITY_SEC,
        page_size=DEFAULT_PAGE_SIZE,
        round_trip_cost_bps=DEFAULT_COST_BPS,
        bootstrap_resamples=DEFAULT_BOOTSTRAP_RESAMPLES,
        calibration_fraction=CALIBRATION_FRACTION,
        concentration_cap=CONCENTRATION_CAP,
        cache_dir=Path("data/synthetic_indices"),
        refresh_cache=False,
        allow_partial_universe=False,
        seed=RANDOM_SEED,
    )


def _normalize_name(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def resolve_universe(
    payload: Sequence[Mapping[str, Any]],
    specs: Sequence[InstrumentSpec] = INSTRUMENTS,
) -> tuple[dict[str, ActiveSymbol], list[str]]:
    """Resolve exact normalized display names and reject ambiguous matches."""
    by_name: dict[str, list[ActiveSymbol]] = {}
    for row in payload:
        symbol = str(row.get("symbol") or row.get("underlying_symbol") or "").strip()
        display_name = str(
            row.get("display_name") or row.get("underlying_symbol_name") or ""
        ).strip()
        if not symbol or not display_name:
            continue
        by_name.setdefault(_normalize_name(display_name), []).append(
            ActiveSymbol(symbol=symbol, display_name=display_name)
        )

    resolved: dict[str, ActiveSymbol] = {}
    errors: list[str] = []
    for spec in specs:
        api_name = spec.api_display_name or spec.display_name
        matches = by_name.get(_normalize_name(api_name), [])
        if len(matches) == 1:
            resolved[spec.key] = matches[0]
        elif not matches:
            errors.append(f"{spec.display_name}: not returned by active_symbols")
        else:
            symbols = ", ".join(sorted(match.symbol for match in matches))
            errors.append(f"{spec.display_name}: ambiguous active symbols ({symbols})")
    return resolved, errors


class DerivMarketDataClient:
    """Minimal request/response client for public Deriv WebSocket market data."""

    def __init__(self, session: aiohttp.ClientSession, url: str = DERIV_PUBLIC_WS) -> None:
        self._session = session
        self._url = url
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._req_id = 0

    async def __aenter__(self) -> DerivMarketDataClient:
        self._ws = await self._session.ws_connect(self._url, heartbeat=30)
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._ws is not None:
            await self._ws.close()

    async def request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self._ws is None:
            raise RuntimeError("Deriv WebSocket is not connected")
        self._req_id += 1
        req_id = self._req_id
        await self._ws.send_json({**payload, "req_id": req_id})
        while True:
            message = await self._ws.receive(timeout=30)
            if message.type == aiohttp.WSMsgType.TEXT:
                response = json.loads(message.data)
                if response.get("req_id") != req_id:
                    continue
                error = response.get("error")
                if error:
                    code = error.get("code", "DerivError")
                    text = error.get("message", "unknown Deriv API error")
                    raise RuntimeError(f"{code}: {text}")
                return response
            if message.type in {
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.ERROR,
            }:
                raise RuntimeError(f"Deriv WebSocket closed during request: {message.type.name}")

    async def active_symbols(self) -> list[dict[str, Any]]:
        response = await self.request({"active_symbols": "brief"})
        rows = response.get("active_symbols")
        if not isinstance(rows, list):
            raise RuntimeError("active_symbols response did not contain a list")
        return [row for row in rows if isinstance(row, dict)]

    async def candle_page(
        self,
        symbol: str,
        *,
        end_epoch: int,
        count: int,
        granularity_sec: int,
    ) -> list[Candle]:
        response = await self.request(
            {
                "ticks_history": symbol,
                "count": count,
                "end": str(end_epoch),
                "style": "candles",
                "granularity": granularity_sec,
                "adjust_start_time": 1,
            }
        )
        rows = response.get("candles")
        if not isinstance(rows, list):
            raise RuntimeError(f"{symbol}: ticks_history response did not contain candles")
        candles = [
            Candle(
                epoch=int(row["epoch"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            )
            for row in rows
        ]
        return sorted(candles, key=lambda candle: candle.epoch)


def _cache_path(config: ProbeConfig, spec: InstrumentSpec, symbol: str) -> Path:
    return config.cache_dir / f"{spec.key}-{symbol}-{config.granularity_sec}s.json"


def _load_cached_candles(path: Path, start_epoch: int, end_epoch: int) -> list[Candle]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("candles", [])
    candles = [Candle(**row) for row in rows]
    filtered = [row for row in candles if start_epoch <= row.epoch <= end_epoch]
    if not filtered or filtered[0].epoch > start_epoch + 2 * 86400:
        return []
    return filtered


def _save_cached_candles(path: Path, candles: Sequence[Candle]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "candles": [asdict(candle) for candle in candles],
    }
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


async def fetch_candles(
    client: DerivMarketDataClient,
    spec: InstrumentSpec,
    active: ActiveSymbol,
    config: ProbeConfig,
    *,
    end_epoch: int,
) -> tuple[list[Candle], Path]:
    start_epoch = end_epoch - config.days * 86400
    path = _cache_path(config, spec, active.symbol)
    if not config.refresh_cache:
        cached = _load_cached_candles(path, start_epoch, end_epoch)
        if cached:
            return cached, path

    by_epoch: dict[int, Candle] = {}
    cursor = end_epoch
    while cursor >= start_epoch:
        page = await client.candle_page(
            active.symbol,
            end_epoch=cursor,
            count=config.page_size,
            granularity_sec=config.granularity_sec,
        )
        if not page:
            break
        for candle in page:
            if start_epoch <= candle.epoch <= end_epoch:
                by_epoch[candle.epoch] = candle
        earliest = page[0].epoch
        if earliest <= start_epoch or earliest >= cursor:
            break
        cursor = earliest - config.granularity_sec
        await asyncio.sleep(0.05)

    candles = sorted(by_epoch.values(), key=lambda candle: candle.epoch)
    if candles:
        _save_cached_candles(path, candles)
    return candles, path


def _quantile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _one_bar_returns(candles: Sequence[Candle], end: int) -> list[float]:
    return [
        candles[index].close / candles[index - 1].close - 1.0
        for index in range(1, end)
        if candles[index - 1].close > 0
    ]


def _signal_at(
    candles: Sequence[Candle],
    index: int,
    spec: InstrumentSpec,
    *,
    lower_tail: float,
    upper_tail: float,
    jump_threshold: float,
) -> int:
    current = candles[index]
    previous = candles[index - 1]
    one_bar_return = current.close / previous.close - 1.0 if previous.close > 0 else 0.0

    if spec.hypothesis == "momentum":
        anchor = candles[index - spec.lookback_bars].close
        return _sign(current.close / anchor - 1.0) if anchor > 0 else 0
    if spec.hypothesis == "drift":
        if spec.direction > 0 and one_bar_return <= lower_tail:
            return 0
        if spec.direction < 0 and one_bar_return >= upper_tail:
            return 0
        return spec.direction
    if spec.hypothesis == "reversion":
        return -_sign(one_bar_return)
    if spec.hypothesis == "jump_continuation":
        return _sign(one_bar_return) if abs(one_bar_return) >= jump_threshold else 0
    if spec.hypothesis == "range_break_continuation":
        history = candles[index - spec.lookback_bars : index]
        if current.close > max(candle.high for candle in history):
            return 1
        if current.close < min(candle.low for candle in history):
            return -1
        return 0
    raise ValueError(f"Unknown hypothesis: {spec.hypothesis}")


def build_observations(
    candles: Sequence[Candle],
    spec: InstrumentSpec,
    start_index: int,
    end_index: int,
    *,
    threshold_end_index: int,
) -> list[Observation]:
    calibration_returns = _one_bar_returns(candles, threshold_end_index)
    lower_tail = _quantile(calibration_returns, 0.01)
    upper_tail = _quantile(calibration_returns, 0.99)
    jump_threshold = _quantile([abs(value) for value in calibration_returns], 0.99)

    observations: list[Observation] = []
    index = max(start_index, spec.lookback_bars, 1)
    final_index = min(end_index, len(candles) - spec.horizon_bars)
    while index < final_index:
        signal = _signal_at(
            candles,
            index,
            spec,
            lower_tail=lower_tail,
            upper_tail=upper_tail,
            jump_threshold=jump_threshold,
        )
        if signal == 0:
            index += 1
            continue
        entry = candles[index].close
        exit_price = candles[index + spec.horizon_bars].close
        if entry <= 0:
            index += 1
            continue
        raw_bps = (exit_price / entry - 1.0) * 10_000.0
        observations.append(
            Observation(
                epoch=candles[index].epoch,
                signal=signal,
                raw_return_bps=raw_bps,
                oriented_return_bps=signal * raw_bps,
            )
        )
        index += max(1, spec.horizon_bars)
    return observations


def block_sign_null(
    values: Sequence[float],
    *,
    resamples: int,
    seed: int,
) -> tuple[float, float]:
    """Return null median and one-sided p-value from contiguous block sign randomization."""
    if not values:
        return 0.0, 1.0
    observed = statistics.mean(values)
    block_size = max(2, int(math.sqrt(len(values))))
    blocks = [values[index : index + block_size] for index in range(0, len(values), block_size)]
    rng = random.Random(seed)
    null_means: list[float] = []
    for _ in range(resamples):
        randomized: list[float] = []
        for block in blocks:
            sign = rng.choice((-1.0, 1.0))
            randomized.extend(sign * value for value in block)
        null_means.append(statistics.mean(randomized))
    count_ge = sum(value >= observed for value in null_means)
    return statistics.median(null_means), (count_ge + 1) / (len(null_means) + 1)


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(p_values), key=lambda item: item[1])
    adjusted = [1.0] * len(p_values)
    previous = 0.0
    for rank, (index, p_value) in enumerate(indexed):
        candidate = min(1.0, p_value * (len(p_values) - rank))
        candidate = max(previous, candidate)
        adjusted[index] = candidate
        previous = candidate
    return adjusted


def day_concentration(observations: Sequence[Observation]) -> float:
    by_day: dict[str, float] = {}
    for observation in observations:
        day = datetime.fromtimestamp(observation.epoch, tz=UTC).date().isoformat()
        by_day[day] = by_day.get(day, 0.0) + abs(observation.oriented_return_bps)
    total = sum(by_day.values())
    return max(by_day.values(), default=0.0) / total if total > 0 else 0.0


def analyze_instrument(
    spec: InstrumentSpec,
    symbol: str,
    candles: Sequence[Candle],
    config: ProbeConfig,
) -> InstrumentResult:
    split = max(spec.lookback_bars + 2, int(len(candles) * config.calibration_fraction))
    calibration = build_observations(
        candles,
        spec,
        0,
        split,
        threshold_end_index=split,
    )
    forward = build_observations(
        candles,
        spec,
        split,
        len(candles),
        threshold_end_index=split,
    )
    calibration_values = [row.oriented_return_bps for row in calibration]
    forward_values = [row.oriented_return_bps for row in forward]
    calibration_mean = statistics.mean(calibration_values) if calibration_values else 0.0
    forward_mean = statistics.mean(forward_values) if forward_values else 0.0
    stable_seed = config.seed + sum(ord(ch) for ch in spec.key)
    null_median, p_raw = block_sign_null(
        forward_values,
        resamples=config.bootstrap_resamples,
        seed=stable_seed,
    )
    midpoint = len(forward_values) // 2
    first_half = statistics.mean(forward_values[:midpoint]) if midpoint else 0.0
    second_half = statistics.mean(forward_values[midpoint:]) if midpoint else 0.0
    concentration = day_concentration(forward)
    sample_ok = len(calibration) >= spec.min_samples and len(forward) >= spec.min_samples
    concentration_ok = concentration <= config.concentration_cap
    beats_null = forward_mean > null_median
    return InstrumentResult(
        key=spec.key,
        display_name=spec.display_name,
        symbol=symbol,
        family=spec.family,
        hypothesis=spec.hypothesis,
        calibration_samples=len(calibration),
        forward_samples=len(forward),
        calibration_mean_bps=calibration_mean,
        forward_mean_bps=forward_mean,
        net_mean_bps=forward_mean - config.round_trip_cost_bps,
        null_median_bps=null_median,
        p_raw=p_raw,
        p_adj=1.0,
        max_day_concentration=concentration,
        first_half_mean_bps=first_half,
        second_half_mean_bps=second_half,
        beats_null=beats_null,
        sample_ok=sample_ok,
        concentration_ok=concentration_ok,
        statistical_pass=False,
        economic_pass=False,
        family_pass_count=0,
        family_breadth_ok=False,
        gate_pass=False,
    )


def apply_gates(
    results: Sequence[InstrumentResult],
    config: ProbeConfig,
) -> list[InstrumentResult]:
    adjusted = holm_adjust([result.p_raw for result in results])
    first_pass: list[InstrumentResult] = []
    for result, p_adj in zip(results, adjusted, strict=True):
        statistical_pass = (
            result.sample_ok
            and result.calibration_mean_bps > 0
            and result.forward_mean_bps > 0
            and result.beats_null
            and p_adj < ALPHA
            and result.concentration_ok
        )
        first_pass.append(
            replace(
                result,
                p_adj=p_adj,
                statistical_pass=statistical_pass,
                economic_pass=statistical_pass and result.net_mean_bps > 0,
            )
        )

    family_counts: dict[str, int] = {}
    for result in first_pass:
        if result.economic_pass:
            family_counts[result.family] = family_counts.get(result.family, 0) + 1

    gated: list[InstrumentResult] = []
    for result in first_pass:
        required = FAMILY_MIN_PASS[result.family]
        count = family_counts.get(result.family, 0)
        half_consistency = (
            result.first_half_mean_bps > 0 and result.second_half_mean_bps > 0
            if required == 1
            else True
        )
        breadth_ok = count >= required and half_consistency
        gated.append(
            replace(
                result,
                family_pass_count=count,
                family_breadth_ok=breadth_ok,
                gate_pass=result.economic_pass and breadth_ok,
            )
        )
    return gated


def decide_verdict(
    audits: Sequence[InstrumentAudit],
    results: Sequence[InstrumentResult],
    config: ProbeConfig,
) -> tuple[str, tuple[str, ...]]:
    data_errors = [audit for audit in audits if audit.error]
    fetch_errors = [audit for audit in data_errors if audit.resolved_symbol is not None]
    unresolved_errors = [audit for audit in data_errors if audit.resolved_symbol is None]
    sample_errors = [result for result in results if not result.sample_ok]
    if (
        fetch_errors
        or (unresolved_errors and not config.allow_partial_universe)
        or sample_errors
        or not results
    ):
        reportable_errors = fetch_errors + (
            [] if config.allow_partial_universe else unresolved_errors
        )
        reasons = [f"{audit.requested_name}: {audit.error}" for audit in reportable_errors]
        reasons.extend(
            f"{result.display_name}: insufficient samples "
            f"(cal={result.calibration_samples}, fwd={result.forward_samples})"
            for result in sample_errors
        )
        if not results and not reasons:
            reasons.append("no instrument produced an analyzable result")
        elif results and not any(result.statistical_pass for result in results):
            reasons.append(
                f"{len(results)} evaluated instruments: no frozen hypothesis clears "
                "the Holm-adjusted block-sign null"
            )
        return BLOCKED_ON_DATA, tuple(reasons)

    passed = [result for result in results if result.gate_pass]
    if passed:
        names = ", ".join(result.display_name for result in passed)
        return (
            HAS_PULSE_COST_PROXY,
            (
                f"proxy-cost statistical pulse on: {names}",
                "promotion remains blocked until executable venue costs are measured",
            ),
        )

    weak = [
        result
        for result in results
        if result.statistical_pass or (result.p_raw < ALPHA and result.forward_mean_bps > 0)
    ]
    if weak:
        return (
            WEAK_EDGE,
            tuple(
                f"{result.display_name}: gross={result.forward_mean_bps:+.2f}bps "
                f"net={result.net_mean_bps:+.2f}bps p_adj={result.p_adj:.4f} "
                f"concentration={result.max_day_concentration:.1%}"
                for result in weak
            ),
        )
    return (
        NO_PULSE,
        ("no frozen hypothesis beats the block-sign null after Holm correction",),
    )


async def run_probe(config: ProbeConfig) -> ProbeReport:
    logger = get_logger("synthetic-index-probe")
    now = datetime.now(UTC)
    complete_minute = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
    end_epoch = int(complete_minute.timestamp())
    audits: list[InstrumentAudit] = []
    results: list[InstrumentResult] = []

    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with DerivMarketDataClient(session) as discovery_client:
            active_rows = await discovery_client.active_symbols()
        resolved, resolution_errors = resolve_universe(active_rows)
        errors_by_name = {
            error.split(":", maxsplit=1)[0]: error.split(":", maxsplit=1)[1].strip()
            for error in resolution_errors
        }
        for spec in INSTRUMENTS:
            active = resolved.get(spec.key)
            if active is None:
                audits.append(
                    InstrumentAudit(
                        key=spec.key,
                        requested_name=spec.display_name,
                        resolved_symbol=None,
                        resolved_name=None,
                        candles=0,
                        start_epoch=None,
                        end_epoch=None,
                        cache_path=None,
                        error=errors_by_name.get(spec.display_name, "unresolved symbol"),
                    )
                )
                continue
            try:
                async with DerivMarketDataClient(session) as history_client:
                    candles, cache_path = await fetch_candles(
                        history_client,
                        spec,
                        active,
                        config,
                        end_epoch=end_epoch,
                    )
                audit = InstrumentAudit(
                    key=spec.key,
                    requested_name=spec.display_name,
                    resolved_symbol=active.symbol,
                    resolved_name=active.display_name,
                    candles=len(candles),
                    start_epoch=candles[0].epoch if candles else None,
                    end_epoch=candles[-1].epoch if candles else None,
                    cache_path=str(cache_path),
                    error=None if candles else "no candles returned",
                )
                audits.append(audit)
                if candles:
                    results.append(analyze_instrument(spec, active.symbol, candles, config))
                logger.info(
                    "%s resolved=%s candles=%d",
                    spec.display_name,
                    active.symbol,
                    len(candles),
                )
            except (aiohttp.ClientError, TimeoutError, RuntimeError, ValueError) as exc:
                audits.append(
                    InstrumentAudit(
                        key=spec.key,
                        requested_name=spec.display_name,
                        resolved_symbol=active.symbol,
                        resolved_name=active.display_name,
                        candles=0,
                        start_epoch=None,
                        end_epoch=None,
                        cache_path=None,
                        error=str(exc),
                    )
                )

    gated = apply_gates(results, config) if results else []
    verdict, reasons = decide_verdict(audits, gated, config)
    return ProbeReport(
        generated_at=datetime.now(UTC).isoformat(),
        config=config,
        audits=tuple(audits),
        results=tuple(gated),
        verdict=verdict,
        reasons=reasons,
    )


def _format_epoch(epoch: int | None) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat() if epoch is not None else "—"


def render_report(report: ProbeReport) -> str:
    lines = [
        "# Synthetic-Index Structural Edge Probe v0",
        "",
        f"**Verdict:** **{report.verdict}**",
        f"**Generated:** {report.generated_at}",
        "**Spec:** [synthetic-index-structural-edge-probe-v0.md]"
        "(../specs/synthetic-index-structural-edge-probe-v0.md)",
        "**Data:** Deriv public `active_symbols` + `ticks_history` WebSocket endpoints",
        "",
        "## Frozen configuration",
        "",
        f"- Window: latest {report.config.days} complete UTC days",
        f"- Candle: {report.config.granularity_sec}s",
        f"- Calibration/forward split: {report.config.calibration_fraction:.0%}/"
        f"{1 - report.config.calibration_fraction:.0%}",
        f"- Block-sign resamples: {report.config.bootstrap_resamples}",
        f"- Round-trip cost proxy: {report.config.round_trip_cost_bps:.1f} bps",
        f"- Daily concentration cap: {report.config.concentration_cap:.0%}",
        f"- Seed: {report.config.seed}",
        "- Global EMA200 trend filter: false / not applicable",
        "",
        "## Data audit",
        "",
        "| Requested instrument | Resolved symbol | Candles | Coverage UTC | Error |",
        "|---|---:|---:|---|---|",
    ]
    for audit in report.audits:
        lines.append(
            f"| {audit.requested_name} | {audit.resolved_symbol or '—'} | {audit.candles} | "
            f"{_format_epoch(audit.start_epoch)} → {_format_epoch(audit.end_epoch)} | "
            f"{audit.error or '—'} |"
        )

    lines.extend(
        [
            "",
            "## Forward results",
            "",
            "| Instrument | Hypothesis | Cal/Fwd N | Cal bps | Fwd bps | Net bps | "
            "p_adj | Max day | Family pass | Gate |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for result in report.results:
        lines.append(
            f"| {result.display_name} | {result.hypothesis} | "
            f"{result.calibration_samples}/{result.forward_samples} | "
            f"{result.calibration_mean_bps:+.2f} | {result.forward_mean_bps:+.2f} | "
            f"{result.net_mean_bps:+.2f} | {result.p_adj:.4f} | "
            f"{result.max_day_concentration:.1%} | {result.family_pass_count} | "
            f"{'PASS' if result.gate_pass else 'FAIL'} |"
        )

    lines.extend(["", "## Verdict reasons", ""])
    lines.extend(f"- {reason}" for reason in report.reasons)
    lines.extend(
        [
            "",
            "## Interpretation ceiling",
            "",
            "The cost input is a screening proxy because historical candles do not contain executable "
            "Deriv MT5/cTrader bid/ask quotes, commissions, financing, or slippage. Even a proxy-cost "
            "pulse is not deployment evidence; it requires a separate executable-cost study and "
            "forward demo execution.",
            "",
        ]
    )
    return "\n".join(lines)


def report_as_json(report: ProbeReport) -> str:
    payload = asdict(report)
    payload["config"]["cache_dir"] = str(report.config.cache_dir)
    return json.dumps(payload, indent=2, sort_keys=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--granularity", type=int, default=DEFAULT_GRANULARITY_SEC)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--round-trip-cost-bps", type=float, default=DEFAULT_COST_BPS)
    parser.add_argument("--bootstrap-resamples", type=int, default=DEFAULT_BOOTSTRAP_RESAMPLES)
    parser.add_argument("--cache-dir", type=Path, default=Path("data/synthetic_indices"))
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--allow-partial-universe", action="store_true")
    parser.add_argument("--seed", type=int, default=RANDOM_SEED)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--json-report", type=Path)
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> ProbeConfig:
    if args.days < 7:
        raise ValueError("--days must be at least 7")
    if args.granularity <= 0:
        raise ValueError("--granularity must be positive")
    if args.page_size <= 0:
        raise ValueError("--page-size must be positive")
    if args.round_trip_cost_bps < 0:
        raise ValueError("--round-trip-cost-bps cannot be negative")
    if args.bootstrap_resamples < MIN_BOOTSTRAP_RESAMPLES:
        raise ValueError(f"--bootstrap-resamples must be at least {MIN_BOOTSTRAP_RESAMPLES}")
    return replace(
        default_config(),
        days=args.days,
        granularity_sec=args.granularity,
        page_size=args.page_size,
        round_trip_cost_bps=args.round_trip_cost_bps,
        bootstrap_resamples=args.bootstrap_resamples,
        cache_dir=args.cache_dir,
        refresh_cache=args.refresh_cache,
        allow_partial_universe=args.allow_partial_universe,
        seed=args.seed,
    )


async def main(argv: Sequence[str] | None = None) -> int:
    configure_logger("INFO")
    args = parse_args(argv)
    config = config_from_args(args)
    report = await run_probe(config)
    rendered = render_report(report)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(report_as_json(report), encoding="utf-8")
    print(rendered)
    return 2 if report.verdict == BLOCKED_ON_DATA else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
