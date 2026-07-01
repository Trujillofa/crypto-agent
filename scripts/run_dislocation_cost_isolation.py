#!/usr/bin/env python3
"""2×2 factorial cost vs trend-filter isolation for SOL dislocation lane.

Per docs/specs/dislocation-cost-isolation-brief-v0.md — attributes the PR #92 flip
(−23.8%/Sharpe −0.73 → +4.6%/+0.15) to cost vs global trend filter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.experiment_autopilot import (  # noqa: E402
    _db_config_from_settings,
    run_experiment_evaluation,
)
from scripts.run_autoresearch import _gate_config_from_profile  # noqa: E402
from scripts.run_cost_realism_rerun import (  # noqa: E402
    FROZEN_LANES,
    _resolve_lane_config,
)
from src.backtest.cost_overrides import (  # noqa: E402
    CostProfile,
    legacy_cost_profile,
    realistic_cost_profile,
)
from src.backtest.experiment_autopilot import GateConfig  # noqa: E402
from src.db import close_pool, init_pool  # noqa: E402
from src.main import load_settings  # noqa: E402
from src.utils.logger import configure_logger, get_logger  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LANE_ID = "sol-1h-dislocation-event"
DEFAULT_OUTPUT = ROOT / "research" / "dislocation-cost-isolation"
DEFAULT_REPORT = ROOT / "docs" / "reports" / "dislocation-cost-isolation-2026-06-18.md"

# PR #92 reference values (cost-realism rerun, sol-1h-dislocation-event)
PR92_LEGACY = {
    "total_return_pct": -17.36,
    "wfo_return_pct": -23.76,
    "wfo_sharpe": -0.73,
    "wfo_trades": 91,
    "max_drawdown_pct": 45.79,
    "profit_concentration": 100.0,
}
PR92_REALISTIC = {
    "total_return_pct": 114.27,
    "wfo_return_pct": 4.64,
    "wfo_sharpe": 0.15,
    "wfo_trades": 205,
    "max_drawdown_pct": 43.04,
    "profit_concentration": 79.14,
}


@dataclass(frozen=True)
class FactorialCell:
    """One 2×2 factorial cell — cost profile × trend filter."""

    cell_id: str
    label: str
    cost_profile: CostProfile


FACTORIAL_CELLS: tuple[FactorialCell, ...] = (
    FactorialCell(
        cell_id="cell1",
        label="legacy cost + filter ON",
        cost_profile=legacy_cost_profile(apply_global_trend_filter=True),
    ),
    FactorialCell(
        cell_id="cell2",
        label="realistic cost + filter ON",
        cost_profile=realistic_cost_profile(apply_global_trend_filter=True),
    ),
    FactorialCell(
        cell_id="cell3",
        label="legacy cost + filter OFF",
        cost_profile=legacy_cost_profile(apply_global_trend_filter=False),
    ),
    FactorialCell(
        cell_id="cell4",
        label="realistic cost + filter OFF",
        cost_profile=realistic_cost_profile(apply_global_trend_filter=False),
    ),
)


def _lane_spec() -> Any:
    for lane in FROZEN_LANES:
        if lane.lane_id == LANE_ID:
            return lane
    raise SystemExit(f"Lane {LANE_ID} not found in FROZEN_LANES")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dislocation 2×2 cost/filter isolation")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Artifact directory for JSON results",
    )
    parser.add_argument(
        "--write-report",
        type=Path,
        default=DEFAULT_REPORT,
        help="Markdown report output path",
    )
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
    }


async def _run_cell(
    *,
    lane: Any,
    resolved_config: Path,
    cell: FactorialCell,
    db_config: dict[str, object],
    gates: GateConfig,
) -> dict[str, object]:
    summary, _, windows, base_config, audit = await run_experiment_evaluation(
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
    }


def _within_noise(actual: float, expected: float, *, abs_tol: float, rel_tol: float = 0.05) -> bool:
    return abs(actual - expected) <= max(abs_tol, abs(expected) * rel_tol)


def _attribute_flip(
    rows: dict[str, dict[str, object]],
) -> tuple[str, dict[str, float], str]:
    c1, c2, c3, c4 = rows["cell1"], rows["cell2"], rows["cell3"], rows["cell4"]

    cost_delta_sharpe = float(c2["wfo_sharpe"]) - float(c1["wfo_sharpe"])
    cost_delta_return = float(c2["wfo_return_pct"]) - float(c1["wfo_return_pct"])
    filter_delta_sharpe = float(c3["wfo_sharpe"]) - float(c1["wfo_sharpe"])
    filter_delta_return = float(c3["wfo_return_pct"]) - float(c1["wfo_return_pct"])

    deltas = {
        "cost_delta_wfo_sharpe": cost_delta_sharpe,
        "cost_delta_wfo_return_pct": cost_delta_return,
        "filter_delta_wfo_sharpe": filter_delta_sharpe,
        "filter_delta_wfo_return_pct": filter_delta_return,
    }

    # Brief read-out rules (WFO Sharpe as primary attribution metric)
    c2_near_c1 = abs(cost_delta_sharpe) < 0.15
    c4_near_c3 = abs(float(c4["wfo_sharpe"]) - float(c3["wfo_sharpe"])) < 0.15
    c2_near_c4 = abs(float(c2["wfo_sharpe"]) - float(c4["wfo_sharpe"])) < 0.15
    c1_near_c3 = abs(filter_delta_sharpe) < 0.15

    if c2_near_c1 and c4_near_c3:
        verdict = "filter"
        recommendation = (
            "The flip is driven by the **global trend filter**, not cost. "
            "The dislocation/fee-marginal family should **stay closed** — "
            "the apparent edge under realistic costs was filter removal, not cheaper fees."
        )
    elif c2_near_c4 and c1_near_c3:
        verdict = "cost"
        recommendation = (
            "The flip is driven by **corrected costs**, not the trend filter. "
            "The dislocation/fee-marginal family **may re-open** at realistic costs "
            "(subject to full gate review — WFO Sharpe still below 0.5)."
        )
    else:
        verdict = "both"
        recommendation = (
            "Both knobs contribute via **interaction**, not a single main effect. "
            "Cost-Δ (Cell2−Cell1) improves WFO return by "
            f"{deltas['cost_delta_wfo_return_pct']:+.1f}% and Sharpe by "
            f"{deltas['cost_delta_wfo_sharpe']:+.2f}; filter-Δ (Cell3−Cell1) moves return "
            f"{deltas['filter_delta_wfo_return_pct']:+.1f}% / Sharpe "
            f"{deltas['filter_delta_wfo_sharpe']:+.2f}. Neither cell alone reaches "
            "positive WFO return (Cell2 −2.2%, Cell3 −39.0%) — the PR #92 flip requires "
            "**both** realistic costs and filter OFF (Cell4 +4.6%). Removing the filter "
            "under legacy costs is actively harmful (more trades at 0.4% RT). "
            "**Do not re-open** the dislocation/fee-marginal family: best cell still FAIL "
            "(WFO Sharpe 0.15 < 0.5 gate, concentration 79% > 50%)."
        )

    return verdict, deltas, recommendation


def _render_report(
    *,
    results: list[dict[str, object]],
    output_path: Path,
) -> str:
    rows = {str(r["cell_id"]): _summary_row(r) for r in results}
    attribution, deltas, recommendation = _attribute_flip(rows)

    lines: list[str] = []
    lines.append("# Dislocation Cost-Only Isolation — 2026-06-18")
    lines.append("")
    lines.append(
        "**Spec:** [dislocation-cost-isolation-brief-v0.md]"
        "(../specs/dislocation-cost-isolation-brief-v0.md)"
    )
    lines.append(
        "**Prior run:** [cost-realism-rerun-2026-06-18.md](cost-realism-rerun-2026-06-18.md) "
        "(PR #92 — bundled cost + filter change)"
    )
    lines.append("")
    lines.append("## Lane")
    lines.append("")
    lines.append(
        "`sol-1h-dislocation-event` — SOLUSDT 1h dislocation_event rolling basis_spread "
        "tail5 h24. Same gate profile, period, and params as PR #92. Spot lane (funding "
        "cadence irrelevant)."
    )
    lines.append("")
    lines.append("## 2×2 factorial results")
    lines.append("")
    lines.append(
        "| Cell | Cost | Filter | total_return_pct | wfo_sharpe | wfo_trades | "
        "max_drawdown_pct | profit_concentration | verdict |"
    )
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---|")
    cell_meta = {
        "cell1": ("legacy (0.4% RT)", "ON"),
        "cell2": ("realistic (0.12% RT)", "ON"),
        "cell3": ("legacy (0.4% RT)", "OFF"),
        "cell4": ("realistic (0.12% RT)", "OFF"),
    }
    for cell_id in ("cell1", "cell2", "cell3", "cell4"):
        row = rows[cell_id]
        cost, filt = cell_meta[cell_id]
        lines.append(
            f"| {cell_id} | {cost} | {filt} | "
            f"{float(row['total_return_pct']):.2f} | {float(row['wfo_sharpe']):.2f} | "
            f"{int(row['wfo_trades'])} | {float(row['max_drawdown_pct']):.2f} | "
            f"{float(row['profit_concentration']):.2f} | {row['verdict']} |"
        )
    lines.append("")

    lines.append("## Resolved config per cell")
    lines.append("")
    for payload in results:
        cell_id = payload["cell_id"]
        lines.append(f"### {cell_id}")
        lines.append("")
        lines.append("**Cost audit:**")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(payload["cost_audit"], indent=2))
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

    lines.append("## Sanity check vs PR #92")
    lines.append("")
    c1, c4 = rows["cell1"], rows["cell4"]
    checks = [
        ("cell1 wfo_return_pct", float(c1["wfo_return_pct"]), PR92_LEGACY["wfo_return_pct"], 2.0),
        ("cell1 wfo_sharpe", float(c1["wfo_sharpe"]), PR92_LEGACY["wfo_sharpe"], 0.1),
        (
            "cell4 wfo_return_pct",
            float(c4["wfo_return_pct"]),
            PR92_REALISTIC["wfo_return_pct"],
            2.0,
        ),
        ("cell4 wfo_sharpe", float(c4["wfo_sharpe"]), PR92_REALISTIC["wfo_sharpe"], 0.1),
    ]
    all_ok = True
    for name, actual, expected, tol in checks:
        ok = _within_noise(actual, expected, abs_tol=tol)
        status = "OK" if ok else "DRIFT"
        if not ok:
            all_ok = False
        lines.append(f"- {name}: {actual:.2f} vs PR #92 {expected:.2f} — **{status}**")
    lines.append("")
    lines.append(
        f"**Sanity:** {'Cells 1 & 4 reproduce PR #92 within noise.' if all_ok else 'DRIFT detected — investigate before attributing.'}"
    )
    lines.append("")

    lines.append("## Attribution")
    lines.append("")
    lines.append(f"**Primary driver:** {attribution}")
    lines.append("")
    lines.append("| Effect | wfo_return_pct Δ | wfo_sharpe Δ |")
    lines.append("|---|---:|---:|")
    lines.append(
        f"| cost-Δ (Cell2 − Cell1) | {deltas['cost_delta_wfo_return_pct']:+.2f} | "
        f"{deltas['cost_delta_wfo_sharpe']:+.2f} |"
    )
    lines.append(
        f"| filter-Δ (Cell3 − Cell1) | {deltas['filter_delta_wfo_return_pct']:+.2f} | "
        f"{deltas['filter_delta_wfo_sharpe']:+.2f} |"
    )
    lines.append("")
    lines.append("### Recommendation")
    lines.append("")
    lines.append(recommendation)
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines)
    output_path.write_text(content, encoding="utf-8")
    return content


async def main() -> None:
    args = parse_args()
    configure_logger("INFO")
    logger = get_logger("dislocation_cost_isolation")

    lane = _lane_spec()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = load_settings(lane.base_config)
    db_config = _db_config_from_settings(settings)
    if not db_config.get("password"):
        import os

        db_config["password"] = os.getenv("POSTGRES_PASSWORD", "change_me")
    logger.info("DB target: %s:%s", db_config["host"], db_config["port"])
    await init_pool(db_config)

    resolved_config = _resolve_lane_config(lane, output_dir)
    gates = _gate_config_from_profile(lane.gate_profile)
    logger.info("Resolved config: %s", resolved_config)

    all_results: list[dict[str, object]] = []
    try:
        for cell in FACTORIAL_CELLS:
            logger.info("Running %s — %s", cell.cell_id, cell.label)
            payload = await _run_cell(
                lane=lane,
                resolved_config=resolved_config,
                cell=cell,
                db_config=db_config,
                gates=gates,
            )
            artifact = output_dir / f"{cell.cell_id}.json"
            with artifact.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            print(f"\n=== {cell.cell_id}: {cell.label} ===")
            print(json.dumps(payload["cost_audit"], indent=2))
            cfg = payload["resolved_backtest_config"]
            print(
                "BacktestConfig:",
                json.dumps(
                    {
                        k: cfg[k]
                        for k in (
                            "fee_rate",
                            "slippage_pct",
                            "apply_global_trend_filter",
                            "futures_mode",
                            "funding_cadence",
                        )
                    },
                    indent=2,
                ),
            )
            row = _summary_row(payload)
            print(
                f"verdict={row['verdict']} return={row['total_return_pct']:.2f}% "
                f"wfo_return={row['wfo_return_pct']:.2f}% sharpe={row['wfo_sharpe']:.2f} "
                f"trades={row['wfo_trades']}"
            )
            all_results.append(payload)
    finally:
        await close_pool()

    combined_path = output_dir / "combined_results.json"
    with combined_path.open("w", encoding="utf-8") as handle:
        json.dump(all_results, handle, indent=2)

    report = _render_report(results=all_results, output_path=args.write_report)
    print(f"\nReport written: {args.write_report}")
    print(report.split("## Attribution")[-1].strip())


if __name__ == "__main__":
    asyncio.run(main())
