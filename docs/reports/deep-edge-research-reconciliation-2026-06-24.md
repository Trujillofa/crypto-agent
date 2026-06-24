# Deep Edge Research — Reconciliation (Claude × Grok)

**Date:** 2026-06-24
**Inputs:** two independent passes —
[Claude pass](#claude-pass-summary) (frontier-reframe) and
[`deep-edge-research-grok-pass.md`](./deep-edge-research-grok-pass.md) (exhaustive-enumeration).
**Canonical bank:** [research-consolidation-2026-06-23.md](./research-consolidation-2026-06-23.md).
**Null discipline:** [RBI_AUTORESEARCH_LOOP.md](../RBI_AUTORESEARCH_LOOP.md) §Mandatory baseline (#118).
**Mode:** research only — no code, no probes, no `--execute`, no live-risk change.

---

## Bottom line (converged)

**Stay banked.** Two independent passes — one reasoning from "what population/edge-type does the
bank *not* cover," one enumerating every candidate space exhaustively — **land on the same place**:
there is no cheap, accessible, high-prior edge left for a public-data solo operator. The terminal
conclusion survives independent scrutiny.

Exactly **one** optional cheap probe survives both passes as honest *epistemic closure* (not optimism):
**A1 — forced-liquidation / cascade flow**, the single public primitive named in the reset doc that was
never run. Expected verdict **NO_PULSE** (same efficient-venue class that killed OFI). **Path 2
illiquid-venue** is the only lane with a non-trivial prior, and it is **not runnable** until the
operator attests venue access + clears the Gate 0 economic sub-gates.

---

## Where the passes converged

| Point | Claude | Grok | Status |
|---|---|---|---|
| Public-data prediction on liquid majors is terminal NULL | yes | yes | **Converged** |
| Missing ingredient is a *differentiated advantage*, not another market/strategy | yes | yes | **Converged** |
| Path 2 illiquid-venue is the #1 surviving lane, but operator-gated (not runnable now) | "hold B" | "rank 1, hold C1" | **Converged** |
| Reject all liquid-major / mid-cap-OFI re-probes (#110 decisive) | reject | reject | **Converged** |
| Any future probe must beat the #118 null (shuffled/phase-randomized, block-bootstrap p_adj, ≤25% conc) | yes | yes | **Converged** |
| Default action | (frontier, see below) | **stay banked** | **Converged after adjudication** |

## Where they diverged — and the adjudication

Claude's pass proposed two *cheap public-data* probes targeting the "illiquid/new population" gap.
Grok's pass independently **pre-rejected both**. On scrutiny, **Grok is right on both** — they fold:

| Claude proposed | Grok's counter | Adjudication |
|---|---|---|
| **A — new-listing microstructure** (first ~72h of new listings; "thin book, not yet MM-saturated") | A3: reject — survivorship/look-ahead, thin n, one-off events | **Folds → reject.** Decisive added reason: new listings are among the *most bot-contested* events in crypto (sniping is a latency game). "New" ≠ "uncrowded" here — the residual edge is a **latency business (C-tier)**, not a cheap retail probe. Claude underweighted this. |
| **E — small/new-perp funding dislocation** ("violent funding because hard to arb") | A4: reject — majors carry already BANK generalizes; alts add a negative-funding tail + venue risk; capacity tiny | **Folds → reject.** The high gross carry on alt perps is *compensation for real frictions* (borrow, custody, thin spot), so excess-after-honest-cost is expected ≤0 — the same "most-arbitraged given frictions" class, and arguably **worse** than majors carry (which Claude's own open question #3 anticipated). Closest call, but a strong prior + an extra kill risk ⇒ not worth the spend. |

**Net:** Claude's *reframe* (look at the illiquid/new population) was directionally correct, but its two
cheap expressions both collapse under first-principles scrutiny — and they collapse **back onto Path 2
illiquid-venue (C1)**, which is exactly where Grok ranked #1. The reframe does **not** open a new cheap
lane; it re-derives the one operator-gated lane both passes already hold.

## One substantive addition Claude's pass contributes (not in Grok's)

Grok's taxonomy is **entirely prediction/arbitrage** edge. It has no category for **non-prediction
access / structural edge**, where for a small operator **size *is* the edge** — flows a whale cannot
harvest without diluting them: airdrop/points farming, restaking/staking yield, launchpad/IEO
allocations, new-listing *funding* capture at the venue level.

This is a **real, durable edge source for this operator** and is genuinely *not covered* by the bank
(which only ever tested prediction/arb edge). But it is **not a `src/strategy` engine lane** — it's an
**operations track** (manual or lightly-scripted, account-level, capacity-capped by design). Recording
it honestly: it belongs on the operator's roadmap as a non-engine edge, not in the RBI loop. It does
**not** change the trading-engine verdict (stay banked).

---

## Converged recommendation

1. **Default — accept the bank.** Idle monitors only; open no new public-data engine lanes. Both
   passes agree the expected value of further Binance-class probes is ≤0 (the "one more probe" trap the
   reset rules forbid).
2. **Optional, max one — A1 liquidation/cascade probe**, *only* if the operator wants to convert
   "reasoned dead" → "measured dead" and close the reset-doc loop. It is the single unprobed public
   primitive; read-only; ~hours; **expected NO_PULSE**. Frozen spec (no alt-shopping): BTC/ETH/SOL
   perps, `!forceOrder@arr` clusters defined ex-ante, horizons +5m/+30m/+2h, #118 null
   (phase-randomized event times + block-bootstrap on excess vs matched quiet windows), net of ~10 bps
   taker. If it pulses it's a genuine surprise worth chasing; if not, the public-data book is sealed.
3. **Hold — Path 2 illiquid-venue.** Operator names venue/pair + feasibility table (spread, depth,
   volume) + economic sub-gates 1–4, sets `PATH2_ILLIQUID_VENUE_ACCESS_ATTESTED`; *then* Gate-1 OFI on
   that surface only. Both passes rank this #1; neither can run it without the operator's real-world input.
4. **Record (non-engine) — access/structural "size-is-edge" track.** Roadmap item for the operator
   (airdrops/points/launchpad/listing-funding), not an RBI lane. Claude's addition; out of engine scope.
5. **Reject without probe** — any liquid-major lane, mid-cap Binance OFI as a Path-2 substitute, alt
   funding carry, listing/unlock subsetting, more Polymarket/mNAV/macro, autoresearch/WFO without a new
   #118-beating `HAS_PULSE`.

## The fork for the operator

```
                 Accept bank  (recommended default)
                        |
        ┌───────────────┴───────────────┐
        ▼                               ▼
  Optional: A1 liquidation        Path 2: attest illiquid venue
  probe (~hours, expect NULL)     → Gate 1 on THAT venue only
  → seals public-data book        → only lane with a real prior
```

---

## Claude pass — summary (for the record)

Reframe: the bank covers **prediction edge on liquid majors**; two spaces it never tested are a
**different population** (illiquid/new) and a **different edge type** (access/structural, size-is-edge).
Proposed cheap probes A (new-listing microstructure) + E (small-perp funding) — **both folded on
reconciliation** (see adjudication). Held Path 2; flagged the access-edge track. Net: converges to Grok.

## Disposition

- **Converged verdict:** stay banked; A1 optional for closure; Path 2 held; access-track recorded.
- **Independence held:** Grok did not read Claude's pass; the agreement is genuine, and the one
  divergence (Claude's cheap picks) was adjudicated *against* Claude on the merits.
- **No code, no probe, no execute** was run for this reconciliation.

---

## A1 measured (2026-06-24)

**Ran:** `scripts/probe_liquidation_cascade.py` — report
[`liquidation-cascade-probe-v0.md`](./liquidation-cascade-probe-v0.md).

| Item | Result |
|------|--------|
| REST `allForceOrders` | Deprecated (`400` out of maintenance) |
| Historical panel | Official UM **metrics** cascade-proxy (OI drop + taker imbalance), 14d, BTC/ETH/SOL |
| Events | 50 / 50 / 86 per symbol |
| Verdict | **WEAK_EDGE** → economically **NO_PULSE** (no cell clears 10bps RT + Holm `p_adj` + breadth) |
| Best net | BTC +0.44bps, ETH +0.69bps @ +120m fade — inside noise, bootstrap `p_adj≈1` |

**Public-data book sealed.** The last reset-doc primitive is measured dead; bank stands. Path 2
remains the only revival path with a non-trivial prior.
