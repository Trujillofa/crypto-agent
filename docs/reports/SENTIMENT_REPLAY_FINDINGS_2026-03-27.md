# Sentiment Replay Findings — 2026-03-27

## Summary

Long-range backtests for `settings.sentiment_macro.yaml` and executor-exit variants do **not** validate or invalidate the live Grok edge, because current backtests are not replaying historical Grok sentiment observations.

## What Was Verified

### 1. Real Grok sentiment is persisted
The runtime records sentiment observations through:
- `src/main.py` → `_record_sentiment_observation(...)`
- `src/core/event_log.py` → `EventLog.log(...)`

Persisted file:
- `data/event_log_sentiment-macro-bot.jsonl`

Event type:
- `sentiment_score`

Payload fields observed:
- `symbol`
- `score`
- `source`
- `provider`
- `model`

### 2. Current stored coverage is short
Observed coverage in `data/event_log_sentiment-macro-bot.jsonl`:
- `sentiment_score` events: 27
- symbols: BTCUSDT=9, ETHUSDT=9, SOLUSDT=9
- sources: all `xai_live`
- date range: 2026-03-27 12:32 UTC → 2026-03-27 20:34 UTC

This is enough to support a minimal replay implementation, but **not** enough for long-range historical validation.

### 3. Long-range backtests remain limited
The previously run 2024-06-01 → 2026-03-27 backtests tested the strategy without historical replay of real Grok observations.
That means they answer:
- how the strategy behaves without replayable real sentiment

They do **not** answer:
- whether live Grok sentiment materially improves the strategy
- whether the live edge is durable

## Recommendation

Implement minimal sentiment replay support now:
1. Load `sentiment_score` events from `data/event_log_sentiment-macro-bot.jsonl`
2. Bucket/lookup by symbol and timestamp
3. In backtest mode, inject replayed sentiment scores when available
4. Fall back to neutral sentiment only when no historical observation exists

## Expected Next Validation

Once replay support exists, compare:
1. replayed Grok sentiment
2. neutral/fallback sentiment
3. no sentiment gate

This should be run first on the currently covered short horizon, then repeated as historical coverage grows.

## Conclusion

The right next step is not further argument over current long-range backtests. The correct next step is replay support, because replay is the only way to test the actual live differential factor.
