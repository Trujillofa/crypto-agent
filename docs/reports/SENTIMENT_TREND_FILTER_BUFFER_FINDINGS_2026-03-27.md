# Sentiment Trend Filter Buffer Findings — 2026-03-27

## Summary

The backtest engine was patched to respect `strategy.global_trend_filter_buffer_pct`, matching runtime behavior.
This exposed a major live/backtest parity gap: previous backtests had been implicitly using strict EMA200 (`0%` buffer), while the live sentiment agent had been allowing buys up to `5%` below EMA200.

## Key Decision

The live paper sentiment agent was updated to:
- `global_trend_filter_buffer_pct: 0.0`

Reason: loosening the trend filter materially worsened historical results, especially on BTC.

## Backtest Window

- Timeframe: `1h`
- Range: `2025-01-01` → `2026-03-27`
- Strategy: `config/settings.sentiment_macro.yaml` family

## BTCUSDT Results

### 5% buffer (live-style historical behavior)
- Trades: 141
- Win rate: 46.10%
- Return: -42.47%
- Max DD: 47.45%
- Profit factor: 0.71

### 1% buffer
- Trades: 64
- Win rate: 46.88%
- Return: -20.64%
- Max DD: 27.84%
- Profit factor: 0.70

### 0% buffer (strict EMA200)
- Trades: 46
- Win rate: 52.17%
- Return: -5.64%
- Max DD: 16.71%
- Profit factor: 0.88

### BTC Conclusion
The `5%` buffer was catastrophic on BTC, allowing 95 extra trades and dramatically worse performance.
Strict EMA200 (`0%`) is clearly superior.

## ETHUSDT Results

### 5% buffer
- Trades: 42
- Win rate: 33.33%
- Return: -30.75%
- Max DD: 31.18%
- Profit factor: 0.45

### 1% buffer
- Trades: 58
- Win rate: 32.76%
- Return: -40.02%
- Max DD: 40.39%
- Profit factor: 0.45

### 0% buffer
- Trades: 42
- Win rate: 33.33%
- Return: -30.75%
- Max DD: 31.18%
- Profit factor: 0.45

### ETH Conclusion
No benefit from loosening the filter. `1%` was worse. Strict EMA200 is at least as good as the looser settings.

## SOLUSDT Results

### 5% buffer
- Trades: 43
- Win rate: 30.23%
- Return: -10.89%
- Max DD: 26.82%
- Profit factor: 0.84

### 1% buffer
- Trades: 54
- Win rate: 31.48%
- Return: -13.72%
- Max DD: 26.23%
- Profit factor: 0.84

### 0% buffer
- Trades: 43
- Win rate: 30.23%
- Return: -10.89%
- Max DD: 26.82%
- Profit factor: 0.84

### SOL Conclusion
No benefit from loosening the filter. `1%` was worse. Strict EMA200 is the safest choice.

## Cross-Asset Conclusion

- BTC strongly benefits from strict EMA200 filtering.
- ETH and SOL do not show any advantage from looser filtering.
- `1%` was worse than strict on ETH and SOL.
- `5%` is not justified by historical evidence and can be severely harmful.

## Operational Outcome

The live paper config `config/settings.sentiment_macro.yaml` was updated to:

```yaml
strategy:
  global_trend_filter_enabled: true
  global_trend_filter_buffer_pct: 0.0
```

and the `agent_sentiment_macro` container was restarted.

## Remaining Caveat

Even with strict EMA200, the sentiment strategy is still not profitable in these backtests.
Strict EMA200 is a major damage reduction, not a complete strategy fix.

## Recommended Next Steps

1. Keep strict EMA200 in live paper mode.
2. Continue collecting `sentiment_score` history for replay-backed validation.
3. Revisit model bake-off only after more replay coverage exists.
4. Focus future strategy work on replay-backed sentiment validation and/or redesign of entry logic rather than loosening the trend filter again.
