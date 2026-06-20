# Polymarket Calibration / Favorite-Longshot Probe — Report

**Verdict:** **WEAK_EDGE**
**Script:** `scripts/probe_polymarket_calibration.py`
**Framing:** probability mispricing on resolved binary markets (read-only).

## Cost assumption
- round_trip_cost = half-spread (~1.0% conservative) + Polymarket taker fee (0% on most markets per CLOB metadata) + Polygon gas/settlement allowance (~0.5%) + slippage buffer; default 2.5% total, override via --round-trip-cost-pct
- Applied round-trip cost: **2.5%**

## STEP 0 — Data feasibility
- Total pulled / cached: 20999
- With price by lead time: {'24h': 7238, '72h': 5210}
- Usable for edge (max across τ): 7238
- Exclusions: {'zero_volume': 6012, 'low_liquidity': 3871, 'disputed_resolution': 26, 'invalid_refunded': 184, 'invalid_outcome': 1, 'no_price_at_72h': 4842, 'no_price_at_24h': 2814, 'no_price_history': 853}
- Disputed/oracle-flagged (excluded): 26
- Category mix: {'other': 4658, 'crypto': 3263, 'sports': 2046, 'politics': 938}
- Pull completeness: pages=210, mode=offset+date_window, termination=complete, incomplete=False
- Pull date coverage: earliest=2023-11-03T00:19:05+00:00, latest=2026-06-20T18:02:22+00:00
- Blocked: False

## STEP 1 — Calibration at τ = 24h
- Observations: 7238
- H1: FAIL | H2 time: FAIL | H2 category: FAIL
- Qualifying buckets: []

| Bucket | n | mean(p) | freq | edge | net_edge | p_raw | p_adj | sig | trade |
|--------|---|---------|------|------|----------|-------|-------|-----|-------|
| [0.0,0.1) | 2668 | 0.020 | 0.009 | +0.011 | -0.014 | 0.0001 | 0.0007 | Y | n |
| [0.1,0.2) | 574 | 0.146 | 0.143 | +0.003 | -0.022 | 0.8244 | 1.0000 | n | n |
| [0.2,0.3) | 593 | 0.250 | 0.219 | +0.031 | +0.006 | 0.0814 | 0.8144 | n | n |
| [0.3,0.4) | 462 | 0.348 | 0.329 | +0.019 | -0.006 | 0.3999 | 1.0000 | n | n |
| [0.4,0.5) | 550 | 0.453 | 0.435 | +0.018 | -0.007 | 0.3851 | 1.0000 | n | n |
| [0.5,0.6) | 1300 | 0.515 | 0.521 | -0.006 | -0.019 | 0.6623 | 1.0000 | n | n |
| [0.6,0.7) | 273 | 0.646 | 0.670 | -0.024 | -0.001 | 0.4087 | 1.0000 | n | n |
| [0.7,0.8) | 222 | 0.748 | 0.784 | -0.036 | +0.011 | 0.2153 | 1.0000 | n | n |
| [0.8,0.9) | 164 | 0.846 | 0.860 | -0.014 | -0.011 | 0.6253 | 1.0000 | n | n |
| [0.9,1.0) | 432 | 0.975 | 0.995 | -0.021 | -0.004 | 0.0064 | 0.0644 | n | n |

## STEP 1 — Calibration at τ = 72h
- Observations: 5210
- H1: PASS | H2 time: PASS | H2 category: FAIL
- Qualifying buckets: [9]

| Bucket | n | mean(p) | freq | edge | net_edge | p_raw | p_adj | sig | trade |
|--------|---|---------|------|------|----------|-------|-------|-----|-------|
| [0.0,0.1) | 2108 | 0.022 | 0.015 | +0.007 | -0.018 | 0.0344 | 0.3438 | n | n |
| [0.1,0.2) | 497 | 0.150 | 0.123 | +0.027 | +0.002 | 0.0913 | 0.9131 | n | n |
| [0.2,0.3) | 508 | 0.250 | 0.248 | +0.002 | -0.023 | 0.9306 | 1.0000 | n | n |
| [0.3,0.4) | 275 | 0.344 | 0.335 | +0.009 | -0.016 | 0.7513 | 1.0000 | n | n |
| [0.4,0.5) | 389 | 0.456 | 0.404 | +0.052 | +0.027 | 0.0380 | 0.3803 | n | n |
| [0.5,0.6) | 623 | 0.521 | 0.472 | +0.049 | +0.024 | 0.0152 | 0.1516 | n | n |
| [0.6,0.7) | 199 | 0.643 | 0.648 | -0.005 | -0.020 | 0.8860 | 1.0000 | n | n |
| [0.7,0.8) | 158 | 0.749 | 0.778 | -0.029 | +0.004 | 0.3968 | 1.0000 | n | n |
| [0.8,0.9) | 128 | 0.848 | 0.875 | -0.027 | +0.002 | 0.3965 | 1.0000 | n | n |
| [0.9,1.0) | 325 | 0.969 | 0.997 | -0.028 | +0.003 | 0.0037 | 0.0367 | Y | Y |

## Reasons
- τ=24h: n=7238, H1=n, H2_time=n, H2_cat=n, qualifying_buckets=[]
- τ=72h: n=5210, H1=Y, H2_time=Y, H2_cat=n, qualifying_buckets=[9]
- H1 passed but H2 and/or category breadth failed
