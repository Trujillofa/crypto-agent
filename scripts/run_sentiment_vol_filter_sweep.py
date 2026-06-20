#!/usr/bin/env python3
"""Sentiment-macro volatility-filter sweep per sentiment-vol-filter-sweep-v0.md.

Sweeps atr_pct_threshold (plus one filter-off arm) on the frozen sentiment-macro lane
at corrected main costs with constant bullish-72 sentiment replay.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.experiment_autopilot import (  # noqa: E402
    _db_config_from_settings,
    _resolve_data_range,
    run_experiment_evaluation,
)
from scripts.run_autoresearch import _gate_config_from_profile  # noqa: E402
from src.backtest.cost_overrides import CostProfile, corrected_main_cost_profile  # noqa: E402
from src.backtest.experiment_autopilot import GateConfig  # noqa: E402
from src.backtest.sentiment_replay import ReplaySentimentScorer  # noqa: E402
from src.db import close_pool, init_pool  # noqa: E402
from src.main import load_settings  # noqa: E402
from src.utils.logger import configure_logger, get_logger  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "research" / "sentiment-vol-filter-sweep"
DEFAULT_REPORT = ROOT / "docs" / "reports" / "sentiment-vol-filter-sweep-2026-06-19.md"
SYNTHETIC_SENTIMENT_LOG = DEFAULT_OUTPUT / "synthetic-sentiment-72.jsonl"

THRESHOLD_GRID: tuple[float, ...] = (0.005, 0.0065, 0.0080, 0.0085, 0.0100, 0.0125)
FILTER_OFF_ARM = "filter_off"
SYNTHETIC_SENTIMENT_SCORE = 72.0
REPLAY_MAX_AGE_HOURS = 24.0
MIN_SENTIMENT_HIT_RATE = 0.99
FORWARD_VALIDATABLE_TRADES_PER_MONTH = 2.0

BASE_CONFIG = ROOT / "config" / "settings.sentiment_macro.yaml"
SYMBOL = "SOLUSDT"
TIMEFRAME = "1h"
REQUESTED_START = "2024-01-01"
REQUESTED_END = "2026-06-01"
TRAIN_MONTHS = 6
TEST_MONTHS = 3
BOOTSTRAP = 500
GATE_PROFILE = "standard"

COST_PROFILE = corrected_main_cost_profile(apply_global_trend_filter=True)


@dataclass(frozen=True)
class LaneSpec:
    """Fixed sentiment-macro lane (pre-registered)."""

    base_config: Path
    symbol: str
    timeframe: str
    requested_start: str
    requested_end: str
    train_months: int
    test_months: int
    bootstrap: int
    gate_profile: str


@dataclass(frozen=True)
class SweepArm:
    """One swept arm: threshold value or filter disabled."""

    arm_id: str
    atr_pct_threshold: float | None = None
    volatility_regime_filter: bool | None = None


LANE = LaneSpec(
    base_config=BASE_CONFIG,
    symbol=SYMBOL,
    timeframe=TIMEFRAME,
    requested_start=REQUESTED_START,
    requested_end=REQUESTED_END,
    train_months=TRAIN_MONTHS,
    test_months=TEST_MONTHS,
    bootstrap=BOOTSTRAP,
    gate_profile=GATE_PROFILE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sentiment-macro volatility-filter sweep")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write-report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--threshold",
        action="append",
        dest="thresholds",
        type=float,
        help="Run only these atr_pct_threshold values (default: full grid)",
    )
    parser.add_argument(
        "--arm",
        action="append",
        dest="arms",
        help="Run only these arm ids (threshold label or filter_off)",
    )
    return parser.parse_args()


def _read_yaml(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at root of {path}")
    return data


def _threshold_arm_id(threshold: float) -> str:
    return f"{threshold:.4f}".rstrip("0").rstrip(".")


def _default_arms() -> tuple[SweepArm, ...]:
    return tuple(
        SweepArm(arm_id=_threshold_arm_id(value), atr_pct_threshold=value)
        for value in THRESHOLD_GRID
    ) + (SweepArm(arm_id=FILTER_OFF_ARM, volatility_regime_filter=False),)


def _parse_iso(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _clamp_date_range(
    *,
    requested_start: str,
    requested_end: str,
    data_start: str,
    data_end: str,
) -> tuple[str, str, dict[str, str | bool]]:
    req_start = _parse_iso(requested_start)
    req_end = _parse_iso(requested_end)
    db_start = _parse_iso(data_start)
    db_end = _parse_iso(data_end)

    effective_start = max(req_start, db_start)
    effective_end = min(req_end, db_end)
    if effective_start >= effective_end:
        raise RuntimeError(
            f"No overlap between requested range ({requested_start}→{requested_end}) "
            f"and DB coverage ({data_start}→{data_end})"
        )

    coverage: dict[str, str | bool] = {
        "requested_start": requested_start,
        "requested_end": requested_end,
        "data_start": data_start,
        "data_end": data_end,
        "effective_start": effective_start.date().isoformat(),
        "effective_end": effective_end.date().isoformat(),
        "clamped_start": effective_start > req_start,
        "clamped_end": effective_end < req_end,
    }
    return effective_start.date().isoformat(), effective_end.date().isoformat(), coverage


def _assert_single_strategy(raw: dict[str, object]) -> list[dict[str, object]]:
    strategy = raw.get("strategy", {})
    assert isinstance(strategy, dict)
    strategies = strategy.get("strategies", [])
    assert isinstance(strategies, list)
    if len(strategies) != 1:
        raise AssertionError(
            f"Expected exactly one strategy in {BASE_CONFIG.name}, found {len(strategies)}"
        )
    entry = strategies[0]
    assert isinstance(entry, dict)
    return strategies


def _resolve_arm_config(*, lane: LaneSpec, arm: SweepArm, output_dir: Path) -> Path:
    merged = _read_yaml(lane.base_config)
    strategies = _assert_single_strategy(merged)
    config = strategies[0].setdefault("config", {})
    assert isinstance(config, dict)

    if arm.arm_id == FILTER_OFF_ARM:
        config["volatility_regime_filter"] = False
    else:
        assert arm.atr_pct_threshold is not None
        config["atr_pct_threshold"] = arm.atr_pct_threshold
        config["volatility_regime_filter"] = True

    resolved_dir = output_dir / "resolved"
    resolved_dir.mkdir(parents=True, exist_ok=True)
    resolved_path = resolved_dir / f"{arm.arm_id}.yaml"
    with resolved_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(merged, handle, sort_keys=False)
    return resolved_path


def _arm_binding_audit(resolved_path: Path, *, arm: SweepArm) -> dict[str, object]:
    raw = _read_yaml(resolved_path)
    strategies = _assert_single_strategy(raw)
    config = strategies[0].get("config", {})
    assert isinstance(config, dict)

    if arm.arm_id == FILTER_OFF_ARM:
        binding = {
            "strategy.strategies[0].config.volatility_regime_filter": bool(
                config.get("volatility_regime_filter")
            ),
            "strategy.strategies[0].config.atr_pct_threshold": float(
                config.get("atr_pct_threshold", 0.005)
            ),
        }
        if binding["strategy.strategies[0].config.volatility_regime_filter"] is not False:
            raise AssertionError("filter_off arm must set volatility_regime_filter=false")
        return binding

    assert arm.atr_pct_threshold is not None
    binding = {
        "strategy.strategies[0].config.atr_pct_threshold": float(config["atr_pct_threshold"]),
        "strategy.strategies[0].config.volatility_regime_filter": bool(
            config.get("volatility_regime_filter", True)
        ),
    }
    if binding["strategy.strategies[0].config.atr_pct_threshold"] != arm.atr_pct_threshold:
        raise AssertionError(
            f"atr_pct_threshold={binding['strategy.strategies[0].config.atr_pct_threshold']} "
            f"!= swept value {arm.atr_pct_threshold}"
        )
    if binding["strategy.strategies[0].config.volatility_regime_filter"] is not True:
        raise AssertionError("threshold arms must keep volatility_regime_filter=true")
    return binding


def _assert_cost_audit(cost_audit: dict[str, object]) -> None:
    if cost_audit.get("fee_rate") != 0.0004:
        raise AssertionError(f"fee_rate mismatch: {cost_audit.get('fee_rate')}")
    if cost_audit.get("slippage_pct") != 0.0002:
        raise AssertionError(f"slippage_pct mismatch: {cost_audit.get('slippage_pct')}")
    if cost_audit.get("funding_cadence") != "scaled_8h":
        raise AssertionError(f"funding_cadence mismatch: {cost_audit.get('funding_cadence')}")
    if cost_audit.get("apply_global_trend_filter") is not True:
        raise AssertionError(
            f"apply_global_trend_filter mismatch: {cost_audit.get('apply_global_trend_filter')}"
        )


def _assert_filter_audit(filter_audit: dict[str, object]) -> None:
    if filter_audit.get("active") is not True:
        raise AssertionError(f"global trend filter expected ON, got {filter_audit}")


def _build_synthetic_sentiment_log(
    *,
    output_path: Path,
    symbol: str,
    effective_start: str,
    effective_end: str,
    score: float,
) -> Path:
    start = _parse_iso(effective_start) - timedelta(hours=24)
    end = _parse_iso(effective_end)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with output_path.open("w", encoding="utf-8") as handle:
        cursor = start
        while cursor <= end:
            event = {
                "type": "sentiment_score",
                "ts": cursor.isoformat(),
                "payload": {"symbol": symbol, "score": score},
            }
            handle.write(json.dumps(event) + "\n")
            count += 1
            cursor += timedelta(hours=1)

    if count == 0:
        raise RuntimeError("Synthetic sentiment log is empty")
    return output_path


async def _audit_sentiment_replay(
    *,
    replay_path: Path,
    symbol: str,
    effective_start: str,
    effective_end: str,
) -> dict[str, object]:
    scorer = ReplaySentimentScorer(
        replay_path,
        max_age_seconds=REPLAY_MAX_AGE_HOURS * 3600.0,
    )
    start = _parse_iso(effective_start)
    end = _parse_iso(effective_end)
    cursor = start
    while cursor <= end:
        await scorer.get_score(symbol, at_time=cursor)
        cursor += timedelta(hours=1)

    stats = scorer.stats()
    lookups = stats["hits"] + stats["misses"]
    hit_rate = stats["hits"] / lookups if lookups else 0.0
    if hit_rate < MIN_SENTIMENT_HIT_RATE:
        raise AssertionError(
            f"Sentiment replay hit-rate {hit_rate:.4f} < {MIN_SENTIMENT_HIT_RATE:.2f}: {stats}"
        )
    return {
        **stats,
        "lookups": lookups,
        "hit_rate": hit_rate,
        "constant_score": SYNTHETIC_SENTIMENT_SCORE,
        "max_age_hours": REPLAY_MAX_AGE_HOURS,
        "replay_path": str(replay_path),
    }


def _oos_months(*, wfo_windows: int, test_months: int) -> float:
    return float(wfo_windows * test_months)


def _summary_row(
    *, arm: SweepArm, payload: dict[str, object], test_months: int
) -> dict[str, object]:
    summary = payload["summary"]
    assert isinstance(summary, dict)
    wfo_windows = int(summary["wfo_windows"])
    wfo_total_trades = int(summary["wfo_total_trades"])
    oos_months = _oos_months(wfo_windows=wfo_windows, test_months=test_months)
    trades_per_month = wfo_total_trades / oos_months if oos_months else 0.0
    return {
        "arm_id": arm.arm_id,
        "atr_pct_threshold": arm.atr_pct_threshold,
        "volatility_regime_filter": arm.volatility_regime_filter,
        "wfo_total_trades": wfo_total_trades,
        "trades_per_month": trades_per_month,
        "wfo_total_return_pct": summary["wfo_total_return_pct"],
        "wfo_mean_sharpe": summary["wfo_mean_sharpe"],
        "max_drawdown_pct": summary["max_drawdown_pct"],
        "profit_concentration_pct": summary["profit_concentration_pct"],
        "bootstrap_p_loss_pct": summary.get("bootstrap_p_loss_pct"),
        "passes_gates": summary["passes_gates"],
        "failure_reasons": summary.get("failure_reasons", []),
        "oos_months": oos_months,
    }


def _select_arms(
    *,
    thresholds: list[float] | None,
    arm_ids: list[str] | None,
) -> tuple[SweepArm, ...]:
    if thresholds is not None and arm_ids is not None:
        raise SystemExit("Use only one of --threshold or --arm")

    allowed_thresholds = {_threshold_arm_id(value): value for value in THRESHOLD_GRID}
    allowed_arm_ids = set(allowed_thresholds) | {FILTER_OFF_ARM}

    if thresholds is not None:
        selected: list[SweepArm] = []
        grid = set(THRESHOLD_GRID)
        for value in thresholds:
            if value not in grid:
                raise SystemExit(
                    "Unknown threshold "
                    f"{value}; allowed values: {', '.join(_threshold_arm_id(v) for v in THRESHOLD_GRID)}"
                )
            selected.append(SweepArm(arm_id=_threshold_arm_id(value), atr_pct_threshold=value))
        return tuple(selected)

    if arm_ids is not None:
        selected_arms: list[SweepArm] = []
        for arm_id in arm_ids:
            if arm_id not in allowed_arm_ids:
                raise SystemExit(
                    f"Unknown arm {arm_id}; allowed values: {', '.join(sorted(allowed_arm_ids))}"
                )
            if arm_id == FILTER_OFF_ARM:
                selected_arms.append(
                    SweepArm(arm_id=FILTER_OFF_ARM, volatility_regime_filter=False)
                )
            else:
                selected_arms.append(
                    SweepArm(
                        arm_id=arm_id,
                        atr_pct_threshold=allowed_thresholds[arm_id],
                    )
                )
        return tuple(selected_arms)

    return _default_arms()


def _rescuable_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        row
        for row in rows
        if bool(row["passes_gates"])
        and float(row["trades_per_month"]) >= FORWARD_VALIDATABLE_TRADES_PER_MONTH
    ]


def _frequency_only_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        row
        for row in rows
        if not bool(row["passes_gates"])
        and float(row["trades_per_month"]) >= FORWARD_VALIDATABLE_TRADES_PER_MONTH
    ]


def _filter_off_row(rows: list[dict[str, object]]) -> dict[str, object] | None:
    for row in rows:
        if row["arm_id"] == FILTER_OFF_ARM:
            return row
    return None


def _decision_verdict(rows: list[dict[str, object]]) -> tuple[str, str]:
    rescuable = _rescuable_rows(rows)
    if rescuable:
        threshold_arms = [
            row for row in rescuable if row["arm_id"] != FILTER_OFF_ARM and row["atr_pct_threshold"]
        ]
        lowest = (
            min(threshold_arms, key=lambda row: float(row["atr_pct_threshold"]))
            if threshold_arms
            else min(rescuable, key=lambda row: float(row["trades_per_month"]))
        )
        best_sharpe = max(rescuable, key=lambda row: float(row["wfo_mean_sharpe"]))
        lowest_label = (
            f"atr_pct_threshold={float(lowest['atr_pct_threshold']):.4f}"
            if lowest.get("atr_pct_threshold") is not None
            else str(lowest["arm_id"])
        )
        best_label = (
            f"atr_pct_threshold={float(best_sharpe['atr_pct_threshold']):.4f}"
            if best_sharpe.get("atr_pct_threshold") is not None
            else str(best_sharpe["arm_id"])
        )
        verdict = (
            "**RECALIBRATION CANDIDATE** — at least one arm delivers forward-validatable "
            f"frequency (≥{FORWARD_VALIDATABLE_TRADES_PER_MONTH:.0f} trades/month) and passes "
            "the standard gate at corrected costs. "
            f"Lowest-vol passing arm: **{lowest_label}** "
            f"({float(lowest['trades_per_month']):.2f} trades/month). "
            f"Best-Sharpe passing arm: **{best_label}** "
            f"(Sharpe {float(best_sharpe['wfo_mean_sharpe']):.2f}). "
            "**Next step:** forward-validate (WFO + bootstrap=1000) and prefer migrating to a "
            "percentile-based vol gate via `atr_percentile`. **Do not auto-promote.**"
        )
        return "rescuable", verdict

    frequency_only = _frequency_only_rows(rows)
    if frequency_only:
        verdict = (
            "**VEHICLE DEAD** — tradeable frequency appears only where the standard gate fails. "
            "The strategy edge does not survive at corrected costs even recalibrated. "
            "Consolidation rec. #2: accept terminal state."
        )
        return "frequency_only", verdict

    filter_off = _filter_off_row(rows)
    max_trades = max(float(row["trades_per_month"]) for row in rows)
    if (
        filter_off is not None
        and float(filter_off["trades_per_month"]) < FORWARD_VALIDATABLE_TRADES_PER_MONTH
    ):
        verdict = (
            f"**RSI/BB CONJUNCTION BINDING** — whole grid yields <"
            f"{FORWARD_VALIDATABLE_TRADES_PER_MONTH:.0f} trades/month even with "
            "volatility_regime_filter disabled. Sentiment is held bullish-constant (72), so it "
            "is not the limiter. Document and escalate; do not silently close."
        )
        return "upstream", verdict

    if max_trades < FORWARD_VALIDATABLE_TRADES_PER_MONTH:
        verdict = (
            f"**STARVED** — no arm reaches {FORWARD_VALIDATABLE_TRADES_PER_MONTH:.0f} trades/month. "
            "Review frontier for manual follow-up."
        )
        return "starved", verdict

    verdict = (
        "**INCONCLUSIVE** — no arm passes both frequency and gate checks under the "
        "pre-registered rule. Review the frontier table."
    )
    return "inconclusive", verdict


async def _run_arm(
    *,
    lane: LaneSpec,
    arm: SweepArm,
    resolved_config: Path,
    effective_start: str,
    effective_end: str,
    replay_path: Path,
    cost_profile: CostProfile,
    db_config: dict[str, object],
    gates: GateConfig,
) -> dict[str, object]:
    sentiment_audit = await _audit_sentiment_replay(
        replay_path=replay_path,
        symbol=lane.symbol,
        effective_start=effective_start,
        effective_end=effective_end,
    )
    summary, _, windows, _, audit = await run_experiment_evaluation(
        settings_path=resolved_config,
        symbol=lane.symbol,
        timeframe=lane.timeframe,
        start=effective_start,
        end=effective_end,
        train_months=lane.train_months,
        test_months=lane.test_months,
        bootstrap=lane.bootstrap,
        gates=gates,
        cost_profile=cost_profile,
        replay_sentiment_path=str(replay_path),
        replay_sentiment_max_age_hours=REPLAY_MAX_AGE_HOURS,
        db_config=db_config,
        manage_pool=False,
    )
    cost_audit = audit["cost_profile"]
    filter_audit = audit["global_trend_filter"]
    assert isinstance(cost_audit, dict)
    assert isinstance(filter_audit, dict)
    _assert_cost_audit(cost_audit)
    _assert_filter_audit(filter_audit)
    arm_binding = _arm_binding_audit(resolved_config, arm=arm)

    return {
        "arm_id": arm.arm_id,
        "atr_pct_threshold": arm.atr_pct_threshold,
        "volatility_regime_filter": arm.volatility_regime_filter,
        "summary": asdict(summary),
        "gates": asdict(gates),
        "windows": [asdict(window) for window in windows],
        "resolved_backtest_config": audit["backtest_config"],
        "cost_audit": cost_audit,
        "global_trend_filter_audit": filter_audit,
        "arm_binding_audit": arm_binding,
        "sentiment_replay_audit": sentiment_audit,
        "effective_start": effective_start,
        "effective_end": effective_end,
    }


def _render_report(
    *,
    rows: list[dict[str, object]],
    coverage: dict[str, str | bool],
    sentiment_note: str,
    output_path: Path,
) -> str:
    _, verdict = _decision_verdict(rows)

    lines: list[str] = []
    lines.append("# Sentiment-Macro Volatility-Filter Sweep — 2026-06-19")
    lines.append("")
    lines.append(
        "**Spec:** [sentiment-vol-filter-sweep-v0.md](../specs/sentiment-vol-filter-sweep-v0.md)"
    )
    lines.append(
        "**Lane:** `sentiment-macro` — SOLUSDT 1h, corrected costs, global trend filter ON, "
        f"constant sentiment replay score **{SYNTHETIC_SENTIMENT_SCORE:.0f}**."
    )
    lines.append("")
    lines.append("## Data coverage")
    lines.append("")
    lines.append(f"- Requested span: {coverage['requested_start']} → {coverage['requested_end']}")
    lines.append(
        f"- DB coverage: {str(coverage['data_start'])[:10]} → {str(coverage['data_end'])[:10]}"
    )
    lines.append(
        f"- **Effective span used:** {coverage['effective_start']} → {coverage['effective_end']}"
    )
    if coverage.get("clamped_start") or coverage.get("clamped_end"):
        lines.append(f"- Clamped: start={coverage['clamped_start']}, end={coverage['clamped_end']}")
    lines.append("")
    lines.append("## Sentiment replay (held constant)")
    lines.append("")
    lines.append(sentiment_note)
    lines.append("")
    lines.append("## Confounds (documented, not corrected)")
    lines.append("")
    lines.append(
        "1. **Constant-72 vs live [50,65) bars (~14%).** Holding 72 applies the +0.15 boost on "
        "100% of bars vs ~85.7% live (boost 0.15 vs 0.05 on the remainder). Slightly optimistic "
        "confidence on ~14% of bars; does not change gate-pass logic (sentiment never < 35 live)."
    )
    lines.append(
        "2. **Historical track record under the cost bug.** The 94 prior live trades ran at the "
        "old ~0.4% RT / ~8× funding defaults. A passing arm here still needs fresh forward "
        "validation at corrected costs."
    )
    lines.append("")
    lines.append("## Frontier (corrected costs, filter ON)")
    lines.append("")
    lines.append(
        "| arm | trades | trades/mo | wfo_return% | Sharpe | max_DD% | "
        "profit_conc% | p_loss% | passes |"
    )
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in sorted(
        rows,
        key=lambda item: (
            1 if item["arm_id"] == FILTER_OFF_ARM else 0,
            float(item["atr_pct_threshold"] or 0.0),
        ),
    ):
        p_loss = row.get("bootstrap_p_loss_pct")
        p_loss_cell = "—" if p_loss is None else f"{float(p_loss):.1f}"
        passes = "PASS" if bool(row["passes_gates"]) else "FAIL"
        arm_label = (
            FILTER_OFF_ARM
            if row["arm_id"] == FILTER_OFF_ARM
            else f"{float(row['atr_pct_threshold']):.4f}"
        )
        lines.append(
            f"| {arm_label} | {int(row['wfo_total_trades'])} | "
            f"{float(row['trades_per_month']):.2f} | {float(row['wfo_total_return_pct']):.2f} | "
            f"{float(row['wfo_mean_sharpe']):.2f} | {float(row['max_drawdown_pct']):.2f} | "
            f"{float(row['profit_concentration_pct']):.1f} | {p_loss_cell} | {passes} |"
        )
    lines.append("")
    lines.append("## Decision")
    lines.append("")
    lines.append(verdict)
    lines.append("")
    lines.append(
        f"_Pre-registered frequency floor: trades_per_month ≥ "
        f"{FORWARD_VALIDATABLE_TRADES_PER_MONTH:.0f}._"
    )
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines)
    output_path.write_text(content, encoding="utf-8")
    return content


async def main() -> None:
    args = parse_args()
    configure_logger("INFO")
    logger = get_logger("sentiment_vol_filter_sweep")

    arms = _select_arms(thresholds=args.thresholds, arm_ids=args.arms)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = load_settings(LANE.base_config)
    db_config = _db_config_from_settings(settings)
    if not db_config.get("password"):
        import os

        db_config["password"] = os.getenv("POSTGRES_PASSWORD", "change_me")
    logger.info("DB target: %s:%s", db_config["host"], db_config["port"])
    await init_pool(db_config)

    all_results: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []
    coverage: dict[str, str | bool] = {}
    sentiment_note = ""

    try:
        data_start, data_end = await _resolve_data_range(LANE.symbol, LANE.timeframe)
        effective_start, effective_end, coverage = _clamp_date_range(
            requested_start=LANE.requested_start,
            requested_end=LANE.requested_end,
            data_start=data_start,
            data_end=data_end,
        )
        logger.info(
            "Effective backtest span: %s → %s (requested %s → %s)",
            effective_start,
            effective_end,
            LANE.requested_start,
            LANE.requested_end,
        )

        replay_path = _build_synthetic_sentiment_log(
            output_path=output_dir / SYNTHETIC_SENTIMENT_LOG.name,
            symbol=LANE.symbol,
            effective_start=effective_start,
            effective_end=effective_end,
            score=SYNTHETIC_SENTIMENT_SCORE,
        )
        baseline_sentiment_audit = await _audit_sentiment_replay(
            replay_path=replay_path,
            symbol=LANE.symbol,
            effective_start=effective_start,
            effective_end=effective_end,
        )
        replay_rel = replay_path.relative_to(ROOT)
        sentiment_note = (
            f"- Replay log: `{replay_rel}`\n"
            f"- Constant score: **{SYNTHETIC_SENTIMENT_SCORE:.0f}** (observed live median; "
            "bullish regime, never below FUD gate 35)\n"
            f"- Max age: {REPLAY_MAX_AGE_HOURS:.0f}h\n"
            f"- Scorer hit-rate: **{float(baseline_sentiment_audit['hit_rate']):.4f}** "
            f"({baseline_sentiment_audit['hits']}/{baseline_sentiment_audit['lookups']} lookups, "
            f"{baseline_sentiment_audit['misses']} misses)"
        )

        gates = _gate_config_from_profile(LANE.gate_profile)
        for arm in arms:
            resolved_config = _resolve_arm_config(lane=LANE, arm=arm, output_dir=output_dir)
            logger.info("Running arm=%s", arm.arm_id)
            payload = await _run_arm(
                lane=LANE,
                arm=arm,
                resolved_config=resolved_config,
                effective_start=effective_start,
                effective_end=effective_end,
                replay_path=replay_path,
                cost_profile=COST_PROFILE,
                db_config=db_config,
                gates=gates,
            )
            payload["coverage"] = coverage
            artifact = output_dir / f"{arm.arm_id}.json"
            with artifact.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            row = _summary_row(arm=arm, payload=payload, test_months=LANE.test_months)
            print(f"\n=== arm={arm.arm_id} ===")
            print(json.dumps(payload["cost_audit"], indent=2))
            print(json.dumps(payload["arm_binding_audit"], indent=2))
            print(json.dumps(payload["sentiment_replay_audit"], indent=2))
            print(
                f"trades={row['wfo_total_trades']} trades/mo={float(row['trades_per_month']):.2f} "
                f"return={float(row['wfo_total_return_pct']):.2f}% "
                f"sharpe={float(row['wfo_mean_sharpe']):.2f} "
                f"passes={row['passes_gates']}"
            )
            all_results.append(payload)
            summary_rows.append(row)
    finally:
        await close_pool()

    combined_path = output_dir / "combined_results.json"
    with combined_path.open("w", encoding="utf-8") as handle:
        json.dump(all_results, handle, indent=2)

    report = _render_report(
        rows=summary_rows,
        coverage=coverage,
        sentiment_note=sentiment_note,
        output_path=args.write_report,
    )
    print(f"\nReport written: {args.write_report}")
    print(report.split("## Decision")[-1].strip())


if __name__ == "__main__":
    asyncio.run(main())
