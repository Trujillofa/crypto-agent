# Backtest Engine Integrity Audit — 2026-06-18

**Author:** Claude (planner/reviewer)
**Trigger:** "It's extremely unusual that so many strategies fail — is something missing/broken in the
tooling?" After ~1,500 autoresearch runs and ~12 probe lanes all closed, while the live
`sentiment-macro-bot` is profitable and the separate cTrader FX agent is profitable.
**Scope:** read-only audit of `src/backtest/engine.py` (789 lines) + the cost settings actually
applied by the gate/WFO path. **No code changed.**

**Update (2026-06-18):** Engine defaults corrected in `feat/backtest-cost-funding-fix` — fee
0.04%/side, slippage 0.02%/side, 8h-scaled futures funding (`scaled_8h` cadence).

**Update (2026-06-18, Task 3):** Global trend filter is now **audited at backtest start**
(`BacktestEngine._resolved_cost_audit()` logs `global_trend_filter_active`, `buffer_pct`,
`source`, and any explicit `config_global_trend_filter_enabled` from YAML). Mechanism and
defaults unchanged (`base.yaml` stays `true`). RBI Gate 0 briefs and autoresearch overlays
must set `strategy.global_trend_filter_enabled` explicitly — see
[trend-filter-opt-in-brief-v0.md](../specs/trend-filter-opt-in-brief-v0.md).

---

## TL;DR

The backtest is **systematically pessimistic about costs** and **silently mutates strategy
signals**. Three of the findings overcharge or distort in ways that would turn marginal-but-real
edges into backtest losers — exactly the symptom observed. None of this was wrong on purpose; they
are stale defaults and a hidden filter. **The verdicts of many closed lanes are suspect until
re-run at realistic settings.**

| # | Finding | Severity | Effect |
|---|---------|----------|--------|
| A | Round-trip cost ~0.4% (0.1% fee + 0.1% slippage, per side) vs ~0.1–0.15% real | **High** | ~3× overcharge; kills high-frequency / small-edge lanes |
| B | Futures funding applied **every bar** instead of every 8h | **High (futures, sub-8h)** | ~8× funding overcharge on 1h futures backtests |
| C | `apply_global_trend_filter` defaults **true** (base.yaml) → silently blocks every BUY below EMA200 | **High** | Neuters mean-reversion/dip-buy lanes that never asked for it |
| D | Sharpe computed on **per-bar** equity incl. flat out-of-market bars | **Medium** | Penalizes intermittent strategies against the `min_wfo_sharpe` gate |
| E | `return_pct` denominator differs spot vs futures (notional vs margin) | Low | Metric inconsistency, not a P&L bug |

---

## A. Cost model is ~3× too high (confirmed)

`BacktestConfig` defaults (`engine.py:38,45`): `fee_rate = 0.001` (0.1%) **and**
`slippage_pct = 0.001` (0.1%), each applied **per side**:
- Entry: `entry_price = price*(1+slippage)` + `fee = notional*fee_rate` (`_open_long` :490-493).
- Exit: `exit_price = price*(1−slippage)` + `exit_fee` (`_close_position` :563-572).
- **Round trip = 0.2% slippage + 0.2% fee = ~0.4%.**

Real Binance USDT-perp on majors: taker **0.04%/side** (0.05% spot), slippage on BTC/ETH/SOL a few
bps → **~0.1–0.15% round trip all-in.** The backtest charges **~3×** reality.

`config/base.yaml` sets **no** fee/slippage override, so every standard lane and the WFO/Gate-2 path
ran on these defaults. (Only the two `rbi_loop.cross-venue-*` configs override slippage to 0.02%;
none override `fee_rate`.) The Gate-1 probe scripts, by contrast, used a realistic **0.04% one-way**
fee — so a lane could pass Gate 1 (realistic cost) and then fail Gate 2 (~0.4% cost). **That is
exactly the daily-trend pattern: HAS_PULSE → Gate-2 FAIL.**

## B. Futures funding overcharged ~8× on sub-8h timeframes (confirmed)

