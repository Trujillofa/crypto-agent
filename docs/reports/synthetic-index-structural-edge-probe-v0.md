# Synthetic-Index Structural Edge Probe v0

**Verdict:** **BLOCKED_ON_DATA**
**Generated:** 2026-06-27T16:41:36.342813+00:00
**Spec:** [synthetic-index-structural-edge-probe-v0.md](../specs/synthetic-index-structural-edge-probe-v0.md)
**Data:** Deriv public `active_symbols` + `ticks_history` WebSocket endpoints

## Frozen configuration

- Window: latest 30 complete UTC days
- Candle: 60s
- Calibration/forward split: 40%/60%
- Block-sign resamples: 2000
- Round-trip cost proxy: 20.0 bps
- Daily concentration cap: 25%
- Seed: 42
- Global EMA200 trend filter: false / not applicable

## Data audit

| Requested instrument | Resolved symbol | Candles | Coverage UTC | Error |
|---|---:|---:|---|---|
| Volatility 75 Index | R_75 | 43190 | 2026-05-28T16:37:00+00:00 → 2026-06-27T16:26:00+00:00 | — |
| Volatility 100 Index | R_100 | 43190 | 2026-05-28T16:37:00+00:00 → 2026-06-27T16:26:00+00:00 | — |
| Volatility 50 Index | R_50 | 43190 | 2026-05-28T16:37:00+00:00 → 2026-06-27T16:26:00+00:00 | — |
| Crash 1000 Index | CRASH1000 | 43190 | 2026-05-28T16:37:00+00:00 → 2026-06-27T16:26:00+00:00 | — |
| Boom 1000 Index | BOOM1000 | 43190 | 2026-05-28T16:37:00+00:00 → 2026-06-27T16:26:00+00:00 | — |
| Crash 500 Index | CRASH500 | 43190 | 2026-05-28T16:37:00+00:00 → 2026-06-27T16:26:00+00:00 | — |
| Boom 500 Index | BOOM500 | 43190 | 2026-05-28T16:37:00+00:00 → 2026-06-27T16:26:00+00:00 | — |
| Step Index | stpRNG | 43190 | 2026-05-28T16:37:00+00:00 → 2026-06-27T16:26:00+00:00 | — |
| Jump 100 Index | JD100 | 43198 | 2026-05-28T16:37:00+00:00 → 2026-06-27T16:34:00+00:00 | — |
| Range Break 100 Index | — | 0 | — → — | not returned by active_symbols |

## Forward results

| Instrument | Hypothesis | Cal/Fwd N | Cal bps | Fwd bps | Net bps | p_adj | Max day | Family pass | Gate |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Volatility 75 Index | momentum | 1151/1727 | -1.19 | -0.79 | -20.79 | 1.0000 | 6.3% | 0 | FAIL |
| Volatility 100 Index | momentum | 1151/1727 | +0.06 | +1.02 | -18.98 | 1.0000 | 6.5% | 0 | FAIL |
| Volatility 50 Index | momentum | 1151/1727 | +0.70 | -0.95 | -20.95 | 1.0000 | 6.1% | 0 | FAIL |
| Crash 1000 Index | drift | 3449/5170 | +0.14 | -0.00 | -20.00 | 1.0000 | 6.2% | 0 | FAIL |
| Boom 1000 Index | drift | 3450/5171 | -0.01 | -0.12 | -20.12 | 1.0000 | 6.0% | 0 | FAIL |
| Crash 500 Index | drift | 3448/5172 | -0.01 | -0.08 | -20.08 | 1.0000 | 6.0% | 0 | FAIL |
| Boom 500 Index | drift | 3449/5170 | +0.12 | +0.00 | -20.00 | 1.0000 | 6.1% | 0 | FAIL |
| Step Index | reversion | 15523/23110 | +0.00 | +0.01 | -19.99 | 0.9085 | 5.8% | 0 | FAIL |
| Jump 100 Index | jump_continuation | 169/239 | +2.00 | -4.95 | -24.95 | 1.0000 | 13.2% | 0 | FAIL |

## Verdict reasons

- Range Break 100 Index: not returned by active_symbols
- 9 evaluated instruments: no frozen hypothesis clears the Holm-adjusted block-sign null

## Interpretation ceiling

The cost input is a screening proxy because historical candles do not contain executable Deriv MT5/cTrader bid/ask quotes, commissions, financing, or slippage. Even a proxy-cost pulse is not deployment evidence; it requires a separate executable-cost study and forward demo execution.
