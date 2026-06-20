# Polymarket Calibration / Favorite-Longshot Probe — Report

**Verdict:** **BLOCKED_ON_DATA**
**Script:** `scripts/probe_polymarket_calibration.py`
**Framing:** probability mispricing on resolved binary markets (read-only).

## Cost assumption
- round_trip_cost = half-spread (~1.0% conservative) + Polymarket taker fee (0% on most markets per CLOB metadata) + Polygon gas/settlement allowance (~0.5%) + slippage buffer; default 2.5% total, override via --round-trip-cost-pct
- Applied round-trip cost: **2.5%**

## STEP 0 — Data feasibility
- Total pulled / cached: 2100
- With price by lead time: {'24h': 350, '72h': 221}
- Usable for edge (min across τ): 221
- Exclusions: {'zero_volume': 242, 'low_liquidity': 1143, 'disputed_resolution': 1, 'invalid_refunded': 18, 'no_price_at_72h': 466, 'no_price_at_24h': 337, 'no_price_history': 9}
- Disputed/oracle-flagged (excluded): 1
- Category mix: {'other': 317, 'crypto': 211, 'sports': 166, 'politics': 2}
- Blocked: True — only 221 markets have price at all lead times (need >= 300; per-τ counts={'24h': 350, '72h': 221}); price_skips={'no_price_at_72h': 466, 'no_price_at_24h': 337, 'no_price_history': 9}

## STEP 1 — Calibration at τ = 24h
- Observations: 350
- H1: FAIL | H2 time: FAIL | H2 category: FAIL
- Qualifying buckets: []

| Bucket | n | mean(p) | freq | edge | net_edge | p_raw | p_adj | sig | trade |
|--------|---|---------|------|------|----------|-------|-------|-----|-------|
| [0.0,0.1) | 127 | 0.030 | 0.000 | +0.030 | +0.005 | 0.0426 | 0.4264 | n | n |
| [0.1,0.2) | 27 | 0.145 | 0.222 | -0.077 | +0.052 | 0.3767 | 1.0000 | n | n |
| [0.2,0.3) | 26 | 0.258 | 0.192 | +0.065 | +0.040 | 0.6099 | 1.0000 | n | n |
| [0.3,0.4) | 25 | 0.353 | 0.320 | +0.033 | +0.008 | 0.9098 | 1.0000 | n | n |
| [0.4,0.5) | 46 | 0.464 | 0.261 | +0.203 | +0.178 | 0.0077 | 0.0768 | n | n |
| [0.5,0.6) | 62 | 0.517 | 0.516 | +0.001 | -0.024 | 1.0000 | 1.0000 | n | n |
| [0.6,0.7) | 14 | 0.647 | 0.571 | +0.076 | +0.051 | 0.7341 | 1.0000 | n | n |
| [0.7,0.8) | 9 | 0.752 | 0.667 | +0.086 | +0.061 | 0.7862 | 1.0000 | n | n |
| [0.8,0.9) | 5 | 0.824 | 1.000 | -0.176 | +0.151 | 0.7620 | 1.0000 | n | n |
| [0.9,1.0) | 9 | 0.967 | 1.000 | -0.033 | +0.008 | 1.0000 | 1.0000 | n | n |

## STEP 1 — Calibration at τ = 72h
- Observations: 221
- H1: FAIL | H2 time: FAIL | H2 category: FAIL
- Qualifying buckets: []

| Bucket | n | mean(p) | freq | edge | net_edge | p_raw | p_adj | sig | trade |
|--------|---|---------|------|------|----------|-------|-------|-----|-------|
| [0.0,0.1) | 91 | 0.032 | 0.000 | +0.032 | +0.007 | 0.1021 | 1.0000 | n | n |
| [0.1,0.2) | 27 | 0.145 | 0.148 | -0.003 | -0.022 | 1.0000 | 1.0000 | n | n |
| [0.2,0.3) | 24 | 0.253 | 0.167 | +0.086 | +0.061 | 0.4751 | 1.0000 | n | n |
| [0.3,0.4) | 15 | 0.359 | 0.267 | +0.092 | +0.067 | 0.6495 | 1.0000 | n | n |
| [0.4,0.5) | 19 | 0.457 | 0.263 | +0.194 | +0.169 | 0.1388 | 1.0000 | n | n |
| [0.5,0.6) | 23 | 0.522 | 0.435 | +0.088 | +0.063 | 0.5268 | 1.0000 | n | n |
| [0.6,0.7) | 10 | 0.652 | 0.500 | +0.152 | +0.127 | 0.4883 | 1.0000 | n | n |
| [0.7,0.8) | 4 | 0.763 | 0.500 | +0.263 | +0.238 | 0.4800 | 1.0000 | n | n |
| [0.8,0.9) | 4 | 0.814 | 1.000 | -0.186 | +0.161 | 0.8797 | 1.0000 | n | n |
| [0.9,1.0) | 4 | 0.947 | 1.000 | -0.053 | +0.028 | 1.0000 | 1.0000 | n | n |

## Reasons
- only 221 markets have price at all lead times (need >= 300; per-τ counts={'24h': 350, '72h': 221}); price_skips={'no_price_at_72h': 466, 'no_price_at_24h': 337, 'no_price_history': 9}
