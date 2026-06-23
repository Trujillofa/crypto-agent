# Lane Brief + Probe Spec — Order-Flow Microstructure Probe v0 (Gate 0 + Gate 1)

**Status:** SPEC / build handoff. Planned by Claude (planner/reviewer); to be built by Grok (builder)
and run read-only before any ingestion-schema change, executor change, config, or live risk.
**Verdict:** pending Gate-1 run.
**Predecessors / context:**
- Program terminal state: [../reports/research-consolidation-2026-06-19.md](../reports/research-consolidation-2026-06-19.md)
- Different-universe program terminal: both mNAV and Polymarket closed WEAK_EDGE (ledger §"Different-universe program status")
- Infrastructure reality (the reason this is a *different* class of probe): the ingest layer is a
  **closed-candle aggregate pipeline** — `src/ingest/websocket.py` subscribes only to
  `@kline_<interval>` and `@markPrice`, and discards every kline update until `k["x"]==true`
  (close). Storage model is `Ohlcv` only (`src/ingest/models.py`); there is **no** depth/tick/trade
  store. So this lane tests data the system has never ingested.

---

## Why this lane (first principles)

Every prior null shares one substrate: **time-aggregated OHLCV candles on liquid majors.** ~1,440+ WFO
runs across directional, regime-gating, and relative-value families collapsed to "no edge," and the
two non-directional pulses (funding carry, mNAV) died on durability/cost. The structural conclusion
is that retail aggregate candle data on liquid assets is efficiently priced — the missing ingredient
is a **differentiated advantage**, most plausibly a *data* advantage the crowd does not pay the cost
to ingest.

Aggregated candles mask the actual mechanic of price formation: the sequence of signed trades and the
depletion of resting liquidity. **Order-flow imbalance (OFI)** — the net of aggressor-buy vs
aggressor-sell volume — is the most-studied microstructure predictor of short-horizon price pressure,
and it is *structurally less crowded at the retail level* because it requires ingesting heavy
tick-rate data that candle-based systems throw away (this codebase literally discards it at
`websocket.py:197`).

This is the first lane whose thesis is "**different telemetry**," not "different lane on the same
telemetry." That is exactly the move the terminal state called for.

## The decisive question this probe must answer: HORIZON

A microstructure edge is only useful to *this* system if it survives at a horizon the executor can
act on. The current executor is **candle-cadence with risk guards** — it has no sub-second or
co-located order path. Therefore:

- If signed-flow predictiveness exists **only at sub-10s horizons** → it is scientifically real but
  **operationally dead for this stack** (capturing it is a co-location/latency business, a Path-2
  escalation decision, not a code change). Record as `NO_PULSE_FOR_STACK`.
- If it exists at **≥60s horizons and survives a realistic taker cost haircut** → `HAS_PULSE`, and
  *only then* is building a faster decision loop + tick ingestion justified.

The probe exists to locate that crossover, cheaply, before any infrastructure is built.

## The cost-discipline that keeps this cheap (the key design choice)

**v0 uses only `aggTrade` data, which Binance serves historically over REST** (`/api/v3/aggTrades`,
paginated by time/`fromId`). Signed order flow is fully reconstructable from aggregate trades using
the maker-side flag — **no live capture, no new persistent store, no schema change.** This makes v0 a
read-only historical study, the same shape as every prior probe.

The expensive part — live L2/depth capture and book reconstruction (`@depth`, `@bookTicker`) — is
**deferred to v1 and gated behind a v0 pulse.** We do not build a tick-ingestion stack to test a
hypothesis that free historical aggTrade data can already falsify.

## Signal definitions (v0, aggTrade-only)

Per symbol, bucket aggTrades and compute, over a rolling lookback window `W`:

- **Signed volume / OFI:** each aggTrade is signed by aggressor side. Binance `aggTrade.m` =
  "was the buyer the maker?" → **`m == true` ⇒ aggressor is the SELLER ⇒ signed volume −qty**;
  **`m == false` ⇒ aggressor is the BUYER ⇒ +qty.** (Get this convention exactly right — inverting it
  flips the entire result. This is the microstructure analogue of the mNAV inverted-extreme bug.)
  `OFI(W) = Σ signed_qty over the window`.
