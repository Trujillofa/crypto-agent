# Strategy Drought Remediation Plan

**Date:** 2026-07-05
**Status:** SUPERSEDED (Tracks A & B) — see counter-evidence below. Track C remains open, blocked on a capital decision.
**Scope:** `sol_1h_trend_pullback_overlay_live`, `sentiment_macro`, `sol_trend_pullback_sparse`

---

## ⚠️ Counter-Evidence — Do Not Execute Tracks A or B (added 2026-07-06)

This plan's diagnosis is correct (no malfunction; the overlay's `buy_threshold` exceeds
what one strategy vote can produce), but Tracks A and B re-open decisions that were
already settled by pre-registered experiments this plan does not cite:

- **Track A (lower overlay `buy_threshold`) was falsified on 2026-06-18.**
  [`overlay-threshold-sweep-2026-06-18.md`](../reports/overlay-threshold-sweep-2026-06-18.md)
  swept exactly the proposed region (0.5–1.27) on the production-mirror config at
  corrected costs (#94), WFO 2024-01 → 2026-02: every threshold with tradeable frequency
  loses (0.80 → −17.5%, 0.90 → fails gates at 77% max DD, 1.00 → −21.6%), and the
  deployed 1.07/1.27 fail the pre-registered ≥2 trades/mo floor. Lowering the threshold
  is not "pure upside" — it converts a dormant agent into a measured losing one. Note the
  overlay's original WFO pass predates the cost fix (#94) and was established under ~3×
  overcharged costs; the >1.0 confluence gate is part of what made it pass.
- **Track B (tighten sentiment_macro gates) was swept on 2026-06-19** (#101,
  [`sentiment-vol-filter-sweep`](../reports/sentiment-vol-filter-sweep-2026-06-19.md)):
  no setting both trades and holds an edge.
- **Both verdicts were consolidated in**
  [`research-consolidation-2026-06-19.md`](../reports/research-consolidation-2026-06-19.md):
  overlay — "Not viable — untradeable gate, no edge beneath it"; sentiment-macro —
  "Not viable". A 7–14 day paper run or a 3-month replay (~17 trades at threshold 0.85)
  cannot overturn a 2-year corrected-cost WFO and should not be run against live capital.

**Resolution:** both agents were disarmed to paper (execution flags off, containers and
data feeds kept) rather than left live-armed and dormant — this plan itself is the
demonstration that a dormant live agent invites exactly the config change the sweep
falsified. Track C (`sol_sparse` funding) stays a human capital decision; note its
"demonstrated edge" is 8 lifetime trades and it has also been dry in paper mode since
2026-05-05, so diagnose the dryness before funding anything.

---

## Executive Summary

A strategy-drought review on 2026-07-05 verified that **no malfunction exists** — all
containers are healthy, ingestion is clean, indicators compute every minute, and the AI
sentiment pipeline is responsive. However, three issues are suppressing trade activity
and degrading PnL. This plan proposes a phased remediation with explicit validation
gates before any live change.

---

## Issue 1 — `sol_1h_trend_pullback_overlay_live`: Never Traded (Buy Threshold Too High)

### Evidence

- **Config:** `config/settings.sol_1h_trend_pullback_overlay_live.yaml`
  - `buy_threshold: 1.27`, `buy_threshold_uptrend: 1.07`
  - 6 strategies configured, each produces confidence ≤ 1.0
- **Runtime logs (live, last 24h):**
  - `2026-07-05 02:04` — BollingerBounce → BUY(0.54) → Consensus HOLD (score 0.54 < 1.27)
  - `2026-07-05 03:04` — BollingerBounce → BUY(0.67) → Consensus HOLD (score 0.67 < 1.27)
  - `2026-07-04 20:04` — RSIReversal → SELL(0.51) → Consensus HOLD (score -0.51 > -0.79)
- **Database:** 0 positions ever for this agent on a live futures account.
- **Aggregator logic** (`src/strategy/aggregator.py:136`): `total_score >= effective_buy_threshold` —
  since individual strategies fire at 0.5–0.7 confidence, the score can only exceed 1.27
  if **2–3 strategies confluence simultaneously**.

### Root Cause

The `buy_threshold` of 1.27 was likely set for a multi-strategy confluence design, but
in practice SOLUSDT 1h rarely produces 2–3 simultaneous BUY signals. The agent has been
live on futures for weeks without a single entry.

### Proposed Fix (Phased)

| Phase | Change | Gate |
|-------|--------|------|
| 1 — Paper | Create/verify paper config with `buy_threshold: 0.85`, `buy_threshold_uptrend: 0.75` | Run paper agent 7–14 days, confirm entries occur |
| 2 — Replay backtest | Backtest 2026-04-01 → 2026-07-05 at thresholds 0.8 / 0.9 / 1.0 | Compare PnL, win rate, max drawdown vs current 1.27 |
| 3 — Live (if Phase 1–2 pass) | Lower live `buy_threshold` to selected value | Monitor first 10 live trades, kill-switch if drawdown > 5% |

### Files to Modify (Phase 3 only)

```
config/settings.sol_1h_trend_pullback_overlay_live.yaml   # buy_threshold, buy_threshold_uptrend
config/settings.sol_1h_trend_pullback_overlay_live.yaml   # per_symbol_aggregator_config.SOLUSDT.buy_threshold
```

---

## Issue 2 — `sentiment_macro`: 1W / 11L Streak, May Drawdown

### Evidence

- **Config:** `config/settings.sentiment_macro.yaml`
  - `buy_threshold: 0.6`, single strategy (`sentiment_mean_reversion`)
  - `atr_pct_threshold: 0.005`, `rsi_oversold: 35.0`
  - `global_trend_filter_buffer_pct: 0.0`
- **Database (last 15 trades, May 7 → May 31):**

  | Date | Symbol | Side | Entry | Exit | PnL (USDT) |
  |------|--------|------|-------|------|------------|
  | May 31 | BTCUSDT | LONG | 73619 | 73228 | -0.39 |
  | May 27 | BTCUSDT | LONG | 74933 | 74280 | -0.65 |
  | May 26 | BTCUSDT | LONG | 76832 | 76832 | -0.06 |
  | May 25 | SOLUSDT | LONG | 85.01 | 84.11 | -0.23 |
  | May 24 | ETHUSDT | LONG | 2100 | 2079 | -0.21 |
  | May 22 | BTCUSDT | LONG | 75718 | 74949 | -0.77 |
  | May 21 | BTCUSDT | LONG | 77216 | 76506 | -0.71 |
  | May 18 | BTCUSDT | LONG | 77095 | 76378 | -0.72 |
  | May 16 | BTCUSDT | LONG | 78044 | 77480 | -0.63 |
  | May 16 | ETHUSDT | LONG | 2195 | 2185 | -0.12 |
  | May 16 | BTCUSDT | LONG | 78488 | 78154 | -0.40 |
  | **May 13** | BTCUSDT | LONG | 79254 | 80460 | **+1.14** |
  | May 8 | BTCUSDT | LONG | 79598 | 80226 | +0.56 |
  | May 7 | BTCUSDT | LONG | 81154 | 80560 | -0.66 |
  | May 7 | ETHUSDT | LONG | 2312 | 2293 | -0.18 |

- **Summary:** 2 wins, 13 losses in the last 15 trades. **All LONGs in a sustained downtrend**
  (BTC fell from ~81k → 73k through May). The strategy is mean-reversion but kept buying dips
  that kept dipping.

### Root Cause

The `sentiment_mean_reversion` strategy buys when RSI is oversold + sentiment is supportive +
Bollinger distance met. In a sustained downtrend, "oversold" conditions persist and dips
keep going lower. The `global_trend_filter_buffer_pct: 0.0` allows entries right at/below
EMA200, and the `atr_pct_threshold: 0.005` may not be filtering enough for the volatility
regime.

### Proposed Fix (Phased)

| Phase | Change | Gate |
|-------|--------|------|
| 1 — Diagnose | Backtest sentiment_mean_reversion on BTC/SOL/ETH Jan–Jun 2026. Identify if edge degraded or if it's purely regime. | Win rate, PnL, regime-conditional metrics |
| 2 — Tighten gates (paper) | Raise `atr_pct_threshold` to 0.006–0.007, raise `sentiment_gate_threshold` to 40.0, add `btc_regime_filter_enabled: true` | 7–14 day paper run, compare entry frequency |
| 3 — Live (if Phase 1–2 pass) | Apply validated config changes to live config | Monitor first 10 live trades |

### Files to Modify (Phase 3 only)

```
config/settings.sentiment_macro.yaml   # atr_pct_threshold, sentiment_gate_threshold, btc_regime_filter
```

---

## Issue 3 — `sol_trend_pullback_sparse`: Paper-Only (Unfunded Spot Account)

### Evidence

- **Config:** `config/settings.sol_trend_pullback_sparse.yaml`
  - `mode: paper`, `test_mode: true`
  - Comment: "spot account has $0 USDT balance (2026-04-25)"
- **Database:** 8 closed trades, +$97.74 lifetime PnL. Last trade May 5 (61 days ago).
- The strategy has a demonstrated edge but cannot trade live without capital.

### Proposed Fix

| Phase | Change | Gate |
|-------|--------|------|
| 1 — Capital decision | Fund the Binance spot account OR migrate to futures | User decision (capital allocation) |
| 2 — Config update | Set `mode: live`, `test_mode: false`, update `order_size_usdt` based on funded balance | Manual verification of account balance |
| 3 — Deploy | Restart container with updated config | Monitor first 5 trades |

### Files to Modify (Phase 2 only, after funding)

```
config/settings.sol_trend_pullback_sparse.yaml   # mode, test_mode, order_size_usdt
```

---

## Implementation Order

```
┌─────────────────────────────────────────────────────────┐
│  PARALLEL TRACK                                         │
│                                                         │
│  Track A: sol_1h overlay        Track B: sentiment_macro│
│  ┌──────────────────────┐       ┌─────────────────────┐ │
│  │ Phase 1: Paper test  │       │ Phase 1: Backtest   │ │
│  │ Phase 2: Replay BT   │       │ Phase 2: Paper test │ │
│  │ Phase 3: Live deploy │       │ Phase 3: Live deploy│ │
│  └──────────────────────┘       └─────────────────────┘ │
│                                                         │
│  Track C: sol_sparse (BLOCKED on capital decision)      │
│  ┌──────────────────────┐                               │
│  │ Phase 1: Fund account│  ← user decision needed      │
│  │ Phase 2: Config      │                               │
│  │ Phase 3: Deploy      │                               │
│  └──────────────────────┘                               │
└─────────────────────────────────────────────────────────┘
```

**Priority:** Track A (sol_1h) is cheapest to validate — it's already live but never
fires, so any threshold change is pure upside. Track B (sentiment) needs the most
analysis since the edge may be regime-conditional. Track C is blocked on a human
decision.

---

## Risk Controls

- **No live config changes in this PR.** This is a planning document only.
- All threshold changes require paper validation + backtest confirmation first.
- Live deployments include a kill-switch: if drawdown exceeds 5% on first 10 trades,
  revert to previous config immediately.
- Each live change is a separate commit + container restart, not a batch deploy.

---

## Verification Checklist (Post-Implementation)

Status checked on 2026-07-08. These gates require current production evidence:
paper entries, backtest output, service logs, and Prometheus/Grafana scrape
state. Production `trades` had zero rows in the last 7 days, so paper-entry and
trade-reduction gates remain open. The sentiment-macro edge-boundary question
was resolved negative by the 2026-06-19 vol-filter sweep: no `atr_pct_threshold`
or filter-off arm both traded and kept an edge at corrected costs.

- `sol_1h_overlay` paper-entry gate is still open: at least 3 paper entries in
  7 days at a viable new threshold.
- `sol_1h_overlay` backtest gate is failed/open: no corrected-cost threshold has
  shown positive expectancy.
- [x] `sentiment_macro`: regime-conditional backtest identifies the edge
  boundary. The 2026-06-19 sweep found the boundary is terminal, not tunable:
  more frequency produced more loss.
- `sentiment_macro` trade-reduction gate is superseded by disarmament: the
  service is now configured as paper, and no production trades were recorded in
  the last 7 days.
- `sol_sparse` live-readiness gate is still open: spot account must be funded
  before any live config change.
- [x] All agents: no new errors in logs after config changes. Checked the
  production tail for all four agent services on 2026-07-08; no error,
  exception, traceback, critical, or failed log lines were present.
- [x] Prometheus/Grafana still scraping all agents after restart. Prometheus
  active targets showed all four agent scrape targets healthy: SOL sparse,
  SOL panic block paper, SOL 1h overlay live, and sentiment macro.
