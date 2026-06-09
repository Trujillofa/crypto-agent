# Cross-Venue Basis / Dislocation — Lane Brief v0

**Status:** Brief created; cheap probe + data ingestion required before any autoresearch or surface.
**Date:** 2026-06-09
**Related:** [research-reset-2026-06-06.md](../reports/research-reset-2026-06-06.md), [RBI_AUTORESEARCH_LOOP.md](../RBI_AUTORESEARCH_LOOP.md), prior single-venue basis work (closed).

---

## Edge Thesis

Perp basis and premium reflect **venue-specific supply/demand and crowding**. When the same symbol shows materially different basis (mark vs index, or funding-implied) across exchanges (Binance USDT-M, Bybit, OKX, etc.), the dislocation can be a leading or coincident signal of:
- Capital flowing to the "cheaper" venue (convergence trades).
- One venue becoming crowded while another lags (arb or risk-off rotation).
- Liquidity or margin regime differences that are not visible in any single exchange's OHLCV or its own premium index.

The core hypothesis (HYP-CV-001): Large, persistent cross-venue basis spreads (or sudden dislocations) predict either mean-reverting convergence or accelerated one-sided moves on the lagging venue, with measurable edge after realistic taker fees + slippage, and with better MAE characteristics than baseline entries in the same regime.

This is a **data-first primitive** (multi-venue derivatives microstructure) rather than another OHLCV shape, session label, or single-venue premium filter.

## Why This Differs from Closed Single-Venue Basis Work

The 2026-06 basis-premium v0 work (probe + risk-filter surface) used only `binance_usdm` `perp_basis_metrics` (mark/index/premium on one exchange). That lane produced HAS_PULSE on extreme tails but the resulting long-risk filter on the SOL 1h overlay was rejected at Phase 2 WFO (only 1 block, no risk improvement, OOS not better).

Cross-venue is different because:
- It compares **relative** dislocation between venues, not absolute level on one venue.
- It can surface venue-arbitrage or rotation effects that a single-exchange premium (continuation-biased) misses.
- It has a natural short-side or hedge interpretation (long the cheap venue, short the rich one, or use as a filter/weight).
- Event density and independence from the existing live SOL overlay are expected to be higher.

Single-venue premium long filters and 1h structure probes are now in the banned/closed set per the research reset. Cross-venue requires new ingestion and is explicitly called out as the #1 allowed next family.

## Data Requirements (v0)

- Per-venue perp basis / mark / index / premium / funding time series at 1h (and optionally 4h).
- Aligned timestamps across venues for the same symbol.
- At minimum: Binance USDT-M (already backfilled via `perp_basis_metrics`) + one additional major (Bybit or OKX) for initial dislocation signal.
- Fields needed: timestamp, symbol, exchange/venue, basis_bps (or mark-index), premium (if available), last_funding_rate or predicted funding, open interest if available.
- Coverage audit first (≥95% overlap with OHLCV bars after reasonable lag tolerance, similar to `audit_basis_premium_coverage.py`).
- New ingestion path (REST bulk or websocket backfill) — **not** in scope for the first cheap probe if we can source a minimal public dataset or extend existing scripts.

Out of scope for v0: sub-second, options, spot basis, every altcoin.

## Expected Regimes

- High funding + basis divergence (one venue very rich, another normalizing).
- Post-liquidation cascades or large OI shifts on one venue.
- Basis convergence after a multi-venue event (e.g., ETF flows, macro news, exchange-specific incidents).
- Low-vol range where small dislocations persist long enough for convergence.

Avoid: Strong one-directional trends where all venues move in lockstep (dislocation collapses to noise).

## Failure Modes (Kill Criteria)

- Dislocations are almost always in the same direction (persistent Binance premium, for example) → no tradable edge, just a constant bias.
- After realistic fees + slippage the forward edge disappears or is concentrated in <5% of events.
- Events too sparse for 20+ WFO trades on any major symbol even after pooling.
- High overlap with the live `agent_sol_1h_trend_pullback_overlay_live` or sentiment-macro (adds correlated long bias instead of diversification).
- MAE control fails (big adverse moves when the "cheap" venue continues to get cheaper).
- Data quality / timestamp misalignment makes the signal non-reproducible in production.

## Independence Expectations

Target: low entry overlap (<35% Jaccard, <40% pct shared) with the promoted SOL 1h technical stack and with sentiment-macro on the same symbols/timeframe.

Prefer surfaces that can be long one venue / flat or short another, or that act as a **portfolio-level risk allocator** (de-risk all agents when aggregate cross-venue stress is high) rather than another per-symbol entry model.

## Validation Gates (RBI-aligned)

**Gate 0 (this brief):** Done.

**Gate 1 (Cheap Probe):** Read-only script. Must show `HAS_PULSE` on at least one major symbol or pooled:
- ≥100 dislocation events (or ≥30 per symbol) in a multi-year window.
- Statistically plausible forward edge after conservative fees/slippage on at least one horizon (e.g. 6h–24h convergence or continuation on the cheap venue).
- Reasonable concentration (no single week dominates).
- MAE not materially worse than baseline entries in the same regime.

**Gate 2 (Bounded Autoresearch / Surface):** Only after HAS_PULSE. Config-only or minimal new filter/router code. Standard gates + filter-specific (trade retention ≥70% if used as overlay filter, clear risk improvement on DD/P(loss) or Sharpe).

**Gate 3 (Promotion Candidate):** Stricter pre-filter before bootstrap=1000.

**Gate 4 (bootstrap=1000):** Required for any paper/live consideration.

**Gate 5 (Overlap + Portfolio Impact):** `analyze_entry_overlap.py` against live agents. Must not increase portfolio tail risk.

**Gate 6 (Paper / Forward):** Minimum 20 closed trades in paper shadow (distinct AGENT_ID) before any live notional. Track blocked counts, venue P&L attribution, slippage, etc.

Human approval required before any compose change, risk limit change, or live deployment.

## Current Best Next Commands (until probe exists)

```bash
# 1. Define / extend a multi-venue coverage + dislocation probe
#    (see step 6 investigation — likely new script or extension of probe_basis_premium + audit)

# 2. Once probe + data land, run via the RBI manifest (see rbi_loop.cross-venue-basis-v1.yaml)
uv run python scripts/rbi_loop_from_manifest.py \
  --manifest config/autoresearch/rbi_loop.cross-venue-basis-v1.yaml
```

Do not start autoresearch or write strategy code until the cheap probe returns HAS_PULSE and the guard advances the lane.

## Stop / Close Rules (in addition to general RBI stop rules)

- No viable cross-venue data source or coverage audit fails after reasonable effort.
- Probe returns NO_PULSE or WEAK_EDGE on all major symbols after two different dislocation definitions.
- Any implementation would require >5 free parameters or heavy per-venue tuning to pass gates.
- Clear evidence that cross-venue signals are just a noisy proxy for the existing sentiment-macro or single-venue basis already explored.

The goal is still the same: a second independent, gate-passing, profitability-positive agent (or a high-quality risk filter / allocator that improves the existing book). Cross-venue basis is the current best first-principles surface that satisfies the "new primitive" rule from the June 2026 reset.
