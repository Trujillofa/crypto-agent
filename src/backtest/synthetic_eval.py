"""Run the real backtest engine over seeded synthetic paths and score pass rate."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from src.backtest.artifacts import fingerprint_rows
from src.backtest.engine import BacktestEngine
from src.backtest.experiment_autopilot import compound_returns_pct, max_drawdown_from_returns
from src.backtest.models import BacktestConfig, BacktestResult
from src.backtest.synthetic import (
    RegimeParams,
    fit_two_state_regime,
    generate_regime_path,
    generate_stress_path,
)
from src.backtest.synthetic_reader import (
    DEFAULT_WARMUP_BARS,
    SyntheticIndicatorReader,
    blowout_funding_settlements,
    eight_hour_settlements,
)
from src.features.reader import FundingSettlement
from src.ingest.models import Ohlcv

DEFAULT_EVAL_BARS = 240
MIN_SCORED_PATHS = 3
MIN_REGIME_SCORED_PATHS = 2
MIN_STRESS_SCORED_PATHS = 2
TARGET_TRADES_PER_PATH = 4
MIN_EVAL_BARS = 480
MAX_EVAL_BARS = 4000
_TIMEFRAME_HOURS = {"15m": 0.25, "1h": 1.0, "4h": 4.0, "1d": 24.0}
DEFAULT_REGIME_PARAMS = RegimeParams(
    mu_calm=0.0,
    sigma_calm=0.008,
    mu_stress=-0.001,
    sigma_stress=0.025,
    p_calm_to_stress=0.08,
    p_stress_to_calm=0.2,
    p_start_stress=0.15,
)
STRESS_SCENARIOS = ("march_2020_gap", "funding_blowout", "flat_wide_range")


@dataclass(frozen=True)
class SyntheticPathRecord:
    name: str
    kind: str
    seed: int
    trades: int
    total_return_pct: float
    max_drawdown_pct: float
    outcome: str  # pass|fail|skip


@dataclass(frozen=True)
class SyntheticFitRecord:
    fit_start: str
    fit_end: str
    row_count: int
    row_fingerprint: str
    params: dict[str, float]


@dataclass(frozen=True)
class SyntheticRuntimeEvidence:
    min_eval_bars: int
    max_eval_bars: int
    generate_min_ms: float
    generate_max_ms: float


@dataclass(frozen=True)
class SyntheticEvalResult:
    status: str  # 'not_run' | 'scored' | 'inconclusive'
    pass_rate_pct: float  # 0.0 when inconclusive or not_run — not a verdict
    scored_paths: int
    total_paths: int
    zero_trade_paths: int
    regime_scored: int = 0
    stress_scored: int = 0
    regime_pass_rate_pct: float = 0.0
    stress_pass_rate_pct: float = 0.0
    paths: tuple[SyntheticPathRecord, ...] = ()
    fit: SyntheticFitRecord | None = None
    seed: int = 0
    eval_bars_used: int = 0
    runtime: SyntheticRuntimeEvidence | None = None


NOT_RUN_RESULT = SyntheticEvalResult(
    status="not_run",
    pass_rate_pct=0.0,
    scored_paths=0,
    total_paths=0,
    zero_trade_paths=0,
)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def bars_from_range(start: str, end: str, timeframe: str) -> int:
    """Count bars between ISO datetimes for 1h/4h/1d/15m (naive timestamps = UTC)."""
    elapsed_hours = (_parse_iso(end) - _parse_iso(start)).total_seconds() / 3600.0
    bar_hours = _TIMEFRAME_HOURS.get(timeframe)
    if bar_hours is None:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    return max(1, math.floor(elapsed_hours / bar_hours))


def eval_bars_from_trade_rate(
    *,
    historical_trades: int,
    historical_bars: int,
    target_trades_per_path: int = TARGET_TRADES_PER_PATH,
) -> int:
    """Size the eval window from historical trades-per-bar, clamped to production bounds."""
    if historical_trades <= 0 or historical_bars <= 0:
        return MIN_EVAL_BARS
    needed = math.ceil(target_trades_per_path / (historical_trades / historical_bars))
    return min(MAX_EVAL_BARS, max(MIN_EVAL_BARS, needed))


def decimal_close_returns(closes: Sequence[float]) -> list[float]:
    """Close-to-close fractional returns using Decimal(str(price))."""
    if len(closes) < 2:
        return []
    out: list[float] = []
    for prev, curr in zip(closes, closes[1:], strict=False):
        if prev == 0.0:
            out.append(0.0)
        else:
            out.append(float(Decimal(str(curr)) / Decimal(str(prev)) - 1))
    return out


def score_path(
    returns: Sequence[float],
    *,
    kind: Literal["regime", "stress"],
    max_drawdown_pct: float = 10.0,
    min_return_pct: float = 0.0,
) -> bool | None:
    """Score one path. Empty (no-trade) returns None and leaves the denominator."""
    if not returns:
        return None
    values = list(returns)
    if kind == "regime":
        return compound_returns_pct(values) >= min_return_pct
    return max_drawdown_from_returns(values) <= max_drawdown_pct


def score_engine_result(
    result: BacktestResult,
    *,
    kind: Literal["regime", "stress"],
    max_drawdown_pct: float = 10.0,
    min_return_pct: float = 0.0,
) -> bool | None:
    """Score engine equity metrics. Zero-trade paths leave the denominator."""
    if result.total_trades == 0:
        return None
    if kind == "regime":
        return result.total_return_pct >= min_return_pct
    return result.max_drawdown * 100.0 <= max_drawdown_pct


def _outcome_label(passed: bool | None) -> str:
    if passed is None:
        return "skip"
    return "pass" if passed else "fail"


def _class_pass_rate(records: Sequence[SyntheticPathRecord]) -> float:
    if not records:
        return 0.0
    return 100.0 * sum(1 for rec in records if rec.outcome == "pass") / len(records)


def _rate_from_outcomes(outcomes: Sequence[bool | None]) -> SyntheticEvalResult:
    scored = [outcome for outcome in outcomes if outcome is not None]
    total = len(outcomes)
    zeros = sum(1 for outcome in outcomes if outcome is None)
    if len(scored) < MIN_SCORED_PATHS:
        return SyntheticEvalResult("inconclusive", 0.0, len(scored), total, zeros)
    rate = 100.0 * sum(1 for outcome in scored if outcome) / len(scored)
    return SyntheticEvalResult("scored", rate, len(scored), total, zeros)


def _rate_from_named(records: Sequence[SyntheticPathRecord]) -> SyntheticEvalResult:
    regime = [rec for rec in records if rec.kind == "regime" and rec.outcome != "skip"]
    stress = [rec for rec in records if rec.kind == "stress" and rec.outcome != "skip"]
    scored = [rec for rec in records if rec.outcome != "skip"]
    zeros = sum(1 for rec in records if rec.outcome == "skip")
    coverage_ok = len(regime) >= MIN_REGIME_SCORED_PATHS and len(stress) >= MIN_STRESS_SCORED_PATHS
    status = "scored" if coverage_ok else "inconclusive"
    if not scored or status == "inconclusive":
        rate = 0.0
    else:
        rate = 100.0 * sum(1 for rec in scored if rec.outcome == "pass") / len(scored)
    return SyntheticEvalResult(
        status=status,
        pass_rate_pct=rate,
        scored_paths=len(scored),
        total_paths=len(records),
        zero_trade_paths=zeros,
        regime_scored=len(regime),
        stress_scored=len(stress),
        regime_pass_rate_pct=_class_pass_rate(regime),
        stress_pass_rate_pct=_class_pass_rate(stress),
        paths=tuple(records),
    )


def _config_for_window(config: BacktestConfig, start: str, end: str) -> BacktestConfig:
    return replace(config, start_date=start, end_date=end)


async def _run_path(
    config: BacktestConfig,
    candles: Sequence[Ohlcv],
    warmup_bars: int,
    *,
    settlements: Sequence[FundingSettlement] | None = None,
) -> BacktestResult:
    reader = SyntheticIndicatorReader(candles, warmup_bars=warmup_bars, funding=settlements)
    cfg = _config_for_window(config, reader.eval_start.isoformat(), reader.eval_end.isoformat())
    return await BacktestEngine(cfg, reader).run()


def _settlements_for(
    config: BacktestConfig,
    candles: Sequence[Ohlcv],
    warmup_bars: int,
    scenario: str | None,
) -> list[FundingSettlement]:
    if not (config.futures_mode and config.execution_profile == "execution_parity_v2"):
        return []
    eval_candles = candles[warmup_bars:]
    start, end = eval_candles[0].open_time, eval_candles[-1].open_time
    if scenario == "funding_blowout":
        return blowout_funding_settlements(eval_candles)
    return eight_hour_settlements(start, end, rate=0.0)


def _generation_ms(
    n_bars: int,
    *,
    seed: int,
    symbol: str,
    timeframe: str,
    start_time: datetime,
    params: RegimeParams,
) -> float:
    started = time.perf_counter()
    generate_regime_path(
        params,
        n_bars=n_bars,
        start_price=100.0,
        seed=seed,
        symbol=symbol,
        timeframe=timeframe,
        start_time=start_time,
    )
    return (time.perf_counter() - started) * 1000.0


def runtime_bound_evidence(
    *,
    seed: int,
    symbol: str,
    timeframe: str,
    start_time: datetime,
    params: RegimeParams,
) -> SyntheticRuntimeEvidence:
    return SyntheticRuntimeEvidence(
        min_eval_bars=MIN_EVAL_BARS,
        max_eval_bars=MAX_EVAL_BARS,
        generate_min_ms=_generation_ms(
            MIN_EVAL_BARS,
            seed=seed,
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_time,
            params=params,
        ),
        generate_max_ms=_generation_ms(
            MAX_EVAL_BARS,
            seed=seed,
            symbol=symbol,
            timeframe=timeframe,
            start_time=start_time,
            params=params,
        ),
    )


def _path_record(
    *,
    name: str,
    kind: Literal["regime", "stress"],
    seed: int,
    result: BacktestResult,
    passed: bool | None,
) -> SyntheticPathRecord:
    return SyntheticPathRecord(
        name=name,
        kind=kind,
        seed=seed,
        trades=result.total_trades,
        total_return_pct=result.total_return_pct,
        max_drawdown_pct=result.max_drawdown * 100.0,
        outcome=_outcome_label(passed),
    )


def _resolve_fit(
    *,
    require_fit: bool,
    fit_start: str | None,
    fit_end: str | None,
    fit_closes: Sequence[float] | None,
    fit_rows: Sequence[Mapping[str, object]] | None,
    regime_params: RegimeParams | None,
) -> tuple[RegimeParams, SyntheticFitRecord | None]:
    if require_fit and (not fit_start or not fit_end or fit_closes is None or len(fit_closes) < 2):
        raise ValueError("require_fit needs fit_start, fit_end, and at least two fit_closes")
    if fit_closes is None:
        params = regime_params if regime_params is not None else DEFAULT_REGIME_PARAMS
        return params, None
    returns = decimal_close_returns(fit_closes)
    params = fit_two_state_regime(returns)
    rows_for_fp: Sequence[Mapping[str, object]]
    if fit_rows is not None:
        rows_for_fp = fit_rows
        row_count = len(fit_rows)
    else:
        rows_for_fp = [{"close": close} for close in fit_closes]
        row_count = len(fit_closes)
    fit = SyntheticFitRecord(
        fit_start=fit_start or "",
        fit_end=fit_end or "",
        row_count=row_count,
        row_fingerprint=fingerprint_rows(rows_for_fp),
        params={key: float(value) for key, value in asdict(params).items()},
    )
    return params, fit


async def evaluate_synthetic_pass_rate(
    config: BacktestConfig,
    *,
    n_regime_paths: int = 3,
    include_stress: bool = True,
    warmup_bars: int = DEFAULT_WARMUP_BARS,
    eval_bars: int | None = None,
    historical_trades: int = 0,
    historical_bars: int = 0,
    seed: int = 42,
    start_price: float = 100.0,
    regime_params: RegimeParams | None = None,
    max_drawdown_pct: float = 10.0,
    min_return_pct: float = 0.0,
    fit_start: str | None = None,
    fit_end: str | None = None,
    fit_closes: Sequence[float] | None = None,
    fit_rows: Sequence[Mapping[str, object]] | None = None,
    require_fit: bool = False,
) -> SyntheticEvalResult:
    """Score regime (return floor) and stress (drawdown) paths. Zero-trade paths leave the rate."""
    if eval_bars is None:
        eval_bars = eval_bars_from_trade_rate(
            historical_trades=historical_trades,
            historical_bars=historical_bars,
        )
    params, fit_record = _resolve_fit(
        require_fit=require_fit,
        fit_start=fit_start,
        fit_end=fit_end,
        fit_closes=fit_closes,
        fit_rows=fit_rows,
        regime_params=regime_params,
    )
    n_bars = warmup_bars + eval_bars
    records: list[SyntheticPathRecord] = []
    path_symbol = config.symbol
    path_timeframe = config.timeframe
    path_start = _parse_iso(config.start_date)

    for i in range(n_regime_paths):
        path_seed = seed + i
        candles, _states = generate_regime_path(
            params,
            n_bars=n_bars,
            start_price=start_price,
            seed=path_seed,
            symbol=path_symbol,
            timeframe=path_timeframe,
            start_time=path_start,
        )
        settlements = _settlements_for(config, candles, warmup_bars, None)
        result = await _run_path(config, candles, warmup_bars, settlements=settlements)
        records.append(
            _path_record(
                name=f"regime_{i}",
                kind="regime",
                seed=path_seed,
                result=result,
                passed=score_engine_result(
                    result,
                    kind="regime",
                    max_drawdown_pct=max_drawdown_pct,
                    min_return_pct=min_return_pct,
                ),
            )
        )

    if include_stress:
        for j, scenario in enumerate(STRESS_SCENARIOS):
            path_seed = seed + 100 + j
            candles = generate_stress_path(
                scenario,
                n_bars=n_bars,
                start_price=start_price,
                seed=path_seed,
                symbol=path_symbol,
                timeframe=path_timeframe,
                start_time=path_start,
            )
            settlements = _settlements_for(config, candles, warmup_bars, scenario)
            result = await _run_path(config, candles, warmup_bars, settlements=settlements)
            records.append(
                _path_record(
                    name=scenario,
                    kind="stress",
                    seed=path_seed,
                    result=result,
                    passed=score_engine_result(
                        result,
                        kind="stress",
                        max_drawdown_pct=max_drawdown_pct,
                        min_return_pct=min_return_pct,
                    ),
                )
            )

    rated = _rate_from_named(records)
    runtime = runtime_bound_evidence(
        seed=seed,
        symbol=path_symbol,
        timeframe=path_timeframe,
        start_time=path_start,
        params=params,
    )
    return replace(
        rated,
        fit=fit_record,
        seed=seed,
        eval_bars_used=eval_bars,
        runtime=runtime,
    )


async def maybe_evaluate_synthetic_pass_rate(
    config: BacktestConfig,
    *,
    enabled: bool,
    n_regime_paths: int = 3,
    include_stress: bool = True,
    warmup_bars: int = DEFAULT_WARMUP_BARS,
    eval_bars: int | None = None,
    historical_trades: int = 0,
    historical_bars: int = 0,
    seed: int = 42,
    start_price: float = 100.0,
    regime_params: RegimeParams | None = None,
    max_drawdown_pct: float = 10.0,
    min_return_pct: float = 0.0,
    fit_start: str | None = None,
    fit_end: str | None = None,
    fit_closes: Sequence[float] | None = None,
    fit_rows: Sequence[Mapping[str, object]] | None = None,
) -> SyntheticEvalResult:
    """Skip path generation when the synthetic gate is disabled."""
    if not enabled:
        return NOT_RUN_RESULT
    return await evaluate_synthetic_pass_rate(
        config,
        n_regime_paths=n_regime_paths,
        include_stress=include_stress,
        warmup_bars=warmup_bars,
        eval_bars=eval_bars,
        historical_trades=historical_trades,
        historical_bars=historical_bars,
        seed=seed,
        start_price=start_price,
        regime_params=regime_params,
        max_drawdown_pct=max_drawdown_pct,
        min_return_pct=min_return_pct,
        fit_start=fit_start,
        fit_end=fit_end,
        fit_closes=fit_closes,
        fit_rows=fit_rows,
        require_fit=True,
    )
