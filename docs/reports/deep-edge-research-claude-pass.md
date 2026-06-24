# Deep Edge Research — Claude independent pass (2026-06-24)

> **Status:** independent *pre-reconciliation* pass (parity with
> [`deep-edge-research-grok-pass.md`](./deep-edge-research-grok-pass.md)). Its two cheap proposals
> (A new-listing, E small-perp funding) **folded on reconciliation** — see
> [`deep-edge-research-reconciliation-2026-06-24.md`](./deep-edge-research-reconciliation-2026-06-24.md)
> for the adjudicated outcome (stay banked; A1 measured dead; Path 2 held). Preserved for the record.

**Goal (intact):** find a *durable, deployable* edge for a solo operator with a server, public
exchange APIs, and modest capital. Not "any positive backtest" — an edge that survives the #118 null,
realistic costs, and forward time.

**Hard constraint from the bank** (`research-consolidation-2026-06-23.md`): directional prediction on
**liquid-major public OHLCV** is terminal NULL across ~1,440 WFO runs + a dozen probes, in coherent
structural ways (efficient venue / most-arbitraged trade / regime artifact / friction-eats-edge). The
cost-model error was found, fixed, and re-screened — **no lane revived**. Microstructure OFI on majors
is NULL (sign corr 0.009). Funding carry on majors banked (excess over risk-free gone).

## The reframe that keeps the goal intact

The bank covers **one edge type on one population**: *prediction edge* on *liquid majors*. Two spaces
it never tested:

1. **A different population** — the *illiquid / newly-listed* regime. Majors are MM-saturated; thin or
   brand-new books are not (yet). The bank says nothing about them.
2. **A different edge type** — *access/structural* edge (not prediction). For a small operator, **size
   is the edge**: flows a whale cannot harvest without diluting them.

Everything else (co-location/latency, proprietary alt-data, capital scale) is a *business*, not
accessible to this operator → out of scope unless the operator decides to become that business.

## Candidate frontier — ranked for THIS operator

Scored on: **accessible now** (public data / trusted venue) × **cheaply falsifiable** × **bank does
NOT already cover** × **deployable capacity > operating cost**.

| # | Candidate | Why it might survive where majors didn't | Accessibility | Cheapest Gate-1 probe (read-only) | Mandatory null (#118) | Risk that kills it |
|---|-----------|------------------------------------------|---------------|-----------------------------------|----------------------|--------------------|
| **A** | **New-listing / low-float microstructure** (first N hours/days of a new Binance/Bybit perp or spot listing) | Book is thin, MM not yet saturated, flow is retail + mechanical (listing pump, index/unlock schedule). *Different population from majors.* | **High** — public OHLCV+aggTrade, trusted venue | Backfill first-72h candles+aggTrade for all listings in last 12–18mo; test mechanical drift/reversion (e.g. listing-pop fade, day-1 close→day-3 drift) | Shuffle across listings; block-bootstrap p_adj; ≤25% conc; cost = new-listing spread (wide!) | Wide spread + tiny size; survivorship/look-ahead on listing dates |
| **E** | **Small / new-perp funding dislocation** | New perps run violent funding (often >100% APR) *because* they're hard to arb (borrow/custody friction). Majors' carry is arbed away; small perps' is not — different population. | **High** — public funding + mark history | Historical funding vs realized cost-to-carry on small/new perps; how persistent, how negative-after-cost | Shuffle perp-IDs; p_adj; conc cap; cost = perp taker + borrow + custody haircut | Custody/venue risk; capacity; funding flips before you capture |
| **B** | **Illiquid-venue microstructure** (Path 2, Gate 0 open) | Same thinness thesis as A, on smaller exchanges | **Medium** — needs venue account + L2/trade data | (parked) Gate 0 economic sub-gates first | per path2 brief | **Custody/solvency** — can lose bankroll to venue; A is the safer expression of the same idea |
| **C** | **On-chain DEX/CEX basis & LP fee capture on small caps** | DEX books are slower; small-cap CEX-DEX basis less arbed | **Medium** — node + capital + gas | DEX-CEX price gap on small caps vs gas+bridge cost | shuffle; p_adj; conc; cost = gas+bridge+slippage | Saturated by pro searchers on the easy/atomic part; gas eats small size |
| **D** | **Access/operational edges** (airdrops, points/restaking, launchpad allocations, new-perp listing funding) | **Size IS the edge** — non-scaling flows whales can't farm | **High** but **not an engine strategy** | n/a — operational checklist, not a backtest | n/a | Not a `src/strategy` fit; time/ops cost; rug risk |

## Recommendation

1. **Run two cheap, public-data Gate-1 probes in parallel — both target the regime gap the bank never
   tested, on trusted venues, no new infra:**
   - **A — new-listing microstructure** (first-72h drift/reversion of new listings).
   - **E — small/new-perp funding dislocation** (funding vs realized cost-to-carry on non-major perps).
   Both are falsifiable cheaply and honestly; both have an obvious null and a wide-spread cost model
   that will kill them fast if there's nothing there (good — cheap NO_PULSE is a win).
2. **Hold B (illiquid-venue) at Gate 0** — A is the same illiquidity thesis without custody risk; only
   advance B if A shows a pulse *and* the operator wants venue exposure.
3. **Record D as a real non-engine edge** for the operator (size-is-edge), but it's an ops track, not
   a strategy-engine lane — don't force it into the backtester.
4. **Reject** any re-probe of liquid-major prediction (majors OHLCV, majors OFI, majors carry) — banked.

**Keep-goal-intact verdict:** the goal (durable deployable edge) is unchanged; the *only* honest place
left to look is the **illiquid/new population** (A, E, B) and **size-is-edge access flows** (D). A and
E are the cheapest falsifiable next probes and respect every hard rule (read-only, HAS_PULSE-gated,
#118 null mandatory, human `--execute`).

## Open questions to reconcile with Grok's pass

- Does Grok independently land on the illiquid/new-population reframe, or propose a genuinely different
  surviving space?
- Survivorship/look-ahead hazard on listing-date data — is A cleanly backfillable without bias?
- Is E distinguishable from the banked majors-carry result, or does it collapse to the same
  "premium already arbed minus custody cost" once costs are honest?
- Any candidate Grok proposes that is *accessible* and *cheaply falsifiable* that I missed?
