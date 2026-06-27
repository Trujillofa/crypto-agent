# Lane Brief — Cross-Sectional Altcoin Momentum Probe v0 (Gate 0 → Gate 1)

**Status:** Gate 0 brief (DRAFT — deliberate reopen candidate; not yet approved).
**Author role:** Claude (planner). Cheap probe `.py` to be implemented + run as the Gate 1 step.
**Decision context:** The program is **banked terminal** as of
[../reports/research-consolidation-2026-06-23.md](../reports/research-consolidation-2026-06-23.md)
and [../reports/deep-edge-research-reconciliation-2026-06-24.md](../reports/deep-edge-research-reconciliation-2026-06-24.md).
Opening this lane is a **deliberate Path-2-style reopen**, not momentum. It is justified only by the
argument below, and is governed by the same hard rule as every lane: **no strategy/sweep code until
this probe returns `HAS_PULSE`.**

---

## Why this lane (first principles) — and why it is *not* already closed

Every dead lane in the program was one of:
- **single-name directional** prediction on liquid majors (OHLCV structure) — efficient venue, NULL; or
- a **single-pair / single-event** signal (carry, mNAV, Polymarket, OFI, unlock, liquidation).

The capstone's killer argument is *"public, liquid, aggregate data is efficiently priced."* That
argument is about **predicting one instrument's own forward return**. It does **not** preclude a
**cross-sectional risk premium** — i.e. that *relative* ranking across a universe sorts future
relative returns even when no single name is individually forecastable. Cross-sectional momentum is
the single most-documented systematic factor in crypto (analogous to equities XS-MOM); it is a
different mathematical object from anything probed here, and the "shared-beta corr 0.59 / breadth
NO_PULSE" finding (daily-trend) was a **market-timing** result, not a cross-sectional rank result.

**Mechanism (not hope):** a long top-decile / short bottom-decile basket harvests dispersion. It is
also structurally **lower-cost-sensitivity than any single-pair lane**: returns come from the
*spread* between legs, and the basket is dollar-neutral, so it does not need a single trade to clear
12bps — it needs the cross-sectional spread to exceed turnover cost.

## The signal / edge (precise definition — implement exactly)

Universe: the liquid Binance USDⓈ-M perp set we already ingest + can backfill (target ≥ 20 names;
**exclude** stablecoins and wrapped/duplicate tickers). Daily bars (resample 1h→1d if needed).

For each rebalance date `t` (weekly, to bound turnover) and each symbol `s`:
- **Momentum score:** `mom(s,t) = return over lookback L` (probe L ∈ {7d, 14d, 30d}), **skip the most
  recent 1d** (avoid 1-bar reversal contamination).
- **Cross-sectional rank:** sort all `s` by `mom`. Long the top quantile `Q` (probe Q ∈ {top 20%,
  top 30%}), short the bottom quantile, **equal-weight, dollar-neutral**.
- **Forward return:** realized basket return over the next holding period `H` (= rebalance interval,
  start at H=7d), **net of turnover cost** at the real round-trip (12bps/side leg turnover) +
  per-leg funding carry over H.

## Gate 1 — `HAS_PULSE` thresholds (pre-registered, falsifiable)

`HAS_PULSE` requires **all** of:
1. **Net positive** mean weekly basket return after 12bps turnover + funding, on the full span.
2. **Robust across the grid**, not a single cell: positive in ≥ ⅔ of the (L × Q) combinations.
3. **Not concentration-driven:** best single week ≤ 35% of total PnL (same concentration gate that
   killed the WFO near-misses).
4. **Monotone-ish sort:** top quantile out-returns bottom quantile (sign-correct) in the pooled
   long/short spread, bootstrap p < 0.05 (1000 resamples).
5. **Survives a random-walk null:** shuffle the cross-sectional labels per date → edge disappears
   (standing Gate-0/Gate-1 requirement per `research-reset` + #118).

Anything less → `WEAK_EDGE`/`NO_PULSE` → **close the lane, document, do not sweep.**

## Honest expected value (read before approving)

- **Prior:** moderate-to-low. XS-MOM is real in the literature but mostly *net of low retail costs*;
  at 12bps/leg + weekly turnover + alt short-borrow/funding, the spread may not survive. Crypto
  XS-MOM also crashes hard in regime flips (it is short-vol in disguise).
- **Kill risks the probe must surface honestly:** (a) survivorship — only include names *listed at
  time t*, never today's universe backfilled; (b) the short leg's funding/borrow is the real cost,
  not a footnote; (c) capacity is fine for ≤$10k, so this is *not* a Path-2 economics-gate casualty
  like illiquid microstructure — the only question is whether the edge clears cost.
- **Why it is still worth one cheap probe:** it is the single untested object that the capstone's own
  efficiency argument does not cover, the data is already in our DB (read-only), and the probe is
  ~1 afternoon. If it is NULL, it *strengthens* the bank decision (closes the last conceptual gap);
  if it pulses, it is the first relative-value vehicle the program has found.

## Cheap probe (Gate 1) — build spec

- Script: `scripts/probe_xs_altcoin_momentum.py` (read-only; TimescaleDB OHLCV mirror).
- CLI: `--universe-min 20 --lookbacks 7,14,30 --quantiles 0.2,0.3 --hold 7 --cost-bps 12
  --funding-cadence scaled_8h --start ... --end ... --shuffle-null --output-dir research/rbi_loop/xs-altcoin-momentum-v0/`
- Output: calibration of the long/short spread per (L,Q) cell + the five gate checks + a
  `probe-verdict.json` (`HAS_PULSE|WEAK_EDGE|NO_PULSE`) consumable by `rbi_loop_guard.py`.
- **Run is the human `--execute` gate.** Nothing here touches live, config, or strategy code.
