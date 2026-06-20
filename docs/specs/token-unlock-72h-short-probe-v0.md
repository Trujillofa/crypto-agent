# Lane Brief — Token-Unlock 72h Shock Short Probe v0 (Gate 0)

**Status:** Gate 1 RUN — **NO_PULSE** (2026-06-20). Effect does not reproduce on independent data. See "## v0 result" below.
**Author role:** planned by Claude (planner/reviewer); cheap probe scaffolded by Claude, to be run/reviewed before any campaign
**Predecessors / context:**
- Structural-probe surface CLOSED at corrected costs: [../reports/research-consolidation-2026-06-19.md](../reports/research-consolidation-2026-06-19.md)
- Macro-event-calendar lane (the prior "new data primitive" attempt): [macro-event-drift-probe-v0.md](macro-event-drift-probe-v0.md), [macro-surprise-drift-probe-v0.md](macro-surprise-drift-probe-v0.md) — calendar NO_PULSE / surprise WEAK_EDGE
- External source idea: `vibe-investing/01.Trading Strategy/Token unlock 72h shock analysis/` — HoKwang Kim, *"The 72-Hour Shock"* (SSRN working paper, 52 hand-collected Binance unlock events)

---

## Why this lane (first principles)

Every closed lane to date — and the macro-calendar lane — shared one weakness, but for two
different reasons:

- OHLCV-structure lanes (sweeps, ranges, trend, breadth, basis) all fade or follow the **same
  market beta** and fail together. Root cause on record: these are **trending assets and we kept
  betting on mean-reversion** ([../reports/research-consolidation-2026-06-19.md]).
- The macro-calendar lane used genuinely exogenous events, but the crypto response to FOMC/CPI/NFP
  is **diffuse** — the whole market moves together, the signal is a weak beta tilt, and it came back
  NO_PULSE / WEAK_EDGE.

Token unlocks are different on the two axes that killed the previous lanes:

1. **The edge is DIRECTIONAL SHORT, not mean-reversion.** The thesis is that scheduled new supply
   hits a thin order book and the token *falls*. We are betting **with** the post-unlock move, not
   fading it. This is the opposite of every closed lane's failure mode.
2. **The event is token-specific, not market-wide.** A SAND or STRK unlock is an idiosyncratic
   supply shock to that token. After subtracting BTC's same-window return, a real unlock effect
   should remain — which is exactly what controls for the macro lane's "everything moves together"
   problem.

The trigger is **exogenous and known ex-ante**: vesting/unlock schedules are published months in
advance. That gives **zero look-ahead on the trigger** — the single hardest property for any
event lane — and the data is free and static (a small historical event table).

## The reference evidence (and why we do NOT trust its numbers)

The source paper reports 46/52 events (88.5%) negative within 72h, mean −16.97%, persisting across
BTC regimes (ANOVA p=0.24) and vs ETH/top-10 benchmarks (binomial p=2.2e-9, Bonferroni-robust).

**We treat every one of those numbers as an unverified prior, for three concrete reasons:**

1. **Reconstructed data.** The paper's own README states files `06_hourly_prices_events.csv`,
   `08_eth_prices_events.csv`, `10_regression_analysis_inputs.csv` are *"structural templates"* —
   row values generated to match reported means, not raw observations. Any backtest on them is
   circular. **Our probe re-fetches prices fresh from Binance and ignores the paper's price columns.**
