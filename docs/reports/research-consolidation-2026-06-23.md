# Research Capstone — 2026-06-23 (PROGRAM TERMINAL)

**Purpose:** Close the entire crypto-agent research program — not just the technical-crypto
structural-probe surface (closed 2026-06-19), but the post-consolidation extensions into a
*different objective* (market-neutral yield), *different universes* (treasury equity, prediction
markets), and *different telemetry* (order-flow microstructure). This is the canonical terminal
artifact; it **supersedes** [research-consolidation-2026-06-19.md](./research-consolidation-2026-06-19.md)
and the ledger points here.

**Related:**
[research-consolidation-2026-06-19.md](./research-consolidation-2026-06-19.md) (technical-crypto close),
[research-reset-2026-06-06.md](./research-reset-2026-06-06.md),
[autoresearch-candidate-ledger.md](./autoresearch-candidate-ledger.md),
specs under `docs/specs/` for each post-consolidation lane.

---

## Executive summary

Across **five distinct objective functions** and **four distinct universes**, a small operator
trading **public data with no differentiated advantage** finds no durable, tradeable edge. This is
not a tuning failure or a run of bad luck: ~1,440+ WFO runs plus a dozen cheap probes fail in a
small number of *coherent structural ways*, the two results that looked alive were caught as
artifacts by the review gate, and the one systematic measurement error (cost model) was found,
fixed, and re-screened with no lane reviving. The missing ingredient is a **differentiated
advantage** — proprietary/alt data, latency/execution infrastructure, capital scale, or privileged
structural access — **not another strategy and not another market.**

The program is therefore **terminal**. The honest forward choice is binary: (1) **bank it** (this
document), or (2) **pursue a differentiated advantage** as a deliberate, separately-scoped new
program — entered only with a specific, named, non-public edge, never on momentum.

---

## The complete scoreboard (by objective function)

### Objective 1 — Directional price prediction on liquid majors (OHLCV structure)
The whole original program. **Uniformly NULL.**

| Surface | Tested | Result |
|---|---|---|
| Classic strategy library (`simple_ma`, `rsi_reversal`, `bollinger`, `macd`, `momentum`, `cci`, `vwap`, `mean_reversion`, `trend_pullback`, `breakout_retest`, MTF set) | per-symbol WFO, SOL/BTC/ETH/BNB/AVAX, 1h/4h | no edge; SOL `trend_pullback` was the only survivor |
| Autoresearch overlay families, Waves 1–10 (~1,440 runs) | `trend_pullback_overlay`, `combined_focus`, `breakout_retest`, `volatility_squeeze`, `funding_extreme`, `regime_gated_pullback`, `range_reversion_bounded`, standalones | 1 "deployable" (SOL 1h overlay) → **0 live fills**, superseded at corrected costs |
| RBI data-first probes | cross-venue basis/dislocation (v0/v1), higher-TF regime allocator, session-liquidity router, liquidity-sweep, funding normalization, short crowding | all NO_PULSE / WEAK_EDGE / WFO-fail → CLOSED |
| Higher-TF trend-following | daily SMA50 | HAS_PULSE but **undeployable** (breadth NO_PULSE, shared-beta corr 0.59) |
| Token-unlock 72h shock (external paper) | 49 events, fresh Binance data | **NO_PULSE** — 49% neg / +0.98% vs claimed 88.5% / −17%; paper used reconstructed prices |

### Objective 2 — Market-neutral yield (non-directional)
The first objective change. **One real pulse — but the tradeable excess is gone.**

| Surface | Result |
|---|---|
| Delta-neutral funding carry (long spot + short perp) | v0 **HAS_PULSE** (~5% notional) → v1 durability **BANK**: on capital ~4%, **excess over risk-free negative** on all three majors, carry **compressed ~80% forward**. The known carry premium, already arbitraged (Ethena-style). |

### Objective 3 — Cross-asset relative value (different universe #1)
| Surface | Result |
|---|---|
| mNAV — crypto-treasury equity vs crypto NAV premium reversion | v0 reported HAS_PULSE → **rejected on review as a 4-bug artifact** → corrected re-run **WEAK_EDGE** (only SBET, one reflexive name, one 2025 window; MSTR fails). Regime/bubble artifact, no cross-name edge. |

