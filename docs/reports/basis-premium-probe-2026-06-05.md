# Basis / Premium Probe — 2026-06-05

**Brief:** [`basis-premium-data-ingestion-brief-v0.md`](../specs/basis-premium-data-ingestion-brief-v0.md)
**Script:** `scripts/probe_basis_premium.py`
**Data:** production TimescaleDB, `perp_basis_metrics` + `ohlcv` (read-only)
**Window:** 2024-01-01 → 2026-06-01, 1h, `binance_usdm`

---

## Probe gates (v0)

| Gate | Threshold |
|------|-----------|
| Events | ≥ 30 per symbol **or** ≥ 100 pooled |
| Forward edge | \|mean\| > 0.15% on 12h and/or 24h, consistent sign if both hit |
| MAE edge | ≥ 10% lower mean long MAE vs all-hours baseline |
| Concentration | max single-event share of positive forwards ≤ 50% |

Tails tested: **5%** and **10%** on `basis_bps` and `premium_index`.
Hypotheses: extreme positive, extreme negative, premium normalization.

---

## Verdict: **HAS_PULSE**

Exit code 0. Multiple scenarios pass on pooled and per-symbol bases.

**Do not** deploy live, autoresearch, or paper from this probe alone.

---

## Headline read (per symbol)

| Symbol | Bars | Best forward signal | MAE pattern | Interpretation |
|--------|------|---------------------|-------------|----------------|
| BTCUSDT | 20,707 | extreme **positive** tail5/10: +0.17–0.42% 12h/24h | MAE **worse** in positive tails | Crowded-long windows still drift up; higher adverse excursion → **risk filter**, not clean long entry |
| ETHUSDT | 20,707 | weak forward everywhere | extreme **negative** premium_index tail5: MAE −29% | Discount/panic windows show lower MAE; forward edge thin → **filter research only** |
| SOLUSDT | 20,707 | extreme **positive** tail5: +0.30% / +0.68% | positive tail MAE **worse** (−30%) | Same as BTC — crowded perp long coincides with continuation but **painful** holds |

Normalization events are **sparse** (500–1,400 per symbol) except BTC (643–1,379). Only **SOL normalization basis_bps tail5** clears gates (MAE −12%, thin forward).

---

## Passing scenarios (script output)

**Per-symbol**

- BTC: extreme ± via `basis_bps` / `premium_index` (mostly **positive** tails on forward)
- ETH: extreme **negative** `premium_index` tail5 only
- SOL: extreme **positive** tails + normalization `basis_bps` tail5

**Pooled**

- `extreme_positive` `basis_bps` tail5 / tail10
- `extreme_positive` `premium_index` tail5 / tail10
- `extreme_negative` `premium_index` tail5

Full list in Hetzner run log (`research/` or CI artifact).

---

## Comparison to failed lanes

Unlike session router v1 (sparse collapse on SOL overlay WFO), this probe has:

- **Large event counts** (~1,000+ per tail per symbol)
- **Pooled passes** across BTC/ETH/SOL
- A distinguishable primitive (mark/index dislocation vs OHLCV shape)

Unlike funding-normalization (NO_PULSE at default thresholds), basis tails fire frequently on all three symbols.

---

## Decision

| Action | Status |
|--------|--------|
| Close data lane | **No** — infra validated |
| Write `basis-premium-risk-filter-surface-v0.md` | **Yes** — done |
| Implementation / autoresearch | **No** until surface brief + filter-first design |
| Live / paper config | **No** |

**Recommended surface v0 shape:** default-off **long risk filter** when `basis_bps` or `premium_index` is in extreme **positive** tail (crowded perp long). Test on SOL overlay WFO A/B before any paper service — mirror session-router discipline.

**Not recommended:** standalone primary entries from positive-premium continuation (forward up but MAE worse).

---

## Commands

```bash
# Coverage (must be PROBE_READY first)
uv run python scripts/audit_basis_premium_coverage.py \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT --timeframe 1h

# Probe
uv run python scripts/probe_basis_premium.py \
  --symbols BTCUSDT,ETHUSDT,SOLUSDT --timeframe 1h
```