2. **Post-hoc mining.** The headline sub-results (UNL-06 "100% win, Sharpe 5.85", UNL-10 "Sharpe
   6.25") are n=14 and n=11, and the 365-day-anniversary filter is *admitted* post-hoc. We discard
   all of those and test only the pooled, full-sample claim.
3. **Selection bias.** 52 hand-picked events is not an externally-defined universe. v0 inherits the
   list as a *seed* only; a HAS_PULSE here mandates a v1 re-run on an externally-sourced unlock
   calendar (Tokenomist/DropsTab/CMC) before any campaign.

## Edge thesis (hypotheses to probe)

A Binance-listed token has a **reliable negative 72h forward return after a scheduled unlock**,
distinct from a random window and surviving a market-beta control and a conservative cost haircut.

- **H1 (raw short edge):** across pooled events, the fraction with negative 72h return ≥ 60% AND
  mean short PnL net of the cost haircut > 0 AND the short return exceeds a random-window baseline.
- **H2 (BTC-relative robustness):** the negative drift persists after subtracting BTC's same-window
  return (≥ 60% negative, mean BTC-relative < 0). This is the gate that distinguishes a real
  supply shock from "alts drifted down that week."

**HAS_PULSE := H1 and H2.** Exactly one → WEAK_EDGE (likely market-beta contamination — record,
do not oversell). Neither → NO_PULSE. Data gate fails → BLOCKED_ON_DATA.

## STEP 0 — Data feasibility (the gate before any edge test)

The probe's mandatory first step. For each seed event it **re-fetches** 1h spot klines from Binance
(`api.binance.com`, public, no key) for `{TOKEN}USDT` and `BTCUSDT` around the unlock, and counts
how many events have usable independent price data (symbol listed at that date, enough bars to span
−45d → +78h). If usable events < `MIN_USABLE_EVENTS` (default 25) → **BLOCKED_ON_DATA**, no edge
claim. Expected attrition: tokens not on Binance *spot* (some were futures-only or listed later),
and unlocks predating the pair's listing.

**Known limitation, documented:** the seed CSV is date-granular (no intraday unlock time); the
probe assumes 00:00 UTC. This is acceptable for a 72h-horizon HAS_PULSE screen but **must be
refined to the real unlock timestamp before any backtest or deployment.**

## Cost realism

Per the cost-tooling finding ([../reports/...] / memory [[backtest-cost-tooling-finding]]), the
engine historically *over*charged majors — but illiquid alts cut the other way (high funding, wide
slippage, real borrow). The probe applies a conservative **1.0% round-trip haircut floor** to the
short PnL. This is a floor, not a model; the gross effect (~−17%) dwarfs it if real, so the haircut
matters far less than (a) data feasibility and (b) whether independently-fetched prices reproduce
the drift at all.

## What a PASS does and does NOT authorize

- HAS_PULSE authorizes **only** writing a v1 brief: re-run on an externally-sourced unlock calendar
  with real intraday timestamps, realistic per-token borrow/funding/slippage, and an honest
  tradeable-universe filter (Binance perp availability, liquidity floor).
- It does **not** authorize a campaign, config, paper agent, or any live risk. Same hard gates as
  every prior lane: `--execute` is a human gate; no automation touches production.
- Ethical note (from the source author): this edge exploits a structural tokenomics weakness by
  shorting. That is a deployment-policy decision for the human, flagged here, not decided here.

## How to run (read-only, dry)

```bash
# from repo root, with the project venv
python scripts/probe_token_unlock_shock.py
# artifacts: research/rbi_loop/token-unlock-72h-short-v0/{probe_result.json,probe_report.md}
```

Outputs a verdict (HAS_PULSE / WEAK_EDGE / NO_PULSE / BLOCKED_ON_DATA), the STEP 0 data audit, and
pooled edge metrics. No DB writes, no orders, no `--execute` path.

## Kill criteria

- BLOCKED_ON_DATA → the Binance-tradeable unlock universe is too thin to backtest; close v0, note
  in ledger, do **not** hand-curate more events to force the sample.
- NO_PULSE on fresh data → the paper's effect was a data-reconstruction artifact or doesn't survive
  independent prices; close the lane.
- WEAK_EDGE (only H1) → market-beta contamination; the "edge" is just alt-beta. Do not promote.

## v0 result (2026-06-20) — NO_PULSE

Probe ran against fresh Binance spot klines. Data feasibility was excellent (**49/52** events
usable, 0 fetch failures), so this is a real edge test, not a data block. The independently-fetched
prices do **not** reproduce the source paper's claim:

| Metric | Source paper claim | This probe (fresh data) |
|---|---|---|
| Negative 72h (raw) | 88.5% (46/52) | **49.0%** (24/49) — a coin flip |
| Mean raw 72h return | −16.97% | **+0.98%** (median +0.93%) |
| Negative 72h vs BTC | 88.5% | **46.9%** |
| Mean BTC-relative 72h | −17.18% | **+1.09%** |

Mean short PnL net of the 1% haircut is **−1.98%**, and the short return is **−1.68%** *worse* than
the random-window baseline. Both gates fail; verdict **NO_PULSE**.

**Interpretation.** This is the most parsimonious confirmation of the brief's stated concern: the
paper's price columns are self-described "structural templates" (reconstructed to match its own
reported means), so its 88.5% / −17% figures were never independent observations. On real,
independently-sourced prices the sign flips to ~breakeven. The cheap probe did exactly its job —
falsified an impressive-looking external claim for a few minutes of network time, before any
campaign cost.

**Residual caveat (one, and it cannot rescue the result).** The seed CSV is date-granular; the
probe anchors entry at 00:00 UTC of the unlock date. A wrong intraday anchor could blur a sharp move
but cannot plausibly turn a −17% mean into +1% across a 72h horizon. If you want to *fully* retire
the hypothesis rather than just this dataset, the only justified follow-up is a **v1 with real
intraday unlock timestamps** from an external calendar (Tokenomist/DropsTab) — not subsetting this
sample to "cliff only" or ">5%", which would be the exact post-hoc overfitting the brief bans.

**Decision:** close v0. Token-unlock 72h short is **rejected at Gate 1** as a standalone edge on
independent data.
