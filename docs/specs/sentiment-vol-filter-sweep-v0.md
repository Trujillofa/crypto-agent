# Sentiment-Macro Volatility-Filter Sweep — v0 (spec)

**Status:** spec — ready to build (Grok)
**Author:** Claude (planner)
**Date:** 2026-06-19
**Related:**
[research-consolidation-2026-06-19.md](../reports/research-consolidation-2026-06-19.md),
[overlay-threshold-frequency-sweep-v0.md](./overlay-threshold-frequency-sweep-v0.md),
memory `phase0-overlay-zero-trades`, `backtest-cost-tooling-finding`.

---

## Why

`sentiment-macro` is the only agent that has ever produced closed trades (94, last
2026-06-01) and is now idle. Diagnosis (2026-06-19, read-only prod logs + indicator DB)
found the cause is **not** a degraded feed (xAI returns 200 / `xai_live`; recorded scores
68–82) and **not** genuine calm — it is a **miscalibrated volatility filter**.

The `sentiment_mean_reversion` BUY entry requires `atr_pct ≤ 0.005`
(`config/settings.sentiment_macro.yaml`). That threshold's config comment says it was set
from BTC/ETH norms (~0.004–0.007), but the agent trades **SOLUSDT**, whose `atr_pct`
distribution (prod, 120d) is:

| p10 | median | p90 |
|---:|---:|---:|
| 0.00518 | 0.00844 | 0.01277 |