### Objective 4 — Probability calibration (different universe #2)
| Surface | Result |
|---|---|
| Polymarket favorite-longshot calibration (20,999 resolved markets) | v0 reported BLOCKED → **rejected on review as a pagination/gating artifact** → corrected re-run **WEAK_EDGE**: market is well-calibrated; τ=24h longshot edge inside cost; τ=72h lone signal is single-category favorite-underpricing, net +0.3¢ on a 97¢ contract. Friction eats the edge. |

### Objective 5 — Microstructure / different telemetry (the would-be public-data advantage)
The most plausible remaining public-data advantage: ingest the tick-level data candle systems throw
away. **NULL — and decisively so.**

| Surface | Result |
|---|---|
| Order-flow imbalance (OFI) from `aggTrade` signed flow, BTC/ETH/SOL, 33.3M trades, 6 horizons | **NO_PULSE** — top-vs-bottom OFI-decile forward spread +0.3 to +4.7 bps, *growing* with horizon (no hidden sub-10s edge), but fails **all four gates on all three symbols**: bootstrap p≈0.48–0.52, non-monotonic, 30–43% single-day concentration, **net edge −7 to −10 bps**. Tick-level sign correlation **0.009** (BTC). Signed flow on liquid majors is already priced by market makers. |

---

## The structural taxonomy (the real finding)

Every detectable edge across all five objectives reduces to one of three structural classes, none of
which a public-data retail operator can monetize:

| Class | What it means | Lanes |
|---|---|---|
| **Efficient venue** | the information is already in the price; no informational edge exists | OHLCV majors (×many), microstructure OFI |
| **Most-arbitraged trade** | a real premium exists but its excess over the cost of capital is competed away | funding carry |
| **Regime / bubble artifact** | the "edge" is one non-recurring episode, not a generalizable relationship | mNAV |
| **Friction eats the edge** | the mispricing is real but smaller than the cost to capture it | Polymarket, microstructure (also) |

**The meta-conclusion:** the failures are not independent accidents — they are the *same fact* seen
from five angles. Public, liquid, aggregate data is efficiently priced; what little structure remains
is either non-recurring or below transaction cost. The differentiator a profitable operator has is
never "a cleverer strategy on the same public data" — it is a **structural advantage**: data others
don't have, latency others can't match, scale others can't deploy, or access others can't get.

---

## Why banking is sound (the verification, not faith)

Before closing a multi-month program, the decision-relevant question is *"could a systematic error
have manufactured these nulls?"* It was checked, and the answer is no:

1. **The one systematic measurement error was found, fixed, and re-screened.** The backtest engine
   overcharged costs ~3× and funding ~8× (and defaulted the trend filter ON) — PRs #91–#98. All
   three were corrected on merit; the engine now resolves `REALISTIC_FEE_RATE` (0.04%/side) +
   `REALISTIC_SLIPPAGE_PCT` (0.02%/side) ≈ 12 bps round-trip and logs a `_resolved_cost_audit()` at
   every run start. **Every closed family was re-screened at corrected costs and no lane revived
   into a deployable candidate** (the most cost-sensitive, AVAX 4h bollinger, swung −25% → +11% but
   still failed on 70% single-trade concentration).
2. **The review gate caught its own false positives.** Both mNAV (HAS_PULSE) and Polymarket
   (BLOCKED) were artifact verdicts, identified on review and corrected to WEAK_EDGE on re-run. A
   process that self-corrects in *both* directions is cross-validated.
3. **The nulls cohere.** They cluster into the taxonomy above rather than scattering — coherence is
   evidence of a real conclusion, not an aggregate of unrelated bugs.
4. **The failure margins are large, not marginal.** Microstructure fails by ~10× on cost *and* is
   statistically insignificant (p≈0.5). These are not near-misses a methodology tweak could flip.

