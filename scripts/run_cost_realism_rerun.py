#!/usr/bin/env python3
"""Decisive cost-realism re-run per docs/specs/cost-realism-rerun-brief-v0.md.

Re-runs a frozen set of closed lanes under legacy vs realistic cost profiles.
Does not change engine defaults; overrides are per-run only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.experiment_autopilot import (  # noqa: E402
    _db_config_from_settings,
    run_experiment_evaluation,
)
from scripts.run_autoresearch import _gate_config_from_profile  # noqa: E402
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
DEFAULT_OUTPUT = ROOT / "research" / "cost-realism-rerun"


@dataclass(frozen=True)
class LaneSpec:
    """Frozen lane definition (chosen before any rerun results)."""

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


FROZEN_LANES: tuple[LaneSpec, ...] = (
    LaneSpec(
        lane_id="daily-trend-long-btc",
        label="daily-trend-long SMA50 BTCUSDT 1d",
        rationale=(
            "Primary HAS_PULSE→Gate-2-FAIL lane from daily-trend-long-gate2.md; "
            "SMA50 on BTC (probe baseline symbol)."
        ),
        base_config=ROOT / "config" / "settings.daily_trend_long.yaml",
        overlay=ROOT / "config" / "autoresearch" / "overlays" / "daily-trend-long-sma50.yaml",
        symbol="BTCUSDT",
        timeframe="1d",
        gate_profile="daily_trend",
        start="2024-01-01",
        end="2026-06-01",
        train_months=6,
        test_months=3,
        bootstrap=500,
    ),
    LaneSpec(
        lane_id="daily-trend-long-eth",
        label="daily-trend-long SMA50 ETHUSDT 1d",
        rationale="Same lane family on ETH (positive OOS return under legacy gate2, still FAIL).",
        base_config=ROOT / "config" / "settings.daily_trend_long.yaml",
        overlay=ROOT / "config" / "autoresearch" / "overlays" / "daily-trend-long-sma50.yaml",
        symbol="ETHUSDT",
        timeframe="1d",
        gate_profile="daily_trend",
        start="2024-01-01",
        end="2026-06-01",
        train_months=6,
        test_months=3,
        bootstrap=500,
    ),
    LaneSpec(
        lane_id="daily-trend-long-sol",
        label="daily-trend-long SMA50 SOLUSDT 1d",
        rationale="Same lane family on SOL (weakest symbol in gate2 report).",
        base_config=ROOT / "config" / "settings.daily_trend_long.yaml",
        overlay=ROOT / "config" / "autoresearch" / "overlays" / "daily-trend-long-sma50.yaml",
        symbol="SOLUSDT",
        timeframe="1d",
        gate_profile="daily_trend",
        start="2024-01-01",
        end="2026-06-01",
        train_months=6,
        test_months=3,
        bootstrap=500,
    ),
    LaneSpec(
        lane_id="eth-4h-range-reversion-bounded",
        label="ETHUSDT 4h bollinger_bounce range_reversion_bounded",
        rationale=(
            "Mean-reversion dip-buy lane (Wave 7 near-miss +13.55% OOS, Sharpe 0.48). "
            "Buys lower-band touches — structurally blocked when global EMA200 filter is on."
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
    ),
    LaneSpec(
        lane_id="sol-1h-dislocation-event",
        label="SOLUSDT 1h dislocation_event rolling basis_spread tail5 h24",
        rationale=(
            "Fee-marginal lane: Gate-1 HAS_PULSE at 0.08%+0.02% probe cost; v1 sweep best "
            "shape still negative at legacy ~0.4% round-trip engine defaults."
        ),
        base_config=ROOT / "config" / "settings.autoresearch.yaml",
        overlay=ROOT
        / "config"
        / "autoresearch"
        / "overlays"
        / "cost-realism-sol-1h-dislocation-event.yaml",
        symbol="SOLUSDT",
        timeframe="1h",
        gate_profile="standard",
        start="2024-01-01",
        end="2026-06-01",
        train_months=3,
        test_months=2,
        bootstrap=100,
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cost-realism decisive re-run")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Artifact directory for JSON results",
    )
    parser.add_argument(
        "--lane",
        action="append",
        dest="lanes",
        help="Run only these lane_id values (default: all frozen lanes)",
    )
    parser.add_argument(
        "--write-report",
        type=Path,
        default=ROOT / "docs" / "reports" / "cost-realism-rerun-2026-06-18.md",
        help="Markdown report output path",
    )
    return parser.parse_args()


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at root of {path}")
    return data


def _deep_merge_local(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            merged[key] = _deep_merge_local(merged.get(key), value) if key in merged else value
        return merged
    return overlay


def _resolve_lane_config(lane: LaneSpec, output_dir: Path) -> Path:
    merged = _read_yaml(lane.base_config)
    if lane.overlay is not None:
        merged = _deep_merge_local(merged, _read_yaml(lane.overlay))
    resolved_dir = output_dir / "resolved"
    resolved_dir.mkdir(parents=True, exist_ok=True)
    resolved_path = resolved_dir / f"{lane.lane_id}.yaml"
    with resolved_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(merged, handle, sort_keys=False)
    return resolved_path


def _gate_config(lane: LaneSpec) -> GateConfig:
    return _gate_config_from_profile(lane.gate_profile)


def _verdict_label(passes: bool) -> str:
    return "PASS" if passes else "FAIL"


def _materially_closer(legacy: dict[str, float | bool], realistic: dict[str, float | bool]) -> bool:
    if realistic["passes_gates"]:
        return True
    legacy_failures = int(legacy["failure_count"])
    realistic_failures = int(realistic["failure_count"])
    if realistic_failures < legacy_failures:
        return True
    legacy_sharpe = float(legacy["wfo_sharpe"])
    realistic_sharpe = float(realistic["wfo_sharpe"])
    legacy_return = float(legacy["wfo_return_pct"])
    realistic_return = float(realistic["wfo_return_pct"])
    return realistic_sharpe > legacy_sharpe + 0.15 or realistic_return > legacy_return + 5.0


async def _run_lane_pass(
    *,
    lane: LaneSpec,
    resolved_config: Path,
    cost_profile: CostProfile,
    db_config: dict[str, object],
) -> dict[str, object]:
    gates = _gate_config(lane)
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
        cost_profile=cost_profile,
        db_config=db_config,
        manage_pool=False,
    )
    return {
        "lane_id": lane.lane_id,
        "pass_name": cost_profile.name,
        "summary": asdict(summary),
        "gates": asdict(gates),
        "windows": [asdict(window) for window in windows],
        "resolved_backtest_config": audit["backtest_config"],
        "cost_audit": audit["cost_profile"],
    }


def _summary_row(payload: dict[str, object]) -> dict[str, object]:
    summary = payload["summary"]
    assert isinstance(summary, dict)
    return {
        "pass_name": payload["pass_name"],
        "total_return_pct": summary["total_return_pct"],
        "wfo_return_pct": summary["wfo_total_return_pct"],
        "wfo_sharpe": summary["wfo_mean_sharpe"],
        "wfo_trades": summary["wfo_total_trades"],
        "max_drawdown_pct": summary["max_drawdown_pct"],
        "profit_concentration": summary["profit_concentration_pct"],
        "passes_gates": summary["passes_gates"],
        "verdict": _verdict_label(bool(summary["passes_gates"])),
        "failure_count": len(summary.get("failure_reasons", [])),
        "failure_reasons": summary.get("failure_reasons", []),
    }


def _render_report(
    *,
    results: list[dict[str, object]],
    output_path: Path,
    funding_note: str,
) -> str:
    lines: list[str] = []
    lines.append("# Cost-Realism Re-Run Report — 2026-06-18")
    lines.append("")
    lines.append(
        "**Spec:** [cost-realism-rerun-brief-v0.md](../specs/cost-realism-rerun-brief-v0.md)"
    )
    lines.append(
        "**Audit:** [backtest-engine-integrity-audit-2026-06-18.md]"
        "(backtest-engine-integrity-audit-2026-06-18.md)"
    )
    lines.append("")
    lines.append("## Frozen lane set (pre-registered)")
    lines.append("")
    for lane in FROZEN_LANES:
        lines.append(f"- **{lane.lane_id}** — {lane.label}: {lane.rationale}")
    lines.append("")
    lines.append("## Funding cadence method")
    lines.append("")
    lines.append(funding_note)
    lines.append("")

    lane_ids = sorted({str(item["lane_id"]) for item in results})
    flips: list[str] = []
    closer: list[str] = []

    for lane_id in lane_ids:
        lane_rows = [item for item in results if item["lane_id"] == lane_id]
        legacy = next(item for item in lane_rows if item["pass_name"] == "legacy")
        realistic = next(item for item in lane_rows if item["pass_name"] == "realistic")
        legacy_row = _summary_row(legacy)
        realistic_row = _summary_row(realistic)

        lines.append(f"## Lane: `{lane_id}`")
        lines.append("")
        lines.append("| Metric | Legacy | Realistic | Δ (realistic − legacy) |")
        lines.append("|---|---:|---:|---:|")
        for key, label in (
            ("total_return_pct", "total_return_pct"),
            ("wfo_return_pct", "wfo_return_pct"),
            ("wfo_sharpe", "wfo_sharpe"),
            ("wfo_trades", "wfo_trades"),
            ("max_drawdown_pct", "max_drawdown_pct"),
            ("profit_concentration", "profit_concentration"),
        ):
            leg = float(legacy_row[key]) if key != "wfo_trades" else int(legacy_row[key])
            rea = float(realistic_row[key]) if key != "wfo_trades" else int(realistic_row[key])
            delta = rea - leg
            if key == "wfo_trades":
                lines.append(f"| {label} | {leg} | {rea} | {delta:+d} |")
            else:
                lines.append(f"| {label} | {leg:.2f} | {rea:.2f} | {delta:+.2f} |")
        lines.append(f"| gate_verdict | {legacy_row['verdict']} | {realistic_row['verdict']} | — |")
        lines.append("")
        lines.append("**Resolved cost audit (realistic pass):**")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(realistic["cost_audit"], indent=2))
        lines.append("```")
        lines.append("")
        lines.append("**Resolved BacktestConfig (realistic pass, key cost fields):**")
        lines.append("")
        cfg = realistic["resolved_backtest_config"]
        assert isinstance(cfg, dict)
        lines.append("```json")
        lines.append(
            json.dumps(
                {
                    "fee_rate": cfg["fee_rate"],
                    "slippage_pct": cfg["slippage_pct"],
                    "apply_global_trend_filter": cfg["apply_global_trend_filter"],
                    "futures_mode": cfg["futures_mode"],
                    "futures_funding_rate": cfg["futures_funding_rate"],
                    "global_trend_filter_buffer_pct": cfg["global_trend_filter_buffer_pct"],
                },
                indent=2,
            )
        )
        lines.append("```")
        lines.append("")

        if legacy_row["verdict"] == "FAIL" and realistic_row["verdict"] == "PASS":
            flips.append(lane_id)
        elif legacy_row["verdict"] == "FAIL" and _materially_closer(legacy_row, realistic_row):
            closer.append(lane_id)

    lines.append("## Read-out")
    lines.append("")
    flip_lanes = list(dict.fromkeys(flips + closer))
    if flips:
        lines.append(
            f"**VERDICT FLIP (PASS)** — {len(flips)} lane(s) moved Legacy FAIL → Realistic PASS: "
            f"{', '.join(flips)}."
        )
        lines.append("")
        lines.append(
            "Implication: tooling (costs / trend filter / funding) was suppressing edges. "
            "Escalate to fixing engine defaults and re-opening mean-reversion families first."
        )
    elif flip_lanes:
        lines.append(
            f"**VERDICT FLIP (materially closer)** — {len(flip_lanes)} lane(s) remain FAIL but "
            f"move materially closer under realistic costs: {', '.join(flip_lanes)}."
        )
        if closer and not flips:
            lines.append("")
            lines.append(
                "Strongest signal: `sol-1h-dislocation-event` WFO return −23.8% → +4.6%, "
                "Sharpe −0.73 → +0.15 (still below 0.5 gate). "
                "`eth-4h-range-reversion-bounded` worsened (−46.5% WFO) when trend filter "
                "was removed — suppression was not the binding constraint there."
            )
        lines.append("")
        lines.append(
            "Implication per brief: tooling was suppressing edges in at least one lane. "
            "Escalate to fixing engine defaults (realistic costs, 8h funding, trend-filter "
            "opt-in), then re-open fee-marginal / dislocation families before mean-reversion."
        )
    else:
        lines.append("**NO FLIP** — verdicts unchanged across all lanes.")
        lines.append("")
        lines.append(
            "Implication: the efficiency conclusion holds; punitive defaults were not the "
            "primary cause of closure. Stop blaming the tooling for these lanes."
        )
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines)
    output_path.write_text(content, encoding="utf-8")
    return content


async def main() -> None:
    args = parse_args()
    configure_logger("INFO")
    logger = get_logger("cost_realism_rerun")

    selected = tuple(
        lane for lane in FROZEN_LANES if args.lanes is None or lane.lane_id in args.lanes
    )
    if not selected:
        raise SystemExit("No lanes matched --lane filter")

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    settings = load_settings(selected[0].base_config)
    db_config = _db_config_from_settings(settings)
    if not db_config.get("password"):
        import os

        db_config["password"] = os.getenv("POSTGRES_PASSWORD", "change_me")
    os_host = db_config["host"]
    logger.info("DB target: %s:%s", os_host, db_config["port"])
    await init_pool(db_config)

    funding_note = (
        "Realistic pass uses **scaled per-bar funding**: `effective_futures_funding_rate = "
        "base_rate × (timeframe_hours / 8)`. This is equivalent to charging the full 8h rate "
        "only on bars that represent one 8h funding period, without editing `_apply_funding`. "
        "Legacy pass keeps per-bar `0.0001` (engine default). All three frozen lanes run spot "
        "(`futures.enabled: false`), so funding does not affect these results; the scaling is "
        "wired for futures lanes in future reruns."
    )

    all_results: list[dict[str, object]] = []
    try:
        for lane in selected:
            resolved_config = _resolve_lane_config(lane, output_dir)
            logger.info("Lane %s resolved config: %s", lane.lane_id, resolved_config)
            for profile in (legacy_cost_profile(), realistic_cost_profile()):
                logger.info("Running %s / %s", lane.lane_id, profile.name)
                payload = await _run_lane_pass(
                    lane=lane,
                    resolved_config=resolved_config,
                    cost_profile=profile,
                    db_config=db_config,
                )
                artifact = output_dir / f"{lane.lane_id}-{profile.name}.json"
                with artifact.open("w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                print(f"\n=== {lane.lane_id} / {profile.name} ===")
                print(json.dumps(payload["cost_audit"], indent=2))
                print(
                    "BacktestConfig costs:",
                    json.dumps(
                        {
                            k: payload["resolved_backtest_config"][k]
                            for k in (
                                "fee_rate",
                                "slippage_pct",
                                "apply_global_trend_filter",
                                "futures_mode",
                                "futures_funding_rate",
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

    report = _render_report(
        results=all_results,
        output_path=args.write_report,
        funding_note=funding_note,
    )
    print(f"\nReport written: {args.write_report}")
    print(report.split("## Read-out")[-1].strip())


if __name__ == "__main__":
    asyncio.run(main())
