# Overlay Threshold × Frequency Sweep — v0 (spec)

**Status:** spec — ready to build (Grok)
**Author:** Claude (planner)
**Date:** 2026-06-18
**Related:**
[closed-family-cost-corrected-rescreen-v0.md](./closed-family-cost-corrected-rescreen-v0.md),
[backtest-engine-integrity-audit-2026-06-18.md](../reports/backtest-engine-integrity-audit-2026-06-18.md),
memory `phase0-overlay-zero-trades`, `backtest-cost-tooling-finding`.

---

## Why

The only deployable technical agent — `sol-1h-trend-pullback-overlay-live` — has **0
fills, ever**, despite the service being healthy for weeks. Live-log diagnosis (2026-06-18)
shows the cause is **not** a broken signal path and **not** market sparsity at the strategy
level. The 6-strategy SOL stack emits BUY votes constantly (RSIReversal, MACDHistogram,
BollingerBounce up to confidence **1.00**), but **every one resolves to `Consensus HOLD,
signals=0`**.

Root cause — the aggregator gate:

```yaml
# config/settings.sol_1h_trend_pullback_overlay_live.yaml
aggregator:
  buy_threshold: 1.27          # 1.07 in an uptrend
per_symbol_aggregator_config:
  SOLUSDT:
    buy_threshold: 1.27
    buy_threshold_uptrend: 1.07
```

A single strategy vote caps at confidence **1.0**, so one strategy can never cross
1.07/1.27 — entry requires **≥2 strategies BUYing the same 1h bar** with summed weighted
confidence > 1.07. On live SOL 1h that almost never co-occurs → ~0 trades → **Phase 0
forward validation can never accumulate its 5→10→20-trade milestones.**

Two confounds make this worth a backtest rather than a blind config tweak:

1. **The overlay was WFO-validated under the cost bug** (pre-#94: ~0.4% round-trip, ~3×
   too high; funding ~8× over). "WFO-passed" was established under wrong physics.
2. **The >1.0 gate is part of what made it pass.** A stricter confluence gate cherry-picks
   the cleanest historical setups → flattering backtest, near-zero live frequency.
   Relaxing it is a *new strategy*, not a tweak.

## Question (single, falsifiable)

