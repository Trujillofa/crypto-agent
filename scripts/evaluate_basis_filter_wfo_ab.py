#!/usr/bin/env python3
"""Evaluate paired WFO results for basis premium risk filter A/B."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_summary(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise ValueError(f"Missing summary in {path}")
    return summary


def _f(summary: dict[str, object], key: str) -> float:
    return float(summary[key])


def _i(summary: dict[str, object], key: str) -> int:
    return int(summary[key])


def evaluate(baseline: dict[str, object], filtered: dict[str, object]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    u_trades = _i(baseline, "wfo_total_trades")
    f_trades = _i(filtered, "wfo_total_trades")
    u_oos = _f(baseline, "wfo_total_return_pct")
    f_oos = _f(filtered, "wfo_total_return_pct")
    u_dd = _f(baseline, "max_drawdown_pct")
    f_dd = _f(filtered, "max_drawdown_pct")
    u_ploss = _f(baseline, "bootstrap_p_loss_pct")
    f_ploss = _f(filtered, "bootstrap_p_loss_pct")
    u_conc = _f(baseline, "profit_concentration_pct")
    f_conc = _f(filtered, "profit_concentration_pct")
    f_blocked = _i(filtered, "basis_blocked_buy_count")

    if f_blocked <= 0:
        reasons.append("basis_blocked_buy_count == 0 on filtered run (filter wiring suspect)")
    if u_trades > 0 and f_trades < 0.70 * u_trades:
        reasons.append(
            f"filtered WFO trades {f_trades} < 70% of baseline {u_trades} "
            f"({f_trades / u_trades * 100:.1f}%)"
        )
    if f_trades < 20:
        reasons.append(f"filtered WFO trades {f_trades} < 20")
    if u_oos > 0 and f_oos < 0.50 * u_oos:
        reasons.append(f"filtered OOS {f_oos:.2f}% < 50% of baseline {u_oos:.2f}%")
    if u_oos <= 0 and f_oos < u_oos * 1.5:
        reasons.append(f"filtered OOS {f_oos:.2f}% not clearly better than baseline {u_oos:.2f}%")
    risk_improved = f_dd <= u_dd and f_ploss <= u_ploss and f_conc <= u_conc + 10.0
    if not risk_improved:
        reasons.append(
            "risk not clearly better: "
            f"DD filtered={f_dd:.2f}% vs {u_dd:.2f}%, "
            f"P(loss) filtered={f_ploss:.2f}% vs {u_ploss:.2f}%, "
            f"conc filtered={f_conc:.2f}% vs {u_conc:.2f}%"
        )

    verdict = "REJECT" if reasons else "PROCEED_PAPER_SHADOW"
    return verdict, reasons


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate basis filter WFO A/B.")
    parser.add_argument("--baseline-json", type=Path, required=True)
    parser.add_argument("--filtered-json", type=Path, required=True)
    args = parser.parse_args()

    baseline = _load_summary(args.baseline_json)
    filtered = _load_summary(args.filtered_json)

    print("Basis Premium Filter WFO A/B Evaluation")
    print("=" * 52)
    print(f"Baseline WFO trades:        {_i(baseline, 'wfo_total_trades')}")
    print(f"Filtered WFO trades:        {_i(filtered, 'wfo_total_trades')}")
    print(f"Baseline OOS return:        {_f(baseline, 'wfo_total_return_pct'):.2f}%")
    print(f"Filtered OOS return:        {_f(filtered, 'wfo_total_return_pct'):.2f}%")
    print(f"Baseline max DD:            {_f(baseline, 'max_drawdown_pct'):.2f}%")
    print(f"Filtered max DD:            {_f(filtered, 'max_drawdown_pct'):.2f}%")
    print(f"Baseline P(loss):           {_f(baseline, 'bootstrap_p_loss_pct'):.2f}%")
    print(f"Filtered P(loss):           {_f(filtered, 'bootstrap_p_loss_pct'):.2f}%")
    print(f"Baseline concentration:     {_f(baseline, 'profit_concentration_pct'):.2f}%")
    print(f"Filtered concentration:     {_f(filtered, 'profit_concentration_pct'):.2f}%")
    print(f"Filtered basis_blocked_buy_count: {_i(filtered, 'basis_blocked_buy_count')}")
    print()

    verdict, reasons = evaluate(baseline, filtered)
    print(f"Verdict: {verdict}")
    if reasons:
        print("Stop criteria triggered:")
        for reason in reasons:
            print(f"  - {reason}")
    else:
        print("All basis filter A/B stop criteria passed — eligible for paper shadow.")
    return 0 if verdict == "PROCEED_PAPER_SHADOW" else 1


if __name__ == "__main__":
    raise SystemExit(main())