- **Normalized OFI:** `OFI(W) / Σ |qty|` (a fraction in [−1, 1]), and a per-symbol z-score of that
  fraction (so BTC and SOL are comparable, per the mNAV normalization lesson).
- **VPIN (optional, secondary):** volume-bucketed order-flow toxicity — |buy − sell| / bucket volume
  averaged over the last N equal-volume buckets. Report if cheap; not required for the verdict.

## Edge thesis (hypotheses to probe)

A high (positive) normalized OFI over window `W` predicts **positive forward return** over horizon `h`
(and symmetrically for negative), beyond cost, at a horizon ≥ 60s.

- **H1 (flow predicts forward return):** forward return conditioned on OFI decile is **monotonic** and
  the top-vs-bottom decile spread is statistically significant (bootstrap p < 0.05, multiple-testing
  corrected across horizons), on ≥ 2 of {BTC, ETH, SOL}.
- **H2 (tradeable horizon):** H1 holds at **h ≥ 60s** — not only at sub-10s. Report the full horizon
  curve {1s, 5s, 10s, 30s, 60s, 300s} so the crossover is visible.
- **H3 (survives cost):** the top-decile-minus-bottom-decile forward edge exceeds a realistic round
  trip of **half-spread + taker fee** per symbol (the edge must pay for crossing the spread, since
  acting on flow means taking liquidity). Do **not** use the broken legacy engine cost model
  ([[backtest-cost-tooling-finding]]); use measured per-symbol spread from the data + current taker
  fees.

**Verdict semantics:**
- `HAS_PULSE` := H1 ∧ H2 ∧ H3 (edge exists, at a tradeable horizon, net of taker cost).
- `WEAK_EDGE` := H1 holds but edge only marginally clears cost, or the tradeable-horizon (≥60s) edge
  is tiny while a larger edge exists only sub-10s.
- `NO_PULSE_FOR_STACK` := H1 holds **only** at sub-10s horizons (real signal, untradeable here) —
  record this distinctly; it is a Path-2 (latency-business) signal, not a code-tweak signal.
- `NO_PULSE` := no monotonic, significant relationship at any horizon.
- `BLOCKED_ON_DATA` := aggTrade backfill coverage too thin or rate-limited to produce stable
  decile statistics.

## STEP 0 — Data feasibility (gate before any edge claim)

Mandatory first step, and it differs from prior probes because **the data is not in the DB.** It must
be backfilled read-only from the public REST endpoint and cached to disk.

- **Scope deliberately small:** a bounded window of **2–4 weeks** for BTCUSDT, ETHUSDT, SOLUSDT —
  **not** a year. Majors print millions of trades/day; 2–4 weeks already yields millions of rows per
  symbol, far more than enough for sub-minute decile statistics, and avoids hammering rate limits.
- **Cover ≥ 2 regimes:** the window must include at least one elevated-volatility stretch and one
  quiet stretch (pick by realized vol, document the choice) so the result is not a single-regime
  artifact.
- **Rate-limit discipline:** paginate by time/`fromId`, respect Binance weight limits with backoff,
  cache raw aggTrades to `data/microstructure/<symbol>/` (gitignored — do **not** commit raw ticks;
  commit only the result JSON + report). If a full window cannot be fetched cleanly →
  `BLOCKED_ON_DATA`, record coverage, do not hand-fill or fabricate.
- **Confirm the `m`-flag convention empirically:** sanity-check that aggressor-buy volume correlates
  with up-ticks on a sample before running the full study (catches a flipped sign early).

## Methodology rigor (lessons from the artifact rejections)

The mNAV and Polymarket probes both reported false verdicts from methodology bugs that the review
gate caught. Bake the fixes in from the start:

1. **No look-ahead.** Forward return at horizon `h` must start **strictly after** the signal window
   `W` closes. The trade(s) used to compute OFI cannot overlap the return measurement.
