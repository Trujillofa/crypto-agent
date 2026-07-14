# SOL 4h execution-parity v2 delta — 2026-07-10

## This comparison is inconclusive for promotion

The frozen production snapshot produced one closed trade across 2,041 SOLUSDT 4h bars. v2 changed the execution trace as intended, but one trade is not enough to validate fill parity or support a new research verdict. Do not start a new research lane from this result.

## The frozen inputs were reproducible

- Config: `config/settings.sol_trend_pullback_sparse.yaml`
- Candle range: 2025-06-01 00:00 UTC through 2026-05-08 00:00 UTC
- Candles: 2,041
- Funding settlements exported: 1,095
- Evidence payload: `/tmp/solusdt_4h_v1_v2_delta.json` on the machine that ran the comparison

The snapshot was exported with read-only SQL from production and both profiles ran locally against the same exported rows and funding events.

## v2 moved the entry to the next bar as designed

| Profile | Trades | Return | Entry | Exit | Exit reason | Fill source |
|---|---:|---:|---|---|---|---|
| `legacy_v1` | 1 | -0.00019% | 2026-05-07 16:00 UTC | 2026-05-08 00:00 UTC | `SIGNAL` | `signal_close` |
| `execution_parity_v2` | 1 | -0.00019% | 2026-05-07 20:00 UTC | 2026-05-08 00:00 UTC | `END_OF_DATA` | `next_bar_open` |

Both profiles used the same entry and exit prices in this isolated case. The v2 signal was queued at the prior bar close, filled at the next open, and the final-bar exit signal was correctly left unfilled before the engine closed the position at end of data. No funding settlement occurred while the position was open.

## Run the evidence gate before new research

Collect at least 20 closed paper or live fills for the active agent, match them to `signal_received` events and recorded trade rows, then compare their timing and fills with v2. Keep v2 as a shadow profile until that evidence exists. A sparse one-trade replay can't validate the model.
