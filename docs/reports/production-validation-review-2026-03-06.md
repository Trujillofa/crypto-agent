# Production Validation Review

**Date:** March 6, 2026
**Scope:** Verify the March 6 Hetzner investigation against deployed config, live logs, and production database state.
**Supersedes:** `docs/reports/production-status-report-2026-03-05.md` for current deployment status.

## Summary

The March 6 investigation is mostly correct.

The deployed system is healthy, the 4-hour strategy cadence is correct, recent March 4 trades are real, and `agent2` is now configured for `BNBUSDT` rather than `SOLUSDT`.

The main correction is the explanation for why no trades fired on recent cycles. That explanation is valid for the default `SOLUSDT` cycle on March 6 at 10:22 UTC, but it does not fully explain `agent2` on March 6 at 02:22 UTC. In that `BNBUSDT` cycle, buy votes were strong enough to clear the configured threshold, so a downstream filter blocked the trade. Based on the live candle state, the likely blocker was the global trend filter because price was still below `EMA200`.

## What Was Verified

### Deployment state

- Hetzner is currently running branch `main` at commit `ee86bad`.
- Trading containers `agent`, `agent_2`, and `agent_btc` were healthy with about 23 hours uptime during validation.
- `agent_2` is wired to `config/settings.agent2.yaml`.

### Active config

- Default agent trades `SOLUSDT` on `4h`.
- `agent2` trades `BNBUSDT` on `4h`.
- `btc-4h` trades `BTCUSDT` on `4h`.
- All three use `strategy.evaluation_interval_seconds: 14400`.
- Default agent has Telegram and AI enabled.
- `agent2` and `btc-4h` have Telegram and AI disabled.
- Default aggregation settings use `buy_threshold: 0.8` and `min_agreement: 1`.
- Default `SOLUSDT` override requires `sell_min_agreement: 2`.
- `agent2` uses global thresholds with `sell_min_agreement: 2`.

## Runtime Evidence

### Recent strategy votes

Observed in Hetzner logs during the last 96 hours:

- March 5, 2026 22:22:55 UTC: default `SOLUSDT` logged `MACDHistogram -> SELL(0.55)`.
- March 6, 2026 10:22:56 UTC: default `SOLUSDT` logged `RSIReversal -> BUY(0.58)` and ended the cycle with `signals=0`.
- March 5, 2026 22:22:55 UTC: `agent2` `BNBUSDT` logged `Bollinger BUY(0.91)`, `CCI SELL(0.70)`, and `VWAP BUY(0.90)`.
- March 6, 2026 02:22:55 UTC: `agent2` `BNBUSDT` logged `RSI BUY(0.57)`, `MACD BUY(0.71)`, and `CCI BUY(0.90)`, then still ended the cycle with `signals=0`.
- March 6, 2026 10:22:56 UTC: `btc-4h` `BTCUSDT` logged `MACDHistogram -> SELL(0.69)` and ended the cycle with `signals=0`.

### Recent trades

Production DB confirms recent trades on March 4, 2026:

- `btc-4h::BTCUSDT` SELL at 15:00:47 UTC.
- `btc-4h::BTCUSDT` BUY at 16:00:03 UTC.
- `default::SOLUSDT` SELL at 19:43:50 UTC.
- `agent2::SOLUSDT` SELL at 19:43:50 UTC.
- `default::SOLUSDT` BUY at 20:43:52 UTC.
- `agent2::SOLUSDT` BUY at 20:43:53 UTC.
- `btc-4h::BTCUSDT` SELL at 19:43:51 UTC and BUY at 20:43:52 UTC.

### Indicator availability

Current 4-hour indicator counts from production:

- `SOLUSDT`: 4570 rows
- `BTCUSDT`: 4538 rows
- `BNBUSDT`: 4516 rows
- `ETHUSDT`: 4510 rows

## Corrections to the March 6 Investigation

### 1. "Only RSI is voting" is too narrow

That statement is true for one default-agent cycle on March 6, but it is not true across the current system state. Recent logs also show MACD, Bollinger, CCI, and VWAP votes on other symbols and cycles.

### 2. "No signals because scores are below threshold" is incomplete

That explanation matches the default `SOLUSDT` cycle at March 6 10:22 UTC, where `RSI BUY(0.58)` stayed below the `0.8` buy threshold.

It does not explain `agent2` at March 6 02:22 UTC. In that cycle, the sum of buy votes was well above threshold, but the engine still ended with `signals=0`.

The most likely explanation is a downstream long-entry filter. The `BNBUSDT` 4-hour candle at 2026-03-06 00:00:00 UTC had:

- `close_price = 649.7`
- `ema_200 = 694.71`

That means price was still below `EMA200`, which matches the code path that converts a BUY into HOLD under the global trend filter.

This point is an inference from deployed code plus production DB state. The expected explicit filter log was not present in the captured server window, which made the earlier report overconfident.

### 3. The March 5 production status report is stale

The older report reflects a prior deployment state. It no longer matches production on March 6, 2026.

Stale items in that report include:

- production branch state
- impossible `buy_threshold: 1.1` claim for the current deployment
- zero-trade claim for `agent2`

## Recommended follow-up

1. Keep the March 5 report as a historical snapshot, but treat this March 6 review as the current source of truth.
2. Log final HOLD reasons at `INFO` when a cycle had real strategy votes. This removes ambiguity when thresholds are met but a later filter blocks execution.
3. If needed, add one more server-side check for the March 6 `BNBUSDT` case after the new logging is deployed to confirm whether the blocker was the global trend filter or the BTC regime filter.
