# Order-Flow Microstructure Probe — Report

**Verdict:** **NO_PULSE**
**Script:** `scripts/probe_orderflow_microstructure.py`
**Framing:** aggTrade signed-flow (OFI) → forward return by horizon; sub-10s-only vs ≥60s is the decisive stack gate.

## Regime window
- Period: 2026-05-23 → 2026-06-06 (anchor: BTCUSDT)
- Elevated-vol UTC days: 2026-05-23, 2026-06-01, 2026-06-02, 2026-06-03, 2026-06-04, 2026-06-05
- Quiet-vol UTC days: 2026-05-24, 2026-05-25, 2026-05-30, 2026-05-31
- Selection: Selected 14d window with 6 elevated-vol days (>=p75=380.1bps) and 4 quiet-vol days (<=p25=200.1bps) from daily (high-low)/close range.

## STEP 0 — Data feasibility
- **BTCUSDT**: trades=17,146,187, coverage=100.0%, half_spread=0.00bps, sign_corr=0.0086
- **ETHUSDT**: trades=13,879,670, coverage=100.0%, half_spread=0.02bps, sign_corr=0.0448
- **SOLUSDT**: trades=2,320,786, coverage=100.0%, half_spread=0.00bps, sign_corr=0.1374

## Horizon curve (top − bottom decile spread, bps)
| Symbol | 1s | 5s | 10s | 30s | 60s | 300s |
|--------|------:|------:|------:|------:|------:|------:|
| BTCUSDT | +0.46 | +0.81 | +1.18 | +1.82 | +2.16 | +2.71 |
| ETHUSDT | +0.40 | +0.55 | +0.85 | +1.46 | +2.40 | +4.14 |
| SOLUSDT | +0.33 | +0.60 | +0.84 | +1.23 | +2.03 | +4.72 |

## Per-symbol detail
### BTCUSDT
| h(s) | spread | monotonic | p_adj | shuffled | net edge | H1 | H3 | conc |
|------|-------:|-----------|------:|---------:|---------:|:--:|:--:|:--:|
| 1 | +0.46 | n | 1.0000 | -0.00 | -9.55 | n | n | 36% |
| 5 | +0.81 | n | 1.0000 | -0.09 | -9.19 | n | n | 32% |
| 10 | +1.18 | n | 1.0000 | -0.03 | -8.82 | n | n | 31% |
| 30 | +1.82 | n | 1.0000 | +0.20 | -8.18 | n | n | 30% |
| 60 | +2.16 | n | 1.0000 | +0.09 | -7.84 | n | n | 30% |
| 300 | +2.71 | n | 1.0000 | +0.37 | -7.29 | n | n | 32% |

### ETHUSDT
| h(s) | spread | monotonic | p_adj | shuffled | net edge | H1 | H3 | conc |
|------|-------:|-----------|------:|---------:|---------:|:--:|:--:|:--:|
| 1 | +0.40 | n | 1.0000 | +0.01 | -9.86 | n | n | 36% |
| 5 | +0.55 | n | 1.0000 | +0.04 | -9.71 | n | n | 37% |
| 10 | +0.85 | n | 1.0000 | +0.09 | -9.41 | n | n | 37% |
| 30 | +1.46 | n | 1.0000 | +0.27 | -8.80 | n | n | 39% |
| 60 | +2.40 | n | 1.0000 | -0.00 | -7.86 | n | n | 38% |
| 300 | +4.14 | n | 1.0000 | +0.35 | -6.12 | n | n | 36% |

### SOLUSDT
| h(s) | spread | monotonic | p_adj | shuffled | net edge | H1 | H3 | conc |
|------|-------:|-----------|------:|---------:|---------:|:--:|:--:|:--:|
| 1 | +0.33 | n | 1.0000 | -0.06 | -10.37 | n | n | 34% |
| 5 | +0.60 | n | 1.0000 | +0.00 | -10.09 | n | n | 36% |
| 10 | +0.84 | n | 1.0000 | -0.01 | -9.85 | n | n | 40% |
| 30 | +1.23 | n | 1.0000 | +0.44 | -9.46 | n | n | 43% |
| 60 | +2.03 | n | 1.0000 | +0.50 | -8.67 | n | n | 42% |
| 300 | +4.72 | n | 1.0000 | +0.79 | -5.97 | n | n | 41% |

## Verdict rationale
- no monotonic significant OFI-return relationship on >=2 symbols (symbols with any H1=0)
