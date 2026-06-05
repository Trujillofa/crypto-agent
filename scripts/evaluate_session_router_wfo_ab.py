#!/usr/bin/env python3
"""Evaluate paired WFO results for session liquidity router A/B."""

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


def evaluate(ungated: dict[str, object], gated: dict[str, object]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    u_trades = _i(ungated, "wfo_total_trades")
    g_trades = _i(gated, "wfo_total_trades")
    u_oos = _f(ungated, "wfo_total_return_pct")
    g_oos = _f(gated, "wfo_total_return_pct")
    u_dd = _f(ungated, "max_drawdown_pct")
    g_dd = _f(gated, "max_drawdown_pct")
    u_ploss = _f(ungated, "bootstrap_p_loss_pct")
    g_ploss = _f(gated, "bootstrap_p_loss_pct")
    u_conc = _f(ungated, "profit_concentration_pct")
    g_conc = _f(gated, "profit_concentration_pct")
    g_blocked = _i(gated, "blocked_buy_count")

    if g_blocked <= 0:
        reasons.append("blocked_buy_count == 0 on gated run (router wiring suspect)")
    if u_trades > 0 and g_trades < 0.70 * u_trades:
        reasons.append(
            f"gated WFO trades {g_trades} < 70% of ungated {u_trades} "
            f"({g_trades / u_trades * 100:.1f}%)"
        )
    if g_trades < 20:
        reasons.append(f"gated WFO trades {g_trades} < 20")
    if u_oos > 0 and g_oos < 0.50 * u_oos:
        reasons.append(f"gated OOS {g_oos:.2f}% < 50% of ungated {u_oos:.2f}%")
    if u_oos <= 0 and g_oos < u_oos * 1.5:
        reasons.append(f"gated OOS {g_oos:.2f}% not clearly better than ungated {u_oos:.2f}%")
    risk_improved = g_dd <= u_dd and g_ploss <= u_ploss and g_conc <= u_conc + 10.0
    if not risk_improved:
        reasons.append(
            "risk not clearly better: "
            f"DD gated={g_dd:.2f}% vs {u_dd:.2f}%, "
            f"P(loss) gated={g_ploss:.2f}% vs {u_ploss:.2f}%, "
            f"conc gated={g_conc:.2f}% vs {u_conc:.2f}%"
        )

    verdict = "REJECT" if reasons else "PROCEED_PAPER_SHADOW"
    return verdict, reasons


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate session router WFO A/B.")
    parser.add_argument("--ungated-json", type=Path, required=True)
    parser.add_argument("--gated-json", type=Path, required=True)
    args = parser.parse_args()

    ungated = _load_summary(args.ungated_json)
    gated = _load_summary(args.gated_json)

    print("Session Router WFO A/B Evaluation")
    print("=" * 52)
    print(f"Ungated WFO trades:     {_i(ungated, 'wfo_total_trades')}")
    print(f"Gated WFO trades:       {_i(gated, 'wfo_total_trades')}")
    print(f"Ungated OOS return:     {_f(ungated, 'wfo_total_return_pct'):.2f}%")
    print(f"Gated OOS return:       {_f(gated, 'wfo_total_return_pct'):.2f}%")
    print(f"Ungated max DD:         {_f(ungated, 'max_drawdown_pct'):.2f}%")
    print(f"Gated max DD:           {_f(gated, 'max_drawdown_pct'):.2f}%")
    print(f"Ungated P(loss):        {_f(ungated, 'bootstrap_p_loss_pct'):.2f}%")
    print(f"Gated P(loss):          {_f(gated, 'bootstrap_p_loss_pct'):.2f}%")
    print(f"Ungated concentration:  {_f(ungated, 'profit_concentration_pct'):.2f}%")
    print(f"Gated concentration:    {_f(gated, 'profit_concentration_pct'):.2f}%")
    print(f"Gated blocked_buy_count: {_i(gated, 'blocked_buy_count')}")
    print()

    verdict, reasons = evaluate(ungated, gated)
    print(f"Verdict: {verdict}")
    if reasons:
        print("Stop criteria triggered:")
        for reason in reasons:
            print(f"  - {reason}")
    else:
        print("All router A/B stop criteria passed — eligible for Phase 3 paper shadow.")
    return 0 if verdict == "PROCEED_PAPER_SHADOW" else 1


if __name__ == "__main__":
    raise SystemExit(main())
