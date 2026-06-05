# Basis / Perp Premium — Data Ingestion Brief v0

**Status:** approved for engineering — **no strategy probe until coverage audit passes**
**Date:** 2026-06-05
**Prerequisite:** Gate discipline validated; OHLCV/session/funding-primary lanes closed on SOL overlay
**Surface (filter-first):** [`basis-premium-risk-filter-surface-v0.md`](./basis-premium-risk-filter-surface-v0.md)

---

## Objective

Add **reliable historical perp premium / basis** to TimescaleDB before any autoresearch
or router work. This lane uses a **different primitive** (derivatives crowding /
mark–index dislocation) than candle patterns, session hours, or funding-rate-only
normalization.

**Out of scope for this brief:** live agents, compose services, autoresearch families,
retuning SOL overlay stacks.

---

## Why now (and why not another filter)

Recent lanes share one failure mode: a **crude statistical pulse** on data we already
have does not survive WFO on the promoted SOL stack.

| Lane | Primitive | Result |
|------|-----------|--------|
| Session router v1 | UTC entry gate on OHLCV | CLOSED — sparse collapse |
| Vol squeeze bounded | OHLCV volatility shape | CLOSED |
| Funding normalization | `funding_rates` only | CLOSED |
| RS rotation | Relative strength | Paused / probe failed |

Basis/premium is **not** another indicator on candles. It may later explain **when not
to take longs** in crowded perp regimes and can support short-side research without
pretending the data already exists.

---

## Scope v0 (single exchange)

| In scope | Out of scope (defer) |
|----------|----------------------|
| Binance USDT-M futures (`exchange = binance_usdm`) | Cross-venue basis (Bybit, OKX, etc.) |
| BTCUSDT, ETHUSDT, SOLUSDT | Alt baskets until coverage proven |
| Intervals: `1h` (probe), `4h` (optional same pipeline) | Sub-minute live-only streams |
| Historical REST backfill + idempotent upsert | Real-time websocket ingest (Phase 3) |
| Coverage audit before probe | Strategy/router implementation |

**Naming:** “cross-exchange basis” is the **research north star**; **v0 implements
Binance mark/index/premium index history** as the first leg. Do not block v0 on
multi-exchange plumbing.

---

## Data source (Binance USDT-M)

Official REST kline endpoints (same cadence as OHLCV backfill):

| Field | Endpoint | Notes |
|-------|----------|-------|
| Mark price OHLC | `GET /fapi/v1/markPriceKlines` | `p` stream equivalent; use close for bar |
| Index price OHLC | `GET /fapi/v1/indexPriceKlines` | Spot index; param `pair=BTCUSDT` |
| Premium index OHLC | `GET /fapi/v1/premiumIndexKlines` | Binance “premium index” = perp premium |
| Funding rate | existing `funding_rates` | 8h events; join at read time, do not duplicate |

**Bulk history (preferred for long backfill):** `data.binance.vision` futures metrics
files if REST pagination is too slow — document path in implementation PR.

**Current mark snapshot (live ops only, not backfill):** `GET /fapi/v1/premiumIndex`
returns `markPrice`, `indexPrice`, `lastFundingRate`, `interestRate`.

---

## Schema

Migration: `migrations/010_add_perp_basis_metrics.sql`

Table: `perp_basis_metrics` (Timescale hypertable on `time`)

| Column | Type | Description |
|--------|------|-------------|
| `time` | `TIMESTAMPTZ` | Bar open (UTC), aligns with `ohlcv.time` |
| `close_time` | `TIMESTAMPTZ` | Bar close |
| `exchange` | `TEXT` | `binance_usdm` (v0 constant) |
| `symbol` | `TEXT` | e.g. `BTCUSDT` |
| `timeframe` | `TEXT` | `1h`, `4h`, … |
| `mark_price` | `DOUBLE PRECISION` | Mark kline close |
| `index_price` | `DOUBLE PRECISION` | Index kline close |
| `premium_index` | `DOUBLE PRECISION` | Premium index kline close (exchange-native) |
| `basis_bps` | `DOUBLE PRECISION` | \((mark - index) / index \times 10{,}000\) at bar close |

**Primary key:** `(time, exchange, symbol, timeframe)`

**Derived fields at ingest:** `basis_bps` computed from mark/index closes; store
`premium_index` separately (they may differ slightly from naive basis).

