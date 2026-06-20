# Sentiment-Macro Volatility-Filter Sweep — 2026-06-19

**Spec:** [sentiment-vol-filter-sweep-v0.md](../specs/sentiment-vol-filter-sweep-v0.md)
**Lane:** `sentiment-macro` — SOLUSDT 1h, corrected costs, global trend filter ON, constant sentiment replay score **72**.

## Data coverage

- Requested span: 2024-01-01 → 2026-06-01
- DB coverage: 2024-01-09 → 2026-02-23
- **Effective span used:** 2024-01-09 → 2026-02-23
- Clamped: start=True, end=True

## Sentiment replay (held constant)

- Replay log: `research/sentiment-vol-filter-sweep/synthetic-sentiment-72.jsonl`
- Constant score: **72** (observed live median; bullish regime, never below FUD gate 35)
- Max age: 24h
- Scorer hit-rate: **1.0000** (18625/18625 lookups, 0 misses)

## Confounds (documented, not corrected)

1. **Constant-72 vs live [50,65) bars (~14%).** Holding 72 applies the +0.15 boost on 100% of bars vs ~85.7% live (boost 0.15 vs 0.05 on the remainder). Slightly optimistic confidence on ~14% of bars; does not change gate-pass logic (sentiment never < 35 live).
2. **Historical track record under the cost bug.** The 94 prior live trades ran at the old ~0.4% RT / ~8× funding defaults. A passing arm here still needs fresh forward validation at corrected costs.

## Frontier (corrected costs, filter ON)

| arm | trades | trades/mo | wfo_return% | Sharpe | max_DD% | profit_conc% | p_loss% | passes |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.0050 | 1 | 0.11 | -2.80 | -0.60 | 5.85 | 100.0 | 96.6 | FAIL |
| 0.0065 | 4 | 0.44 | -5.25 | -0.64 | 19.28 | 100.0 | 100.0 | FAIL |
| 0.0080 | 13 | 1.44 | -11.80 | -0.80 | 25.56 | 100.0 | 95.6 | FAIL |
| 0.0085 | 15 | 1.67 | -16.41 | -1.43 | 40.07 | 100.0 | 99.8 | FAIL |
| 0.0100 | 20 | 2.22 | -27.25 | -1.83 | 39.24 | 100.0 | 92.2 | FAIL |
| 0.0125 | 31 | 3.44 | -36.91 | -1.94 | 68.49 | 100.0 | 99.0 | FAIL |
| filter_off | 46 | 5.11 | -46.24 | -0.97 | 91.92 | 100.0 | 99.8 | FAIL |

## Decision

**VEHICLE DEAD** — tradeable frequency appears only where the standard gate fails. The strategy edge does not survive at corrected costs even recalibrated. Consolidation rec. #2: accept terminal state.

_Pre-registered frequency floor: trades_per_month ≥ 2._