**Therefore further double-checking has negative expected value** — it is the gate-shopping /
"one more probe" trap the program's own stop rules (research-reset-2026-06-06) were written to
prevent. The verification that mattered is done.

---

## The forward fork

| Path | What it is | When to take it |
|---|---|---|
| **1 — Bank (this document)** | Accept the terminal state. Keep live services as idle monitors. Stop opening public-data lanes on majors. | Default. The evidence supports it now. |
| **2 — Pursue a differentiated advantage** | A separate, larger program that *starts* from a named non-public edge (illiquid-venue microstructure where the book is thin, a latency/co-location setup, a proprietary alt-data feed, an on-chain/MEV execution stack) and only then selects a market. Gate 0 becomes "do I have a credible, defensible information/latency/access asymmetry?" | Only with a specific advantage named out loud, entered deliberately — never on momentum. |

These are sequential, not exclusive: banking gives Path 2 a clean baseline ("here is everything that
does not work without an edge, so the edge must come first").

> **Update (2026-06-24) — both forks resolved; program fully terminal.** Fork 1 stands and the
> public-data book is **sealed**: the last unprobed reset-doc primitive (A1 forced-liquidation cascade)
> was measured dead (#119), and an independent dual-pass edge review (Claude × Grok) converged on stay
> banked. Fork 2 was *opened deliberately* (illiquid-venue microstructure, 2026-06-23) and then
> **closed at Gate 0 on economics (2026-06-24)** for the accessible operator profile (solo, public
> data, ≤$10k): it fails its own spread-vs-edge / capacity / custody / defensibility sub-gates on
> paper — see [path2-gate0-economics-close-2026-06-24.md](./path2-gate0-economics-close-2026-06-24.md).
> The **only** surviving Path-2 prior is a C-tier *business* (MM rebate / latency / privileged access),
> out of reach from the current profile and requiring its own Gate 0 brief if ever pursued. **No
> public-data or accessible Path-2 lane has a positive-EV next step.** Forward tracks are non-research:
> redeploy the proven method to the working (cTrader FX) system, and/or the access/"size-is-edge"
> operations track. Reconciliation: [deep-edge-research-reconciliation-2026-06-24.md](./deep-edge-research-reconciliation-2026-06-24.md).

---

## What stays / what stops

| Item | State |
|------|-------|
| Directional OHLCV-structure research on majors | **Stopped** — efficient venue, no edge at correct costs |
| SOL overlay / sentiment-macro live services | **Idle monitors only** — not viable forward vehicles (#99, #101) |
| Funding carry | **Banked** — known premium, excess over risk-free gone forward (#105) |
| Different-universe lanes (mNAV, Polymarket) | **Closed WEAK_EDGE** — artifact verdicts corrected on review |
| Microstructure / tick-ingestion build | **Not built** — OFI NO_PULSE on majors; would be a from-scratch ingest+exec build, now unjustified (#110) |
| Forced-liquidation / cascade flow (A1) | **Measured dead** — WEAK_EDGE → economically NO_PULSE; last reset-doc primitive, public-data book now sealed (#119) |
| Path 2 illiquid-venue microstructure (accessible expression) | **Closed at Gate 0 (economics)** — fails spread-vs-edge/capacity/custody/defensibility for solo ≤$10k; reopens only with a C-tier venue advantage (2026-06-24) |
| Corrected cost/funding defaults (#94) + run-start cost audit (#96) | **Kept** — correctness fix, applies to all future backtests |
| RBI loop tooling + hard rules (cheap-probe HAS_PULSE, `--execute` human gate) | **Kept** — reusable for any future data-first primitive |
| All probe scripts + seed data (unlock, carry, mNAV, Polymarket, microstructure) | **Kept** — reusable infra + a proven review discipline that caught two false positives |

**Discipline to carry forward:** the profitable cTrader FX agent derives per-instrument costs
empirically (`derive_sm_pair_costs.py`). The cost-realism saga here is the same lesson — calibrate
costs from data before trusting any backtest verdict, and re-screen on capital + excess-over-risk-free
+ forward before spending build capital on any "pulse."
