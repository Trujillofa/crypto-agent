# Overlay Threshold × Frequency Sweep — Extended Window (2026-10-31)

**Spec:** [overlay-threshold-frequency-sweep-v0.md](../specs/overlay-threshold-frequency-sweep-v0.md)
**Extension of:** [overlay-threshold-sweep-2026-06-18.md](overlay-threshold-sweep-2026-06-18.md) — same pre-registered spec, window extended 2026-06-01 → 2026-11-01 (8 additional months of out-of-sample data).
**Lane:** `sol-1h-trend-pullback-overlay-live` — SOLUSDT 1h, corrected costs, global trend filter ON (production mirror).

## Data coverage

- Requested span: 2024-01-01 → 2026-11-01
- DB coverage: 2024-01-09 → 2026-10-31
- **Effective span used:** 2024-01-09 → 2026-10-31
- Clamped: start=True, end=True

## Frontier (corrected costs, filter ON)

| buy_threshold | trades | trades/mo | wfo_return% | Sharpe | max_DD% | profit_conc% | p_loss% | passes |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.50 | 233 | 15.53 | -89.92 | -1.81 | 99.83 | 100.0 | 100.0 | FAIL |
| 0.60 | 174 | 11.60 | -55.71 | -0.69 | 95.70 | 100.0 | 98.0 | FAIL |
| 0.70 | 103 | 6.87 | -42.63 | -0.67 | 93.05 | 100.0 | 99.0 | FAIL |
| 0.80 | 80 | 5.33 | -44.77 | -0.74 | 87.00 | 100.0 | 96.4 | FAIL |
| 0.90 | 60 | 4.00 | -26.45 | -0.51 | 79.82 | 66.1 | 94.2 | FAIL |
| 1.00 | 42 | 2.80 | -44.31 | -0.94 | 86.01 | 100.0 | 97.0 | FAIL |
| 1.07 | 19 | 1.27 | -1.05 | -0.21 | 50.26 | 50.8 | 85.8 | FAIL |
| 1.27 | 11 | 0.73 | 23.36 | 0.94 | 30.35 | 42.1 | 53.4 | FAIL |

## Decision

**OVERLAY NOT A VIABLE FORWARD VEHICLE** — tradeable frequency appears only where the standard gate fails. The overlay edge depends on a confluence gate that is live-untradeable at corrected costs. Consolidation direction: document/accept or pivot.

_Pre-registered frequency floor: trades_per_month ≥ 2._
