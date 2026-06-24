# Forced Liquidation / Cascade Flow Probe — Report

**Verdict:** **WEAK_EDGE**
**Date:** 2026-06-24
**Script:** `scripts/probe_liquidation_cascade.py`
**Spec:** [liquidation-cascade-probe-v0.md](../specs/liquidation-cascade-probe-v0.md)

**Window:** 2026-06-08 → 2026-06-22 (UTC)

## STEP 0 — Data audit

- REST `allForceOrders`: HTTP 400: {"code":400,"msg":"The endpoint has been out of maintenance"}
- WebSocket path: `wss://fstream.binance.com/market/ws/<symbol>@forceOrder`
- UM metrics days loaded: 14
- Force orders collected (this run): 0
- Force orders cached (loaded): 0
- Events per symbol: {'BTCUSDT': 50, 'ETHUSDT': 50, 'SOLUSDT': 86}

## Frozen cascade-proxy definition

- OI drop ≤ -0.15% (5m)
- Long-cascade: taker ratio ≤ 0.55
- Short-cascade: taker ratio ≥ 1.8
- Dedup gap: 30m

## Results by symbol

### BTCUSDT (50 events)
| Horizon | Orient | Excess bps | Net bps | p_adj | beats null | conc | pass |
|---------|--------|----------:|--------:|------:|:----------:|:----:|:----:|
| +5m | fade | +4.04 | -5.96 | 1.000 | Y | 20% | n |
| +5m | continuation | -0.47 | -10.47 | 1.000 | n | 20% | n |
| +30m | fade | +3.82 | -6.18 | 1.000 | Y | 18% | n |
| +30m | continuation | +1.20 | -8.80 | 1.000 | n | 18% | n |
| +120m | fade | +10.44 | +0.44 | 1.000 | Y | 16% | n |
| +120m | continuation | -0.32 | -10.32 | 1.000 | n | 16% | n |

### ETHUSDT (50 events)
| Horizon | Orient | Excess bps | Net bps | p_adj | beats null | conc | pass |
|---------|--------|----------:|--------:|------:|:----------:|:----:|:----:|
| +5m | fade | -2.66 | -12.66 | 1.000 | n | 20% | n |
| +5m | continuation | +0.02 | -9.98 | 1.000 | Y | 20% | n |
| +30m | fade | -1.18 | -11.18 | 1.000 | Y | 17% | n |
| +30m | continuation | -5.02 | -15.02 | 1.000 | n | 17% | n |
| +120m | fade | +10.69 | +0.69 | 1.000 | Y | 19% | n |
| +120m | continuation | +7.85 | -2.15 | 1.000 | n | 19% | n |

### SOLUSDT (86 events)
| Horizon | Orient | Excess bps | Net bps | p_adj | beats null | conc | pass |
|---------|--------|----------:|--------:|------:|:----------:|:----:|:----:|
| +5m | fade | +0.44 | -9.56 | 1.000 | Y | 18% | n |
| +5m | continuation | -5.89 | -15.89 | 1.000 | n | 18% | n |
| +30m | fade | -7.75 | -17.75 | 1.000 | Y | 15% | n |
| +30m | continuation | -17.15 | -27.15 | 1.000 | n | 15% | n |
| +120m | fade | -16.67 | -26.67 | 1.000 | Y | 16% | n |
| +120m | continuation | -35.03 | -45.03 | 1.000 | n | 16% | n |

## Verdict rationale
- BTCUSDT: best net edge +0.44bps (RT cost 10.0bps)
- ETHUSDT: best net edge +0.69bps (RT cost 10.0bps)
- SOLUSDT: best net edge -9.56bps (RT cost 10.0bps)
- no horizon/orientation clears Holm-adjusted bootstrap + breadth + cost gates
