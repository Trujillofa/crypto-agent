# Brief — Cost-Realism Re-Run (decisive tooling test)

**Status:** experiment spec — to be run by Grok (builder); Claude reviews
**Trigger / context:** [backtest-engine-integrity-audit-2026-06-18.md](../reports/backtest-engine-integrity-audit-2026-06-18.md)
— the backtest overcharges costs ~3× (fee+slippage), overcharges 1h-futures funding ~8×, and a
default-on global trend filter silently blocks below-EMA200 buys. This experiment tests whether
those artifacts — not the market — closed the marginal lanes.

---

## Hypothesis

Lanes that passed Gate 1 (realistic 0.04% probe cost) but failed Gate 2 (the engine's ~0.4%
round-trip default) failed because of **cost/behavior calibration**, not absence of edge. At
realistic settings, ≥1 closed lane's verdict flips.

## The experiment (do NOT change engine defaults yet — override per run, measure, report)

Re-run a small, pre-chosen set of **already-closed** lanes under **realistic** vs the **legacy**
cost settings, side by side, and compare verdicts.

### Settings to compare (two passes per lane)

| Knob | Legacy (as-run) | Realistic |
|------|-----------------|-----------|
| `fee_rate` (per side) | 0.001 (0.1%) | **0.0004** (0.04%) |
| `slippage_pct` (per side) | 0.001 (0.1%) | **0.0002** (0.02%) |
| futures funding cadence | every bar | **every 8h** (apply only on bars crossing 00/08/16 UTC, or scale per-bar by `tf_hours/8`) |
| `apply_global_trend_filter` | true (base.yaml) | **false** (let the strategy's own logic stand) |

Realistic fee/slippage are Binance USDT-perp majors taker + a few bps; confirm against the cTrader
agent's empirical-cost approach (`derive_sm_pair_costs.py`) as a sanity reference for methodology.

### Lanes to re-run (frozen set — no cherry-picking)

1. **daily-trend-long** (SMA50, BTC/ETH/SOL) — the clean HAS_PULSE→Gate-2-FAIL case. Primary.
2. **One mean-reversion lane** most likely suppressed by the global trend filter (pick from the
   closed ledger — e.g. an RSI/Bollinger dip-buy; document which and why).
3. **One fee-marginal lane** that died "just inside the fee bar" (document the pick).

### Method (point-in-time, no other changes)

- Use the existing WFO/autoresearch path; only override the four knobs above. **Print the resolved
  `BacktestConfig`** in the output so the applied costs are auditable.
- Run **both** passes (legacy + realistic) for each lane so the delta is attributable to costs alone.
- Keep the **same gate profile** otherwise (`standard` / `daily_trend` as originally used). Do **not**
  also loosen gate thresholds — this isolates the cost effect.

## Funding-cadence fix detail (Finding B)

`_apply_funding` currently charges every bar. For the realistic pass, apply funding only when the bar
timestamp crosses an 8h boundary (00:00/08:00/16:00 UTC), **or** equivalently scale the per-bar
charge by `timeframe_hours / 8`. Use whichever is cleaner; document the choice. (For the experiment
this can be a local override in the run harness — engine default fix comes later if verdicts flip.)

## Pass / read-out

For each lane, report a side-by-side table: `total_return_pct`, `wfo_sharpe`, `wfo_trades`,
`max_drawdown_pct`, `profit_concentration`, and **gate verdict** under Legacy vs Realistic.

- **VERDICT FLIPS (≥1 lane Legacy=FAIL → Realistic=PASS or materially closer):** the tooling was
  suppressing edges. → escalate: fix engine defaults (realistic costs, 8h funding, trend-filter
  opt-in), then re-open the most affected closed families (mean-reversion first).
- **NO FLIP (verdicts unchanged):** costs/filter were not the cause; the efficiency conclusion holds
  and we stop blaming the tooling. Either outcome is high-information.

## Guardrails

1. **Override per-run; do not silently edit engine defaults** during the experiment (a default change
   only happens *after* a flip is demonstrated and reviewed).
2. **Frozen lane set** — no swapping lanes after seeing results to manufacture a flip.
3. **Run both passes** — a realistic-only run proves nothing without the legacy baseline.
4. **Print resolved config + costs** for auditability.
5. Realistic numbers are defensible (Binance majors taker 0.04%/side); do not over-optimistically set
   fee/slippage to ~0.

## Reviewer (Claude) checkpoints

(a) both passes run with only the four knobs changed; (b) resolved `BacktestConfig`/costs printed and
realistic; (c) funding-cadence fix correct (8h, not per-bar); (d) frozen lane set; (e) side-by-side
deltas reported with honest flip / no-flip verdict and the implication stated.
