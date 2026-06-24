# Path 2 Gate 0 — Illiquid-Venue Microstructure (Differentiated Advantage)

**Status:** **CLOSED at Gate 0 on economics** (2026-06-24) for a solo, public-data operator at
≤ $10k venue exposure — sub-gate 2 (spread vs edge) fails decisively; 1/3/4 reinforce. Decision:
[path2-gate0-economics-close-2026-06-24.md](../reports/path2-gate0-economics-close-2026-06-24.md).
Reopens only with a **differentiated venue advantage** (MM rebate / latency / privileged access =
C-tier business), which requires a fresh Gate 0 brief. *History: program reopened 2026-06-23, Gate 0
attestation complete (`OPEN_PENDING_INFRA`); economics evaluated on paper 2026-06-24 → close.*
**Program context:** [research-consolidation-2026-06-23.md](../reports/research-consolidation-2026-06-23.md)
(Path 2 fork). Prior public-data program is **banked** (terminal).
**RBI manifest:** `config/autoresearch/rbi_loop.path2-illiquid-venue.yaml`

---

## Named differentiated advantage (explicit)

**Illiquid-venue microstructure where the book is thin.**

This is one concrete item from the capstone Path 2 list. The edge premise is **not** another
OHLCV-structure variant on liquid Binance majors (falsified in #110). It is **access to order-book
and trade telemetry on venues or pairs where depth is thin enough that signed flow and queue position
may not be fully arbed by market makers at sub-second horizons** — i.e. a venue/pair selection
driven by structural illiquidity, not by strategy tuning on BTC/ETH/SOL.

---

## Path 2 Gate 0 premise (capstone language)

Per [research-consolidation-2026-06-23.md](../reports/research-consolidation-2026-06-23.md) §The
forward fork, Path 2 is entered only with a **specific, named, non-public edge**, and market
selection follows the edge — never on momentum.

**Gate 0 question (this lane):** *"do I have a credible, defensible information/latency/access asymmetry?"* (capstone verbatim)

| Sub-question | Required for Gate 0 pass |
|---|---|
| Named advantage stated out loud | ✅ `illiquid venue microstructure where the book is thin` |
| Credible possession of non-public edge | ⏳ **Pending** — operator must supply **evidence**: a named venue + access (account / historical L2+trades / vendor path) **and** a feasibility table. A bare env-var boolean is a *declaration*, not evidence — the attestation state is `ACCESS_DECLARED_PENDING_EVIDENCE` until the feasibility numbers exist. |
| Economic viability (capacity & defensibility) | ⏳ **Pending** — see sub-gates below; this is a Gate-0 disqualifier, not a Gate-1 detail |
| Market selection follows edge | ⏳ **Pending** — candidate venues/pairs to be named only after access is confirmed |

Gate 0 **does not** claim `HAS_PULSE` on any market data. It records the deliberate reopening of
research under Path 2 and blocks Gate 1 until infra exists **and** the economic-viability sub-gates
below are answered with numbers.

### Gate 0 economic-viability sub-gates (capacity & defensibility)

"Credible" is not enough — the capstone question asks for a **defensible** asymmetry, and illiquid-venue
microstructure is the *least* defensible edge type. These must be answered **before** any Gate 1 data
spend, because each can disqualify the entire premise on its own:

1. **Capacity / size.** A thin book means even a *real* edge cannot absorb size without you moving the
   price against yourself. Required number: the **max position size at which the edge survives** (impact
   < edge). If that size is too small to clear the operator's per-trade operating cost, the lane is dead
   regardless of statistical pulse — an edge you can't deploy is not an edge.
2. **Spread vs edge.** Thin books carry wide spreads. A signed-flow edge of +5 bps is worthless against
   a 50 bps cost to cross. Required: the venue-appropriate round-trip cost, and the edge must clear it
   with margin (same discipline as #110, which failed net −7 to −10 bps on majors at ~10 bps cost).
3. **Venue / custody risk.** Illiquid venues are usually smaller exchanges: solvency, withdrawal, and
   rug risk mean you can lose the **bankroll to the venue**, not just the trade. Required: an explicit
   max-capital-at-venue cap and an honest statement of counterparty exposure.
4. **Self-limiting / competition.** The thinness that creates the edge is the same thinness that caps it
   and invites market makers back once you trade size. Required: why the asymmetry *persists* after you
   start trading it — i.e. what makes it **defensible**, not just currently-present.

If sub-gates 1 or 3 fail (no deployable size, or unacceptable venue risk), **close the lane at Gate 0** —
do not spend on Gate 1 data. These are economic questions, answerable on paper before any probe.

---

## Why this advantage (first principles)

The capstone taxonomy shows three structural classes none of which a **public-data retail operator on
liquid majors** can monetize. The microstructure probe (#110) falsified signed OFI on Binance majors
(p≈0.5, net edge negative). The remaining honest hypothesis for microstructure is **not** "try
harder on BTC" but **change the venue surface** to where:

1. **Book depth is thin** — queue position and trade impact persist longer than on majors.
2. **Data is not universally consumed** — fewer participants run tick-level models on that venue.
3. **Execution asymmetry is plausible** — co-location or privileged API tiers are not assumed in
   Gate 0; they are listed as separate Path 2 items if this lane advances.

This lane is **distinct** from latency/co-location, proprietary alt-data, and on-chain/MEV stacks
(other capstone Path 2 items). Those require separate Gate 0 briefs if pursued in parallel.

---

## What this lane is NOT (hard stops)

- **Not** a re-probe of Binance major OFI, aggTrade REST, or candle-structure overlays.
- **Not** autoresearch or WFO until Gate 1 names a venue/pair and a cheap probe shows `HAS_PULSE`
  on **that** illiquid surface.
- **Not** a lowering of RBI gates, `--execute` bypass, or live-risk change.
- **Not** a claim that illiquid microstructure works without evidence — Gate 0 only opens the lane.

---

## Gate 1 plan (after Gate 0 infra attestation)

When the operator supplies credible access (exchange account + historical L2/trades, or documented
data vendor path):

1. **Name the venue and 1–3 candidate pairs** with documented average spread, depth at BBO, and
   daily volume (feasibility table in probe report).
2. **Cheap probe** (new script, path2-prefixed): repeat OFI-decile forward-return test on the
   illiquid surface only; same four gates as #110 but with venue-appropriate cost model.
3. **Verdict:** `HAS_PULSE` / `WEAK_EDGE` / `NO_PULSE` / `BLOCKED_ON_DATA` — only then update
   manifest `probe_verdict` for the standard RBI guard.

Until Gate 1, manifest `probe_verdict` remains unset; `path2_gate0_attestation` JSON is the
authoritative Path 2 state file.

---

## Validation commands

```bash
# Gate 0 attestation (Path 2 RUN_CHEAP_PROBE equivalent)
uv run python scripts/probe_path2_gate0_attestation.py \
  --lane-name path2-illiquid-venue \
  --brief docs/specs/path2-illiquid-venue-gate0.md \
  --named-advantage "illiquid venue microstructure where the book is thin" \
  --output research/rbi_loop/path2-illiquid-venue/gate0-attestation.json

# RBI supervisor (dry then --execute per manifest)
uv run python scripts/rbi_loop_from_manifest.py \
  --manifest config/autoresearch/rbi_loop.path2-illiquid-venue.yaml
```

---

## Expected failure modes

- **No venue access** → lane stays OPEN at Gate 0; no `HAS_PULSE` claim; no build spend.
- **Venue access but OFI null** → `NO_PULSE` → `CLOSE_LANE` per standard guard; record in ledger.
- **Post-hoc venue shopping** after a null → banned; one venue batch per Gate 1 tranche.

This brief completes Path 2 Gate 0 for the illiquid-venue microstructure advantage.
