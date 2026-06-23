# Path 2 Gate 0 — Illiquid-Venue Microstructure (Differentiated Advantage)

**Status:** Path 2 program **reopened** (2026-06-23). Gate 0 attestation complete; Gate 1 market
selection and data access **pending operator infra**.
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
| Credible possession of non-public edge | ⏳ **Pending** — operator must supply venue access, data feed, or execution path not available in the public major-venue probes |
| Market selection follows edge | ⏳ **Pending** — candidate venues/pairs to be named only after access is confirmed |

Gate 0 **does not** claim `HAS_PULSE` on any market data. It records the deliberate reopening of
research under Path 2 and blocks Gate 1 until infra exists.

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