2. **No bid-ask-bounce artifact.** Measure forward return on a consistent price reference (e.g.
   horizon-ahead trade price or a mid built from a short VWAP), and haircut by half-spread in H3 —
   do not let alternating buy/sell prints manufacture a spurious "edge."
3. **Decile monotonicity, not a single correlation** (Polymarket-bucket lesson): the relationship
   must be monotonic across OFI deciles, not just a nonzero aggregate slope.
4. **Train/forward split.** Fit decile boundaries on the first portion, evaluate the edge on the
   held-out forward portion. Report both.
5. **Significance + multiple-testing correction.** Bootstrap the top-vs-bottom decile spread
   (≥ 1000 resamples, block bootstrap to respect autocorrelation); correct across the 6 horizons
   (the mNAV "OR-pass across horizons" bug).
6. **Baseline / control.** Compare against shuffled-sign flow (destroys the order-flow information,
   preserves volume distribution) — the edge must beat this null.
7. **Concentration cap.** Confirm the edge is not driven by a single day/hour (e.g. ≤ 25% of the
   top-decile PnL from any one UTC day), per the mNAV concentration cap.

## Execution-feasibility caveat (a PASS is not an agent)

Even `HAS_PULSE` at ≥60s does **not** yield a deployable agent. Acting on a 1–5 minute flow signal
requires (a) a **sub-candle decision loop** (the current loop is candle-cadence), (b) **tick
ingestion in production** (new `@aggTrade` WebSocket consumer + a store the engine can read), and
(c) **taker execution** with spread-aware sizing. So the honest sequence is:

1. v0 aggTrade-only historical probe (this spec) → verdict.
2. **Only if `HAS_PULSE`:** v1 — live L2/`bookTicker` capture probe to test whether *top-of-book
   imbalance* adds to the aggTrade signal, **plus** an execution-feasibility audit (can the system
   run a sub-candle taker loop safely? — analogous to `short-side-parity-audit-v0.md`).
3. Only then a surface brief. No campaign, config, paper agent, or live risk before that.

## Pre-committed stop rule

- `NO_PULSE` → the order-flow data-advantage thesis is falsified at low cost on the cheapest available
  telemetry. **Do not build the tick-ingestion stack.** This closes the microstructure lane and, with
  it, the most plausible remaining public-data advantage — escalate to the explicit bank-vs-Path-2
  decision in the consolidation doc.
- `NO_PULSE_FOR_STACK` (sub-10s only) → the signal is real but requires a latency/co-location
  capability this operator does not have. This is **not** a code decision — it is a Path-2 business
  decision (build/buy low-latency infra) that must be made deliberately, not on probe momentum.
- `WEAK_EDGE` → do not advance to v1/audit without a clear, cost-surviving ≥60s edge.

## How to run (when built — read-only, dry)

```bash
python scripts/probe_orderflow_microstructure.py    # to be built by Grok
# raw cache:  data/microstructure/<symbol>/*.parquet|jsonl   (gitignored)
# artifacts:  research/rbi_loop/microstructure-orderflow-v0/{probe_result.json,probe_report.md}
```

Read-only against the public Binance `aggTrades` REST endpoint. No DB writes, no orders, no
`--execute` path, no ingestion-schema change. Same Gate-1 verdict semantics as every prior probe.

## Kill criteria (summary)

| Verdict | Meaning | Action |
|---------|---------|--------|
| `HAS_PULSE` | monotonic, significant, cost-surviving edge at ≥60s | advance to v1 + execution audit (not deploy) |
| `WEAK_EDGE` | edge marginal at tradeable horizon | stop; do not build |
| `NO_PULSE_FOR_STACK` | edge only sub-10s | stop; flag as Path-2 latency decision |
| `NO_PULSE` | no edge at any horizon | close lane; falsifies the public-data advantage thesis |
| `BLOCKED_ON_DATA` | aggTrade coverage too thin | record coverage, do not hand-fill |