`_apply_funding` (`engine.py:629-642`) is called **once per bar** while a futures position is open
(`run` :227), charging `qty*price*futures_funding_rate` (default 0.0001) each bar. Real perp funding
settles **every 8 hours** (3×/day). On a **1h** futures backtest this charges **24×/day → ~8×** the
real funding drag. This directly penalizes the one approach with live PnL (`sentiment-macro`, 1h
futures) in any replay/backtest, and any 1h futures lane.

## C. A hidden global trend filter mutates signals by default (confirmed)

`apply_global_trend_filter` defaults **true** (`engine.py:49`) and `config/base.yaml:69` sets
`global_trend_filter_enabled: true` (buffer 0.05). In `run` (:251-265) **any BUY** is converted to
HOLD when `price < EMA200*(1−buffer)`. Consequences:
- Every strategy is **silently forced long-only-above-EMA200**, regardless of its own logic.
- **Mean-reversion / dip-buying lanes are structurally blocked** from their core trade — and several
  such lanes were "closed for no edge." Some of those closures may be the *filter's* doing, not the
  signal's. Only `settings.daily_trend_long.yaml` explicitly set it `false`.

## D. Sharpe includes flat bars → intermittent strategies under-score (consideration)

`_calculate_metrics` (:707-744) builds returns from the **per-bar** equity curve, which is appended
**every bar** (:312) including out-of-market bars (return 0). Annualization then uses
`periods_per_year` for the full bar count. A strategy in-market only part of the time has its return
stream diluted by zeros, distorting the per-bar Sharpe that feeds `min_wfo_sharpe ≥ 0.5`. This is
defensible as a *portfolio* (capital-efficiency) Sharpe, but it penalizes signal quality for
intermittent strategies — relevant to the daily-trend / event lanes that sit flat much of the time.
A trade-based or active-period Sharpe would be more informative for the gate.

## E. `return_pct` denominator inconsistency (low)

Spot uses `entry_notional + entry_fee`; futures uses `margin_used + entry_fee` (`_close_position`
:575-582). Leverage makes these non-comparable across modes — a reporting wrinkle, not a P&L error.

---

## What is *not* broken (checked)

- **No same-bar SL/TP cheat.** Exit checks run at the start of each bar (`run` :226-237) *before*
  entry (`_process_signal` :310); a position opened on bar N cannot be stopped out on bar N. First
  exit opportunity is bar N+1. ✓
- **SL-before-TP ordering is conservative** (`_check_fixed_exit` :347-371): if a bar spans both, it
  takes the stop. ✓
- **Signal-at-close / fill-at-close** is the standard mild-optimistic convention; slippage partly
  offsets it. Acceptable. ✓

These rule out the most common look-ahead bugs — so the issue is **cost/behavior calibration**, not
future leakage. That is good news: it is fixable and testable.

---

## Implication for the closed lanes

The "markets are efficient" conclusion was drawn through a lens that (A) overcharges costs ~3×,
(B) overcharges 1h-futures funding ~8×, and (C) silently blocks below-trend buys. The cross-asset
lane had **zero** forward signal (cost-independent) so it stays closed. But **fee-marginal** lanes —
anything that died "just inside the fee bar," and the mean-reversion family especially — may have
been suppressed by the tooling, not the market.

## Recommended next steps

1. **Cost-realism re-run** (decisive, builder task — see
   [cost-realism-rerun-brief-v0.md](../specs/cost-realism-rerun-brief-v0.md)): re-run daily-trend +
   1–2 fee-marginal closed lanes at realistic fee (0.04%/side), slippage (~0.02%/side), 8h funding,
   and trend filter off; see if verdicts flip.
2. **If verdicts flip** → fix the defaults (realistic fee/slippage, 8h-aligned funding, trend filter
   opt-in not opt-out), then re-open the most affected closed lanes (mean-reversion first).
3. **Adopt the cTrader agent's cost discipline** — empirical per-symbol costs rather than one flat guess.
4. **Gate metric review** — consider a trade-based/active-period Sharpe alongside per-bar.