Only **8.4%** of SOL bars clear the gate. The RSI<35 + lower-band dip setup fires ~10% of
bars (239/120d, 81/30d), but the low-vol cull leaves **19/120d, 6/30d** — and the cull is
structural (oversold dips spike ATR, so the strategy's own setups trip its own filter).

## Question (single, falsifiable)

> Is there an `atr_pct_threshold` (or the filter disabled) at which `sentiment_mean_reversion`
> on SOLUSDT 1h reaches **both** (a) ≥2 trades/month and (b) a surviving edge under the
> standard gate at **corrected costs (#94)**?

If yes → recalibration candidate to forward-validate (and motivates a percentile-based gate).
If frequency only comes with gate failure → the vehicle is dead even recalibrated → accept
the terminal state (consolidation rec. #2). If ~0 trades even filter-off → the binding
constraint is upstream (RSI/BB conjunction or the sentiment/aggregator interaction below).

## Non-goals

- No live config change, no auto-promotion, no code change to the strategy in this task
  (percentile-gate is a *follow-up* if the frontier justifies it).
- Not a new research lane — this validates an existing live asset (consolidation work).

---

## Method

Clone `scripts/run_overlay_threshold_sweep.py` → `scripts/run_sentiment_vol_filter_sweep.py`.
Reuse `run_experiment_evaluation()` from `scripts/experiment_autopilot.py` verbatim (it
accepts `cost_profile`, `gates`, and `replay_sentiment_path`). **Swept axis =
`atr_pct_threshold`** plus one filter-disabled arm.

### Fixed lane

| Field | Value |
|---|---|
| base_config | `config/settings.sentiment_macro.yaml` |
| symbol / timeframe | `SOLUSDT` / `1h` |
| start / end | `2024-01-01` → `2026-06-01` (assert candle coverage; clamp to DB and report actual span) |
| train_months / test_months | 6 / 3 |
| bootstrap | 500 |
| gate_profile | `standard` |
| trading mode | futures (config-native) |
| exits | config-native ATR SL/TP/trailing — preserve from resolved config |

### Swept grid

```
atr_pct_threshold ∈ {0.005 (current), 0.0065, 0.0080, 0.0085, 0.0100, 0.0125}   # absolute
plus one arm: volatility_regime_filter = false   (filter disabled)
```

The grid spans the current value up through SOL's median (0.0085) and ~p90 (0.0125). For each
value `v`, set in the resolved config:

- `strategy.strategies[0].config.atr_pct_threshold = v`

For the disabled arm, set `strategy.strategies[0].config.volatility_regime_filter = false`
(leave `atr_pct_threshold` at its native value). Leave `rsi_oversold`, `bb_distance_threshold`,
the sentiment thresholds, and the aggregator (`buy_threshold: 0.6`) at native values.
(There is one strategy in this config; assert that before indexing.)

### Costs & sentiment (held constant — mirror production)

- `cost_profile = corrected_main_cost_profile(apply_global_trend_filter=True)` — fee
  0.04%/side, slippage 0.02%/side, `scaled_8h` funding, global trend filter at the
  production/base default. Emit the `cost_audit` + `global_trend_filter_audit` +
  BacktestConfig dump per run (mismatch = hard fail), exactly like the overlay sweep.
- **Sentiment = neutral-50 fallback (no replay).** Do **not** pass `replay_sentiment_path`.
  Recorded sentiment exists only for ~1 month (2026-03-27 → 2026-04-27) and cannot support a
  multi-year WFO, so the swept comparison must hold sentiment constant. Document this
  prominently — see confounds.

### Two confounds to document in the report (do not engineer around them in v0)

1. **Neutral-50 understates frequency vs live.** At sentiment = 50, BUY confidence is
   `0.5 + min(0.3, (35−rsi)/30·0.3) + 0.05`; the aggregator needs ≥ `buy_threshold` (0.6),
   which requires **rsi ≤ 30** (not <35). Live, the observed bullish scores (68–82) add the
   +0.15 boost and re-open rsi<35. So a neutral-50 run is a *conservative* (stricter) proxy:
   if it trades, live would trade at least as often; if it shows ~0 even filter-off, part of
   that is this interaction, not only the vol gate — flag it rather than over-claim.
2. **Historical track record is under the cost bug.** The 94 prior trades ran at the old
   ~0.4% RT / ~8× funding. A passing config here still needs fresh forward validation.

### Per-arm outputs

Record from `summary`: `wfo_total_trades`, `trades_per_month` (= wfo_total_trades /
OOS_months), `wfo_total_return_pct`, `wfo_mean_sharpe`, `max_drawdown_pct`,
`profit_concentration_pct`, bootstrap `p_loss` (if surfaced), `passes_gates`,
`failure_reasons`. Write per-arm JSON to `research/sentiment-vol-filter-sweep/<arm>.json`
(arm = the threshold value or `filter_off`), `combined_results.json`, resolved configs to
`research/sentiment-vol-filter-sweep/resolved/`, and a frontier report to
`docs/reports/sentiment-vol-filter-sweep-2026-06-19.md`.

---

## Decision rule (pre-registered)

Forward-validatable frequency = `trades_per_month ≥ 2` (same floor as the overlay sweep and
the WFO ≥20-trade convention).

- **Some arm has `≥2 trades/mo` AND `passes_gates`:** recalibration candidate. Report the
  lowest-vol-threshold and best-Sharpe passing arms. **Next step = forward-validate that
  config** (own WFO at corrected costs + bootstrap=1000, and prefer migrating to a
  *percentile*-based vol gate using the existing `atr_percentile` column so it self-calibrates
  per asset). Consolidation → restore sentiment-macro as a live vehicle.
- **Frequency only with gate failure** (every ≥2/mo arm fails; passing arms are starved):
  the strategy's edge does not survive at corrected costs even recalibrated. Vehicle dead →
  consolidation rec. #2 (accept terminal state).
- **~0 trades even with the filter disabled:** the vol gate is not the binding constraint.
  Given confound #1, re-examine the RSI/BB conjunction and the sentiment/aggregator
  interaction before concluding; document and escalate, do not silently close.

Report the full frontier (trades/mo vs profit_concentration vs Sharpe) regardless of verdict.

---

## Acceptance criteria

1. `scripts/run_sentiment_vol_filter_sweep.py` runs all arms end-to-end against the DB with
   no manual edits; `--threshold` / `--arm` filter supported (like the overlay `--threshold`).
2. Each run's `cost_audit` shows fee 0.0004 / slippage 0.0002 / `scaled_8h`; filter audit
   shows the held-constant global-trend-filter setting. Mismatch = hard fail.
3. Each resolved config shows the arm's `atr_pct_threshold` (or `volatility_regime_filter:
   false`) actually applied — assert in the audit, not just on disk.
4. Report renders the frontier table, states the effective DB-clamped span, applies the
   decision rule to a one-line verdict, and reproduces both confound notes.
5. No `replay_sentiment_path` is passed (sentiment = neutral-50); the report says so.

## Notes for the builder

- Copy the overlay sweep's `main()` pool handling (`run_experiment_evaluation(...,
  manage_pool=False)` inside one `init_pool/close_pool`); DB target via
  `_db_config_from_settings(load_settings(base_config))` + `POSTGRES_PASSWORD` env fallback.
- Watch the `CostProfile.to_audit_dict` dead-code trap from #94 (fixed #95/#96) if you copy
  cost-override code — confirm the audit dict is populated.
- Backtest only; does not touch the live agent.

## Out-of-scope follow-ups (flag, don't do here)

- **Recorder gap:** the prod sentiment event log (`data/event_log_sentiment-macro-bot.jsonl`)
  stopped at 2026-04-27 although the agent still calls xAI hourly. If sentiment-faithful
  validation is ever wanted, this observability gap must be fixed first.
- **Percentile gate:** migrating `volatility_regime_filter` from an absolute `atr_pct`
  threshold to the `atr_percentile` column — only if this sweep shows a viable band.
