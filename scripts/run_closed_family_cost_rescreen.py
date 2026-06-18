#!/usr/bin/env python3
"""Closed-family cost-corrected re-screen per closed-family-cost-corrected-rescreen-v0.md.

Re-runs frozen mean-reversion lanes at corrected main defaults (fee 0.04%, slippage
0.02%, 8h funding) with trend filter OFF (Cell A) vs ON (Cell B). Best-of per lane.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.experiment_autopilot import (  # noqa: E402
    _db_config_from_settings,
    run_experiment_evaluation,
)
from scripts.run_autoresearch import _gate_config_from_profile  # noqa: E402
from scripts.run_cost_realism_rerun import _resolve_lane_config  # noqa: E402
from src.backtest.cost_overrides import CostProfile, corrected_main_cost_profile  # noqa: E402
from src.backtest.experiment_autopilot import GateConfig  # noqa: E402
from src.db import close_pool, init_pool  # noqa: E402
from src.main import load_settings  # noqa: E402
from src.utils.logger import configure_logger, get_logger  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = ROOT / "research" / "closed-family-cost-rescreen"
DEFAULT_REPORT = ROOT / "docs" / "reports" / "closed-family-cost-corrected-rescreen-2026-06-18.md"


@dataclass(frozen=True)
class OriginalVerdict:
    """Legacy-cost reference verdict (cited, not re-run)."""

    wfo_return_pct: float | None
    wfo_sharpe: float | None
    wfo_trades: int | None
    max_drawdown_pct: float | None
    profit_concentration: float | None
    verdict: str
    source: str
    note: str = ""


@dataclass(frozen=True)
class LaneSpec:
    """Frozen mean-reversion lane (pre-registered)."""

    lane_id: str
    label: str
    rationale: str
    base_config: Path
    overlay: Path | None
    symbol: str
    timeframe: str
    gate_profile: str
    start: str
    end: str
    train_months: int
    test_months: int
    bootstrap: int
    original: OriginalVerdict
    skipped: bool = False
    skip_reason: str = ""


FROZEN_LANES: tuple[LaneSpec, ...] = (
    LaneSpec(
        lane_id="sol-4h-rsi-reversal",
        label="SOLUSDT 4h rsi_reversal standalone",
        rationale=(
            "Production/autoresearch mean-reversion vote on SOL 4h (BASE_STRATEGY_CONFIGS "
            "params). Original closure: five-strategy stack 0/704 config-search passes."
        ),
        base_config=ROOT / "config" / "settings.autoresearch.yaml",
        overlay=ROOT / "config" / "autoresearch" / "overlays" / "rescreen-sol-4h-rsi-reversal.yaml",
        symbol="SOLUSDT",
        timeframe="4h",
        gate_profile="standard",
        start="2024-01-01",
        end="2026-06-01",
        train_months=6,
        test_months=3,
        bootstrap=500,
        original=OriginalVerdict(
            wfo_return_pct=None,
            wfo_sharpe=None,
            wfo_trades=None,
            max_drawdown_pct=20.66,
            profit_concentration=None,
            verdict="FAIL",
            source="docs/reports/current-strategy-review-2026-03-03.md (SOL 4h stack)",
            note=(
                "Standalone legacy WFO not archived; ensemble best-cluster DD ~20.7%, "
                "bootstrap P(loss) 48–56%, concentration fail, 0/704 passes."
            ),
        ),
    ),
    LaneSpec(
        lane_id="avax-4h-bollinger-strategy",
        label="AVAXUSDT 4h bollinger_bounce standalone",
        rationale=(
            "AVAX 4h WFO sweep best-shape config BB_D0.0_RSI30_70_TS720_SL2.0_TP3.0 "
            "(docs/reports/avax-wfo-bollinger-20260408-153456.json)."
        ),
        base_config=ROOT / "config" / "settings.autoresearch.yaml",
        overlay=ROOT
        / "config"
        / "autoresearch"
        / "overlays"
        / "rescreen-avax-4h-bollinger-strategy.yaml",
        symbol="AVAXUSDT",
        timeframe="4h",
        gate_profile="standard",
        start="2024-02-11",
        end="2026-04-08",
        train_months=6,
        test_months=3,
        bootstrap=500,
        original=OriginalVerdict(
            wfo_return_pct=-25.27,
            wfo_sharpe=-1.45,
            wfo_trades=65,
            max_drawdown_pct=60.31,
            profit_concentration=None,
            verdict="FAIL",
            source="docs/reports/avax-wfo-bollinger-20260408-153456.json (legacy costs)",
            note="AVAX WFO oos_* metrics under pre-#94 engine defaults.",
        ),
    ),
    LaneSpec(
        lane_id="avax-4h-mean-reversion",
        label="AVAXUSDT 4h mean_reversion standalone",
        rationale=(
            "AVAX 4h WFO best candidate MR_L120_ZE2.0_ZX0.25_TS1440_SL2.5_TP3.0 — "
            "positive OOS return but sparse trades."
        ),
        base_config=ROOT / "config" / "settings.autoresearch.yaml",
        overlay=None,
        symbol="AVAXUSDT",
        timeframe="4h",
        gate_profile="standard",
        start="2024-02-11",
        end="2026-04-08",
        train_months=6,
        test_months=3,
        bootstrap=500,
        original=OriginalVerdict(
            wfo_return_pct=14.31,
            wfo_sharpe=1.61,
            wfo_trades=4,
            max_drawdown_pct=5.29,
            profit_concentration=None,
            verdict="FAIL",
            source="docs/reports/avax-wfo-mean_reversion-20260408-153450.json (legacy costs)",
            note="Failed oos_trades / oos_win_rate gates; pair-spread strategy.",
        ),
        skipped=True,
        skip_reason=(
            "Config not runnable on current harness: MeanReversionStrategy is not registered "
            "in settings YAML registry and indicators table lacks pair_close_price required by "
            "src/strategy/mean_reversion.py. Per brief: document and skip — no new param search."
        ),
    ),
    LaneSpec(
        lane_id="eth-4h-range-reversion-bounded",
        label="ETHUSDT 4h range_reversion_bounded (bollinger_bounce)",
        rationale=(
            "Wave 7 near-miss (+13.55% OOS, 24 WFO trades, Sharpe 0.48). "
            "Same overlay as cost-realism rerun #92."
        ),
        base_config=ROOT / "config" / "settings.autoresearch.yaml",
        overlay=ROOT
        / "config"
        / "autoresearch"
        / "overlays"
        / "cost-realism-eth-4h-range-reversion-bounded.yaml",
        symbol="ETHUSDT",
        timeframe="4h",
        gate_profile="standard",
        start="2024-01-01",
        end="2026-06-01",
        train_months=6,
        test_months=3,
        bootstrap=100,
        original=OriginalVerdict(
            wfo_return_pct=1.27,
            wfo_sharpe=-0.71,
            wfo_trades=15,
            max_drawdown_pct=28.69,
            profit_concentration=100.0,
            verdict="FAIL",
            source="research/cost-realism-rerun/eth-4h-range-reversion-bounded-legacy.json",
            note="Ledger Wave 7 NEAR_MISS: +13.55% OOS at b=100 but DD/P(loss)/Sharpe fail.",
        ),
    ),
)


@dataclass(frozen=True)
class FilterCell:
    """One trend-filter cell at corrected main costs."""

    cell_id: str
    label: str
    cost_profile: CostProfile


FILTER_CELLS: tuple[FilterCell, ...] = (
    FilterCell(
        cell_id="cell_a",
        label="corrected cost + filter OFF",
        cost_profile=corrected_main_cost_profile(apply_global_trend_filter=False),
    ),
    FilterCell(
        cell_id="cell_b",
        label="corrected cost + filter ON",
        cost_profile=corrected_main_cost_profile(apply_global_trend_filter=True),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Closed-family cost-corrected re-screen")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--write-report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--lane", action="append", dest="lanes", help="Run only these lane_ids")
    return parser.parse_args()


def _verdict_label(passes: bool) -> str:
    return "PASS" if passes else "FAIL"


def _summary_row(payload: dict[str, object]) -> dict[str, object]:
    summary = payload["summary"]
    assert isinstance(summary, dict)
    return {
        "cell_id": payload["cell_id"],
        "label": payload["label"],
        "total_return_pct": summary["total_return_pct"],
        "wfo_return_pct": summary["wfo_total_return_pct"],
        "wfo_sharpe": summary["wfo_mean_sharpe"],
        "wfo_trades": summary["wfo_total_trades"],
        "max_drawdown_pct": summary["max_drawdown_pct"],
        "profit_concentration": summary["profit_concentration_pct"],
        "passes_gates": summary["passes_gates"],
        "verdict": _verdict_label(bool(summary["passes_gates"])),
        "failure_reasons": summary.get("failure_reasons", []),
    }


def _fmt_metric(value: float | int | None, *, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def _pick_best_cell(rows: dict[str, dict[str, object]]) -> str:
    """Best-of screen: PASS first, else highest wfo_sharpe."""

    passing = [cid for cid, row in rows.items() if bool(row["passes_gates"])]
    if passing:
        return max(passing, key=lambda cid: float(rows[cid]["wfo_sharpe"]))
    return max(rows, key=lambda cid: float(rows[cid]["wfo_sharpe"]))


async def _run_cell(
    *,
    lane: LaneSpec,
    resolved_config: Path,
    cell: FilterCell,
    db_config: dict[str, object],
    gates: GateConfig,
) -> dict[str, object]:
    summary, _, windows, _, audit = await run_experiment_evaluation(
        settings_path=resolved_config,
        symbol=lane.symbol,
        timeframe=lane.timeframe,
        start=lane.start,
        end=lane.end,
        train_months=lane.train_months,
        test_months=lane.test_months,
        bootstrap=lane.bootstrap,
        gates=gates,
        cost_profile=cell.cost_profile,
        db_config=db_config,
        manage_pool=False,
    )
    return {
        "lane_id": lane.lane_id,
        "cell_id": cell.cell_id,
        "label": cell.label,
        "summary": asdict(summary),
        "gates": asdict(gates),
        "windows": [asdict(window) for window in windows],
        "resolved_backtest_config": audit["backtest_config"],
        "cost_audit": audit["cost_profile"],
        "global_trend_filter_audit": audit["global_trend_filter"],
    }


def _render_report(
    *,
    results: list[dict[str, object]],
    skipped_lanes: list[LaneSpec],
    output_path: Path,
) -> str:
    lines: list[str] = []
    lines.append("# Closed-Family Cost-Corrected Re-Screen — 2026-06-18")
    lines.append("")
    lines.append(
        "**Spec:** [closed-family-cost-corrected-rescreen-v0.md]"
        "(../specs/closed-family-cost-corrected-rescreen-v0.md)"
    )
    lines.append(
        "**Audit:** [backtest-engine-integrity-audit-2026-06-18.md]"
        "(backtest-engine-integrity-audit-2026-06-18.md)"
    )
    lines.append(
        "**Predecessors:** [cost-realism-rerun-2026-06-18.md](cost-realism-rerun-2026-06-18.md), "
        "[dislocation-cost-isolation-2026-06-18.md](dislocation-cost-isolation-2026-06-18.md)"
    )
    lines.append("")
    lines.append("## Frozen lane set (pre-registered)")
    lines.append("")
    for lane in FROZEN_LANES:
        status = "SKIP" if lane.skipped else "RUN"
        lines.append(f"- **{lane.lane_id}** [{status}] — {lane.label}: {lane.rationale}")
    lines.append("")
    lines.append(
        "Costs: **main defaults post #94** (fee 0.04%/side, slippage 0.02%/side, "
        "`scaled_8h` funding). Only the global trend filter is swept (Cell A OFF, Cell B ON)."
    )
    lines.append("")

    if skipped_lanes:
        lines.append("## Skipped lanes")
        lines.append("")
        for lane in skipped_lanes:
            lines.append(f"### `{lane.lane_id}`")
            lines.append("")
            lines.append(lane.skip_reason)
            lines.append("")
            lines.append(
                f"**Original legacy verdict (reference):** {lane.original.verdict} — "
                f"{lane.original.source}"
            )
            if lane.original.note:
                lines.append(f"_{lane.original.note}_")
            lines.append("")

    lane_ids = sorted({str(r["lane_id"]) for r in results})
    any_pass = False

    for lane_id in lane_ids:
        lane = next(item for item in FROZEN_LANES if item.lane_id == lane_id)
        lane_rows = [item for item in results if item["lane_id"] == lane_id]
        rows = {str(item["cell_id"]): _summary_row(item) for item in lane_rows}
        best_id = _pick_best_cell(rows)
        best = rows[best_id]
        if bool(best["passes_gates"]):
            any_pass = True

        lines.append(f"## Lane: `{lane_id}`")
        lines.append("")
        lines.append(f"**Best-of screen:** {best_id} ({best['label']}) → **{best['verdict']}**")
        lines.append("")
        lines.append("| Metric | Cell A (filter OFF) | Cell B (filter ON) | Original (legacy) |")
        lines.append("|---|---:|---:|---:|")
        cell_a = rows["cell_a"]
        cell_b = rows["cell_b"]
        orig = lane.original
        for key, label in (
            ("wfo_return_pct", "wfo_return_pct"),
            ("wfo_sharpe", "wfo_sharpe"),
            ("wfo_trades", "wfo_trades"),
            ("max_drawdown_pct", "max_drawdown_pct"),
            ("profit_concentration", "profit_concentration"),
        ):
            orig_val = getattr(orig, key)
            a_val = cell_a[key]
            b_val = cell_b[key]
            if key == "wfo_trades":
                lines.append(
                    f"| {label} | {int(a_val)} | {int(b_val)} | {_fmt_metric(orig_val, digits=0)} |"
                )
            else:
                lines.append(
                    f"| {label} | {float(a_val):.2f} | {float(b_val):.2f} | "
                    f"{_fmt_metric(orig_val)} |"
                )
        lines.append(f"| verdict | {cell_a['verdict']} | {cell_b['verdict']} | {orig.verdict} |")
        lines.append("")
        if orig.note:
            lines.append(f"_Original reference: {orig.source}. {orig.note}_")
        else:
            lines.append(f"_Original reference: {orig.source}_")
        lines.append("")

        for payload in lane_rows:
            cell_id = payload["cell_id"]
            lines.append(f"### {cell_id} — resolved cost + filter audit")
            lines.append("")
            lines.append("**Cost audit:**")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(payload["cost_audit"], indent=2))
            lines.append("```")
            lines.append("")
            lines.append("**Global trend filter audit:**")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(payload["global_trend_filter_audit"], indent=2))
            lines.append("```")
            lines.append("")
            cfg = payload["resolved_backtest_config"]
            assert isinstance(cfg, dict)
            lines.append("**BacktestConfig (key fields):**")
            lines.append("")
            lines.append("```json")
            lines.append(
                json.dumps(
                    {
                        "fee_rate": cfg["fee_rate"],
                        "slippage_pct": cfg["slippage_pct"],
                        "apply_global_trend_filter": cfg["apply_global_trend_filter"],
                        "futures_mode": cfg["futures_mode"],
                        "futures_funding_rate": cfg["futures_funding_rate"],
                        "funding_cadence": cfg["funding_cadence"],
                        "global_trend_filter_buffer_pct": cfg["global_trend_filter_buffer_pct"],
                    },
                    indent=2,
                )
            )
            lines.append("```")
            lines.append("")

    lines.append("## Decision")
    lines.append("")
    if any_pass:
        lines.append(
            "**TOOLING WAS HIDING AN EDGE** — at least one lane's best cell passes the "
            "standard gate at corrected costs. **Do not auto-promote.** Flag for focused "
            "cost×filter attribution (like #95) before re-opening; notify for human review."
        )
    else:
        lines.append(
            "**MEAN-REVERSION FAMILY GENUINELY CLOSED** — no runnable lane's best cell passes "
            "the standard gate at corrected main defaults under either trend-filter setting. "
            "Combined with dislocation isolation (#95), the cost bug hid **no deployable edge** "
            "in fee-marginal / trend-filter-confounded families."
        )
        lines.append("")
        lines.append(
            "**Recommendation:** Stop the structural-probe program and consolidate on "
            "sentiment-macro / SOL overlay Phase 0 forward validation."
        )
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines)
    output_path.write_text(content, encoding="utf-8")
    return content


async def main() -> None:
    args = parse_args()
    configure_logger("INFO")
    logger = get_logger("closed_family_cost_rescreen")

    runnable = tuple(
        lane
        for lane in FROZEN_LANES
        if not lane.skipped and (args.lanes is None or lane.lane_id in args.lanes)
    )
    skipped = [lane for lane in FROZEN_LANES if lane.skipped]
    if args.lanes:
        skipped = [lane for lane in skipped if lane.lane_id in args.lanes]

    if not runnable and not skipped:
        raise SystemExit("No lanes matched --lane filter")

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    all_results: list[dict[str, object]] = []
    if runnable:
        settings = load_settings(runnable[0].base_config)
        db_config = _db_config_from_settings(settings)
        if not db_config.get("password"):
            import os

            db_config["password"] = os.getenv("POSTGRES_PASSWORD", "change_me")
        logger.info("DB target: %s:%s", db_config["host"], db_config["port"])
        await init_pool(db_config)

        try:
            for lane in runnable:
                resolved_config = _resolve_lane_config(lane, output_dir)
                gates = _gate_config_from_profile(lane.gate_profile)
                logger.info("Lane %s resolved: %s", lane.lane_id, resolved_config)
                for cell in FILTER_CELLS:
                    logger.info("Running %s / %s", lane.lane_id, cell.cell_id)
                    payload = await _run_cell(
                        lane=lane,
                        resolved_config=resolved_config,
                        cell=cell,
                        db_config=db_config,
                        gates=gates,
                    )
                    artifact = output_dir / f"{lane.lane_id}-{cell.cell_id}.json"
                    with artifact.open("w", encoding="utf-8") as handle:
                        json.dump(payload, handle, indent=2)
                    print(f"\n=== {lane.lane_id} / {cell.cell_id} ===")
                    print(json.dumps(payload["cost_audit"], indent=2))
                    print(json.dumps(payload["global_trend_filter_audit"], indent=2))
                    row = _summary_row(payload)
                    print(
                        f"verdict={row['verdict']} wfo_return={row['wfo_return_pct']:.2f}% "
                        f"sharpe={row['wfo_sharpe']:.2f} trades={row['wfo_trades']}"
                    )
                    all_results.append(payload)
        finally:
            await close_pool()

    combined_path = output_dir / "combined_results.json"
    with combined_path.open("w", encoding="utf-8") as handle:
        json.dump(all_results, handle, indent=2)

    report = _render_report(
        results=all_results,
        skipped_lanes=skipped,
        output_path=args.write_report,
    )
    print(f"\nReport written: {args.write_report}")
    print(report.split("## Decision")[-1].strip())


if __name__ == "__main__":
    asyncio.run(main())
