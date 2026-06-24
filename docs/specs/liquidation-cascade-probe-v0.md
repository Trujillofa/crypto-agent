# Lane Brief — Forced Liquidation / Cascade Flow Probe v0 (Gate 1, A1)

**Status:** Gate 1 cheap probe (A1 epistemic closure).
**Context:** [deep-edge-research-reconciliation-2026-06-24.md](../reports/deep-edge-research-reconciliation-2026-06-24.md).
**Script:** `scripts/probe_liquidation_cascade.py`.

---

## Why this lane

The bank closed OHLCV structure, OFI microstructure (#110), and cross-venue dislocation. The reset doc
listed **order book / liquidation data** as the remaining public primitive; liquidations were never probed.
Thesis: **mechanical forced liquidation** creates short-horizon price pressure distinct from chart patterns.

## Data reality (Step 0)

| Source | Status |
|--------|--------|
| `GET /fapi/v1/allForceOrders` | **Deprecated** (`400` out of maintenance) |
| `data.binance.vision` force-order archives | **Not published** |
| WebSocket `<symbol>@forceOrder` / `!forceOrder@arr` | **Live only** (`wss://fstream.binance.com/market/ws/…`) |
| UM **metrics** (OI + taker ratio, 5m) | **Historical** via data.binance.vision |

When REST force-order history is unavailable, v0 uses **official UM metrics** to define frozen
**cascade-proxy events** (OI drop + extreme taker imbalance). WebSocket force orders are collected when
present and reported separately; the primary historical panel uses metrics (documented in report).

## Frozen universe

- Symbols: **BTCUSDT, ETHUSDT, SOLUSDT** (UM perps).
- Window: **14 days**, default ending T−2 UTC (aligns with OFI probe discipline).
- Horizons: **+5m, +30m, +120m** (1m klines, strictly after event time).
- Round-trip cost: **10 bps** taker (same as #110).

## Frozen cascade-proxy event (metrics)

Per 5m metrics row (ex-ante, no post-hoc tuning):

- `oi_change_pct` = Δ `sum_open_interest` vs previous 5m bucket, as % of prior OI.
- **Long-cascade event:** `oi_change_pct ≤ −0.15%` AND `sum_taker_long_short_vol_ratio ≤ 0.55`.
- **Short-cascade event:** `oi_change_pct ≤ −0.15%` AND `sum_taker_long_short_vol_ratio ≥ 1.80`.
- Deduplicate: minimum **30 minutes** between events per symbol (keep larger |oi_change|).

## Hypotheses (report separately)

- **H1a (fade):** after long-cascade (sell pressure), forward return **positive** (buy the flush).
- **H1b (continuation):** after long-cascade, forward return **negative** (more selling).
- Gate uses the **stronger** orientated edge per horizon if it clears cost + null.

## Gate 1 / #118 null

Every verdict requires **all** of:

1. **Null:** phase-randomized event timestamps (preserve hour-of-day marginal), block-bootstrap
   `p_adj < 0.05` on oriented excess vs matched quiet windows.
2. **Beats null:** real oriented excess > null median excess.
3. **Concentration:** no single UTC day > **25%** of total oriented edge.
4. **Cost:** net edge > **0** after 10 bps RT.
5. **Breadth:** same oriented relationship on **≥ 2 / 3** symbols at one horizon (Holm-corrected across
   3 horizons × 2 orientations).

`HAS_PULSE` = all gates pass. Else `NO_PULSE`. `BLOCKED_ON_DATA` only if metrics + klines cannot be loaded.

## Hard stops

- No liquid-major OHLCV lane reopening.
- No alt-symbol shopping after null.
- No autoresearch / `--execute` / live-risk change from this probe.
