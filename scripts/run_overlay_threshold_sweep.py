#!/usr/bin/env python3
"""Overlay buy_threshold × frequency sweep per overlay-threshold-frequency-sweep-v0.md.

Sweeps buy_threshold on the frozen sol-1h-trend-pullback-overlay-live lane at
corrected main costs (fee 0.04%, slippage 0.02%, scaled_8h funding, trend filter ON).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
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
from src.db import close_pool, init_pool  # noqa: E402
from src.main import load_settings  # noqa: E402
from src.utils.logger import configure_logger, get_logger  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "research" / "overlay-threshold-sweep"
DEFAULT_REPORT = ROOT / "docs" / "reports" / "overlay-threshold-sweep-2026-06-18.md"

THRESHOLD_GRID: tuple[float, ...] = (0.50, 0.60, 0.70, 0.80, 0.90, 1.00, 1.07, 1.27)
FORWARD_VALIDATABLE_TRADES_PER_MONTH = 2.0

BASE_CONFIG = ROOT / "config" / "settings.sol_1h_trend_pullback_overlay_live.yaml"
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
    """Fixed overlay lane (pre-registered)."""

    base_config: Path
    symbol: str
    timeframe: str
    requested_start: str
    requested_end: str
    train_months: int
    test_months: int
    bootstrap: int
    gate_profile: str


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
    parser = argparse.ArgumentParser(description="Overlay buy_threshold frequency sweep")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write-report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--threshold",
        action="append",
        dest="thresholds",
        type=float,
        help="Run only these buy_threshold values (default: full grid)",
    )
    return parser.parse_args()


def _read_yaml(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at root of {path}")
    return data


def _threshold_label(threshold: float) -> str:
    return f"{threshold:.2f}"


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


def _resolve_threshold_config(
    *,
    lane: LaneSpec,
    threshold: float,
    output_dir: Path,
) -> Path:
    merged = _read_yaml(lane.base_config)
    strategy = merged.setdefault("strategy", {})
    if not isinstance(strategy, dict):
        raise ValueError("strategy must be a mapping")
    aggregator = strategy.setdefault("aggregator", {})
    if not isinstance(aggregator, dict):
        raise ValueError("strategy.aggregator must be a mapping")
    aggregator["buy_threshold"] = threshold
    aggregator["buy_threshold_uptrend"] = threshold

    per_symbol = strategy.setdefault("per_symbol_aggregator_config", {})
    if not isinstance(per_symbol, dict):
        raise ValueError("strategy.per_symbol_aggregator_config must be a mapping")
    sol = per_symbol.setdefault(lane.symbol, {})
    if not isinstance(sol, dict):
        raise ValueError(f"per_symbol config for {lane.symbol} must be a mapping")
    sol["buy_threshold"] = threshold
    sol["buy_threshold_uptrend"] = threshold

    resolved_dir = output_dir / "resolved"
    resolved_dir.mkdir(parents=True, exist_ok=True)
    resolved_path = resolved_dir / f"{_threshold_label(threshold)}.yaml"
    with resolved_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(merged, handle, sort_keys=False)
    return resolved_path


def _threshold_binding_audit(
    resolved_path: Path, *, symbol: str, threshold: float
) -> dict[str, float]:
    raw = _read_yaml(resolved_path)
    strategy = raw.get("strategy", {})
    assert isinstance(strategy, dict)
    aggregator = strategy.get("aggregator", {})
    assert isinstance(aggregator, dict)
    per_symbol_root = strategy.get("per_symbol_aggregator_config", {})
    assert isinstance(per_symbol_root, dict)
    per_symbol = per_symbol_root.get(symbol, {})
    assert isinstance(per_symbol, dict)

    binding = {
        "aggregator.buy_threshold": float(aggregator["buy_threshold"]),
        "aggregator.buy_threshold_uptrend": float(aggregator["buy_threshold_uptrend"]),
        f"per_symbol_aggregator_config.{symbol}.buy_threshold": float(per_symbol["buy_threshold"]),
        f"per_symbol_aggregator_config.{symbol}.buy_threshold_uptrend": float(
            per_symbol["buy_threshold_uptrend"]
        ),
    }
    for key, value in binding.items():
        if value != threshold:
            raise AssertionError(f"{key}={value} != swept threshold {threshold}")
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


def _oos_months(*, wfo_windows: int, test_months: int) -> float:
    return float(wfo_windows * test_months)


def _summary_row(
    *,
    threshold: float,
    payload: dict[str, object],
    test_months: int,
) -> dict[str, object]:
    summary = payload["summary"]
    assert isinstance(summary, dict)
    wfo_windows = int(summary["wfo_windows"])
    wfo_total_trades = int(summary["wfo_total_trades"])
    oos_months = _oos_months(wfo_windows=wfo_windows, test_months=test_months)
    trades_per_month = wfo_total_trades / oos_months if oos_months else 0.0
    return {
        "buy_threshold": threshold,
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


def _select_thresholds(requested: list[float] | None) -> tuple[float, ...]:
    if requested is None:
        return THRESHOLD_GRID
    grid = set(THRESHOLD_GRID)
    selected: list[float] = []
    for value in requested:
        if value not in grid:
            raise SystemExit(
                f"Unknown threshold {value}; allowed values: "
                + ", ".join(_threshold_label(item) for item in THRESHOLD_GRID)
            )
        selected.append(value)
    return tuple(selected)


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


def _decision_verdict(rows: list[dict[str, object]]) -> tuple[str, str]:
    rescuable = _rescuable_rows(rows)
    if rescuable:
        lowest = min(rescuable, key=lambda row: float(row["buy_threshold"]))
        best_sharpe = max(rescuable, key=lambda row: float(row["wfo_mean_sharpe"]))
        verdict = (
            "**OVERLAY RESCUABLE** — at least one threshold delivers forward-validatable "
            f"frequency (≥{FORWARD_VALIDATABLE_TRADES_PER_MONTH:.0f} trades/month) and passes "
            "the standard gate at corrected costs. "
            f"Lowest rescuable threshold: **{_threshold_label(float(lowest['buy_threshold']))}** "
            f"({float(lowest['trades_per_month']):.2f} trades/month). "
            f"Best-Sharpe rescuable: **{_threshold_label(float(best_sharpe['buy_threshold']))}** "
            f"(Sharpe {float(best_sharpe['wfo_mean_sharpe']):.2f}). "
            "**Next step:** re-validate that config as a new strategy (WFO + bootstrap=1000) "
            "before any live change. **Do not auto-promote.**"
        )
        return "rescuable", verdict

    frequency_only = _frequency_only_rows(rows)
    if frequency_only:
        verdict = (
            "**OVERLAY NOT A VIABLE FORWARD VEHICLE** — tradeable frequency appears only where "
            "the standard gate fails. The overlay edge depends on a confluence gate that is "
            "live-untradeable at corrected costs. Consolidation direction: document/accept or pivot."
        )
        return "frequency_only", verdict

    max_trades = max(float(row["trades_per_month"]) for row in rows)
    if max_trades < FORWARD_VALIDATABLE_TRADES_PER_MONTH:
        verdict = (
            f"**UPSTREAM CONSTRAINT** — whole grid yields <{FORWARD_VALIDATABLE_TRADES_PER_MONTH:.0f} "
            "trades/month even at buy_threshold=0.50. The binding limiter is likely not the "
            "aggregator (regime filter / data / sizing). **Next step:** run filter-OFF follow-up, "
            "then escalate if still starved."
        )
        return "upstream", verdict

    verdict = (
        "**INCONCLUSIVE** — no threshold passes both frequency and gate checks under the "
        "pre-registered rule. Review the frontier table for manual follow-up."
    )
    return "inconclusive", verdict


async def _run_threshold(
    *,
    lane: LaneSpec,
    threshold: float,
    resolved_config: Path,
    effective_start: str,
    effective_end: str,
    cost_profile: CostProfile,
    db_config: dict[str, object],
    gates: GateConfig,
) -> dict[str, object]:
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
        db_config=db_config,
        manage_pool=False,
    )
    cost_audit = audit["cost_profile"]
    filter_audit = audit["global_trend_filter"]
    assert isinstance(cost_audit, dict)
    assert isinstance(filter_audit, dict)
    _assert_cost_audit(cost_audit)
    _assert_filter_audit(filter_audit)
    threshold_binding = _threshold_binding_audit(
        resolved_config,
        symbol=lane.symbol,
        threshold=threshold,
    )

    return {
        "buy_threshold": threshold,
        "summary": asdict(summary),
        "gates": asdict(gates),
        "windows": [asdict(window) for window in windows],
        "resolved_backtest_config": audit["backtest_config"],
        "cost_audit": cost_audit,
        "global_trend_filter_audit": filter_audit,
        "buy_threshold_binding_audit": threshold_binding,
        "effective_start": effective_start,
        "effective_end": effective_end,
    }


def _render_report(
    *,
    rows: list[dict[str, object]],
    coverage: dict[str, str | bool],
    output_path: Path,
) -> str:
    _, verdict = _decision_verdict(rows)

    lines: list[str] = []
    lines.append("# Overlay Threshold × Frequency Sweep — 2026-06-18")
    lines.append("")
    lines.append(
        "**Spec:** [overlay-threshold-frequency-sweep-v0.md]"
        "(../specs/overlay-threshold-frequency-sweep-v0.md)"
    )
    lines.append(
        "**Lane:** `sol-1h-trend-pullback-overlay-live` — SOLUSDT 1h, corrected costs, "
        "global trend filter ON (production mirror)."
    )
    lines.append("")
    lines.append("## Data coverage")
    lines.append("")
    lines.append(f"- Requested span: {coverage['requested_start']} → {coverage['requested_end']}")
    lines.append(f"- DB coverage: {coverage['data_start'][:10]} → {coverage['data_end'][:10]}")
    lines.append(
        f"- **Effective span used:** {coverage['effective_start']} → {coverage['effective_end']}"
    )
    if coverage.get("clamped_start") or coverage.get("clamped_end"):
        lines.append(f"- Clamped: start={coverage['clamped_start']}, end={coverage['clamped_end']}")
    lines.append("")
    lines.append("## Frontier (corrected costs, filter ON)")
    lines.append("")
    lines.append(
        "| buy_threshold | trades | trades/mo | wfo_return% | Sharpe | max_DD% | "
        "profit_conc% | p_loss% | passes |"
    )
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in sorted(rows, key=lambda item: float(item["buy_threshold"])):
        p_loss = row.get("bootstrap_p_loss_pct")
        p_loss_cell = "—" if p_loss is None else f"{float(p_loss):.1f}"
        passes = "PASS" if bool(row["passes_gates"]) else "FAIL"
        lines.append(
            f"| {float(row['buy_threshold']):.2f} | {int(row['wfo_total_trades'])} | "
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
    logger = get_logger("overlay_threshold_sweep")

    thresholds = _select_thresholds(args.thresholds)
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
    coverage: dict[str, str] = {}

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

        gates = _gate_config_from_profile(LANE.gate_profile)
        for threshold in thresholds:
            resolved_config = _resolve_threshold_config(
                lane=LANE,
                threshold=threshold,
                output_dir=output_dir,
            )
            logger.info("Running buy_threshold=%s", _threshold_label(threshold))
            payload = await _run_threshold(
                lane=LANE,
                threshold=threshold,
                resolved_config=resolved_config,
                effective_start=effective_start,
                effective_end=effective_end,
                cost_profile=COST_PROFILE,
                db_config=db_config,
                gates=gates,
            )
            payload["coverage"] = coverage
            artifact = output_dir / f"{_threshold_label(threshold)}.json"
            with artifact.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            row = _summary_row(threshold=threshold, payload=payload, test_months=LANE.test_months)
            print(f"\n=== buy_threshold={_threshold_label(threshold)} ===")
            print(json.dumps(payload["cost_audit"], indent=2))
            print(json.dumps(payload["buy_threshold_binding_audit"], indent=2))
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

    report = _render_report(rows=summary_rows, coverage=coverage, output_path=args.write_report)
    print(f"\nReport written: {args.write_report}")
    print(report.split("## Decision")[-1].strip())


if __name__ == "__main__":
    asyncio.run(main())