> Is there a `buy_threshold` that delivers **both** (a) enough live frequency to forward-
> validate **and** (b) a surviving edge under the standard gate at **corrected costs (#94)**?

## Non-goals

- Not a new structural-probe lane (the probe program is exhausted). This validates the
  **existing deployable asset**, which is consolidation work.
- No auto-promotion. The sweep produces a frontier + recommendation for human review.
- No live config change in this task.

---

## Method

Clone `scripts/run_closed_family_cost_rescreen.py` → `scripts/run_overlay_threshold_sweep.py`.
Reuse `run_experiment_evaluation()` from `scripts/experiment_autopilot.py` verbatim — it
already returns the gate summary and an `audit` dump (cost + filter + BacktestConfig).
**Swept axis = `buy_threshold`** (instead of the trend-filter cells).

### Fixed lane

| Field | Value |
|---|---|
| base_config | `config/settings.sol_1h_trend_pullback_overlay_live.yaml` |
| symbol | `SOLUSDT` |
| timeframe | `1h` |
| start / end | `2024-01-01` → `2026-06-01` (assert candle coverage first; clamp to earliest SOL 1h candle if needed) |
| train_months / test_months | 6 / 3 |
| bootstrap | 500 (match siblings) |
| gate_profile | `standard` (`_gate_config_from_profile("standard")`) |
| trading mode | **futures** (config-native: `default_trading_mode: futures`, leverage 3, funding applies) |
| exits | config-native ATR SL/TP/trailing (`backtest_use_executor_exit_model: true`) — preserve from resolved config |

### Swept grid

```
buy_threshold ∈ {0.50, 0.60, 0.70, 0.80, 0.90, 1.00, 1.07, 1.27}
```

For each value `v`, the resolved config MUST set **all four** keys to `v` so the swept
value actually binds (per-symbol config otherwise wins, and the uptrend discount otherwise
masks the effect):

- `aggregator.buy_threshold = v`
- `aggregator.buy_threshold_uptrend = v`
- `per_symbol_aggregator_config.SOLUSDT.buy_threshold = v`
- `per_symbol_aggregator_config.SOLUSDT.buy_threshold_uptrend = v`

Leave `sell_threshold`, `min_agreement`, `min_confidence`, and `btc_regime_filter_*` at
their native config values. Collapsing `buy_threshold_uptrend` to `v` (rather than keeping
the 1.07/1.27 ratio) is intentional: it makes "effective entry bar" a single interpretable
axis. (`1.07` and `1.27` are included so the production points appear on the frontier.)

### Costs & filters (held constant — mirror production)

- `cost_profile = corrected_main_cost_profile(apply_global_trend_filter=True)` — corrected
  fee 0.04%/side, slippage 0.02%/side, `scaled_8h` funding, **global trend filter at the
  production/base default (ON), buffer 0.0**. The point is "would *production* have traded
  at threshold `v`," so every non-swept knob matches prod.
- Emit the same `cost_audit` + `global_trend_filter_audit` + BacktestConfig dump per run as
  the closed-family report, to prove costs/filters resolved as intended.
- **Follow-up only if the whole grid still yields ~0 trades:** rerun with
  `apply_global_trend_filter=False` to test whether the EMA200 filter (not the aggregator)
  is the binding constraint. Do not run this second axis preemptively.

### Per-threshold outputs

For each `v`, record from `summary`:

- `wfo_total_trades`, and **`trades_per_month` = wfo_total_trades / OOS_months** (the
  frequency axis; OOS_months = number of test windows × test_months)
- `wfo_total_return_pct`, `wfo_mean_sharpe`, `max_drawdown_pct`,
  `profit_concentration_pct`
- bootstrap `p_loss` if surfaced in `summary`; else note absent
- `passes_gates` (bool), `failure_reasons` (list)

Write per-run JSON to `research/overlay-threshold-sweep/<v>.json`, combined to
`combined_results.json`, resolved configs to `research/overlay-threshold-sweep/resolved/`,
and a report to `docs/reports/overlay-threshold-sweep-2026-06-18.md` (a frontier table:
one row per threshold, columns = the metrics above).

---

## Decision rule (pre-registered)

Define **forward-validatable frequency** as `trades_per_month ≥ 2` (≥ ~20 trades in a
~10-month OOS span — enough to clear Phase 0's 20-trade milestone in a quarter, and the
WFO ≥20-trade floor the program already uses).

- **At least one threshold has `trades_per_month ≥ 2` AND `passes_gates = True`:**
  → the overlay is rescuable. Report the lowest such threshold (most frequency) and the
  best-Sharpe such threshold. **Next step = re-validate that config** as a new strategy
  (its own WFO confirmation at corrected costs + a fresh bootstrap=1000) before any live
  change. Consolidation direction = **B (re-validate at lower threshold).**

- **Frequency only appears with gate failure** (every `trades_per_month ≥ 2` row fails;
  the only passing rows are starved): → the overlay's edge depends on a confluence gate
  that is live-untradeable. The overlay is **not** a viable forward-validation vehicle.
  Consolidation direction = **A (document, accept) or C (pivot)** — and the consolidation
  doc says so with this evidence.

- **Whole grid ~0 trades even at `buy_threshold = 0.50`:** the binding constraint is *not*
  the aggregator. Run the filter-OFF follow-up; if still ~0, escalate — the limiter is
  upstream (regime filter / data / sizing), needs separate diagnosis.

Report the frontier even when the rule is unambiguous — the trades-per-month vs
profit_concentration shape is the deliverable.

---

## Acceptance criteria

1. `scripts/run_overlay_threshold_sweep.py` runs all 8 thresholds end-to-end against the
   DB without manual edits, `--threshold` filter supported (like `--lane`).
2. Each run's `cost_audit` shows fee 0.0004 / slippage 0.0002 / `scaled_8h`; filter audit
   shows the held-constant setting. Mismatch = hard fail.
3. Each resolved config shows all four `buy_threshold*` keys = the run's `v` (assert in the
   audit, not just on disk).
4. Report renders the frontier table + applies the decision rule to a one-line verdict.
5. Candle-coverage assertion up front; if SOL 1h data is short, the report states the
   actual span used.

## Notes for the builder

- `run_experiment_evaluation(..., manage_pool=False)` inside a single `init_pool/close_pool`
  — copy the closed-family `main()` pool handling.
- DB target comes from `_db_config_from_settings(load_settings(base_config))` +
  `POSTGRES_PASSWORD` env fallback (same as the sibling script).
- Watch the `CostProfile.to_audit_dict` dead-code trap from #94 (fixed in #95/#96) — if you
  copy cost-override code, confirm the audit dict is actually populated.
- This is a backtest against historical data; it does not touch the live agent.
</content>
</invoke>