**Funding:** keep in `funding_rates`. Probes join `funding_time <= time` LATERAL,
same pattern as `IndicatorReader`.

---

## Ingestion requirements

1. **Idempotent:** `ON CONFLICT DO UPDATE` (re-backfill overwrites bad/partial rows).
2. **Aligned bars:** For each `(symbol, timeframe, time)` row, mark/index/premium
   must come from the **same** open time; reject partial bars.
3. **Rate limits:** reuse aiohttp session + backoff (mirror `import_funding_rates.py`).
4. **Backfill window:** default `2024-01-01` → `min(ohlcv.max_time, now)` per symbol.
5. **Script:** `scripts/import_perp_basis_metrics.py` (implementation PR — not required
   to merge this brief-only commit).

---

## Coverage audit (mandatory gate)

Script: `scripts/audit_basis_premium_coverage.py`

Run on prod DB **after migration** and **after backfill attempt**:

```bash
uv run python scripts/audit_basis_premium_coverage.py \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT \
  --timeframe 1h \
  --exchange binance_usdm
```

**Probe-ready thresholds (v0):**

| Check | Threshold |
|-------|-----------|
| Table exists | migration `010` applied |
| Rows per symbol | ≥ 1 |
| Overlap vs `ohlcv` 1h | ≥ **95%** of OHLCV bar count in shared range |
| Range start lag | `perp_basis_metrics.min(time)` ≤ `ohlcv.min(time)` + **7 days** |
| Max gap | no gap **> 2 × bar duration** inside overlap (1h → 2h) |
| End lag | `basis.max(time) >= ohlcv.max(time) - 2 × bar duration` |
| `blocked` sanity | N/A at data layer |

Exit code **0** = `PROBE_READY`; **1** = not ready (print remediation).

Also print `funding_rates` overlap summary (existing table) for context.

---

## Phase plan

### Phase 1 — Schema + audit (this brief)

- [x] Brief v0
- [x] Migration `010_add_perp_basis_metrics.sql`
- [x] `audit_basis_premium_coverage.py`

### Phase 2 — Backfill

- [x] `import_perp_basis_metrics.py` for BTC/ETH/SOL `1h`
- [x] Run on Hetzner prod DB; archive row counts in report snippet
- [x] Audit → `PROBE_READY`

### Phase 3 — Cheap statistical probe (no autoresearch)

Script: `scripts/probe_basis_premium.py` — report
[`basis-premium-probe-2026-06-05.md`](../reports/basis-premium-probe-2026-06-05.md) (**HAS_PULSE**)

Questions:

1. Does **extreme premium** (tail of `premium_index` or `basis_bps`) predict 12h/24h
   forward return vs baseline?
2. Does premium **normalization** (exit from extreme) reduce adverse excursion?
3. Effect class: long-only edge, short-only edge, or **risk filter only**?

Pass criteria (probe-only, not WFO gates):

- ≥ 30 events per symbol at default tails, or pooled ≥ 100
- At least one symbol shows \|mean forward\| > 0.15% with consistent sign across 12h
  and 24h **or** ≥ 10% MAE improvement vs baseline at same sample count
- Not 100% profit concentration on one event window

Fail → **CLOSED** in ledger; do not write strategy brief.

### Phase 4 — Strategy surface brief (only if Phase 3 passes)

- Entry as **risk filter** on existing long stack first (default-off router)
- Standalone primary only if filter probe shows independent edge

---

## Parallel track (unchanged)

- Phase 0 weekly on `agent_sol_1h_trend_pullback_overlay_live` + `agent_sentiment_macro`
- **Do not** add live agents until forward fills accumulate
- **Do not** run autoresearch on basis until Phase 3 `HAS_PULSE`

---

## Success / stop

| Outcome | Action |
|---------|--------|
| Audit fails after backfill | Fix ingest; do not probe |
| Probe `NO_PULSE` | Close lane; keep table for future research |
| Probe `HAS_PULSE` | Write `basis-premium-risk-filter-surface-v0.md`; filter-first |
| Probe `HAS_PULSE` but WFO fails later | Close strategy lane; retain data infra |

---

## References

- Existing funding ingest: `scripts/import_funding_rates.py`, `migrations/008_*`
- Funding probe pattern: `scripts/probe_funding_normalization.py`
- Ledger: `docs/reports/autoresearch-candidate-ledger.md`
- Next path: `docs/reports/autoresearch-next-candidate-path-2026-06-04.md`
