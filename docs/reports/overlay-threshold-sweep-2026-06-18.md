# Overlay Threshold × Frequency Sweep — 2026-06-18

**Spec:** [overlay-threshold-frequency-sweep-v0.md](../specs/overlay-threshold-frequency-sweep-v0.md)
**Lane:** `sol-1h-trend-pullback-overlay-live` — SOLUSDT 1h, corrected costs, global trend filter ON (production mirror).

## Data coverage

- Requested span: 2024-01-01 → 2026-06-01
- DB coverage: 2024-01-09 → 2026-02-23
- **Effective span used:** 2024-01-09 → 2026-02-23
- Clamped: start=True, end=True

## Frontier (corrected costs, filter ON)

| buy_threshold | trades | trades/mo | wfo_return% | Sharpe | max_DD% | profit_conc% | p_loss% | passes |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.50 | 158 | 17.56 | -75.37 | -0.90 | 99.74 | 100.0 | 100.0 | FAIL |
| 0.60 | 121 | 13.44 | -50.73 | -0.49 | 97.51 | 100.0 | 99.6 | FAIL |
| 0.70 | 70 | 7.78 | -26.08 | -0.33 | 93.90 | 100.0 | 98.6 | FAIL |
| 0.80 | 51 | 5.67 | -17.45 | -0.40 | 79.15 | 100.0 | 91.2 | FAIL |
| 0.90 | 41 | 4.56 | 3.42 | 0.36 | 76.67 | 60.4 | 86.8 | FAIL |
| 1.00 | 27 | 3.00 | -21.58 | -0.36 | 79.14 | 100.0 | 94.8 | FAIL |
| 1.07 | 11 | 1.22 | 53.47 | 1.00 | 29.17 | 95.2 | 24.6 | FAIL |
| 1.27 | 6 | 0.67 | 19.42 | 1.05 | 28.43 | 54.0 | 30.4 | FAIL |

## Decision

**OVERLAY NOT A VIABLE FORWARD VEHICLE** — tradeable frequency appears only where the standard gate fails. The overlay edge depends on a confluence gate that is live-untradeable at corrected costs. Consolidation direction: document/accept or pivot.

_Pre-registered frequency floor: trades_per_month ≥ 2._
